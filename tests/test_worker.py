from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from leaseledger import Store, Worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "worker.db")

    def test_worker_drains_queue_and_returns_real_stats(self):
        for i in range(5):
            self.store.enqueue("x", {"id": i})
        seen = []
        result = Worker(self.store, lambda payload: seen.append(payload["id"]), lease_seconds=3).run(
            stop_when_empty=True
        )
        self.assertEqual(sorted(seen), list(range(5)))
        self.assertEqual((result.claimed, result.completed, result.failed, result.stale), (5, 5, 0, 0))
        self.assertEqual(self.store.stats()["done"], 5)

    def test_worker_catches_handler_error_and_schedules_retry(self):
        job = self.store.enqueue("task", {"x": 1}, max_attempts=2)

        def crash(_):
            raise RuntimeError("simulated worker failure")

        stats = Worker(self.store, crash, lease_seconds=3, backoff_seconds=60).run(stop_when_empty=True)
        self.assertEqual(stats.failed, 1)
        state = self.store.get(job.job_id)
        self.assertEqual(state["attempts"], 1)
        self.assertEqual(state["status"], "queued")
        self.assertIn("RuntimeError", state["last_error"])

    def test_slow_worker_renews_lease(self):
        job = self.store.enqueue("task", {"x": 1})

        def slow(_):
            time.sleep(3.3)

        stats = Worker(self.store, slow, lease_seconds=3).run(stop_when_empty=True)
        self.assertEqual(stats.completed, 1)
        self.assertIn("heartbeat", [event["action"] for event in self.store.events(job.job_id)])

    def test_invalid_worker_config(self):
        with self.assertRaises(ValueError):
            Worker(self.store, lambda _: None, lease_seconds=1)
        with self.assertRaises(ValueError):
            Worker(self.store, lambda _: None, poll_seconds=0)
        with self.assertRaises(ValueError):
            Worker(self.store, lambda _: None).run(max_jobs=0)
        for max_jobs in (True, 0.5, float("nan"), float("inf"), "1"):
            with self.subTest(max_jobs=max_jobs), self.assertRaises(ValueError):
                Worker(self.store, lambda _: None).run(max_jobs=max_jobs)
        for interval in (-1, float("nan"), float("inf"), -float("inf"), 3601):
            with self.subTest(backoff=interval), self.assertRaises(ValueError):
                Worker(self.store, lambda _: None, backoff_seconds=interval)


if __name__ == "__main__":
    unittest.main()
