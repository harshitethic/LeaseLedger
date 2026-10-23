---
name: Reproducible bug report
about: Report an independently reproducible defect in LeaseLedger
title: "bug: "
labels: []
assignees: []
---

## Environment

- OS and version:
- Python and SQLite versions:
- Git commit or release:

## Minimal steps to reproduce

1.
2.
3.

For queue correctness, include whether expired worker leases, fencing tokens, WAL/recovery, idempotency, retries or transaction contention are involved. Include timing/clock assumptions.

## Expected behavior

Describe the observable result that should happen.

## Actual behavior

Describe what actually happened. Attach sanitized terminal output/screenshots if necessary.

## Verification

- [ ] I reproduced this myself from a clean checkout or a stated baseline.
- [ ] I used synthetic/non-confidential data and removed secrets from logs.
- [ ] I checked [independent evaluation guidance](../../docs/independent-evaluation.md) and existing issues.

## Additional context and limitations

State anything you did not test. For sensitive security reports, use SECURITY.md instead of publishing exploit details.

> Real experiences are welcome even when critical. Maintainer, AI-generated or fixture-only tests must not be represented as independent validation.
