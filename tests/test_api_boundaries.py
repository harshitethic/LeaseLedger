"""Regression tests for invalid public API inputs and early worker validation."""
import tempfile
import unittest
from pathlib import Path
from leaseledger import Store, Worker

class ApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "jobs.db")

    def test_enqueue_rejects_non_string_topics(self):
        for value in (None, 42, [], {"name": "work"}, b"work"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.enqueue(value, {})
        self.assertEqual(self.store.stats()["queued"], 0)

    def test_enqueue_rejects_non_string_idempotency_keys(self):
        for value in (1, False, ["id"], {"key": "x"}, b"key"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.enqueue("work", {}, key=value)
        self.assertEqual(self.store.stats()["queued"], 0)

    def test_priority_must_be_an_integer_not_bool_or_float(self):
        for value in (True, False, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.enqueue("work", {}, priority=value)
        self.assertTrue(self.store.enqueue("work", {}, priority=-1000).created)

    def test_max_attempts_rejects_fractional_and_boolean_values(self):
        for value in (True, False, 2.5, "3", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.enqueue("work", {}, max_attempts=value)
        self.assertTrue(self.store.enqueue("work", {}, max_attempts=1).created)

    def test_claim_owner_rejects_non_strings_without_claiming(self):
        job = self.store.enqueue("work", {})
        for value in (None, 123, [], b"worker"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.claim(value)
        self.assertEqual(self.store.get(job.job_id)["status"], "queued")

    def test_claim_owner_rejects_control_characters(self):
        job = self.store.enqueue("work", {})
        for owner in ("worker\nspoof", "worker\x00hidden", "worker\r", "worker\t"):
            with self.subTest(owner=repr(owner)), self.assertRaises(ValueError):
                self.store.claim(owner)
        self.assertEqual(self.store.get(job.job_id)["status"], "queued")

    # INSERT_CASES

if __name__ == "__main__":
    unittest.main()
