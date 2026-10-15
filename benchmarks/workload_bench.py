"""Repeated, finite-backlog workload measurements with verified job accounting."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time

from leaseledger import Store


def distribution(values):
    """Nearest-rank percentiles; no interpolation or averaging of percentiles."""
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "samples": len(ordered),
        **{f"p{p}": ordered[math.ceil(p / 100 * len(ordered)) - 1] for p in (50, 95, 99)},
        "max": ordered[-1],
    }


def worker(db, folder, number, gate, handler_ms, retry_every):
    queue = Store(db)
    folder = Path(folder)
    (folder / f"{number}.ready").touch()
    gate.wait()
    samples = {name: [] for name in ("claim_ms", "handler_ms", "ack_ms", "retry_ms", "residence_ms")}
    completed = []
    attempts = 0
    while True:
        started = time.perf_counter_ns()
        lease = queue.claim(f"bench-{number}", lease_seconds=60)
        claimed = time.perf_counter_ns()
        if lease is None:
            break
        attempts += 1
        samples["claim_ms"].append((claimed - started) / 1e6)
        handler_start = time.perf_counter_ns()
        if handler_ms:
            time.sleep(handler_ms / 1000)
        samples["handler_ms"].append((time.perf_counter_ns() - handler_start) / 1e6)
        retry = retry_every and (lease.payload["n"] + 1) % retry_every == 0 and lease.attempt == 1
        before_write = time.perf_counter_ns()
        if retry:
            if not queue.fail(lease, "synthetic retry", base_delay=0):
                raise RuntimeError("Retry rejected; workload lost its lease")
            samples["retry_ms"].append((time.perf_counter_ns() - before_write) / 1e6)
        else:
            if not queue.complete(lease):
                raise RuntimeError("ACK rejected; workload lost its lease")
            finished = time.perf_counter_ns()
            samples["ack_ms"].append((finished - before_write) / 1e6)
            samples["residence_ms"].append((finished - lease.payload["enqueued_ns"]) / 1e6)
            completed.append(lease.job_id)
    finished_ns = time.perf_counter_ns()
    (folder / f"{number}.json").write_text(
        json.dumps(
            {"completed": completed, "attempts": attempts, "samples": samples, "finished_ns": finished_ns}
        )
    )


def validate(jobs, workers, handler_ms, retry_every, timeout):
    if not 1 <= jobs <= 20_000 or not 1 <= workers <= 16:
        raise ValueError("jobs must be 1..20000 and workers 1..16")
    if not math.isfinite(handler_ms) or not 0 <= handler_ms <= 100:
        raise ValueError("handler-ms must be finite and in 0..100")
    if not 0 <= retry_every <= jobs:
        raise ValueError("retry-every must be 0 (disabled) or 1..jobs")
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("timeout must be finite and in (0, 600]")


def run_trial(*, jobs, workers, handler_ms=0, retry_every=0, timeout=120, worker_target=worker):
    validate(jobs, workers, handler_ms, retry_every, timeout)
    with tempfile.TemporaryDirectory(prefix="leaseledger-workload-") as directory:
        root = Path(directory)
        db = root / "queue.db"
        queue = Store(db)
        enqueues = []
        ids = set()
        enqueue_start = time.perf_counter()
        for n in range(jobs):
            before = time.perf_counter_ns()
            job = queue.enqueue(
                "workload", {"n": n, "body": "x" * 512, "enqueued_ns": before}, max_attempts=2
            )
            enqueues.append((time.perf_counter_ns() - before) / 1e6)
            ids.add(job.job_id)
        enqueue_seconds = time.perf_counter() - enqueue_start
        with queue._connection() as connection:
            settings = {
                name: connection.execute("PRAGMA " + name).fetchone()[0]
                for name in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys", "page_size")
            }
        ctx = multiprocessing.get_context("spawn")
        gate = ctx.Event()
        processes = []
        launch_start = time.perf_counter()
        deadline = time.monotonic() + timeout
        try:
            for number in range(workers):
                process = ctx.Process(
                    target=worker_target, args=(str(db), directory, number, gate, handler_ms, retry_every)
                )
                process.start()
                processes.append(process)
            while not all((root / f"{number}.ready").exists() for number in range(workers)):
                if any(process.exitcode is not None for process in processes):
                    raise RuntimeError("Worker exited before the start barrier")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Worker startup exceeded trial deadline")
                time.sleep(0.005)
            startup_seconds = time.perf_counter() - launch_start
            start_ns = time.perf_counter_ns()
            gate.set()
            pending = {process.sentinel: process for process in processes}
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Worker processing exceeded trial deadline")
                for sentinel in wait(pending, timeout=remaining):
                    process = pending.pop(sentinel)
                    process.join()
                    if process.exitcode != 0:
                        raise RuntimeError(f"Worker failed with exit code {process.exitcode}")
            worker_wall_seconds = time.perf_counter() - launch_start
        finally:
            # Reap every child before its database is removed, including siblings
            # of a failed worker. Never leave a workload running after a timeout.
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                process.close()
        reports = [json.loads((root / f"{n}.json").read_text()) for n in range(workers)]
        completed = [job_id for report in reports for job_id in report["completed"]]
        retries = jobs // retry_every if retry_every else 0
        attempts = sum(report["attempts"] for report in reports)
        counts = queue.stats()
        with queue._connection() as connection:
            events = dict(connection.execute("SELECT action,COUNT(*) FROM events GROUP BY action").fetchall())
        expected_events = {"enqueued": jobs, "claimed": jobs + retries, "completed": jobs}
        if retries:
            expected_events["retry"] = retries
        if Counter(completed) != Counter(ids) or attempts != jobs + retries:
            raise RuntimeError("Missing/duplicate jobs or unexpected attempts")
        if counts != {"queued": 0, "leased": 0, "done": jobs, "dead": 0} or events != expected_events:
            raise RuntimeError("Final state or event journal does not match workload")
        if not queue.verify_integrity()["healthy"]:
            raise RuntimeError("SQLite integrity check failed")
        drain_seconds = (max(report["finished_ns"] for report in reports) - start_ns) / 1e9
        latencies = {"enqueue_ms": distribution(enqueues)}
        for name in reports[0]["samples"]:
            latencies[name] = distribution([value for report in reports for value in report["samples"][name]])
        return {
            "enqueue_seconds": enqueue_seconds,
            "startup_seconds": startup_seconds,
            "drain_seconds": drain_seconds,
            "worker_wall_seconds": worker_wall_seconds,
            "completed_jobs_per_second": jobs / drain_seconds,
            "attempts": attempts,
            "latency": latencies,
            "completed_by_worker": [len(report["completed"]) for report in reports],
            "final_stats": counts,
            "event_counts": events,
            "sqlite": settings,
        }


def run_sweep(*, jobs, worker_counts, handler_delays, repeats, retry_every=0, timeout=120):
    if not 1 <= repeats <= 10 or not worker_counts or not handler_delays:
        raise ValueError("repeats must be 1..10; supply workers and handler delays")
    if len(worker_counts) > 4 or len(handler_delays) > 4:
        raise ValueError("At most four worker counts and four handler delays")
    # Validate the entire sweep before starting any subprocess or database.
    for count in worker_counts:
        for delay in handler_delays:
            validate(jobs, count, delay, retry_every, timeout)
    runs = []
    for delay in handler_delays:
        for count in worker_counts:
            trials = [
                run_trial(
                    jobs=jobs, workers=count, handler_ms=delay, retry_every=retry_every, timeout=timeout
                )
                for _ in range(repeats)
            ]
            rates = [trial["completed_jobs_per_second"] for trial in trials]
            runs.append(
                {
                    "workers": count,
                    "handler_ms": delay,
                    "trials": trials,
                    "rate_summary": {
                        "median": statistics.median(rates),
                        "min": min(rates),
                        "max": max(rates),
                    },
                }
            )
    root = Path(__file__).resolve().parents[1]
    source = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("leaseledger/store.py", "benchmarks/workload_bench.py")
    }
    return {
        "schema_version": 1,
        "scope": "finite preloaded backlog; synthetic no-op/blocked-I/O handlers; single host; not production capacity",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "sqlite": sqlite3.sqlite_version,
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "start_method": "spawn",
            "source_sha256": source,
        },
        "parameters": {
            "jobs": jobs,
            "worker_counts": worker_counts,
            "handler_delays_ms": handler_delays,
            "repeats": repeats,
            "retry_every": retry_every,
            "trial_timeout_seconds": timeout,
            "body_bytes": 512,
        },
        "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=500)
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument("--handler-ms", default="0,5")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--retry-every", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        result = run_sweep(
            jobs=args.jobs,
            worker_counts=[int(v) for v in args.workers.split(",")],
            handler_delays=[float(v) for v in args.handler_ms.split(",")],
            repeats=args.repeats,
            retry_every=args.retry_every,
            timeout=args.timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
