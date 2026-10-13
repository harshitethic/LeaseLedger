# Contributing

1. Open an issue describing a concrete bug, change or security problem.
2. Include environment, Python version, a reproducible minimal test and expected/actual behavior.
3. Write a regression test first where possible. Keep the supported semantics
   **at-least-once**, not exactly-once.
4. Run `python3 -m unittest discover -s tests -v` and `python3 -m compileall -q leaseledger`.
5. Submit a focused PR. Describe transaction/clock/fencing implications,
   benchmark methodology for performance changes and migration impact.

Do not add generated activity, benchmark numbers without execution evidence,
or new third-party runtime dependencies without a design discussion.
No proprietary code, credentials, or user data in fixtures.
