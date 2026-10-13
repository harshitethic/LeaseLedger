"""Deterministic, seeded fault-injection model for queue transitions."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


class Clock:
    def __init__(self):
        self.value = 1_950_000_000.0

    def __call__(self):
        return self.value


class SeededModelTests(unittest.TestCase):
    def run_seed(self, seed):
        randomizer = random.Random(seed)
        clock = Clock()
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "state.db", clock=clock)
            jobs = {}
            keys = {}
            handles = []
            actions = {}
            for step in range(160):
                choice = randomizer.randrange(100)
                if choice < 24 and len(jobs) < 20:
                    key = None if randomizer.randrange(4) else f"key-{randomizer.randrange(7)}"
                    topic = randomizer.choice(["a", "b"])
                    priority = randomizer.choice([-2, 0, 5])
                    delay = randomizer.choice([0, 2, 7])
                    attempts = randomizer.choice([1, 2, 3])
                    result = store.enqueue(
                        topic,
                        {"step": step},
                        key=key,
                        priority=priority,
                        delay=delay,
                        max_attempts=attempts,
                    )
                    if key in keys and key is not None:
                        self.assertFalse(result.created)
                        self.assertEqual(result.job_id, keys[key])
                    else:
                        self.assertTrue(result.created)
                        jobs[result.job_id] = {
                            "status": "queued",
                            "topic": topic,
                            "priority": priority,
                            "available_at": clock.value + delay,
                            "attempts": 0,
                            "max_attempts": attempts,
                            "fence": 0,
                            "lease_owner": None,
                            "lease_until": None,
                        }
                        actions[result.job_id] = ["enqueued"]
                        if key is not None:
                            keys[key] = result.job_id
                elif choice < 57:
                    topic = randomizer.choice([None, "a", "b"])
                    owner = randomizer.choice(["A", "B"])
                    duration = randomizer.choice([1, 4, 12])

                    # Independent reference selection with the same documented
                    # priority/order policy, but no access to SQLite queries.
                    expected = None
                    while True:
                        candidates = [
                            (id, j)
                            for id, j in jobs.items()
                            if (topic is None or j["topic"] == topic)
                            and (
                                (j["status"] == "queued" and j["available_at"] <= clock.value)
                                or (j["status"] == "leased" and j["lease_until"] <= clock.value)
                            )
                        ]
                        if not candidates:
                            break
                        id, j = min(
                            candidates,
                            key=lambda pair: (
                                -pair[1]["priority"],
                                pair[1]["lease_until"]
                                if pair[1]["status"] == "leased"
                                else pair[1]["available_at"],
                                pair[0],
                            ),
                        )
                        if j["status"] == "leased" and j["attempts"] >= j["max_attempts"]:
                            j.update(status="dead", lease_owner=None, lease_until=None)
                            actions[id].append("dead")
                            continue
                        j.update(status="leased", lease_owner=owner, lease_until=clock.value + duration)
                        j["attempts"] += 1
                        j["fence"] += 1
                        actions[id].append("claimed")
                        expected = (id, j["fence"], j["attempts"])
                        break

                    lease = store.claim(owner, topic=topic, lease_seconds=duration)
                    self.assertEqual(lease is None, expected is None, (seed, step))
                    if lease:
                        self.assertEqual(
                            (lease.job_id, lease.fence, lease.attempt),
                            expected,
                            (seed, step),
                        )
                        handles.append(lease)
                elif choice < 80 and handles:
                    lease = randomizer.choice(handles)
                    j = jobs[lease.job_id]
                    valid = (
                        j["status"] == "leased"
                        and j["lease_owner"] == lease.owner
                        and j["fence"] == lease.fence
                        and j["lease_until"] > clock.value
                    )
                    operation = randomizer.choice(["heartbeat", "complete", "fail"])
                    if operation == "heartbeat":
                        extension = randomizer.choice([1, 4, 10])
                        actual = store.heartbeat(lease, extend_seconds=extension)
                        if valid:
                            j["lease_until"] = max(j["lease_until"], clock.value + extension)
                            actions[lease.job_id].append("heartbeat")
                    elif operation == "complete":
                        actual = store.complete(lease)
                        if valid:
                            j.update(status="done", lease_owner=None, lease_until=None)
                            actions[lease.job_id].append("completed")
                    else:
                        base = randomizer.choice([0, 1, 3])
                        actual = store.fail(lease, "test failure", base_delay=base)
                        if valid:
                            dead = j["attempts"] >= j["max_attempts"]
                            j.update(
                                status="dead" if dead else "queued",
                                available_at=clock.value
                                if dead
                                else clock.value + min(3600, base * 2 ** (j["attempts"] - 1)),
                                lease_owner=None,
                                lease_until=None,
                            )
                            actions[lease.job_id].append("dead" if dead else "retry")
                    self.assertEqual(actual, valid, (seed, step, operation))
                elif choice < 89 and jobs:
                    id = randomizer.choice(list(jobs))
                    j = jobs[id]
                    dead = j["status"] == "dead"
                    self.assertEqual(store.requeue_dead(id), dead, (seed, step))
                    if dead:
                        j.update(
                            status="queued",
                            attempts=0,
                            available_at=clock.value,
                            lease_owner=None,
                            lease_until=None,
                        )
                        actions[id].append("requeued")
                else:
                    clock.value += randomizer.choice([0, 0.5, 1, 2, 6, 16])

                # Batching invariant checks keeps CI fast while still
                # exercising every state transition between checkpoints.
                if step % 10 != 0 and step != 159:
                    continue
                for id, expected in jobs.items():
                    actual = store.get(id)
                    for field, value in expected.items():
                        self.assertEqual(actual[field], value, (seed, step, id, field))
                    self.assertEqual(
                        [x["action"] for x in store.events(id)],
                        actions[id],
                        (seed, step, id),
                    )

            self.assertTrue(store.verify_integrity()["healthy"])
            for status, count in store.stats().items():
                self.assertEqual(
                    count,
                    sum(j["status"] == status for j in jobs.values()),
                    (seed, status),
                )

    def test_seeded_sequences(self):
        for seed in (1, 7, 29, 2026):
            with self.subTest(seed=seed):
                self.run_seed(seed)


if __name__ == "__main__":
    unittest.main()
