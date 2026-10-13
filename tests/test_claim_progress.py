"""Prevent false 'empty queue' when expired terminal leases fill a claim batch."""

import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


class TestClock:
    def __init__(self) -> None:
        self.t = 1_900_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class ClaimProgressTests(unittest.TestCase):
    def test_more_than_one_cleanup_batch_does_not_hide_ready_work(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = TestClock()
            store = Store(Path(folder) / "jobs.db", clock=clock)

            # All these have already consumed their only permitted attempt.
            for number in range(121):
                store.enqueue("poison", {"n": number}, priority=100, max_attempts=1)
                lease = store.claim("worker", lease_seconds=1, topic="poison")
                self.assertIsNotNone(lease)

            ready = store.enqueue("useful", {"run": True}, priority=0)
            clock.advance(1.1)

            # A single claim must not return None just because its bounded
            # cleanup batch encountered too many terminal expired leases.
            result = store.claim("replacement")
            self.assertIsNotNone(result, "A ready job exists behind cleanup batch")
            self.assertEqual(result.job_id, ready.job_id)
            self.assertTrue(store.complete(result))
            self.assertEqual(store.stats()["dead"], 121)
            self.assertEqual(store.stats()["done"], 1)
            self.assertEqual(store.stats()["leased"], 0)


if __name__ == "__main__":
    unittest.main()
