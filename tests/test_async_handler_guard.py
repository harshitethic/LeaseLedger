"""Handlers must do their work before a durable job is acknowledged."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from leaseledger import Store, Worker


class SynchronousHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "jobs.db")

    def test_async_function_rejected_before_claim(self):
        async def handler(payload):
            await asyncio.sleep(0)

        job = self.store.enqueue("task", {"value": 1})
        with self.assertRaisesRegex(ValueError, "synchronous"):
            Worker(self.store, handler)
        self.assertEqual(self.store.get(job.job_id)["status"], "queued")

    def test_async_callable_instance_rejected_before_claim(self):
        class AsyncCallable:
            async def __call__(self, payload):
                return payload

        with self.assertRaisesRegex(ValueError, "synchronous"):
            Worker(self.store, AsyncCallable())

    def test_coroutine_returned_from_sync_wrapper_fails_the_job(self):
        invoked = []
        def wrapper(payload):
            invoked.append(payload)
            return asyncio.sleep(0)

        job = self.store.enqueue("task", {"value": 42}, max_attempts=1)
        worker = Worker(self.store, wrapper, backoff_seconds=0)
        stats = worker.run(max_jobs=1)

        self.assertEqual(invoked, [{"value": 42}])
        self.assertEqual((stats.completed, stats.failed), (0, 1))
        record = self.store.get(job.job_id)
        self.assertEqual(record["status"], "dead")
        self.assertIn("awaitable", record["last_error"])
        self.assertEqual(
            [item["action"] for item in self.store.events(job.job_id)],
            ["enqueued", "claimed", "dead"],
        )

    def test_sync_function_completes_normally(self):
        invoked = []
        job = self.store.enqueue("task", {"value": 9})
        worker = Worker(self.store, lambda payload: invoked.append(payload))
        stats = worker.run(max_jobs=1)
        self.assertEqual(stats.completed, 1)
        self.assertEqual(invoked, [{"value": 9}])
        self.assertEqual(self.store.get(job.job_id)["status"], "done")


if __name__ == "__main__":
    unittest.main()
