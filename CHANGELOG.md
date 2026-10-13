## 0.1.2 — 2026-10-14

- Fix claim starvation when more than 100 expired terminal leases had priority over runnable work.
- Commit expired-lease dead-letter cleanup in bounded 100-row batches and retry selection until genuinely empty.
- Add a reproduction that fails against v0.1.1, plus an independent deterministic seeded state-machine test covering 640 mixed operations.
- Document the failure, invariants, regression and throughput/availability limitations.

## 0.1.1 — 2026-10-14

- Added consistency-checked SQLite online backup with no-overwrite publication, symlink protection, private temporary files, sidecar cleanup and atomic publication
- Added verify and backup CLI operations and an operational restore runbook
- Added seven backup/integrity regressions and a real abrupt worker-crash test
- Expanded from 22 to 30 tests with no new runtime dependencies

# Changelog

## 0.1.0 — 2026-10-14

Initial implementation:

- SQLite WAL durable jobs, atomic multi-process leases and monotonically increasing fencing tokens
- Heartbeats, deterministic exponential backoff, retries and dead-letter recovery
- Producer idempotency keys, priority, delayed jobs and persisted audit events
- Single-host worker CLI and trusted Python handler interface
- Deterministic unit tests, four-process integration, reproducible measured benchmark
- Three-version Python CI matrix and Ruff formatting/lint checks

No hosted SaaS, exactly-once side effects, distributed replication, load testing in production or deployment availability is implied.
