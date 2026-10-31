"""Reject boolean worker backoff values before accepting work."""

import tempfile
import unittest
from pathlib import Path

from leaseledger import Store, Worker


class WorkerBackoffValidationTests(unittest.TestCase):
    def test_boolean_backoff_is_rejected_before_any_job_is_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "jobs.db")
            store.enqueue("demo", {"id": 1})

            for invalid in (True, False):
                with self.subTest(backoff=invalid):
                    with self.assertRaisesRegex(ValueError, "Invalid worker backoff interval"):
                        Worker(store, lambda payload: None, backoff_seconds=invalid)
            self.assertEqual(store.stats()["queued"], 1)

    def test_numeric_zero_backoff_remains_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "jobs.db")
            worker = Worker(store, lambda payload: None, backoff_seconds=0)
            self.assertEqual(worker.backoff_seconds, 0)


if __name__ == "__main__":
    unittest.main()
