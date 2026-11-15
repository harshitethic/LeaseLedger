import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ExistingDatabaseCliTests(unittest.TestCase):
    def run_cli(self, database, command, *options):
        return subprocess.run(
            [sys.executable, "-m", "leaseledger", "--db", str(database), command, *options],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_verify_and_backup_do_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "misspelled.db"
            destination = Path(directory) / "backup.db"
            for command, options in (
                ("verify", ()),
                ("backup", (str(destination),)),
            ):
                with self.subTest(command=command):
                    result = self.run_cli(database, command, *options)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("existing LeaseLedger database", result.stderr)
                    self.assertFalse(database.exists())
                    self.assertFalse(destination.exists())

    def test_verify_and_backup_reject_uninitialized_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "unrelated.db"
            destination = Path(directory) / "backup.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE unrelated (value INTEGER)")
            for command, options in (
                ("verify", ()),
                ("backup", (str(destination),)),
            ):
                with self.subTest(command=command):
                    result = self.run_cli(database, command, *options)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("does not contain the LeaseLedger queue tables", result.stderr)
                    self.assertFalse(destination.exists())
                    with sqlite3.connect(database) as connection:
                        tables = {
                            row[0] for row in connection.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        self.assertEqual(tables, {"unrelated"})

    def test_verify_and_backup_reject_non_sqlite_file(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "garbage.db"
            destination = Path(directory) / "backup.db"
            database.write_bytes(b"not a SQLite database")
            for command, options in (
                ("verify", ()),
                ("backup", (str(destination),)),
            ):
                with self.subTest(command=command):
                    result = self.run_cli(database, command, *options)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("not a readable LeaseLedger", result.stderr)
                    self.assertEqual(database.read_bytes(), b"not a SQLite database")
                    self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
