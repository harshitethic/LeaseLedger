# Five-minute independent review: LeaseLedger

This review guide is for an **independent developer** evaluating the correctness and usability of a single-host SQLite-backed task queue. A passing test does not certify production correctness.

## Reproduce the core failure semantics

Requirements: Python 3.11+, local disk (not NFS), a fresh checkout.

```bash
python3 -m examples.recovery_walkthrough
python3 -m unittest discover -s tests -v
python3 -m compileall -q leaseledger
```

1. Compare the first command's structured output with [the checked example](recovery-demo-output.json). The walkthrough should reject an expired worker's ACK, reclaim work under a new fencing token, and verify an online backup.
2. Inspect [transaction/fencing design](architecture.md) and [the reproduced starvation failure](notes/expired-lease-cleanup-progress.md), not only the README.
3. For performance, run the [repeated measured workload](workload-benchmarks.md) with full raw trials, Python/SQLite versions, OS, CPU, filesystem, and actual observed handler durations. Treat zero-delay workloads as no-op experiments, **not** production throughput.

## Challenge cases worth reporting

- Abrupt worker death while leased, then restart after expiration; confirm no stale-worker completion.
- A producer retry with an idempotency key after network/client uncertainty.
- Heavy contention while a lease is near expiration; note time/clock limitations.
- Dead-letter / retry exhaustion followed by unrelated runnable work.
- WAL backup during writes and restore verification.

## Report external evidence, not marketing numbers

Open a [reproducible issue](https://github.com/harshitethic/LeaseLedger/issues) with commit SHA, OS, Python/SQLite versions, exact command, expected and actual output, and **sanitized** evidence. Mark whether you ran the reproduction yourself.

A review is *independent* only when performed by someone other than the maintainer or its automation. Do not claim successful external installs, production use, external security audits or endorsements without links. Start with [CONTRIBUTING.md](../CONTRIBUTING.md). The maintainers explicitly **do not** promise distributed coordination or exactly-once external effects.

## Release readiness checklist

- [ ] Fresh-machine editable installation and CLI smoke on supported Python platforms
- [ ] All tests and lint pass on the release commit
- [ ] Backup/recovery walkthrough repeated using the release artifact, not just main
- [ ] Version and changelog match immutable release artifacts
- [ ] Benchmark configuration and raw results are attached and limitations stated
- [ ] External feedback linked **only when received and verified**

These are review criteria, not assertions that every item was re-tested for this documentation change.
