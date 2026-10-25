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

    # INSERT_CASES

if __name__ == "__main__":
    unittest.main()
