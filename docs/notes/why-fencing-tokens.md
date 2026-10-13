# A lease is not a lock: why workers need fencing tokens

*Engineering note — LeaseLedger v0.1.0, October 2026*

Consider a durable queue with two workers and a 30-second lease.

1. Worker A claims job #42 and starts sending a document to object storage.
2. The worker pauses for 40 seconds (GC, scheduling, a frozen process, or the host suspends).
3. The lease expires. Worker B claims the same job and successfully finishes it.
4. Worker A resumes and tries to mark the task as done.

A simple `status='leased'` check is insufficient. Worker A might see B's
new lease and still write an acknowledgement. This breaks the meaning of job
ownership. Worse, both workers may have performed an external side effect.

## A fencing number closes the *database* race

LeaseLedger stores an integer `fence` on each job. Every successful claim
increments it inside the same SQLite `BEGIN IMMEDIATE` transaction that
writes the new owner and expiry.

| Time | Worker A | Database | Worker B |
| --- | --- | --- | --- |
| T0 | Claim #42, fence 1 | leased/A/1 | — |
| T1 | Paused | lease expires | — |
| T2 | Paused | leased/B/2 | Claim #42, fence 2 |
| T3 | Resume with fence 1 | Reject stale update | Complete with fence 2 |

The commit operation uses an optimistic ownership condition:

```sql
UPDATE jobs
SET status = 'done', lease_owner = NULL, lease_until = NULL
WHERE id = :id
  AND status = 'leased'
  AND lease_owner = :owner
  AND fence = :fence
  AND lease_until > :now;
```

A successful acknowledgement must update exactly one row. **Zero rows** means
the lease is gone, expired, or replaced; the application must not assume it
owns the task. The `heartbeat` and `fail` paths use the same guard.

## At-least-once is not exactly-once

Fencing a queue record prevents a stale worker from changing queue state.
It **does not roll back an external API request** made before a crash. If A
sent a document and B sent it again, the database cannot undo either upload.

For real side effects, propagate the job's durable idempotency identifier to
the downstream service. For example, payment processors often accept a
client-defined idempotency key; object storage may permit conditional writes.

Some downstream services cannot enforce fencing. Then design the handler to
reconcile observed state, accept possible duplicates, or use transactional
outbox/inbox patterns when appropriate. Never claim exactly-once delivery by
renaming a retry queue.

## Recovery behavior

LeaseLedger increments `attempts` when a job is claimed, not when it is
completed. When a process dies:

- If the expired lease has attempts remaining, the next worker obtains a
  **new fence** and runs it again.
- If the expired lease consumed the final attempt, the job transitions to
  `dead` in the claim transaction, with an audit event.
- An operator may explicitly requeue dead work, which resets attempts, but
  **does not reset the fence**.

Backoff after a handler failure is `min(cap, base × 2^(attempt - 1))`.
Expired worker crashes reclaim immediately; an operator can tune lease length
and heartbeat cadence to reduce premature replay.

## Reproduce the bug prevention

Clone the project and run these tests:

```bash
python -m unittest tests.test_store.StoreTests.test_crashed_worker_reclaim_increments_attempt_and_fence -v
python -m unittest tests.test_store.StoreTests.test_final_attempt_crash_goes_dead_not_infinite_retry -v
python -m unittest tests.test_multiprocess -v
```

The first two use a controlled wall clock to prove stale owner rejections and
final-attempt expiry without waiting 30 seconds. The third launches four real
OS processes that compete to claim 48 jobs and verifies all complete without
a collision *in that no-fault run*. CI runs them on three Python versions.

Source: [atomic claim and fenced updates](../../leaseledger/store.py).
Tests: [store regressions](../../tests/test_store.py) and
[multi-process integration](../../tests/test_multiprocess.py).

## Trade-offs we keep visible

This project uses a local SQLite WAL database. `BEGIN IMMEDIATE` serializes
writers, which is a valid single-host concurrency strategy—not cross-host
consensus. Clocks can jump; leases require monitoring. The event journal
grows without an archival process. On larger deployments, a Postgres-backed
adapter, distributed broker or purpose-built queue may be appropriate.

The point is not that this is the fastest queue. The point is that failure
modes are testable and the guarantees are stated precisely.
