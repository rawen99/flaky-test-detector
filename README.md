# Flaky Test Detector

A GitHub Action that detects flaky tests, classifies them by **root-cause
category**, and publishes a diagnosis report to your workflow's job summary.

Most flaky-test tooling answers *"which tests are flaky?"*. This action also
answers *"what kind of flaky?"* — so developers know where to start looking.

## How it works

1. Your workflow runs the **same commit** multiple times in parallel, saving
   each run's JUnit XML results as an artifact.
2. This action downloads them all and compares outcomes per test:
   - changed outcome across runs → **FLAKY**
   - failed every run → **BROKEN** (a real bug — never dismissed as flakiness)
   - passed every run → **STABLE**
3. Each flaky test is classified against a taxonomy of flaky-test behaviour
   profiles derived from the research literature (Luo et al., FSE 2014;
   Eck et al., ESEC/FSE 2019), using failure-message signatures with
   run-duration variance as a supporting signal.
4. A report is published to the job summary: verdict, category, confidence,
   failure rate, and flip rate (Kowalczyk et al., ICSE-SEIP 2020) per test —
   plus a **suggested remediation** per category, drawn from the fixing
   strategies and frequencies reported by Luo et al. and Eck et al.

The taxonomy's full derivation and method are documented in
[TAXONOMY.md](TAXONOMY.md). All report content originating from test output
is neutralised before rendering (see Security notes).

## Categories

| Category | Typical signature |
|---|---|
| `ASYNC_TIMING` | Premature assertion (`assert False is True`), timeout errors, high duration variance |
| `ORDER_DEPENDENT` | Missing expected state: `assert None is True`, `KeyError` |
| `CONCURRENCY_RACE` | Count mismatches from unsynchronised shared state |
| `UNSEEDED_RANDOM` | Failure messages naming values that vary between runs |
| `RESOURCE_CONFLICT` | `Address already in use`, file locks, connection refused |
| `TIME_DEPENDENT` | Messages referencing datetime or date boundaries |

## Usage

```yaml
name: Flaky Test Detection

on: [push]

jobs:
  run-suite:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        run: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: pytest --junitxml=results-${{ matrix.run }}.xml || true
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: results-${{ matrix.run }}
          path: results-${{ matrix.run }}.xml

  detect-flaky:
    needs: run-suite
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v5
        with:
          path: all-results
      - uses: rawen99/flaky-test-detector@v1
        with:
          results-path: all-results
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `results-path` | yes | — | Directory of JUnit XML files from multiple runs of the same commit (searched recursively) |
| `fail-on-flaky` | no | `false` | Fail the job if any flaky tests are found |
| `report-file` | no | `flaky-report.md` | Where to write the Markdown report |
| `json-file` | no | `flaky-report.json` | Where to write the raw JSON report |

## Outputs

| Output | Description |
|---|---|
| `flaky-count` | Number of flaky tests detected |
| `broken-count` | Number of consistently failing tests |
| `stable-count` | Number of stable tests |
| `report-path` | Path of the generated Markdown report |

Outputs let you build follow-on steps — for example, only notify a channel
when `flaky-count` is non-zero.

## Design notes

- **Heuristic classification, not ML** — transparent, dependency-free, works
  with zero training data, and every verdict comes with a stated reason.
  The trade-off is fragility across frameworks; signatures are currently
  tuned for pytest/JUnit-style output.
- **BROKEN is separated from FLAKY by design.** A test failing every run is a
  genuine failure; a flakiness tool that taught developers to ignore it would
  be harmful.
- **Two metrics per test**: failure rate (how often) and flip rate (how
  erratically — the proportion of consecutive run pairs whose outcome
  changed), following Kowalczyk et al.'s flakiness modelling at Apple.

## Self-testing

`.github/workflows/self-test.yml` runs this action against fixture results
with known ground truth (2 flaky, 1 broken, 1 stable) on every push, and
fails if detection or classification deviates. The detector is itself tested.

## Security notes

Failure messages and test names originate in test output, which can be
attacker-influenced. All such content is neutralised (backticks and pipes
removed, newlines collapsed, length-capped) before being rendered into the
job summary, preventing Markdown injection into the report (CWE-117).
The action requires no secrets and sends no data outside the workflow.

## Limitations

- Requires multiple runs of the same commit (compute cost is bounded by your
  matrix size; runs execute in parallel).
- Classification is only as good as the message signatures; unrecognised
  failures are reported as `UNCLASSIFIED` rather than guessed.
- Order-dependence is inferred from message shape; a targeted isolated re-run
  would be stronger evidence (future work).

## License

MIT
