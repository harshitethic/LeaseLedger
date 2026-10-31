"""Invalid lone surrogates must fail predictably without mutating the queue."""

import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


class UnicodeValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "jobs.db")

    def test_surrogate_payload_is_rejected_before_a_job_is_inserted(self):
        with self.assertRaisesRegex(ValueError, "Job payload must be JSON-serializable"):
            self.store.enqueue("demo", {"broken": "\ud800"})
        self.assertEqual(self.store.stats()["queued"], 0)

    def test_surrogate_idempotency_key_is_rejected_before_insert(self):
        with self.assertRaisesRegex(ValueError, "Invalid idempotency key"):
            self.store.enqueue("demo", {}, key="\ud800")
        self.assertEqual(self.store.stats()["queued"], 0)

    def test_surrogate_worker_name_does_not_claim_available_job(self):
        job_id = self.store.enqueue("demo", {}).job_id
        with self.assertRaisesRegex(ValueError, "Invalid lease parameters"):
            self.store.claim("\ud800")
        self.assertEqual(self.store.get(job_id)["status"], "queued")

    def test_surrogate_failure_reason_preserves_existing_lease(self):
        self.store.enqueue("demo", {})
        lease = self.store.claim("worker")
        with self.assertRaisesRegex(ValueError, "Failure reason must be valid UTF-8"):
            self.store.fail(lease, "bad \udfff")
        self.assertEqual(self.store.get(lease.job_id)["status"], "leased")

    def test_non_ascii_unicode_remains_supported(self):
        outcome = self.store.enqueue("demo", {"text": "हैलो 🙂"}, key="ключ")
        lease = self.store.claim("worker-é")
        self.assertEqual(lease.job_id, outcome.job_id)
        self.assertTrue(self.store.fail(lease, "échec"))


if __name__ == "__main__":
    unittest.main()
