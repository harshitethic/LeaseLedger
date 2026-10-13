# ADR-0001: SQLite WAL for a single-host queue

**Status:** Accepted for v0.1 (scope is intentionally limited)

## Context

We want to demonstrate durable at-least-once delivery, atomic claims,
fencing tokens and process crash recovery without requiring a cloud account
or a separately deployed broker.

## Decision

Use SQLite in WAL mode, `BEGIN IMMEDIATE` for write/claim transactions, a
15-second busy timeout, strict state constraints, persisted Unix timestamps,
and monotonically increasing per-job fencing numbers.

## Alternatives considered

- **In-memory queue:** simple, loses jobs on process death, cannot coordinate
  independent processes.
- **Redis Streams:** useful consumer groups and throughput, but introduces a
  service, Redis durability/replication choices, and consumer acknowledgement
  semantics. Valid for cross-host use.
- **Postgres + SKIP LOCKED:** a strong next step for multi-host deployments,
  but adds database provisioning and transaction pool management.
- **Kafka:** powerful replicated log and replay semantics; excessive
  operational overhead for a portable single-host example.

## Consequences

Positive: easy to run, no runtime packages, deterministic transaction tests,
real multiprocess integration, portable SQLite database. New DB files are
created with restricted permissions on Unix.

Trade-offs: single-writer bottleneck, local filesystem only, no cross-host
high availability, manual dead-letter operations, no built-in encryption,
clock-sensitive leases, and no exactly-once side-effect guarantee.

**Revisit this ADR** if production use requires multiple hosts, thousands of
active workers or stronger operational controls. Do not market this design as a
distributed consensus system.
