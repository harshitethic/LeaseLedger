"""Re-run the actual user-facing demo and verify its JSON contract."""

import json
import subprocess
import sys
import unittest

from examples.recovery_walkthrough import demonstrate


class RecoveryWalkthroughTests(unittest.TestCase):
    def test_demo_calls_the_real_queue_and_preserves_invariants(self):
        report = demonstrate()
        self.assertTrue(report["verified"])
        self.assertEqual(len(report["steps"]), 4)
        by_step = {item["step"]: item for item in report["steps"]}
        self.assertFalse(by_step["idempotent_enqueue"]["created_duplicate"])
        self.assertTrue(by_step["idempotent_enqueue"]["same_job_id"])
        self.assertEqual(by_step["claim"]["fence"], 1)
        recovery = by_step["expired_lease_reclaimed"]
        self.assertEqual(recovery["replacement_fence"], 2)
        self.assertFalse(recovery["old_ack_accepted"])
        self.assertTrue(recovery["current_ack_accepted"])
        backup = by_step["verified_online_backup_restore"]
        self.assertTrue(backup["integrity_healthy"])
        self.assertTrue(backup["backup_consistent"])
        self.assertEqual(backup["event_actions"], ["enqueued", "claimed", "claimed", "completed"])
        self.assertEqual(backup["queue_counts"]["done"], 1)

    def test_documented_module_command_emits_parseable_real_results(self):
        output = subprocess.run(
            [sys.executable, "-m", "examples.recovery_walkthrough"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        report = json.loads(output.stdout)
        self.assertTrue(report["verified"])
        self.assertEqual(report["project"], "LeaseLedger")
        self.assertEqual(output.stderr, "")
        from pathlib import Path

        documented = json.loads(
            (Path(__file__).parent.parent / "docs" / "recovery-demo-output.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report, documented, "Checked-in demo evidence is out of date")


if __name__ == "__main__":
    unittest.main()
