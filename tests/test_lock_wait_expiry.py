"""A real SQLite writer wait must not freeze lease-validation time."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from leaseledger import Store
from tests.test_store import MutableClock


class ObservedStore(Store):
    @contextmanager
    def _connection(self):
        with super()._connection() as connection:
            # SQLite calls this immediately before executing BEGIN IMMEDIATE.
            # The other connection already holds the write lock at that point.
            connection.set_trace_callback(
                lambda sql: self.write_started.set() if sql == "BEGIN IMMEDIATE" else None
            )
            yield connection


class LockWaitExpiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "jobs.db"
        self.clock = MutableClock()
        self.queue = ObservedStore(self.path, clock=self.clock)
        self.queue.write_started = threading.Event()

    def after_lock_wait(self, operation, *, elapsed=10):
        self.queue.write_started.clear()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(operation)
                try:
                    self.assertTrue(self.queue.write_started.wait(timeout=3), "writer never attempted BEGIN")
                    self.assertFalse(future.done(), "operation bypassed the held SQLite write lock")
                    self.clock.advance(elapsed)
                finally:
                    blocker.rollback()
                return future.result(timeout=3)

    def assert_expired_operation_refused(self, action):
        job = self.queue.enqueue("work", {})
        lease = self.queue.claim("worker", lease_seconds=10)
        before = self.queue.get(job.job_id)
        events = self.queue.events(job.job_id)
        self.assertFalse(self.after_lock_wait(lambda: action(lease)))
        self.assertEqual(self.queue.get(job.job_id), before)
        self.assertEqual(self.queue.events(job.job_id), events)
        replacement = self.queue.claim("replacement")
        self.assertEqual(replacement.fence, lease.fence + 1)
        self.assertTrue(self.queue.complete(replacement))

    def test_ack_refuses_lease_expired_while_waiting_for_writer(self):
        self.assert_expired_operation_refused(self.queue.complete)

    def test_heartbeat_cannot_revive_lease_expired_during_writer_wait(self):
        self.assert_expired_operation_refused(self.queue.heartbeat)

    def test_failure_cannot_requeue_lease_expired_during_writer_wait(self):
        self.assert_expired_operation_refused(lambda lease: self.queue.fail(lease, "temporary"))

    def test_claim_sees_job_becoming_ready_during_writer_wait(self):
        job = self.queue.enqueue("delayed", {}, delay=5)
        lease = self.after_lock_wait(lambda: self.queue.claim("worker", lease_seconds=30))
        self.assertIsNotNone(lease)
        self.assertEqual(lease.job_id, job.job_id)
        self.assertEqual(lease.expires_at, self.clock.value + 30)
        self.assertEqual(self.queue.events(job.job_id)[-1]["occurred_at"], self.clock.value)

    def test_claim_grants_full_interval_after_lock_acquisition(self):
        self.queue.enqueue("work", {})
        lease = self.after_lock_wait(lambda: self.queue.claim("worker", lease_seconds=5))
        self.assertEqual(lease.expires_at, self.clock.value + 5)
        self.assertTrue(self.queue.complete(lease))

    def test_enqueue_delay_starts_after_lock_acquisition(self):
        job = self.after_lock_wait(lambda: self.queue.enqueue("work", {}, delay=5))
        row = self.queue.get(job.job_id)
        self.assertEqual(row["created_at"], self.clock.value)
        self.assertEqual(row["available_at"], self.clock.value + 5)
        self.assertIsNone(self.queue.claim("worker"))
        self.clock.advance(5)
        self.assertEqual(self.queue.claim("worker").job_id, job.job_id)
