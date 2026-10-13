# Online SQLite backup and verified recovery

LeaseLedger v0.1.1 adds **consistent online backups** through SQLite's backup API. Copying a live database file directly is unsafe in WAL mode: committed pages may still be in the -wal sidecar.

## Backup and verify

~~~bash
leaseledger --db jobs.db verify
leaseledger --db jobs.db backup /secure-backups/jobs-snapshot.db
leaseledger --db /secure-backups/jobs-snapshot.db verify
~~~

The backup **must have a new filename**. It never overwrites an existing file or symlink. The parent backup directory must already exist with appropriate restricted permissions.

The command creates a 0600 private temporary database in the destination directory, takes an online SQLite snapshot, checks pages and foreign keys, folds the snapshot out of WAL mode, fsyncs it, and atomically publishes it via a no-overwrite hard link. Temporary files are removed after handled errors. If a filesystem does not support hard links, the backup fails rather than using a dangerous overwrite fallback.

## Restore procedure

1. Stop every worker and producer for the target database. Do not restore over running connections.
2. Preserve the old DB and its WAL sidecars separately, with private access controls.
3. Run the verify command against the snapshot.
4. Copy the verified snapshot to a new, empty database location in a restricted directory.
5. Point one worker at it, inspect stats and events, and only then resume other workers.

Restoring may replay jobs whose handlers performed external side effects after the snapshot was taken. This is the expected **at-least-once** limitation; use downstream idempotency keys.

## What the verifier checks

PRAGMA quick_check checks SQLite page structure; PRAGMA foreign_key_check finds broken relational references. A healthy result reports both as passing. These checks do not prove exactly-once delivery, encryption, recoverability from host loss, or business-data correctness.

## Reproduced failure paths

- A concurrent producer enqueues while the snapshot runs.
- A recovered DB includes state, deduplication keys and durable event history.
- Existing files and destination symlinks cannot be overwritten.
- Publication failure cleans up the private snapshot and WAL sidecar files.
- Injected foreign-key violations fail integrity verification.
- An OS process exits abruptly after a claim; once expired, the job is reclaimed with a new fencing token, and stale acknowledgement fails.

Regression tests: [tests/test_backup.py](../tests/test_backup.py) and [tests/test_multiprocess.py](../tests/test_multiprocess.py).

## Operational limits

Manual only; no automatic scheduler, cloud replication, encryption or retention. Monitor free disk space, load, clocks, successful backup checks, and regular restoration drills. Requires a reliable **local filesystem** supporting hard links. Use a managed database or message broker if you need cross-host high availability.
