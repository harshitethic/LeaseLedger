from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


class MutableClock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = MutableClock()
        self.path = Path(self.temp.name) / "jobs.db"
        self.queue = Store(self.path, clock=self.clock)

    def test_job_ids_reject_boolean_and_fractional_aliases(self):
        first = self.queue.enqueue("x", {})
        self.assertEqual(self.queue.get(first.job_id)["id"], first.job_id)
        for bad in (True, False, 1.0, 0, -1, "1"):
            with self.subTest(job_id=bad):
                for method in (self.queue.get, self.queue.events, self.queue.requeue_dead):
                    with self.assertRaises(ValueError):
                        method(bad)
        self.assertEqual(self.queue.stats()["queued"], 1)

    def test_empty_stats_and_no_claim(self):
        self.assertEqual(self.queue.stats(), {"queued": 0, "leased": 0, "done": 0, "dead": 0})
        self.assertIsNone(self.queue.claim("worker"))

    def test_round_trip_unicode_json_and_event_journal(self):
        job = self.queue.enqueue("email.send", {"msg": "你好 🌍"}, key="message-1")
        lease = self.queue.claim("worker-A")
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.payload, {"msg": "你好 🌍"})
        self.assertEqual(lease.fence, 1)
        self.assertTrue(self.queue.complete(lease))
        self.assertEqual(self.queue.get(job.job_id)["status"], "done")
        self.assertEqual(
            [x["action"] for x in self.queue.events(job.job_id)], ["enqueued", "claimed", "completed"]
        )

    def test_idempotent_enqueue_returns_existing_id_without_replacement(self):
        first = self.queue.enqueue("alpha", {"value": 1}, key="external-42")
        second = self.queue.enqueue("beta", {"value": 2}, key="external-42")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(self.queue.get(first.job_id)["payload"], {"value": 1})
        self.assertEqual(self.queue.stats()["queued"], 1)

    def test_without_keys_identical_payloads_are_independent_jobs(self):
        a = self.queue.enqueue("t", {})
        b = self.queue.enqueue("t", {})
        self.assertNotEqual(a.job_id, b.job_id)

    def test_invalid_inputs_rejected(self):
        for topic in ("", "has spaces", "../escape"):
            with self.subTest(topic=topic), self.assertRaises(ValueError):
                self.queue.enqueue(topic, {})
        for opts in (
            {"priority": 1001},
            {"priority": 1.5},
            {"priority": True},
            {"max_attempts": 0},
            {"max_attempts": 1.5},
            {"max_attempts": True},
            {"delay": -1},
            {"delay": True},
            {"delay": "3"},
            {"delay": None},
            {"delay": float("inf")},
            {"key": ""},
        ):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                self.queue.enqueue("valid", {}, **opts)
        with self.assertRaises(ValueError):
            self.queue.enqueue("valid", {"bad": float("nan")})
        with self.assertRaises(ValueError):
            self.queue.enqueue("valid", {"large": "x" * 65536})

    def test_priority_tie_breaking_and_ready_delay(self):
        self.queue.enqueue("tasks", {"id": "later"}, priority=99, delay=10)
        self.queue.enqueue("tasks", {"id": "low"}, priority=1)
        self.queue.enqueue("tasks", {"id": "high"}, priority=9)
        self.assertEqual(self.queue.claim("x").payload["id"], "high")
        self.assertEqual(self.queue.claim("x").payload["id"], "low")
        self.assertIsNone(self.queue.claim("x"))
        self.clock.advance(10)
        self.assertEqual(self.queue.claim("x").payload["id"], "later")

    def test_topic_filter_preserves_other_queued_jobs(self):
        self.queue.enqueue("a", 1)
        self.queue.enqueue("b", 2)
        self.assertEqual(self.queue.claim("x", topic="b").topic, "b")
        self.assertEqual(self.queue.stats()["queued"], 1)
        self.assertEqual(self.queue.claim("x", topic="a").topic, "a")

    def test_crashed_worker_reclaim_increments_attempt_and_fence(self):
        job = self.queue.enqueue("work", {"x": 1}, max_attempts=3)
        stale = self.queue.claim("first", lease_seconds=10)
        self.clock.advance(10.1)
        current = self.queue.claim("second", lease_seconds=10)
        self.assertEqual(current.job_id, job.job_id)
        self.assertEqual((current.attempt, current.fence), (2, 2))
        self.assertFalse(self.queue.complete(stale))
        self.assertFalse(self.queue.heartbeat(stale))
        self.assertFalse(self.queue.fail(stale, "obsolete"))
        self.assertTrue(self.queue.complete(current))
        self.assertEqual(self.queue.stats()["done"], 1)

    def test_no_ack_after_expired_lease_even_before_reclaim(self):
        job = self.queue.enqueue("work", {})
        lease = self.queue.claim("A", lease_seconds=10)
        self.clock.advance(11)
        self.assertFalse(self.queue.complete(lease))
        self.assertEqual(self.queue.get(job.job_id)["status"], "leased")

    def test_final_attempt_crash_goes_dead_not_infinite_retry(self):
        job = self.queue.enqueue("w", {}, max_attempts=2)
        self.queue.claim("A", lease_seconds=10)
        self.clock.advance(11)
        second = self.queue.claim("B", lease_seconds=10)
        self.assertEqual(second.attempt, 2)
        self.clock.advance(11)
        self.assertIsNone(self.queue.claim("C"))
        self.assertEqual(self.queue.get(job.job_id)["status"], "dead")
        self.assertIn("dead", [e["action"] for e in self.queue.events(job.job_id)])

    def test_heartbeat_extends_lease_and_avoids_reclaim(self):
        self.queue.enqueue("w", {})
        lease = self.queue.claim("A", lease_seconds=10)
        self.clock.advance(9)
        self.assertTrue(self.queue.heartbeat(lease, extend_seconds=20))
        self.clock.advance(9)
        self.assertIsNone(self.queue.claim("B"))
        self.assertTrue(self.queue.complete(lease))

    def test_heartbeat_rejects_ambiguous_extension_without_event(self):
        job = self.queue.enqueue("work", {})
        lease = self.queue.claim("first", lease_seconds=10)
        before = self.queue.events(job.job_id)
        for extension in (True, None, "5", float("nan")):
            with self.subTest(extend_seconds=extension), self.assertRaises(ValueError):
                self.queue.heartbeat(lease, extend_seconds=extension)
        self.assertEqual(self.queue.events(job.job_id), before)
        self.assertTrue(self.queue.complete(lease))

    def test_failure_retry_has_backoff_then_dead_letter(self):
        job = self.queue.enqueue("w", {}, max_attempts=3)
        for index, delay in enumerate((2, 4, 8), start=1):
            lease = self.queue.claim("A", lease_seconds=10)
            self.assertEqual(lease.attempt, index)
            self.assertTrue(self.queue.fail(lease, "temporary", base_delay=2))
            if index < 3:
                self.assertEqual(self.queue.get(job.job_id)["status"], "queued")
                self.assertIsNone(self.queue.claim("B"))
                self.clock.advance(delay)
            else:
                self.assertEqual(self.queue.get(job.job_id)["status"], "dead")
        self.assertEqual([e["action"] for e in self.queue.events(job.job_id)].count("retry"), 2)

    def test_dead_letter_can_be_requeued_explicitly(self):
        job = self.queue.enqueue("x", {}, max_attempts=1)
        lease = self.queue.claim("A")
        self.assertTrue(self.queue.fail(lease, "bad input"))
        self.assertFalse(self.queue.requeue_dead(99999))
        self.assertTrue(self.queue.requeue_dead(job.job_id))
        self.assertEqual(self.queue.claim("B").attempt, 1)
        self.assertEqual(self.queue.get(job.job_id)["fence"], 2)

    def test_fencing_prevents_wrong_owner(self):
        self.queue.enqueue("work", {})
        valid = self.queue.claim("A")
        forged = type(valid)(
            valid.job_id, valid.topic, valid.payload, "B", valid.fence, valid.attempt, valid.expires_at
        )
        self.assertFalse(self.queue.complete(forged))
        self.assertFalse(self.queue.heartbeat(forged))
        self.assertTrue(self.queue.complete(valid))

    def test_database_survives_restarting_queue(self):
        job = self.queue.enqueue("persist", {"value": 42})
        other = Store(self.path, clock=self.clock)
        lease = other.claim("second-process")
        self.assertEqual(lease.job_id, job.job_id)
        self.assertTrue(other.complete(lease))
        self.assertEqual(self.queue.stats()["done"], 1)
        self.assertEqual(len(self.queue.events(job.job_id)), 3)

    def test_reject_bad_time_and_bad_lease_values(self):
        with self.assertRaises(ValueError):
            self.queue.claim("")
        with self.assertRaises(ValueError):
            self.queue.claim("worker", lease_seconds=0)
        for duration in (True, None, "5", float("nan")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.queue.claim("worker", lease_seconds=duration)
        self.clock.value = float("nan")
        with self.assertRaises(ValueError):
            self.queue.enqueue("valid", {})


if __name__ == "__main__":
    unittest.main()
