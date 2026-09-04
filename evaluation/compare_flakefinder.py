"""
compare_flakefinder.py -- Head-to-head against pytest-flakefinder.

pytest-flakefinder (PyPI, ~1.1.0) is a real, widely-used pytest plugin for
flaky-test detection. It is a genuinely DIFFERENT detection strategy from this
project's, which makes it a more informative comparator than a tool that
shares the same underlying approach:

    pytest-flakefinder : repeats each test N times WITHIN A SINGLE SESSION,
                         in a single process, on a single machine.

    this detector      : compares outcomes ACROSS N SEPARATE SESSIONS,
                         each in its own process (and, in CI, its own runner).

The distinction matters. Flakiness caused by state that persists within a
process -- notably test-order dependency, where one test pollutes or primes
another -- is invisible to intra-session repetition, because the polluting
test runs once and the repeated test then sees a stable environment. Luo et
al. (FSE 2014) attribute 12% of flaky tests to order dependency, and Lam et
al. (ICST 2019) report 50.5% in their dataset, so this is not a marginal case.

This experiment runs both tools on the same harness at the same injected
failure rate and scores both against the same ground truth.

Usage:
    PYTHONPATH=tests python evaluation/compare_flakefinder.py \
        --harness tests --runs 10 --repeats 5
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import detect_flaky  # noqa: E402


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

TRUE_FLAKY = {t for t, v in GROUND_TRUTH.items() if v == "FLAKY"}
TRUE_BROKEN = {t for t, v in GROUND_TRUTH.items() if v == "BROKEN"}


def _strip_repeat_index(test_id):
    """flakefinder names repeats as `test_name[3]`; normalise back."""
    if test_id.endswith("]") and "[" in test_id:
        return test_id[: test_id.rindex("[")]
    return test_id


def run_flakefinder(harness_dir, repeats, flake_rate, project_root):
    """Run pytest-flakefinder and infer which tests it identifies as flaky.

    flakefinder itself only repeats tests; the flakiness inference is the
    standard one -- a test that both passed and failed within the session is
    non-deterministic. We apply that rule to its output.
    """
    env = dict(os.environ)
    env["FLAKE_RATE"] = str(flake_rate)
    env["PYTHONPATH"] = harness_dir

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        xml_path = tmp.name

    subprocess.run(
        [sys.executable, "-m", "pytest", harness_dir,
         "--flake-finder", f"--flake-runs={repeats}",
         f"--junitxml={xml_path}", "-q", "-p", "no:randomly"],
        env=env, cwd=project_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    outcomes = defaultdict(list)
    try:
        root = ET.parse(xml_path).getroot()
        for tc in root.iter("testcase"):
            raw = f"{tc.get('classname','')}::{tc.get('name','')}"
            test_id = _strip_repeat_index(raw)
            failed = any(c.tag in ("failure", "error") for c in tc)
            outcomes[test_id].append("failed" if failed else "passed")
    finally:
        os.unlink(xml_path)

    flaky = {
        t for t, hist in outcomes.items()
        if "failed" in hist and "passed" in hist
    }
    return flaky


def run_this_detector(harness_dir, runs, flake_rate, project_root):
    """Run the harness `runs` times in separate processes, then analyse."""
    env = dict(os.environ)
    env["FLAKE_RATE"] = str(flake_rate)
    env["PYTHONPATH"] = harness_dir

    workdir = tempfile.mkdtemp(prefix="cmp-")
    try:
        for i in range(1, runs + 1):
            subprocess.run(
                [sys.executable, "-m", "pytest", harness_dir,
                 f"--junitxml={os.path.join(workdir, f'results-{i}.xml')}",
                 "-q", "-p", "randomly"],
                env=env, cwd=project_root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        report = detect_flaky.analyse(workdir)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    flaky = {t["test"] for t in report["tests"] if t["verdict"] == "FLAKY"}
    categories = {
        t["test"]: t["category"] for t in report["tests"]
        if t["verdict"] == "FLAKY"
    }
    return flaky, categories


def score(flagged):
    tp = len(flagged & TRUE_FLAKY)
    fp = len(flagged - TRUE_FLAKY)
    fn = len(TRUE_FLAKY - flagged)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision, "recall": recall, "f1": f1,
        "tp": tp, "fp": fp, "fn": fn,
        "bugs_mislabelled": len(flagged & TRUE_BROKEN),
        "missed": sorted(TRUE_FLAKY - flagged),
    }


def mean(values):
    return round(statistics.mean(values), 3) if values else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="tests")
    parser.add_argument("--runs", type=int, default=10,
                        help="Separate sessions for this detector")
    parser.add_argument("--repeats", type=int, default=10,
                        help="In-session repeats for flakefinder")
    parser.add_argument("--rate", type=float, default=0.3)
    parser.add_argument("--trials", type=int, default=5,
                        help="Repetitions, since the phenomenon is non-deterministic")
    parser.add_argument("--out", default="flakefinder_comparison.json")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(args.harness)) or "."

    ff_scores, ours_scores = [], []
    ff_missed, ours_missed = defaultdict(int), defaultdict(int)

    for trial in range(1, args.trials + 1):
        ff = run_flakefinder(args.harness, args.repeats, args.rate, project_root)
        s_ff = score(ff)
        ff_scores.append(s_ff)
        for m in s_ff["missed"]:
            ff_missed[m] += 1

        ours, _cats = run_this_detector(args.harness, args.runs, args.rate, project_root)
        s_ours = score(ours)
        ours_scores.append(s_ours)
        for m in s_ours["missed"]:
            ours_missed[m] += 1

        print(f"  trial {trial}: flakefinder R={s_ff['recall']:.2f}  "
              f"this detector R={s_ours['recall']:.2f}")

    summary = {
        "trials": args.trials,
        "injected_rate": args.rate,
        "flakefinder": {
            "precision": mean([s["precision"] for s in ff_scores]),
            "recall": mean([s["recall"] for s in ff_scores]),
            "f1": mean([s["f1"] for s in ff_scores]),
            "bugs_mislabelled": mean([s["bugs_mislabelled"] for s in ff_scores]),
            "missed_counts": dict(ff_missed),
            "provides_diagnosis": False,
        },
        "this_detector": {
            "precision": mean([s["precision"] for s in ours_scores]),
            "recall": mean([s["recall"] for s in ours_scores]),
            "f1": mean([s["f1"] for s in ours_scores]),
            "bugs_mislabelled": mean([s["bugs_mislabelled"] for s in ours_scores]),
            "missed_counts": dict(ours_missed),
            "provides_diagnosis": True,
        },
    }

    print(f"\n{'Tool':<28}{'P':>7}{'R':>7}{'F1':>7}{'BugsMislab':>12}{'Diagnosis':>11}")
    print("-" * 72)
    for label, key in (("pytest-flakefinder", "flakefinder"),
                       ("this detector", "this_detector")):
        d = summary[key]
        print(f"{label:<28}{d['precision']:>7}{d['recall']:>7}{d['f1']:>7}"
              f"{d['bugs_mislabelled']:>12}"
              f"{('yes' if d['provides_diagnosis'] else 'no'):>11}")

    if ff_missed:
        print(f"\npytest-flakefinder consistently missed:")
        for test, n in sorted(ff_missed.items(), key=lambda x: -x[1]):
            print(f"  {test}  ({n}/{args.trials} trials)")

    with open(args.out, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
