# Independent-reproducible workload evidence — 23 October 2026

This is a **maintainer-run** workload on one Kali workstation. It is not independent user adoption, a third-party benchmark, an SLO, a certified comparison against competing queues, or evidence of production capacity.

## Environment and command

- Machine: Intel Core i9-14900K, 32 logical CPUs available; Linux 7.1.5+kali-amd64 x86_64; Python **3.13.12**; SQLite **3.46.1**.
- Source: checkout containing [the existing workload benchmark](../benchmarks/workload_bench.py); the JSON includes SHA-256 hashes of the benchmark source and queue implementation.
- Workload: **100 preloaded jobs** per trial; **3 trials** for each combination of **1/2 workers** and **0/5 ms requested handler delay** (12 trials). Every 10th job retries once; payload size 512 bytes.
- Actual command, from checkout root:

```bash
python3 -m benchmarks.workload_bench \
  --jobs 100 --workers 1,2 --handler-ms 0,5 \
  --repeats 3 --retry-every 10 > workload.json
```

[Full machine-readable raw trials](../benchmarks/evidence-2026-10-23-kali.json) (26,537 bytes, SHA-256 `4986eb7d60c61f1d820d91888e2fe1a2cf77f31b36b60d061f42aa908e4ca8d5`).

## Completed jobs per second across a finite preloaded queue

| Workers | Requested handler sleep | Median of 3 trials | Minimum | Maximum |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0 ms | 3,124.62 | 3,105.94 | 3,164.26 |
| 2 | 0 ms | 3,274.05 | 2,841.23 | 3,283.18 |
| 1 | 5 ms | 132.62 | 130.70 | 134.49 |
| 2 | 5 ms | 277.27 | 258.25 | 314.92 |

In this small synthetic run, increasing workers helps the blocked-I/O case, while zero-delay throughput shows contention/scheduling variation. These are **not** steady-state throughput numbers and must not be projected to arbitrary workloads.

The 12 trials collectively completed **1,200** synthetic jobs, with **120** deliberate first-attempt retries. The source JSON retains operation-level p50/p95/p99, observed handler duration, worker distribution, run times and final SQLite/event counts; no trials were discarded.

## What was independently checked here?

This run was executed on the maintainer-authorized Kali workstation using Python 3.13.12, after a fresh clone. The companion correctness check completed:

```bash
python3 -m examples.recovery_walkthrough
python3 -m unittest discover -s tests -q
python3 -m compileall -q leaseledger
```

The recovery walkthrough verified stale-ACK rejection and consistent online backup/restore; **54 tests passed**. These checks do **not** replace an independent review or cover side effects such as delivering actual payments or messages.

## Reproducibility and scope limits

- The benchmark preloads jobs and measures a finite drain, not time-varying production traffic.
- The requested five-millisecond sleep is synthetic, not measured external I/O.
- Python scheduler, CPU frequency, filesystem and host contention affect results; do not rank different machines without controlled conditions.
- Three repetitions per configuration are descriptive, not confidence intervals; do not infer significance from small differences.
- LeaseLedger remains single-host, SQLite-local and at-least-once. It has no claim of exactly-once real external effects or multi-region operation.

For a larger matrix and the metric definitions, see [the original repeated workload study](workload-benchmarks.md). External replication or contradiction should be linked from [independent evaluation](independent-evaluation.md).
