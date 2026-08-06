# Taxonomy of Flaky Test Behaviour Profiles: Derivation and Method

This document describes how the six-category taxonomy used by the detector
was derived, the criterion applied, and the decisions it produced. It exists
because a taxonomy is only trustworthy if the method behind it is stated and
inspectable.

## Source

The base is Luo, Hariri, Eloussi and Marinov, *An Empirical Analysis of Flaky
Tests* (FSE 2014) — the canonical root-cause study, derived from manual
inspection of 201 commits that fixed flaky tests across 51 Apache projects.
Their Table 2 gives ten root-cause categories with frequencies (n = 161
classified commits):

| Luo category | Commits | Share |
|---|---|---|
| Async Wait | 74 | 45% |
| Concurrency | 32 | 20% |
| Test Order Dependency | 19 | 12% |
| Resource Leak | 11 | 7% |
| Network | 10 | 6% |
| Time | 5 | 3% |
| IO | 4 | 2% |
| Randomness | 4 | 2% |
| Floating Point Operations | 3 | 2% |
| Unordered Collections | 1 | <1% |

Two later studies were used as cross-checks: Eck, Palomba, Castelluccio and
Bacchelli (ESEC/FSE 2019), who re-derived a taxonomy from 200 Mozilla flaky
tests classified by the developers who fixed them; and Kowalczyk et al.
(ICSE-SEIP 2020), whose related-work section confirms Luo's ten verbatim.

## Criterion

**Observability through the detector's interface.** The detector's only input
is JUnit XML, which exposes exactly three fields per test: outcome
(pass/fail), failure message text, and duration. The criterion applied to
each of Luo's ten categories is therefore:

> Does this root cause leave a trace in those three fields that is
> distinguishable from the traces of the other categories?

A frequency threshold was explicitly rejected as the criterion: it would keep
and drop categories inconsistently (retaining Network at 6% while dropping
nothing it can distinguish it from) and it ties the taxonomy to one study's
prevalence figures, which Eck et al. show are ecosystem-dependent.

## Decisions

| Luo category | Distinguishable trace in (outcome, message, duration)? | Decision |
|---|---|---|
| Async Wait | Yes — premature-assertion messages; high duration variance as supporting signal | **Keep** as `ASYNC_TIMING` |
| Concurrency | Yes — count-mismatch assertions | **Keep** as `CONCURRENCY_RACE` |
| Test Order Dependency | Yes — missing-state messages (`None`, `KeyError`) | **Keep** as `ORDER_DEPENDENT` |
| Resource Leak | Trace exists but is **indistinguishable from Network and IO** | **Merge** |
| Network | Trace exists but is **indistinguishable from Resource Leak and IO** | **Merge** → `RESOURCE_CONFLICT` |
| IO | Trace exists but is **indistinguishable from the other two** | **Merge** |
| Time | Yes — messages referencing datetime/date boundaries | **Keep** as `TIME_DEPENDENT` |
| Randomness | Yes — messages naming values that vary across failures | **Keep** as `UNSEEDED_RANDOM` |
| Floating Point | **No** — produces a plain wrong-value assertion identical to any other | **Drop** |
| Unordered Collections | **No** — produces a plain collection-comparison failure | **Drop** |

Note what the criterion does that frequency cannot: Time (3%) and Randomness
(2%) are retained while Floating Point (2%) is dropped — consistent under
observability, arbitrary under any frequency threshold.

### The merge

From a JUnit XML failure message, a leaked file handle, a port collision and
a network refusal all surface as the same class of operating-system error
(`OSError`, `Address already in use`, `Connection refused`, lock errors). An
instrument that cannot distinguish categories should not pretend to; they are
merged into `RESOURCE_CONFLICT`. Supporting evidence: Eck et al. found zero
instances of Network, IO or Unordered Collections in the Mozilla dataset,
suggesting the boundaries between these tail categories are not stable across
ecosystems.

### The drops

Floating Point (2%) and Unordered Collections (<1%) fail the criterion
outright — their failures are textually indistinguishable from ordinary
assertion failures — and are also the two rarest categories in Luo's data.

## The contribution: signatures

Luo's taxonomy classifies by root cause *in the source code*, established by
reading fix commits. This project adds an operationalisation: for each
retained category, an **observable signature** in CI test output. This is the
same structural move as Habchi et al. (ICSME 2022), who operationalised the
same taxonomy as static code metrics; here the observation channel is the
test log rather than the code.

| Category | Signature in test output |
|---|---|
| `ASYNC_TIMING` | Premature assertions (`assert False is True`), timeouts; duration variance as supporting signal |
| `ORDER_DEPENDENT` | Expected state absent: `assert None is True`, `KeyError`, `'NoneType' object` |
| `CONCURRENCY_RACE` | Numeric count mismatch (`assert 37 == 40`), dictionary-changed-size errors |
| `UNSEEDED_RANDOM` | Failure message names a value that differs between failures; random/uuid references |
| `RESOURCE_CONFLICT` | `Address already in use`, `Connection refused`, file locks, `Errno 98/48` |
| `TIME_DEPENDENT` | Messages referencing datetime, timestamps, timezone, date boundaries |

## Known limitations of the taxonomy

1. **Eck et al.'s four additional categories are not covered.** Their study
   found Too Restrictive Range (17%), Test Case Timeout (8%), Platform
   Dependency and Test Suite Timeout — none present in Luo. Test Case Timeout
   in particular passes the observability criterion (timeouts leave a clear
   trace) and is a genuine candidate for a seventh category; it is noted as
   future work rather than added, to keep the evaluated taxonomy fixed.
2. **Signatures are ecosystem-tuned.** The patterns are calibrated to
   pytest/JUnit-style message formats; other frameworks would need their own
   signature sets, though the category structure transfers.
3. **Signature collision is possible.** A plain numeric assertion in a
   non-concurrent test matches the `CONCURRENCY_RACE` pattern; classification
   confidence levels exist precisely to express this uncertainty, and
   `UNCLASSIFIED` is preferred over guessing when nothing matches.
