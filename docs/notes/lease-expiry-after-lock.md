# Lease expiry must be checked after the writer wait

## Reproduced defect

In v0.1.2, `complete`, `heartbeat` and `fail` sampled the wall clock before
`BEGIN IMMEDIATE`. Another SQLite writer could hold that lock while the
lease expired. Once the lock became available, the operation validated the
lease against its old timestamp and incorrectly mutated the job and journal.
A heartbeat could revive an expired lease even without a replacement worker.

The same ordering shortened newly granted leases, hid delayed jobs that
became ready while waiting, and started producer delays before their enqueue
transaction acquired the lock. The maximum SQLite busy wait is 15 seconds,
so this matters especially for short leases and contended local databases.

## Fix and contract

Sample the clock inside the acquired write transaction. Claim selection
refreshes its clock for each cleanup candidate, including after a committed
100-item cleanup batch. Heartbeat, acknowledgement and failure checks still
require the current owner, fence, leased status and a strictly future expiry.
Rejected operations append no event and leave the job unchanged.

The reference point is validation within the write transaction, not the
instant the caller receives a reply. A process pause or slow commit can still
consume the remaining lease interval. Wall-clock adjustments remain relevant;
this is not a hard real-time deadline or exactly-once external-effects guarantee.
Use appropriately sized leases and downstream idempotency.

## Executable evidence

```bash
python -m unittest tests.test_lock_wait_expiry -v
python -m unittest discover -s tests -v
python -m benchmarks.queue_bench --jobs 80 --workers 4
```

The regression opens a separate real SQLite connection and acquires its write
lock. A second thread attempts the queue operation. SQLite's trace callback
signals the attempted `BEGIN IMMEDIATE`; the test advances an injected clock
and releases the blocker. No sleep is used to guess whether contention began.
Only time is controlled: SQLite locking, SQL execution and transaction commits
are real.

Six scenarios fail on base commit `fd5a99a` and pass after the fix:

- Expired ACK, heartbeat and failure are refused without changing job or events.
- A delayed job that becomes ready during contention is claimed.
- A new claim receives its requested interval after lock acquisition.
- Enqueue timestamps and delays start after lock acquisition.

The rejected-operation cases also reclaim the job with a new fence and
successfully acknowledge it, proving that rejection preserves recovery.

The complete 40-test suite and 80-job/four-process benchmark smoke passed on
Kali with Python 3.12.14 and macOS with Python 3.14.7. The smoke checks job accounting; it is not a capacity
or latency benchmark. The cloud editing environment blocked the existing
multiprocessing manager's Unix socket, so full local verification used the
user-authorized device instead of skipping that test or weakening it.

No database schema change or data migration is required. Existing databases
remain readable. Rolling back the code reintroduces the expiry defect.
