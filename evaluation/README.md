# Evaluation

Three experiments, all scored against the harness's known ground truth.

Run with:

```bash
pip install pytest pytest-randomly
PYTHONPATH=. python evaluation/run_sweep.py --harness tests --runs 30
```

Outputs CSVs and `summary.json` into `evaluation-results/`.

## Why ground truth matters

The harness injects six faults of known category. Point a detector at a real
repository and no one knows the correct answer, so nothing can be scored.
Here the oracle is known by construction, so precision, recall and per-category
classification accuracy are all measurable.

## E1 — Detection accuracy vs injected failure rate

Runs the harness at `FLAKE_RATE` of 0.05, 0.1, 0.2, 0.3 and 0.5, and scores
detection at each.

The question this answers: **how rare can flakiness become before detection
breaks down?** This matters because a test failing 50% of the time is trivial
to catch in a handful of runs; one failing 2% of the time is nearly invisible.

## E2 — Detection latency

Takes one result set and re-analyses truncated prefixes of it (first 2 runs,
first 3, and so on), measuring how many runs would have sufficed to flag every
injected flaky test.

This is what turns the choice of run count from an assumption into a measured
parameter. Directly relevant to justifying the workflow's matrix size.

## E3 — Sensitivity of `DURATION_VARIANCE_THRESHOLD`

The classifier has exactly one free numeric parameter: the coefficient of
variation above which run-duration spread is treated as supporting evidence
for a timing diagnosis. E3 sweeps it across 0.1–1.0 and measures the effect on
classification accuracy.

The point is not to tune it but to establish whether it is load-bearing at all.
Reporting that a parameter does not matter is a legitimate result, and a more
honest one than quietly picking a value.

## Interpreting the outputs

| File | Contents |
|---|---|
| `e1_accuracy_by_rate.csv` | precision, recall, F1, classification accuracy, TP/FP/FN per flake rate |
| `e2_detection_latency.csv` | recall and count detected as a function of number of runs |
| `e3_variance_threshold.csv` | classification accuracy and F1 per threshold value |
| `summary.json` | all of the above, machine-readable |

## Threats to validity to state in the report

- Faults are **injected and simulated**, not naturally occurring. This buys
  exact ground truth at some cost to ecological validity.
- A single ecosystem (Python/pytest) and a single CI platform.
- The harness is small (7 tests); absolute counts are therefore coarse-grained
  and each individual test carries substantial weight in the metrics.
- Results vary between runs by construction — the phenomenon under study is
  non-deterministic. Re-running the sweep will not reproduce identical numbers,
  and the report should say so and give the number of repetitions used.

## E4 — Comparative evaluation

`compare_baselines.py` scores this detector against three alternative
approaches on the same data and the same oracle.

| Comparator | Source |
|---|---|
| B1 naive ("any failure is flaky") | Reimplemented strawman — the implicit ad-hoc model |
| B2 rerun-based | Reimplemented — the decision rule behind pytest-rerunfailures, Surefire rerun, Gradle test-retry, Google TAP |
| B3 flip-rate ranking | **Real tool**, `flaky-tests-detection` from PyPI (WithSecureOpenSource), run via its own CLI |
| D | This detector |

The harness includes `test_genuine_bug.py`, which fails deterministically every
run. It is the discriminating case: an approach that reports a real bug as
flakiness is worse than no tool at all, because it teaches developers to
dismiss a true failure.

**Threat to validity to state:** B1 and B2 are faithful reimplementations of
published decision rules, not the tools themselves. The published tools differ
in *how* they trigger reruns, not in how they infer flakiness from outcomes,
so the comparison is on the inference rule rather than the full tool. B3 is
the genuine article and is the load-bearing comparison.
