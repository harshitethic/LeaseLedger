# A verified one-minute LeaseLedger recovery demonstration

**No accounts, Redis, network access, Docker or external services needed.**

This is not a synthetic screenshot or a claim of production adoption. The
[walkthrough program](../examples/recovery_walkthrough.py) actually creates a
private temporary SQLite database, applies durable state transitions through
the same public Store API as consumers, verifies a backup, and prints a JSON
report. We checked the output into
[docs/recovery-demo-output.json](recovery-demo-output.json) for easy inspection.
A regression test runs the command and asserts the actual JSON equals the
checked-in evidence on Python 3.11, 3.12 and 3.13 in GitHub Actions.

## Try it

~~~bash
git clone https://github.com/harshitethic/LeaseLedger.git
cd LeaseLedger
python3 -m examples.recovery_walkthrough
~~~

No installation required when running from the repository root; Python
3.11+ and its standard library suffice.

## Four observable steps

1. **Idempotent producer request:** enqueue a receipt and repeat the same
   durable key. The first attempt creates a job; the second returns the
   existing ID without changing the payload.
2. **Leased ownership:** worker A claims the job, attempt 1, fence token 1.
3. **Failure and fenced recovery:** a controlled test clock advances 11
   seconds past the 10-second lease, modeling a stalled or crashed process.
   Worker B reclaims the job with attempt 2 and fence token 2. A stale ACK
   from worker A returns false; worker B completes it successfully.
4. **Consistent recovery snapshot:** SQLite backup produces a verified,
   private snapshot. The recovered queue has exactly one completed job,
   no queued or dead jobs, and event history recording the two claims.

The example **simulates crash timing through a controlled clock**; it does
not kill a worker OS process. That separate real process-death case is
tested by [tests/test_multiprocess.py](../tests/test_multiprocess.py).
No external side effect is performed, so the demonstration does not
claim exactly-once effects from an at-least-once queue.

## Machine-verifiable output contract

~~~bash
python3 -m unittest tests.test_recovery_walkthrough -v
python3 -m unittest discover -s tests -v
~~~

The test runs the actual user-facing command in a subprocess, parses the
output, compares it to the versioned JSON evidence and verifies the
success/failure invariants directly. The JSON verified field is emitted
only after those program assertions pass; it is not a remote attestation
or a certificate of production safety.

See [the architecture](architecture.md), [100-expired-lease failure
analysis](notes/expired-lease-cleanup-progress.md) and [the backup
runbook](backup-and-recovery.md) for implementation details and limits.
