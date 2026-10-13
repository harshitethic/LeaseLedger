"""Concurrency integration: separate OS processes compete for the same jobs."""

from __future__ import annotations

import multiprocessing
import os
import time
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


def claim_then_crash(db_path: str, output) -> None:
    """Simulate an abrupt SIGKILL-equivalent exit after durable claim commit."""
    store = Store(db_path)
    lease = store.claim("crashed-child", lease_seconds=1)
    output.send(lease)
    output.close()
    os._exit(23)


class MultiprocessTests(unittest.TestCase):
    def test_abrupt_process_death_releases_only_after_lease_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "crash.db"
            store = Store(path)
            item = store.enqueue("crash-recovery", {"value": 42}, max_attempts=3)
            ctx = multiprocessing.get_context("spawn")
            receive, send = ctx.Pipe(duplex=False)
            process = ctx.Process(target=claim_then_crash, args=(str(path), send))
            process.start()
            send.close()
            try:
                self.assertTrue(receive.poll(10))
                first = receive.recv()
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 23)
                self.assertEqual(first.job_id, item.job_id)
                self.assertEqual(first.fence, 1)
                self.assertIsNone(store.claim("replacement", lease_seconds=1))
                time.sleep(1.3)
                replacement = store.claim("replacement", lease_seconds=1)
                self.assertIsNotNone(replacement)
                self.assertEqual(replacement.attempt, 2)
                self.assertEqual(replacement.fence, 2)
                self.assertFalse(store.complete(first))
                self.assertTrue(store.complete(replacement))
                self.assertEqual(store.stats()["done"], 1)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                receive.close()

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
