"""
detect_flaky.py -- Flaky test detection and taxonomy classification.

Reads a set of JUnit XML result files produced by running the SAME commit
multiple times, and reports:

  1. Which tests are FLAKY   (changed outcome across runs -- same code)
  2. Which tests are BROKEN  (failed in every run -- a real bug, not flakiness)
  3. Which tests are STABLE  (passed in every run)
  4. For each flaky test, its likely TAXONOMY CATEGORY, inferred from the
     failure message signature.

The core insight: if the code did not change between runs, then a test that
sometimes passes and sometimes fails is non-deterministic by definition.

Usage:
    python detect_flaky.py <directory-of-xml-files> [--markdown report.md] [--json report.json]
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


# ---------------------------------------------------------------------------
# TAXONOMY CLASSIFICATION RULES
#
# Each rule maps an observable failure signature to a taxonomy category.
# Rules are evaluated in order; the first match wins. Confidence reflects how
# distinctive the signature is -- some signatures (e.g. a port-binding error)
# are near-unambiguous, while others (a bare assertion failure) are weaker
# evidence and rely on supporting signals such as duration variance.
# ---------------------------------------------------------------------------

CLASSIFICATION_RULES = [
    {
        "category": "RESOURCE_CONFLICT",
        "confidence": "high",
        "patterns": [
            r"Address already in use",
            r"Errno 98",
            r"Errno 48",
            r"Connection refused",
            r"Permission denied",
            r"File exists",
            r"Resource temporarily unavailable",
            r"database is locked",
        ],
        "reason": "Error refers to contention for a shared external resource (port, file, socket, lock).",
    },
    {
        "category": "ORDER_DEPENDENT",
        "confidence": "medium",
        "patterns": [
            r"assert None is True",
            r"assert None ==",
            r"KeyError",
            r"'NoneType' object",
            r"assert \{\} ==",
            r"assert \[\] ==",
        ],
        "reason": "Expected state was missing or empty, suggesting the test relied on state set up by another test.",
    },
    {
        "category": "TIME_DEPENDENT",
        "confidence": "medium",
        "patterns": [
            r"datetime",
            r"timestamp",
            r"\bdate\b",
            r"timezone",
            r"assert 5\d < 55",
        ],
        "reason": "Failure references wall-clock time or a date boundary.",
    },
    {
        "category": "CONCURRENCY_RACE",
        "confidence": "medium",
        "patterns": [
            r"assert \d+ == \d+",
            r"RuntimeError: dictionary changed size",
            r"deadlock",
            r"race",
        ],
        "reason": "A counted or accumulated value did not match the expected total, typical of unsynchronised shared state.",
    },
    {
        "category": "UNSEEDED_RANDOM",
        "confidence": "medium",
        "patterns": [
            r"assert (\d+) != \1",
            r"assert '(.+)' != '\1'",
            r"\brandom\b",
            r"\bshuffle\b",
            r"\buuid\b",
            r"\bfaker\b",
        ],
        "reason": "Assertion failed on a value that varies between runs, suggesting unseeded random data.",
    },
    {
        "category": "ASYNC_TIMING",
        "confidence": "medium",
        "patterns": [
            r"assert False is True",
            r"TimeoutError",
            r"timed out",
            r"not found",
            r"not visible",
            r"StaleElement",
            r"WaitTimeout",
        ],
        "reason": "Expected condition was not yet satisfied, consistent with checking before an async operation completed.",
    },
]

# If duration varies this much relative to the mean, treat it as supporting
# evidence for a timing-related cause.
DURATION_VARIANCE_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# REMEDIATION GUIDANCE
#
# Per-category fixing strategies drawn from the empirical literature:
# Luo et al. (FSE 2014) Table 4/5 and Eck et al. (ESEC/FSE 2019) Table 1 both
# report the strategies developers actually used, with frequencies. Surfacing
# these turns detection into actionable advice -- "reacting to the presence
# of flaky tests" rather than only reporting them.
# ---------------------------------------------------------------------------

REMEDIATION = {
    "ASYNC_TIMING": (
        "Replace fixed sleeps with condition-based waits (waitFor). "
        "Luo et al. found 54% of async-wait flaky tests were fixed with waitFor, "
        "and Eck et al. report 86% of fixes in this category added a wait-for."
    ),
    "ORDER_DEPENDENT": (
        "Set up and clean shared state in setUp/tearDown so each test is "
        "self-contained. Luo et al. found 74% of order-dependency fixes cleaned "
        "shared state; run the test in isolation to confirm the dependency."
    ),
    "CONCURRENCY_RACE": (
        "Protect shared state with a lock, or remove the concurrency from the "
        "test. Luo et al.: 31% fixed by adding locks, 25% by making the code "
        "deterministic."
    ),
    "UNSEEDED_RANDOM": (
        "Seed the random generator so runs are reproducible, and handle "
        "boundary values explicitly. Eck et al. report this category is always "
        "fixed by replacing the random call with a reliable/seeded generator."
    ),
    "RESOURCE_CONFLICT": (
        "Allocate resources dynamically (ephemeral ports, unique temp paths) "
        "and release them in teardown. Luo et al.: 50% of resource-leak fixes "
        "destroy the conflicting object before continuing."
    ),
    "TIME_DEPENDENT": (
        "Avoid reading the real clock in tests: freeze or mock time. Note that "
        "Eck et al. found 75% of developers simply disabled such tests -- "
        "mocking the clock fixes them instead of losing the coverage."
    ),
    "UNCLASSIFIED": (
        "Reproduce locally by rerunning the test repeatedly (in isolation and "
        "in suite order) and inspect what varies between passing and failing "
        "runs: timing, ordering, environment, or data."
    ),
}


def classify_failure(messages, durations):
    """Infer the taxonomy category for a flaky test from its failure signatures.

    Returns (category, confidence, reason).
    """
    blob = " ".join(messages)

    for rule in CLASSIFICATION_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, blob, re.IGNORECASE):
                confidence = rule["confidence"]
                reason = rule["reason"]

                # Supporting signal: high duration variance strengthens a
                # timing diagnosis and weakens others.
                if rule["category"] == "ASYNC_TIMING" and _high_variance(durations):
                    confidence = "high"
                    reason += " Run duration also varies substantially between runs."

                return rule["category"], confidence, reason

    # Nothing matched -- fall back on duration variance alone.
    if _high_variance(durations):
        return (
            "ASYNC_TIMING",
            "low",
            "No matching error signature, but run duration varies substantially between runs.",
        )

    return "UNCLASSIFIED", "none", "Failure signature did not match any known taxonomy category."


def _high_variance(durations):
    """True if run durations vary widely relative to their mean."""
    usable = [d for d in durations if d is not None]
    if len(usable) < 3:
        return False
    mean = statistics.mean(usable)
    if mean == 0:
        return False
    return (statistics.pstdev(usable) / mean) > DURATION_VARIANCE_THRESHOLD


def parse_run(path):
    """Parse one JUnit XML file into {test_id: {...}}."""
    results = {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        print(f"  ! skipping unreadable file {path}: {exc}", file=sys.stderr)
        return results

    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        test_id = f"{classname}::{name}" if classname else name

        try:
            duration = float(testcase.get("time", 0) or 0)
        except ValueError:
            duration = None

        status = "passed"
        message = None
        for child in testcase:
            if child.tag in ("failure", "error"):
                status = "failed"
                message = (child.get("message") or "") + " " + (child.text or "")
            elif child.tag == "skipped":
                status = "skipped"

        results[test_id] = {
            "status": status,
            "message": message,
            "duration": duration,
        }
    return results


def analyse(directory):
    """Aggregate all runs in a directory and classify every test."""
    files = sorted(glob.glob(os.path.join(directory, "**", "*.xml"), recursive=True))
    if not files:
        raise SystemExit(f"No .xml result files found under: {directory}")

    print(f"Reading {len(files)} result file(s) from {directory}")

    history = defaultdict(lambda: {"statuses": [], "messages": [], "durations": []})
    for path in files:
        for test_id, result in parse_run(path).items():
            record = history[test_id]
            record["statuses"].append(result["status"])
            record["durations"].append(result["duration"])
            if result["message"]:
                record["messages"].append(result["message"])

    report = {"total_runs": len(files), "tests": []}

    for test_id, record in sorted(history.items()):
        statuses = record["statuses"]
        runs = len(statuses)
        failures = statuses.count("failed")
        passes = statuses.count("passed")

        # Flip rate: how often the outcome CHANGED between consecutive runs.
        # A high flip rate is a strong flakiness signal (cf. Kowalczyk et al.,
        # "Modeling and Ranking Flaky Tests at Apple", ICSE-SEIP 2020).
        flips = sum(1 for a, b in zip(statuses, statuses[1:]) if a != b)
        flip_rate = flips / (runs - 1) if runs > 1 else 0.0

        if failures == 0:
            verdict = "STABLE"
            category, confidence, reason = "-", "-", "Passed in every run."
        elif passes == 0:
            verdict = "BROKEN"
            category, confidence, reason = "-", "-", (
                "Failed in every run -- this is a consistent failure, not flakiness."
            )
        else:
            verdict = "FLAKY"
            category, confidence, reason = classify_failure(
                record["messages"], record["durations"]
            )

        report["tests"].append({
            "test": test_id,
            "verdict": verdict,
            "runs": runs,
            "passed": passes,
            "failed": failures,
            "failure_rate": round(failures / runs, 3),
            "flip_rate": round(flip_rate, 3),
            "category": category,
            "confidence": confidence,
            "reason": reason,
            "example_message": (record["messages"][0][:200].strip() if record["messages"] else None),
        })

    return report


def _sanitize(text, limit=200):
    """Neutralise content destined for the Markdown report.

    Failure messages and test names originate in test output, which can be
    attacker-influenced (e.g. a test exercising a parser echoes its input).
    Without neutralisation, crafted content can break out of code spans and
    inject arbitrary Markdown into the job summary (CWE-117). Backticks and
    pipes are removed, newlines collapsed, length capped.
    """
    if text is None:
        return None
    cleaned = text.replace("`", "'").replace("|", "/")
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def to_markdown(report):
    """Render the report as Markdown (suitable for a GitHub job summary)."""
    flaky = [t for t in report["tests"] if t["verdict"] == "FLAKY"]
    broken = [t for t in report["tests"] if t["verdict"] == "BROKEN"]
    stable = [t for t in report["tests"] if t["verdict"] == "STABLE"]

    lines = [
        "# Flaky Test Report",
        "",
        f"Analysed **{report['total_runs']} runs** of the same commit.",
        "",
        f"- 🔴 **{len(flaky)} flaky** (changed outcome across runs)",
        f"- ❌ **{len(broken)} consistently failing** (real failures, not flakiness)",
        f"- ✅ **{len(stable)} stable**",
        "",
    ]

    if flaky:
        lines += [
            "## Flaky tests",
            "",
            "| Test | Category | Confidence | Failure rate | Flip rate |",
            "|---|---|---|---|---|",
        ]
        for t in flaky:
            lines.append(
                f"| `{_sanitize(t['test'], 120)}` | **{t['category']}** | {t['confidence']} | "
                f"{t['failed']}/{t['runs']} ({t['failure_rate']:.0%}) | {t['flip_rate']:.2f} |"
            )
        lines.append("")
        lines += ["### Diagnosis and suggested remediation", ""]
        for t in flaky:
            lines.append(f"**`{_sanitize(t['test'], 120)}`** — {t['category']}")
            lines.append(f"> {t['reason']}")
            if t["example_message"]:
                lines.append(f"> ")
                lines.append(f"> Example failure: `{_sanitize(t['example_message'])}`")
            advice = REMEDIATION.get(t["category"])
            if advice:
                lines.append(f"> ")
                lines.append(f"> **Suggested fix:** {advice}")
            lines.append("")

    if broken:
        lines += ["## Consistently failing tests", "",
                  "These failed in *every* run. They are most likely genuine bugs "
                  "and should not be dismissed as flakiness.", ""]
        for t in broken:
            lines.append(f"- `{_sanitize(t['test'], 120)}`")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Detect and classify flaky tests from JUnit XML results.")
    parser.add_argument("directory", help="Directory containing JUnit XML result files")
    parser.add_argument("--markdown", help="Write a Markdown report to this path")
    parser.add_argument("--json", help="Write the raw report as JSON to this path")
    parser.add_argument("--fail-on-flaky", action="store_true",
                        help="Exit with code 1 if any flaky tests are found")
    parser.add_argument("--github-outputs",
                        help="Path to a GitHub Actions outputs file to append counts to")
    args = parser.parse_args()

    report = analyse(args.directory)
    markdown = to_markdown(report)
    print()
    print(markdown)

    if args.markdown:
        with open(args.markdown, "w") as fh:
            fh.write(markdown)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)

    if args.github_outputs:
        counts = {
            "flaky-count": sum(1 for t in report["tests"] if t["verdict"] == "FLAKY"),
            "broken-count": sum(1 for t in report["tests"] if t["verdict"] == "BROKEN"),
            "stable-count": sum(1 for t in report["tests"] if t["verdict"] == "STABLE"),
        }
        with open(args.github_outputs, "a") as fh:
            for key, value in counts.items():
                fh.write(f"{key}={value}\n")

    if args.fail_on_flaky and any(t["verdict"] == "FLAKY" for t in report["tests"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
