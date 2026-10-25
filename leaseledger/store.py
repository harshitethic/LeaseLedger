"""LeaseLedger: SQLite-backed, multi-process durable work queue.

Guarantee: *at-least-once* delivery. Leases, attempt counters, immutable fence
tokens, transactions and an event journal support crashes and stale workers.
Not a cross-machine distributed queue: SQLite requires a reliable local FS.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class Lease:
    job_id: int
    topic: str
    payload: Any
    owner: str
    fence: int
    attempt: int
    expires_at: float


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    created: bool


class Store:
    """Multi-process queue with transactionally fenced acknowledgements."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        if not self.path.parent.is_dir():
            raise ValueError("Database parent directory must already exist")
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self.initialize()

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise ValueError("Clock must return a positive finite Unix timestamp")
        return value

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('queued','leased','done','dead')),
                  priority INTEGER NOT NULL DEFAULT 0,
                  available_at REAL NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL,
                  lease_owner TEXT,
                  lease_until REAL,
                  fence INTEGER NOT NULL DEFAULT 0,
                  dedupe_key TEXT UNIQUE,
                  last_error TEXT,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_ready
                    ON jobs(status, priority DESC, available_at, id);
                CREATE INDEX IF NOT EXISTS jobs_leases
                    ON jobs(status, lease_until);
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
                  action TEXT NOT NULL,
                  actor TEXT,
                  details TEXT,
                  occurred_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id,id);
            """)

    @staticmethod
    def _encode(payload: Any) -> str:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(text.encode("utf-8")) > 65536:
            raise ValueError("Job payload exceeds 64 KiB")
        return text

    @staticmethod
    def _event(
        db: sqlite3.Connection, job_id: int, action: str, actor: str | None, details: str | None, now: float
    ) -> None:
        db.execute(
            "INSERT INTO events (job_id, action, actor, details, occurred_at) VALUES (?,?,?,?,?)",
            (job_id, action, actor, details[:500] if details else None, now),
        )

    def enqueue(
        self,
        topic: str,
        payload: Any,
        *,
        key: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        delay: float = 0,
    ) -> EnqueueResult:
        if not 1 <= len(topic) <= 100 or not all(c.isalnum() or c in "_-.:" for c in topic):
            raise ValueError("Invalid topic")
        if key is not None and not 1 <= len(key) <= 200:
            raise ValueError("Invalid idempotency key")
        if (
            type(priority) is not int
            or type(max_attempts) is not int
            or not -1000 <= priority <= 1000
            or not 1 <= max_attempts <= 100
            or type(delay) not in (int, float)
            or not math.isfinite(delay)
            or not 0 <= delay <= 86400 * 365
        ):
            raise ValueError("Invalid enqueue options")
        text = self._encode(payload)
        with self._write() as db:
            now = self._now()
            cursor = db.execute(
                """INSERT INTO jobs
                   (topic,payload,status,priority,available_at,max_attempts,dedupe_key,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(dedupe_key) DO NOTHING""",
                (topic, text, "queued", priority, now + delay, max_attempts, key, now, now),
            )
            if cursor.rowcount:
                job_id = int(cursor.lastrowid)
                self._event(db, job_id, "enqueued", None, topic, now)
                return EnqueueResult(job_id, True)
            row = db.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
            if row is None:
                raise RuntimeError("Idempotent enqueue violated unique key lookup")
            return EnqueueResult(int(row["id"]), False)

    def claim(self, owner: str, *, lease_seconds: float = 30, topic: str | None = None) -> Lease | None:
        if (
            not 1 <= len(owner) <= 100
            or type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 1 <= lease_seconds <= 86400
        ):
            raise ValueError("Invalid lease parameters")
        if topic is not None and not 1 <= len(topic) <= 100:
            raise ValueError("Invalid topic")
        while True:
            with self._write() as db:
                # Process at most 100 expired final-attempt leases per transaction.
                # Commit each batch; do not report queue empty after 100 tombstones.
                for _ in range(100):
                    # Lock acquisition can wait up to busy_timeout. Sample
                    # readiness/expiry after that wait, and again after each
                    # cleanup item, rather than granting an already-old lease.
                    now = self._now()
                    predicate = "" if topic is None else " AND topic = :topic"
                    row = db.execute(
                        f"""SELECT * FROM jobs WHERE
                            ((status='queued' AND available_at<=:now)
                            OR (status='leased' AND lease_until<=:now)){predicate}
                            ORDER BY priority DESC, COALESCE(lease_until,available_at) ASC, id ASC
                            LIMIT 1""",
                        {"now": now, "topic": topic},
                    ).fetchone()
                    if row is None:
                        return None
                    job_id = int(row["id"])
                    if row["status"] == "leased" and row["attempts"] >= row["max_attempts"]:
                        db.execute(
                            """UPDATE jobs SET status='dead', lease_owner=NULL, lease_until=NULL,
                               last_error='Lease expired after maximum attempts', updated_at=?
                               WHERE id=?""",
                            (now, job_id),
                        )
                        self._event(
                            db, job_id, "dead", str(row["lease_owner"]), "expired after max attempts", now
                        )
                        continue

                    fence = int(row["fence"]) + 1
                    attempt = int(row["attempts"]) + 1
                    until = now + lease_seconds
                    db.execute(
                        """UPDATE jobs SET status='leased', lease_owner=?, lease_until=?,
                           fence=?, attempts=?, updated_at=? WHERE id=?""",
                        (owner, until, fence, attempt, now, job_id),
                    )
                    self._event(db, job_id, "claimed", owner, f"fence={fence},attempt={attempt}", now)
                    return Lease(
                        job_id, str(row["topic"]), json.loads(row["payload"]), owner, fence, attempt, until
                    )
            # All 100 selected leases had exhausted attempts. Commit the
            # cleanup batch and keep searching instead of returning None.
            # Otherwise a worker using stop_when_empty=True can terminate
            # despite lower-priority jobs being ready.

    def _valid_lease_update(
        self, db: sqlite3.Connection, lease: Lease, now: float, update: str, args: tuple[Any, ...]
    ) -> bool:
        cursor = db.execute(
            f"""UPDATE jobs SET {update}
                WHERE id=? AND status='leased' AND lease_owner=?
                AND fence=? AND lease_until>?""",
            (*args, lease.job_id, lease.owner, lease.fence, now),
        )
        return cursor.rowcount == 1

    def heartbeat(self, lease: Lease, *, extend_seconds: float = 30) -> bool:
        if (
            type(extend_seconds) not in (int, float)
            or not math.isfinite(extend_seconds)
            or not 1 <= extend_seconds <= 86400
        ):
            raise ValueError("Invalid heartbeat interval")
        with self._write() as db:
            # Validate expiry only after acquiring the SQLite writer lock.
            now = self._now()
            ok = self._valid_lease_update(
                db,
                lease,
                now,
                "lease_until=MAX(lease_until,?), updated_at=?",
                (now + extend_seconds, now),
            )
            if ok:
                self._event(db, lease.job_id, "heartbeat", lease.owner, None, now)
            return ok

    def complete(self, lease: Lease) -> bool:
        with self._write() as db:
            now = self._now()
            ok = self._valid_lease_update(
                db,
                lease,
                now,
                "status='done', lease_owner=NULL, lease_until=NULL, updated_at=?",
                (now,),
            )
            if ok:
                self._event(db, lease.job_id, "completed", lease.owner, None, now)
            return ok

    def fail(self, lease: Lease, reason: str, *, base_delay: float = 1.0, max_delay: float = 3600.0) -> bool:
        if (
            not math.isfinite(base_delay)
            or not math.isfinite(max_delay)
            or base_delay < 0
            or max_delay < base_delay
        ):
            raise ValueError("Invalid backoff settings")
        reason = reason[:500]
        with self._write() as db:
            now = self._now()
            row = db.execute(
                """SELECT attempts,max_attempts FROM jobs WHERE id=? AND status='leased'
                   AND lease_owner=? AND fence=? AND lease_until>?""",
                (lease.job_id, lease.owner, lease.fence, now),
            ).fetchone()
            if row is None:
                return False
            is_dead = int(row["attempts"]) >= int(row["max_attempts"])
            delay = min(max_delay, base_delay * 2 ** min(int(row["attempts"]) - 1, 30))
            db.execute(
                """UPDATE jobs SET status=?,available_at=?,lease_owner=NULL,
                   lease_until=NULL,last_error=?,updated_at=? WHERE id=?""",
                ("dead" if is_dead else "queued", now if is_dead else now + delay, reason, now, lease.job_id),
            )
            self._event(db, lease.job_id, "dead" if is_dead else "retry", lease.owner, reason, now)
            return True

    @staticmethod
    def _require_job_id(job_id: int) -> None:
        if type(job_id) is not int or job_id < 1:
            raise ValueError("Job ID must be a positive integer")

    def requeue_dead(self, job_id: int) -> bool:
        self._require_job_id(job_id)
        with self._write() as db:
            now = self._now()
            changed = (
                db.execute(
                    """UPDATE jobs SET status='queued',attempts=0,available_at=?,
                   lease_owner=NULL,lease_until=NULL,last_error=NULL,updated_at=?
                   WHERE id=? AND status='dead'""",
                    (now, now, job_id),
                ).rowcount
                == 1
            )
            if changed:
                self._event(db, job_id, "requeued", None, None, now)
            return changed

    def get(self, job_id: int) -> dict[str, Any] | None:
        self._require_job_id(job_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            return result

    def events(self, job_id: int) -> list[dict[str, Any]]:
        self._require_job_id(job_id)
        with self._connection() as db:
            return [
                dict(x)
                for x in db.execute(
                    "SELECT action,actor,details,occurred_at FROM events WHERE job_id=? ORDER BY id",
                    (job_id,),
                )
            ]

    def verify_integrity(self) -> dict[str, Any]:
        """Check SQLite pages and relational integrity without modifying jobs."""
        with self._connection() as db:
            check = str(db.execute("PRAGMA quick_check").fetchone()[0])
            foreign_key_violations = len(db.execute("PRAGMA foreign_key_check").fetchall())
            return {
                "quick_check": check,
                "foreign_key_violations": foreign_key_violations,
                "healthy": check == "ok" and foreign_key_violations == 0,
            }

    def backup_to(self, destination: str | Path) -> Path:
        """Write a consistent SQLite snapshot while workers may be active.

        Never overwrite an existing file (including a symlink), and never
        copy a live WAL database with shutil.copyfile. The temporary snapshot
        is private, validated, fsynced and hard-linked into its final name so
        another process cannot race to overwrite an existing backup.
        """
        target = Path(destination).absolute()
        if not target.parent.is_dir():
            raise ValueError("Backup parent directory must already exist")
        if target == self.path.absolute() or target == self.path.resolve():
            raise ValueError("Refusing to overwrite the running database")
        fd, temporary_name = tempfile.mkstemp(
            prefix=".leaseledger-backup-", suffix=".db", dir=str(target.parent)
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with self._connection() as source:
                # sqlite3.Connection.__exit__ only commits/rolls back; it does
                # NOT close the connection. Close explicitly before publish
                # so WAL frames are checkpointed and sidecars disappear.
                with closing(sqlite3.connect(str(temporary), timeout=15, isolation_level=None)) as snapshot:
                    source.backup(snapshot, pages=256, sleep=0.1)
                    if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("Snapshot integrity verification failed")
                    if snapshot.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise sqlite3.DatabaseError("Snapshot foreign-key verification failed")
                    snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    # A standalone snapshot should not require a sidecar WAL
                    # to be restored, so convert to rollback-journal mode.
                    snapshot.execute("PRAGMA journal_mode=DELETE")
            read_fd = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(read_fd)
            finally:
                os.close(read_fd)
            # Link is atomic and fails if destination exists, even when it is
            # a symlink. os.replace would silently overwrite prior backups.
            os.link(temporary, target)
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass  # Directory sync is unavailable on some filesystems.
            return target
        finally:
            temporary.unlink(missing_ok=True)
            # Remove any unpublished transient sidecars after closing the DB.
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    def stats(self) -> dict[str, int]:
        result = {name: 0 for name in ("queued", "leased", "done", "dead")}
        with self._connection() as db:
            for row in db.execute("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status"):
                result[str(row["status"])] = int(row["count"])
        return result
