"""A healthy SQLite file may be an unusable queue after schema damage."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from leaseledger import Store


class QueueSchemaIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "jobs.db"
        self.store = Store(self.path)

    def test_missing_events_table_fails_integrity_and_backup(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE events")

        report = self.store.verify_integrity()
        self.assertEqual(report["quick_check"], "ok")
        self.assertEqual(report["foreign_key_violations"], 0)
        self.assertFalse(report["healthy"])
        target = self.path.parent / "damaged-backup.db"
        with self.assertRaisesRegex(sqlite3.DatabaseError, "missing required queue"):
            self.store.backup_to(target)
        self.assertFalse(target.exists())

    def test_missing_required_job_column_fails_integrity_and_backup(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("ALTER TABLE jobs DROP COLUMN last_error")
        report = self.store.verify_integrity()
        self.assertFalse(report["healthy"])
        target = self.path.parent / "incomplete-backup.db"
        with self.assertRaisesRegex(sqlite3.DatabaseError, "missing required queue"):
            self.store.backup_to(target)
        self.assertFalse(target.exists())

    def test_normal_queue_and_backup_remain_healthy(self):
        job = self.store.enqueue("receipt", {"order": 123})
        self.assertTrue(self.store.verify_integrity()["healthy"])
        backup = self.store.backup_to(self.path.parent / "snapshot.db")
        self.assertTrue(backup.is_file())
        restored = Store(backup)
        self.assertTrue(restored.verify_integrity()["healthy"])
        self.assertEqual(restored.get(job.job_id)["payload"], {"order": 123})


if __name__ == "__main__":
    unittest.main()
