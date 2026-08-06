"""
compare_baselines.py -- Comparative evaluation against existing approaches.

Scores this detector against three alternative approaches on the *same*
result data and the *same* ground truth, so differences are attributable to
the approach rather than to the workload.

Comparators
-----------
B1  Naive failure-based
    "Any test that failed at least once is flaky." This is the implicit model
    behind ad-hoc practice: a developer sees red, re-runs, moves on.
    Reimplemented here (trivially) as a strawman lower bound.

B2  Rerun-based detection
    "A test that failed and later passed on the same commit is flaky."
    This is the model used by pytest-rerunfailures, Maven Surefire's rerun
    option, Gradle test-retry, and Google's TAP. Reimplemented faithfully:
    the published tools differ in *how* they trigger reruns, not in how they
    decide flakiness from the outcomes.

B3  Flip-rate ranking (REAL TOOL, not a reimplementation)
    WithSecureOpenSource/flaky-tests-detection, installed from PyPI, run via
    its own CLI on the same JUnit XML. Implements the flip-rate scoring of
    Kowalczyk et al. (Apple, ICSE-SEIP 2020) and ships as a GitHub Action --
    making it the closest available comparator to this project.

D   This detector.

Scoring dimensions
------------------
1. Detection      -- precision / recall / F1 against known injected faults
2. Bug safety     -- does the approach mislabel a CONSISTENTLY FAILING test
                     (a genuine bug) as flakiness? This is the failure mode
                     with real cost: a tool that teaches developers to ignore
                     a real failure is worse than no tool.
3. Diagnosis      -- does the approach say anything about WHY the test is
                     flaky, or only that it is?

Usage:
    python compare_baselines.py --results <dir-of-junit-xml>
"""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import detect_flaky  # noqa: E402


# Ground truth for the harness, INCLUDING a deliberately always-failing test.
# The always-failing test is the discriminating case: it is a genuine bug,
# and any approach that reports it as flaky is actively harmful.
GROUND_TRUTH = {
    "tests.test_async_timing::test_async_timing": "FLAKY",
    "tests.test_concurrency_race::test_concurrency_race": "FLAKY",
    "tests.test_order_dependent::test_order_dependent_consumer": "FLAKY",
    "tests.test_resource_conflict::test_resource_conflict": "FLAKY",
    "tests.test_time_dependent::test_time_dependent": "FLAKY",
    "tests.test_unseeded_random::test_unseeded_random": "FLAKY",
    "tests.test_order_dependent::test_order_dependent_setup": "STABLE",
    "tests.test_genuine_bug::test_genuine_bug": "BROKEN",
}


def load_histories(results_dir):
    """Build {test_id: [status, status, ...]} across all result files."""
    histories = defaultdict(list)
    files = sorted(
        f for f in os.listdir(results_dir) if f.endswith(".xml")
    )
    for fname in files:
        root = ET.parse(os.path.join(results_dir, fname)).getroot()
        for tc in root.iter("testcase"):
            test_id = f"{tc.get('classname','')}::{tc.get('name','')}"
            failed = any(c.tag in ("failure", "error") for c in tc)
            histories[test_id].append("failed" if failed else "passed")
    return histories, len(files)


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------

def b1_naive(histories):
    """Any test that ever failed is reported as flaky."""
    return {t for t, h in histories.items() if "failed" in h}


def b2_rerun(histories):
    """Flaky if the test both failed and passed across the runs.

    This is what a rerun-based detector concludes: the failure did not
    reproduce, therefore the test is unreliable.
    """
    return {
        t for t, h in histories.items()
        if "failed" in h and "passed" in h
    }


def b3_fliprate_real(results_dir):
    """Run the real WithSecure flaky-tests-detection tool via its CLI."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "flaky_tests_detection.check_flakes",
             f"--junit-files={results_dir}", "--grouping-option=runs",
             "--window-size=5", "--window-count=4", "--top-n=50"],
            capture_output=True, text=True, timeout=300,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    flagged = set()
    # The tool writes its ranking to stderr, not stdout; parse both defensively.
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        m = re.match(r"^(\S+::\S+)\s+---\s+score:\s+([\d.]+)", line.strip())
        if m and float(m.group(2)) > 0:
            flagged.add(m.group(1))
    return flagged or None


def d_this_detector(results_dir):
    """This project's detector. Returns (flaky set, categories dict)."""
    report = detect_flaky.analyse(results_dir)
    flaky = {t["test"] for t in report["tests"] if t["verdict"] == "FLAKY"}
    categories = {
        t["test"]: t["category"] for t in report["tests"]
        if t["verdict"] == "FLAKY"
    }
    broken = {t["test"] for t in report["tests"] if t["verdict"] == "BROKEN"}
    return flaky, categories, broken


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score(flagged, diagnoses=None):
    """Score a set of flagged-as-flaky test IDs against ground truth."""
    true_flaky = {t for t, v in GROUND_TRUTH.items() if v == "FLAKY"}
    broken = {t for t, v in GROUND_TRUTH.items() if v == "BROKEN"}

    tp = len(flagged & true_flaky)
    fp = len(flagged - true_flaky)
    fn = len(true_flaky - flagged)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # Bug safety: did it call a genuinely broken test "flaky"?
    bugs_mislabelled = len(flagged & broken)

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "bugs_mislabelled_as_flaky": bugs_mislabelled,
        "provides_diagnosis": diagnoses is not None,
        "categories_assigned": len(diagnoses) if diagnoses else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare against baseline detectors.")
    parser.add_argument("--results", required=True, help="Directory of JUnit XML results")
    parser.add_argument("--out", default="comparison.json")
    args = parser.parse_args()

    histories, n_runs = load_histories(args.results)
    print(f"Loaded {len(histories)} tests across {n_runs} runs\n")

    results = {}

    results["B1 naive (any failure)"] = score(b1_naive(histories))
    results["B2 rerun-based"] = score(b2_rerun(histories))

    b3 = b3_fliprate_real(args.results)
    if b3 is not None:
        results["B3 flip-rate (WithSecure, real tool)"] = score(b3)
    else:
        print("  ! WithSecure tool unavailable; skipping B3\n")

    flaky, categories, broken = d_this_detector(args.results)
    results["D this detector"] = score(flaky, diagnoses=categories)

    header = f"{'Approach':<40} {'P':>6} {'R':>6} {'F1':>6} {'BugsMislabelled':>16} {'Diagnosis':>10}"
    print(header)
    print("-" * len(header))
    for name, s in results.items():
        print(f"{name:<40} {s['precision']:>6} {s['recall']:>6} {s['f1']:>6} "
              f"{s['bugs_mislabelled_as_flaky']:>16} "
              f"{('yes' if s['provides_diagnosis'] else 'no'):>10}")

    with open(args.out, "w") as fh:
        json.dump({"runs": n_runs, "results": results}, fh, indent=2)
    print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
