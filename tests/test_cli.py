import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_cli_enqueue_work_inspect_and_events(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "cli.db")

            def invoke(*args):
                return subprocess.run(
                    [sys.executable, "-m", "leaseledger", "--db", db, *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True,
                    timeout=30,
                ).stdout.strip()

            self.assertIn("Initialized", invoke("init"))
            created = json.loads(invoke("enqueue", "receipt", '{"order_id":"A42"}', "--key", "order-A42"))
            self.assertTrue(created["created"])
            repeated = json.loads(invoke("enqueue", "receipt", '{"order_id":"A42"}', "--key", "order-A42"))
            self.assertFalse(repeated["created"])
            handled = json.loads(
                invoke("work", "--handler", "examples.handlers:print_receipt", "--once", "--max-jobs", "1")
            )
            self.assertEqual(handled["completed"], 1)
            self.assertEqual(json.loads(invoke("stats"))["done"], 1)
            report = json.loads(invoke("verify"))
            self.assertTrue(report["healthy"])
            backup = str(Path(temp) / "snapshot.db")
            saved = json.loads(invoke("backup", backup))
            self.assertEqual(saved["backup"], backup)
            self.assertTrue(saved["verified"])
            self.assertTrue(Path(backup).is_file())
            self.assertEqual(json.loads(invoke("get", str(created["job_id"])))["status"], "done")
            events = json.loads(invoke("events", str(created["job_id"])))
            self.assertEqual([e["action"] for e in events], ["enqueued", "claimed", "completed"])


if __name__ == "__main__":
    unittest.main()
