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
