"""Online SQLite backup/restore and corruption-detection regression coverage."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from leaseledger import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "jobs.db")

    def test_consistent_backup_has_job_and_audit_events(self):
        first = self.store.enqueue("queue", {"id": 1})
        second = self.store.enqueue("queue", {"id": 2})
        lease = self.store.claim("w")
        self.assertTrue(self.store.complete(lease))
        original = self.store.verify_integrity()
        self.assertEqual(
            original,
            {
                "quick_check": "ok",
                "foreign_key_violations": 0,
                "healthy": True,
            },
        )

        destination = self.root / "backup.db"
        output = self.store.backup_to(destination)
        self.assertEqual(output, destination)
        self.assertTrue(output.is_file())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

        recovered = Store(destination)
        self.assertEqual(recovered.verify_integrity(), original)
        self.assertEqual(recovered.get(first.job_id)["status"], "done")
        self.assertEqual(recovered.get(second.job_id)["status"], "queued")
        self.assertEqual(
            [e["action"] for e in recovered.events(first.job_id)],
            ["enqueued", "claimed", "completed"],
        )
        self.store.enqueue("queue", {"id": 3})
        self.assertEqual(recovered.stats()["queued"], 1)

    def test_never_overwrites_existing_backup_or_source(self):
        self.store.enqueue("q", {})
        destination = self.root / "keep.db"
        destination.write_bytes(b"IMPORTANT")
        with self.assertRaises(FileExistsError):
            self.store.backup_to(destination)
        self.assertEqual(destination.read_bytes(), b"IMPORTANT")
        with self.assertRaises(ValueError):
            self.store.backup_to(self.store.path)
        self.assertEqual(list(self.root.glob(".leaseledger-backup-*")), [])

    def test_never_follows_existing_backup_symlink(self):
        self.store.enqueue("q", {})
        protected = self.root / "protected.txt"
        protected.write_text("DO NOT CHANGE")
        alias = self.root / "alias.db"
        try:
            alias.symlink_to(protected)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(FileExistsError):
            self.store.backup_to(alias)
        self.assertEqual(protected.read_text(), "DO NOT CHANGE")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(list(self.root.glob(".leaseledger-backup-*")), [])

    def test_failed_publication_cleans_up_temporary_snapshot(self):
        self.store.enqueue("q", {})
        with patch(
            "leaseledger.store.os.link", side_effect=OSError("simulated cross-device or permission failure")
        ):
            with self.assertRaises(OSError):
                self.store.backup_to(self.root / "bad.db")
        self.assertFalse((self.root / "bad.db").exists())
        self.assertEqual(list(self.root.glob(".leaseledger-backup-*")), [])

    def test_online_backup_while_writer_enqueues_is_consistent(self):
        errors = []
        ready = threading.Event()

        def writer():
            try:
                for n in range(75):
                    self.store.enqueue("concurrent", {"n": n})
                    if n == 10:
                        ready.set()
            except Exception as exc:
                errors.append(exc)

        process = threading.Thread(target=writer)
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=10))
            backup = self.store.backup_to(self.root / "concurrent.db")
        finally:
            process.join(timeout=20)
        self.assertFalse(process.is_alive())
        self.assertEqual(errors, [])
        snap = Store(backup)
        self.assertTrue(snap.verify_integrity()["healthy"])
        self.assertGreaterEqual(snap.stats()["queued"], 11)
        self.assertLessEqual(snap.stats()["queued"], 75)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM events WHERE action='enqueued'").fetchone()[0],
            )
        self.assertEqual(self.store.stats()["queued"], 75)

    def test_foreign_key_integrity_problem_detected(self):
        self.store.enqueue("q", {})
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO events (job_id, action, occurred_at) VALUES (?,?,?)",
                (99999, "injected", 100),
            )
            connection.commit()
        report = self.store.verify_integrity()
        self.assertEqual(report["quick_check"], "ok")
        self.assertEqual(report["foreign_key_violations"], 1)
        self.assertFalse(report["healthy"])

    def test_missing_backup_parent_rejected(self):
        with self.assertRaises(ValueError):
            self.store.backup_to(self.root / "nonexistent" / "backup.db")


if __name__ == "__main__":
    unittest.main()
