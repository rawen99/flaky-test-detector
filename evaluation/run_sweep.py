"""
run_sweep.py -- Evaluation sweep for the flaky test detector.

Runs the injection harness at a range of injected failure rates, applies the
detector to each set of results, and scores its output against ground truth.

Because the faults were injected deliberately, the correct answer is known in
advance -- which is what makes precision, recall and per-category
classification accuracy measurable at all.

Experiments performed
---------------------
E1  Detection accuracy vs injected failure rate
    Does detection degrade as flakiness becomes rarer?

E2  Detection latency
    How many runs are needed before a flaky test is first flagged?

E3  Sensitivity of DURATION_VARIANCE_THRESHOLD
    Is the classifier's one free parameter well chosen, or arbitrary?

Usage:
    python run_sweep.py --harness ../tests --runs 30
"""

import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# GROUND TRUTH
#
# Every test in the harness, with whether it is flaky and (if so) which
# taxonomy category it belongs to. This is the oracle the detector is scored
# against. It is known exactly because the faults were injected deliberately.
# --------------------------------------------------------------------------

GROUND_TRUTH = {
    "tests.test_async_timing::test_async_timing": ("FLAKY", "ASYNC_TIMING"),
    "tests.test_genuine_bug::test_genuine_bug": ("BROKEN", None),
    "tests.test_concurrency_race::test_concurrency_race": ("FLAKY", "CONCURRENCY_RACE"),
    "tests.test_order_dependent::test_order_dependent_consumer": ("FLAKY", "ORDER_DEPENDENT"),
    "tests.test_order_dependent::test_order_dependent_setup": ("STABLE", None),
    "tests.test_resource_conflict::test_resource_conflict": ("FLAKY", "RESOURCE_CONFLICT"),
    "tests.test_time_dependent::test_time_dependent": ("FLAKY", "TIME_DEPENDENT"),
    "tests.test_unseeded_random::test_unseeded_random": ("FLAKY", "UNSEEDED_RANDOM"),
}

FLAKE_RATES = [0.05, 0.1, 0.2, 0.3, 0.5]
VARIANCE_THRESHOLDS = [0.1, 0.25, 0.5, 0.75, 1.0]


def run_harness(harness_dir, out_dir, flake_rate, runs):
    """Execute the harness `runs` times at a given injected failure rate."""
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ)
    env["FLAKE_RATE"] = str(flake_rate)
    env["PYTHONPATH"] = harness_dir

    project_root = os.path.dirname(os.path.abspath(harness_dir))
    for i in range(1, runs + 1):
        subprocess.run(
            [sys.executable, "-m", "pytest", harness_dir,
             f"--junitxml={os.path.join(out_dir, f'results-{i}.xml')}",
             "-q", "-p", "randomly"],
            env=env, cwd=project_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def score(report):
    """Score a detector report against ground truth.

    Returns detection precision/recall/F1 plus classification accuracy.
    """
    predicted = {t["test"]: t for t in report["tests"]}

    tp = fp = fn = 0
    correct_category = 0
    total_true_flaky = 0
    per_category = {}

    for test_id, (true_verdict, true_category) in GROUND_TRUTH.items():
        pred = predicted.get(test_id)
        pred_flaky = pred is not None and pred["verdict"] == "FLAKY"
        true_flaky = true_verdict == "FLAKY"

        if true_flaky:
            total_true_flaky += 1
            per_category.setdefault(true_category, {"detected": 0, "classified": 0, "total": 0})
            per_category[true_category]["total"] += 1

        if pred_flaky and true_flaky:
            tp += 1
            per_category[true_category]["detected"] += 1
            if pred["category"] == true_category:
                correct_category += 1
                per_category[true_category]["classified"] += 1
        elif pred_flaky and not true_flaky:
            fp += 1
        elif not pred_flaky and true_flaky:
            fn += 1

    # Any test the detector flagged that isn't in ground truth is a false positive.
    for test_id, t in predicted.items():
        if t["verdict"] == "FLAKY" and test_id not in GROUND_TRUTH:
            fp += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    class_acc = correct_category / tp if tp else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "classification_accuracy": round(class_acc, 3),
        "per_category": per_category,
    }


