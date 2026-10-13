# Failure analysis: 100 expired leases hid valid work

## Symptom and reproducible defect

The original `Store.claim()` used a bounded loop of 100 row selections
inside one SQLite write transaction. Each expired, maximum-attempt lease
was correctly marked `dead`, but if the first 100 candidates were all
expired, the loop returned `None` without looking past that batch.

That matters because workers can run with `stop_when_empty=True` or
`--once`. `None` means **no runnable work**, not merely **I did
maintenance on 100 expired rows**. A lower-priority ready job could be
left unprocessed indefinitely until somebody tried to claim again.

## Regression

`tests/test_claim_progress.py` creates 121 higher-priority, expired,
terminal leases and one lower-priority, ready job. It then makes **one**
claim. The test fails on the previous v0.1.1 implementation because
`claim()` returns `None`; the new implementation returns the runnable
job and leaves exactly 121 durable dead-letter records.

## Fix

Process expired final-attempt leases in batches of 100 *per write
transaction*, committing the dead-letter states and their audit events
at each batch boundary. If 100 entries were handled without a claim,
open the next bounded transaction and keep searching. `None` is
returned only when a query actually finds no ready candidate.

This avoids giant write transactions holding SQLite's single-writer
lock for unbounded cleanup; it does not promise constant-time claim
performance with an arbitrarily large backlog of poisoned jobs.

## Additional validation

`tests/test_state_machine.py` uses four deterministic seeds and 160
operations per seed. A separate reference model predicts enqueues,
prioritized claims, expiry, failed/finished work, stale acknowledgements,
heartbeats, manual dead-letter requeue, and per-job audit events.
After periodic batches it compares all job state fields, tokens, and
event sequences with the actual database. This is reproducible
fault-injection, not an unverified production benchmark or a formal proof.

Run:

```bash
python -m unittest tests.test_claim_progress -v
python -m unittest tests.test_state_machine -v
python -m unittest discover -s tests -v
```

The project remains single-host and at-least-once: duplicate external
side effects are possible around worker crashes and require downstream
idempotency.
