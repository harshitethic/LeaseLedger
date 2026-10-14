"""One-command, offline queue recovery demo with verified observable outcomes.

Run: python -m examples.recovery_walkthrough

Uses a real SQLite database in a temporary directory and a controlled clock.
No provider credentials, network access, shell execution or sleeping required.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from leaseledger import Store


class DemoClock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def demonstrate() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    clock = DemoClock()
    with tempfile.TemporaryDirectory(prefix="leaseledger-demo-") as directory:
        workspace = Path(directory)
        store = Store(workspace / "demo.db", clock=clock)

        initial = store.enqueue("receipts", {"order": "DEMO-42"}, key="receipt-DEMO-42")
        duplicate = store.enqueue("receipts", {"order": "DEMO-42"}, key="receipt-DEMO-42")
        assert initial.created and not duplicate.created
        assert initial.job_id == duplicate.job_id
        steps.append(
            {
                "step": "idempotent_enqueue",
                "created_first": initial.created,
                "created_duplicate": duplicate.created,
                "same_job_id": initial.job_id == duplicate.job_id,
            }
        )

        expired = store.claim("worker-A", lease_seconds=10)
        assert expired is not None
        steps.append(
            {
                "step": "claim",
                "owner": expired.owner,
                "attempt": expired.attempt,
                "fence": expired.fence,
            }
        )

        # Simulate a process crash or long pause by advancing a controlled
        # clock; no real system is killed and no external side effects occur.
        clock.advance(11)
        replacement = store.claim("worker-B", lease_seconds=10)
        assert replacement is not None
        assert replacement.job_id == expired.job_id
        assert replacement.fence == expired.fence + 1
        stale_ack = store.complete(expired)
        current_ack = store.complete(replacement)
        assert not stale_ack and current_ack
        steps.append(
            {
                "step": "expired_lease_reclaimed",
                "replacement_owner": replacement.owner,
                "replacement_attempt": replacement.attempt,
                "replacement_fence": replacement.fence,
                "old_ack_accepted": stale_ack,
                "current_ack_accepted": current_ack,
            }
        )

        snapshot = store.backup_to(workspace / "verified-snapshot.db")
        recovered = Store(snapshot, clock=clock)
        original = store.stats()
        from_snapshot = recovered.stats()
        verification = recovered.verify_integrity()
        history = [entry["action"] for entry in recovered.events(initial.job_id)]
        assert original == from_snapshot
        assert original == {"queued": 0, "leased": 0, "done": 1, "dead": 0}
        assert verification["healthy"]
        assert history == ["enqueued", "claimed", "claimed", "completed"]
        steps.append(
            {
                "step": "verified_online_backup_restore",
                "backup_consistent": original == from_snapshot,
                "integrity_healthy": verification["healthy"],
                "queue_counts": from_snapshot,
                "event_actions": history,
            }
        )

    return {
        "project": "LeaseLedger",
        "demo": "deterministic_offline_recovery",
        "scope": "single-host SQLite; at-least-once; no external side effect",
        "steps": steps,
        "verified": True,
    }


def main() -> None:
    print(json.dumps(demonstrate(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
