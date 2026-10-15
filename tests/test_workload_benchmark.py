"""Check benchmark accounting and child cleanup, never assert a speed threshold."""

import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmarks.workload_bench import distribution, run_sweep, run_trial


def hung_worker(db, folder, number, gate, handler_ms, retry_every):
    Path(folder, f"{number}.ready").touch()
    gate.wait()
    time.sleep(30)


def failed_worker(db, folder, number, gate, handler_ms, retry_every):
    Path(folder, f"{number}.ready").touch()
    gate.wait()
    if number == 0:
        os._exit(23)
    time.sleep(30)  # A crashed worker must not leave its live sibling behind.


class WorkloadBenchmarkTests(unittest.TestCase):
    def test_percentiles_use_nearest_rank_and_empty_samples_are_explicit(self):
        self.assertEqual(distribution([]), {"samples": 0, "p50": None, "p95": None, "p99": None, "max": None})
        result = distribution(list(range(1, 101)))
        self.assertEqual(result, {"samples": 100, "p50": 50, "p95": 95, "p99": 99, "max": 100})
        self.assertEqual(distribution([9, 1])["p50"], 1)

    def test_real_workers_retry_and_account_for_every_job_and_event(self):
        result = run_trial(jobs=12, workers=2, handler_ms=1, retry_every=4)
        self.assertEqual(result["final_stats"], {"queued": 0, "leased": 0, "done": 12, "dead": 0})
        self.assertEqual(result["event_counts"], {"enqueued": 12, "claimed": 15, "completed": 12, "retry": 3})
        self.assertEqual(result["attempts"], 15)
        self.assertEqual(sum(result["completed_by_worker"]), 12)
        for name, count in (
            ("claim_ms", 15),
            ("handler_ms", 15),
            ("ack_ms", 12),
            ("retry_ms", 3),
            ("residence_ms", 12),
        ):
            value = result["latency"][name]
            self.assertEqual(value["samples"], count)
            self.assertLessEqual(value["p50"], value["p95"])
            self.assertLessEqual(value["p95"], value["p99"])
            self.assertGreaterEqual(value["p50"], 0)
        self.assertEqual(result["sqlite"]["journal_mode"], "wal")
        self.assertEqual(result["sqlite"]["foreign_keys"], 1)

    def test_sweep_retains_each_trial_and_records_source_fingerprints(self):
        result = run_sweep(jobs=3, worker_counts=[1, 2], handler_delays=[0], repeats=2)
        self.assertEqual(len(result["runs"]), 2)
        for run in result["runs"]:
            self.assertEqual(len(run["trials"]), 2)
            rates = [trial["completed_jobs_per_second"] for trial in run["trials"]]
            self.assertEqual(run["rate_summary"]["median"], sum(rates) / 2)
            self.assertTrue(all(trial["latency"]["retry_ms"]["samples"] == 0 for trial in run["trials"]))
        self.assertEqual(result["environment"]["start_method"], "spawn")
        self.assertEqual(len(result["environment"]["source_sha256"]["leaseledger/store.py"]), 64)

    def test_invalid_sweep_is_rejected_before_any_trial_runs(self):
        base = dict(jobs=10, worker_counts=[1, 2], handler_delays=[0], repeats=2)
        for override in (
            {"worker_counts": [1, 0]},
            {"handler_delays": [0, float("nan")]},
            {"repeats": 0},
            {"timeout": float("inf")},
            {"retry_every": 11},
        ):
            with self.subTest(override=override), patch("benchmarks.workload_bench.run_trial") as trial:
                with self.assertRaises(ValueError):
                    run_sweep(**(base | override))
                trial.assert_not_called()

    def assert_children_reaped(self, target, expected):
        before = {process.pid for process in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as root, patch("tempfile.tempdir", root):
            with self.assertRaises(expected):
                run_trial(jobs=3, workers=2, timeout=1 if target is hung_worker else 10, worker_target=target)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual({process.pid for process in multiprocessing.active_children()}, before)

    def test_timeout_reaps_all_workers_before_removing_database(self):
        self.assert_children_reaped(hung_worker, TimeoutError)

    def test_failed_worker_reaps_its_siblings_and_removes_database(self):
        self.assert_children_reaped(failed_worker, RuntimeError)
