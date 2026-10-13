# Cross-platform verification — 14 October 2026

This document records **two actual local executions**, not a performance
service-level objective. Code and input: LeaseLedger v0.1.0 plus the [fencing
explanation](../docs/notes/why-fencing-tokens.md).

Each machine ran the same commands on a local temporary SQLite WAL database:

```bash
python3.12 -m unittest discover -s tests -q
python3.12 -m benchmarks.queue_bench --jobs 1000 --workers 4
```

The benchmark uses four spawned OS processes, enqueues 1,000 tiny JSON
objects, claims each job and immediately acknowledges it. It does **not**
perform network calls, database queries beyond the queue, expensive business
logic or downstream API effects.

| Environment | Automated tests | Enqueue time | Complete time | Complete rate |
| --- | ---: | ---: | ---: | ---: |
| Kali Linux 7.1.5 x86_64, Intel Core i9-14900K, Python 3.12.14 | 22/22 | 0.0882 s | 0.2912 s | 3,434.40 jobs/s |
| macOS Darwin 25.6 arm64, Apple M1, Python 3.12.15 | 22/22 | 3.7762 s | 4.0537 s | 246.69 jobs/s |

**Both runs** completed all 1,000 jobs with:

```json
{"queued":0,"leased":0,"done":1000,"dead":0}
```

These are one-run **end-to-end microbenchmarks**, including process startup.
Differences can be dominated by local filesystem and SQLite sync behavior,
process spawn cost, disk speed, CPU scheduling and load. No hard conclusions
can be drawn about a CPU's throughput from this table. The queue is a
**single-writer SQLite design**; the benchmark is not a multi-host scale test.

For meaningful performance engineering, repeat many runs, report p50/p95 and
tail latency, measure realistic handler durations, vary workers and queue size,
include failure injection, record SQLite settings and hardware, and compare
with a suitable broker under a fair workload. No such results are claimed here.

Independently, [GitHub Actions CI](https://github.com/harshitethic/LeaseLedger/actions/workflows/ci.yml)
passed on Python 3.11, 3.12 and 3.13 on GitHub-hosted Linux, including
lint/format and a separate reproducible 80-job benchmark smoke test.

No secret values, personal files or production customer data were used.
