"""Plot retained trial ranges; optional dependency: matplotlib."""

import argparse
import json
from pathlib import Path


def main():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text()) for path in args.reports]
    delays = sorted({run["handler_ms"] for report in reports for run in report["runs"]})
    plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "leaseledger-workloads-v1", "font.size": 10})
    figure, axes = plt.subplots(
        len(reports), len(delays), figsize=(6 * len(delays), 3.6 * len(reports) + 1), squeeze=False
    )
    for row, report in zip(axes, reports):
        for axis, delay in zip(row, delays):
            runs = sorted(
                (run for run in report["runs"] if run["handler_ms"] == delay), key=lambda run: run["workers"]
            )
            rates = [[trial["completed_jobs_per_second"] for trial in run["trials"]] for run in runs]
            # Recompute from raw trials, rather than trusting cached summaries.
            from statistics import median

            medians = [median(values) for values in rates]
            low = [middle - min(values) for middle, values in zip(medians, rates)]
            high = [max(values) - middle for middle, values in zip(medians, rates)]
            environment = report["environment"]
            label = f"{environment['machine']} / Python {environment['python']}"
            axis.errorbar(
                [run["workers"] for run in runs],
                medians,
                yerr=[low, high],
                marker="o",
                capsize=5,
                linewidth=2,
                label=label,
            )
            title = "No-op handler" if delay == 0 else f"Requested {delay:g} ms blocked handler"
            if delay:
                observed = [trial["latency"]["handler_ms"]["p50"] for run in runs for trial in run["trials"]]
                title += f"\nObserved handler medians: {min(observed):.2f}–{max(observed):.2f} ms"
            axis.set_title(f"{label}\n{title}", fontsize=10)
            axis.set_xlabel("Worker processes")
            axis.set_ylabel("Completed jobs / second")
            axis.set_xticks(sorted({run["workers"] for run in report["runs"]}))
            axis.set_ylim(bottom=0)
            axis.grid(axis="y", alpha=0.25)
    figure.suptitle("LeaseLedger: measured finite-backlog workloads", fontsize=15, fontweight="bold")
    figure.text(
        0.5,
        0.025,
        "Points: trial medians · Whiskers: observed min–max · Separate y-axes per panel\nSynthetic workload, not production capacity or a device ranking",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.93))
    figure.savefig(args.output, metadata={"Date": None})
    plt.close(figure)
    if args.output.suffix.lower() == ".svg":
        args.output.write_text(
            "\n".join(line.rstrip() for line in args.output.read_text().splitlines()) + "\n"
        )


if __name__ == "__main__":
    main()
