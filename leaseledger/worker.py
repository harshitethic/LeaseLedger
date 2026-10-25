"""Trusted user-supplied handlers with automatic lease heartbeats."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .store import Lease, Store

log = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    stale: int = 0


class Worker:
    """Single-threaded handler loop; heartbeat thread extends a durable lease.

    Handler code is trusted and intentionally runs on the host. This module is
    a job queue, NOT a sandbox for executing arbitrary third-party functions.
    """

    def __init__(
        self,
        store: Store,
        handler: Callable[[Any], Any],
        *,
        name: str | None = None,
        topic: str | None = None,
        lease_seconds: float = 30,
        poll_seconds: float = 0.5,
        backoff_seconds: float = 1,
    ) -> None:
        if not 3 <= lease_seconds <= 86400:
            raise ValueError("Lease interval must be >=3 seconds")
        if not 0.01 <= poll_seconds <= 60:
            raise ValueError("Invalid poll interval")
        if not callable(handler):
            raise ValueError("Worker handler must be callable")
        self.store = store
        self.handler = handler
        self.name = name or f"worker-{uuid.uuid4().hex[:12]}"
        self.topic = topic
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.backoff_seconds = backoff_seconds
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _heartbeat(self, lease: Lease, stop: threading.Event) -> None:
        interval = max(1, self.lease_seconds / 3)
        while not stop.wait(interval):
            try:
                if not self.store.heartbeat(lease, extend_seconds=self.lease_seconds):
                    log.warning("Worker %s lost lease %s; handler may be duplicated", self.name, lease.job_id)
                    return
            except Exception:
                log.exception("Lease heartbeat failed for job %s", lease.job_id)
                return

    def run(self, *, max_jobs: int | None = None, stop_when_empty: bool = False) -> WorkerStats:
        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be >=1")
        result = WorkerStats()
        while not self.stop_event.is_set():
            if max_jobs is not None and result.claimed >= max_jobs:
                break
            lease = self.store.claim(self.name, lease_seconds=self.lease_seconds, topic=self.topic)
            if lease is None:
                if stop_when_empty:
                    break
                self.stop_event.wait(self.poll_seconds)
                continue
            result.claimed += 1
            heartbeat_stop = threading.Event()
            thread = threading.Thread(
                target=self._heartbeat,
                args=(lease, heartbeat_stop),
                daemon=True,
                name=f"leaseledger-heartbeat-{lease.job_id}",
            )
            thread.start()
            error: str | None = None
            try:
                self.handler(lease.payload)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
            finally:
                heartbeat_stop.set()
                thread.join(timeout=max(1, self.lease_seconds))
            if error is not None:
                if self.store.fail(lease, error, base_delay=self.backoff_seconds):
                    result.failed += 1
                else:
                    result.stale += 1
            elif self.store.complete(lease):
                result.completed += 1
            else:
                result.stale += 1
        return result
