"""Small operational CLI for queue inspection and trusted local workers."""

from __future__ import annotations

import argparse
import importlib
import json
import signal
from pathlib import Path
from typing import Any, Callable

from .store import Store
from .worker import Worker


def _trusted_handler(spec: str) -> Callable[[Any], Any]:
    """Import explicitly user-selected trusted Python code (not untrusted jobs)."""
    if ":" not in spec:
        raise ValueError("Handler must look like module:function")
    module, name = spec.split(":", 1)
    if not module or not name.isidentifier():
        raise ValueError("Invalid handler specification")
    fn = getattr(importlib.import_module(module), name)
    if not callable(fn):
        raise ValueError("Handler is not callable")
    return fn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leaseledger", description="Durable, fenced single-host job queue")
    parser.add_argument("--db", default="leaseledger.db", help="SQLite path (local filesystem only)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize SQLite tables")
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("topic")
    enqueue.add_argument("payload", help="JSON payload")
    enqueue.add_argument("--key")
    enqueue.add_argument("--priority", type=int, default=0)
    enqueue.add_argument("--attempts", type=int, default=3)
    enqueue.add_argument("--delay", type=float, default=0)

    work = commands.add_parser("work", help="Run trusted local handler")
    work.add_argument("--handler", required=True, help="module:function")
    work.add_argument("--topic")
    work.add_argument("--once", action="store_true", help="Exit when no jobs are ready")
    work.add_argument("--max-jobs", type=int)
    work.add_argument("--name")
    work.add_argument("--lease", type=float, default=30)

    commands.add_parser("verify", help="Check SQLite page and foreign-key integrity")
    backup = commands.add_parser("backup", help="Consistent online backup; never overwrites files")
    backup.add_argument("destination", help="Path to a NEW backup file")
    commands.add_parser("stats")
    get = commands.add_parser("get")
    get.add_argument("id", type=int)
    events = commands.add_parser("events")
    events.add_argument("id", type=int)
    retry = commands.add_parser("retry")
    retry.add_argument("id", type=int)
    args = parser.parse_args(argv)
    queue = Store(Path(args.db))

    if args.command == "init":
        print(f"Initialized {queue.path}")
    elif args.command == "enqueue":
        outcome = queue.enqueue(
            args.topic,
            json.loads(args.payload),
            key=args.key,
            priority=args.priority,
            max_attempts=args.attempts,
            delay=args.delay,
        )
        print(json.dumps({"job_id": outcome.job_id, "created": outcome.created}))
    elif args.command == "verify":
        report = queue.verify_integrity()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["healthy"] else 1
    elif args.command == "backup":
        saved = queue.backup_to(args.destination)
        print(json.dumps({"backup": str(saved), "verified": True}))
    elif args.command == "stats":
        print(json.dumps(queue.stats(), sort_keys=True))
    elif args.command == "get":
        print(json.dumps(queue.get(args.id), sort_keys=True))
    elif args.command == "events":
        print(json.dumps(queue.events(args.id), sort_keys=True))
    elif args.command == "retry":
        print(json.dumps({"requeued": queue.requeue_dead(args.id)}))
    elif args.command == "work":
        fn = _trusted_handler(args.handler)
        worker = Worker(
            queue,
            fn,
            name=args.name,
            topic=args.topic,
            lease_seconds=args.lease,
        )
        # SIGTERM handler only valid in main thread.
        signal.signal(signal.SIGTERM, lambda *_: worker.stop())
        signal.signal(signal.SIGINT, lambda *_: worker.stop())
        result = worker.run(max_jobs=args.max_jobs, stop_when_empty=args.once)
        print(json.dumps(vars(result), sort_keys=True))
    return 0
