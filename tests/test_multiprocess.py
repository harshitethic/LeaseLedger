"""Concurrency integration: separate OS processes compete for the same jobs."""

from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


def worker_process(db_path: str, results) -> None:
    store = Store(db_path)
    while True:
        lease = store.claim(f"pid-{multiprocessing.current_process().pid}", lease_seconds=30)
        if lease is None:
            break
        if store.complete(lease):
            results.put(lease.job_id)


class MultiprocessTests(unittest.TestCase):
    def test_four_processes_claim_each_job_once_without_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "concurrent.db"
            store = Store(path)
            ids = {store.enqueue("demo", {"id": n}).job_id for n in range(48)}
            ctx = multiprocessing.get_context("spawn")
            with ctx.Manager() as manager:
                results = manager.Queue()
                processes = [ctx.Process(target=worker_process, args=(str(path), results)) for _ in range(4)]
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=35)
                    if process.is_alive():
                        process.terminate()
                        process.join()
                        self.fail("Worker process did not exit")
                    self.assertEqual(process.exitcode, 0)
                completed = [results.get(timeout=5) for _ in range(len(ids))]
            self.assertEqual(set(completed), ids)
            self.assertEqual(len(completed), len(set(completed)))
            self.assertEqual(store.stats()["done"], 48)


if __name__ == "__main__":
    unittest.main()
