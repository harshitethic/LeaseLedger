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

    def test_claim_topic_uses_enqueue_topic_syntax(self):
        job = self.store.enqueue("valid.topic", {})
        for topic in ("has spaces", "../escape", "\n", 44, [], ""):
            with self.subTest(topic=topic), self.assertRaises(ValueError):
                self.store.claim("worker", topic=topic)
        self.assertEqual(self.store.claim("worker", topic="valid.topic").job_id, job.job_id)

    def test_fail_invalid_reason_does_not_mutate_active_lease(self):
        job = self.store.enqueue("work", {})
        lease = self.store.claim("worker")
        for reason in (None, 42, {"message": "error"}):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.store.fail(lease, reason)
        self.assertEqual(self.store.get(job.job_id)["status"], "leased")
        self.assertTrue(self.store.fail(lease, "temporary", base_delay=0))

    def test_job_id_boundaries_are_consistent(self):
        job = self.store.enqueue("work", {})
        for job_id in (0, -1, True, 1.0, "1", None):
            for method in (self.store.get, self.store.events, self.store.requeue_dead):
                with self.subTest(job_id=job_id, method=method.__name__), self.assertRaises(ValueError):
                    method(job_id)
        self.assertEqual(self.store.get(job.job_id)["status"], "queued")
        self.assertEqual(self.store.events(job.job_id)[0]["action"], "enqueued")

    def test_worker_checks_handler_before_queue_processing(self):
        for handler in (None, "module:function", 42, object()):
            with self.subTest(handler=handler), self.assertRaises(ValueError):
                Worker(self.store, handler)

    def test_worker_rejects_invalid_names_before_running(self):
        for name in ("", "x" * 101, "worker\nspoof", 3, b"worker"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Worker(self.store, lambda _: None, name=name)
        self.assertTrue(Worker(self.store, lambda _: None).name.startswith("worker-"))

    def test_worker_topic_validation_matches_store(self):
        for topic in ("", "invalid topic", "../escape", 42, "\n"):
            with self.subTest(topic=topic), self.assertRaises(ValueError):
                Worker(self.store, lambda _: None, topic=topic)
        self.assertEqual(Worker(self.store, lambda _: None, topic="ok.topic").topic, "ok.topic")

    def test_worker_backoff_is_bounded_before_handler_failure(self):
        for backoff in (-1, float("nan"), float("inf"), 3600.01, "5", None):
            with self.subTest(backoff=backoff), self.assertRaises(ValueError):
                Worker(self.store, lambda _: None, backoff_seconds=backoff)
        self.assertEqual(Worker(self.store, lambda _: None, backoff_seconds=0).backoff_seconds, 0)

    def test_max_jobs_rejects_coercible_nonintegers(self):
        worker = Worker(self.store, lambda _: None)
        for max_jobs in (0, -1, True, 1.5, "2"):
            with self.subTest(max_jobs=max_jobs), self.assertRaises(ValueError):
                worker.run(max_jobs=max_jobs, stop_when_empty=True)

    # INSERT_CASES

if __name__ == "__main__":
    unittest.main()
