"""Reproducible local measurements, not a published universal throughput claim."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import tempfile
import time
from pathlib import Path

from leaseledger import Store


def worker(db: str, results) -> None:
    store = Store(db)
    completed = 0
    while True:
        lease = store.claim(f"benchmark-worker-{multiprocessing.current_process().pid}")
        if lease is None:
            break
        if store.complete(lease):
            completed += 1
    results.put(completed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 200_000 or not 1 <= args.workers <= 64:
        parser.error("jobs must be 1..200000, workers 1..64")
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "bench.db")
        queue = Store(db)
        start_enqueue = time.perf_counter()
        for n in range(args.jobs):
            queue.enqueue("benchmark", {"n": n})
        enqueue_secs = time.perf_counter() - start_enqueue

        ctx = multiprocessing.get_context("spawn")
        with ctx.Manager() as manager:
            results = manager.Queue()
            start_work = time.perf_counter()
            procs = [ctx.Process(target=worker, args=(db, results)) for _ in range(args.workers)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(timeout=120)
                if proc.is_alive():
                    proc.terminate()
                    proc.join()
                    raise RuntimeError("Worker exceeded 120-second benchmark budget")
                if proc.exitcode != 0:
                    raise RuntimeError(f"Worker failed: exit {proc.exitcode}")
            completed = sum(results.get(timeout=15) for _ in procs)
            process_secs = time.perf_counter() - start_work
        counts = queue.stats()
        if completed != args.jobs or counts["done"] != args.jobs:
            raise RuntimeError(f"Benchmark lost jobs: completed={completed} stats={counts}")
        print(
            json.dumps(
                {
                    "jobs": args.jobs,
                    "workers": args.workers,
                    "enqueue_seconds": round(enqueue_secs, 4),
                    "process_seconds": round(process_secs, 4),
                    "enqueue_jobs_per_second": round(args.jobs / enqueue_secs, 2),
                    "processed_jobs_per_second": round(args.jobs / process_secs, 2),
                    "final_stats": counts,
                    "scope": "local single-host SQLite WAL; measures this run only",
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