def analyse_dir(results_dir, variance_threshold=None):
    """Run the detector over a results directory, optionally overriding the
    duration-variance threshold (for the sensitivity experiment)."""
    import detect_flaky
    if variance_threshold is not None:
        detect_flaky.DURATION_VARIANCE_THRESHOLD = variance_threshold
    return detect_flaky.analyse(results_dir)


def experiment_1(harness_dir, runs, workdir):
    """Detection accuracy across injected failure rates."""
    rows = []
    for rate in FLAKE_RATES:
        out = os.path.join(workdir, f"rate-{rate}")
        run_harness(harness_dir, out, rate, runs)
        report = analyse_dir(out)
        s = score(report)
        rows.append({
            "flake_rate": rate, "runs": runs,
            "precision": s["precision"], "recall": s["recall"], "f1": s["f1"],
            "classification_accuracy": s["classification_accuracy"],
            "tp": s["tp"], "fp": s["fp"], "fn": s["fn"],
        })
        print(f"  rate={rate}: P={s['precision']} R={s['recall']} "
              f"F1={s['f1']} classification={s['classification_accuracy']}")
    return rows


def experiment_2(harness_dir, runs, workdir):
    """Detection latency: how many runs are needed to first flag each test?

    Re-analyses truncated prefixes of the same result set, so the answer is
    'how many runs would have sufficed', not a fresh experiment per length.
    """
    out = os.path.join(workdir, "latency")
    run_harness(harness_dir, out, 0.3, runs)

    rows = []
    for n in range(2, runs + 1):
        subset = os.path.join(workdir, f"latency-{n}")
        os.makedirs(subset, exist_ok=True)
        for i in range(1, n + 1):
            src = os.path.join(out, f"results-{i}.xml")
            if os.path.exists(src):
                shutil.copy(src, subset)
        report = analyse_dir(subset)
        s = score(report)
        rows.append({"runs": n, "recall": s["recall"], "detected": s["tp"]})
        shutil.rmtree(subset)
    return rows


def experiment_3(harness_dir, runs, workdir):
    """Sensitivity of the duration-variance threshold."""
    out = os.path.join(workdir, "variance")
    run_harness(harness_dir, out, 0.3, runs)

    rows = []
    for threshold in VARIANCE_THRESHOLDS:
        report = analyse_dir(out, variance_threshold=threshold)
        s = score(report)
        rows.append({
            "threshold": threshold,
            "classification_accuracy": s["classification_accuracy"],
            "f1": s["f1"],
        })
        print(f"  threshold={threshold}: classification={s['classification_accuracy']}")
    return rows


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Evaluation sweep for the flaky test detector.")
    parser.add_argument("--harness", default="tests", help="Path to the harness tests directory")
    parser.add_argument("--runs", type=int, default=30, help="Runs per configuration")
    parser.add_argument("--outdir", default="evaluation-results", help="Where to write results")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="sweep-")

    try:
        print(f"E1: detection accuracy vs injected failure rate ({args.runs} runs each)")
        e1 = experiment_1(args.harness, args.runs, workdir)
        write_csv(os.path.join(args.outdir, "e1_accuracy_by_rate.csv"), e1)

        print(f"\nE2: detection latency")
        e2 = experiment_2(args.harness, args.runs, workdir)
        write_csv(os.path.join(args.outdir, "e2_detection_latency.csv"), e2)

        print(f"\nE3: duration-variance threshold sensitivity")
        e3 = experiment_3(args.harness, args.runs, workdir)
        write_csv(os.path.join(args.outdir, "e3_variance_threshold.csv"), e3)

        summary = {"runs_per_config": args.runs, "e1": e1, "e2": e2, "e3": e3}
        with open(os.path.join(args.outdir, "summary.json"), "w") as fh:
            json.dump(summary, fh, indent=2)

        print(f"\nWritten to {args.outdir}/")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
