---
name: the-instrument-is-what-is-wrong
description: "in complexity work here, every error of the 2026-09-16 round was in the measurement, never the code under test; build the cross-check before believing a number that agrees with you"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 738f91ee-ad65-4791-a2df-c8daafeefa81
  modified: 2026-09-17T00:27:09.520Z
---

Across one round of complexity work (2026-09-16), **seven errors, all of them in an instrument and
none in the code under test**:

1. `_FLOW_COUNTS` proposed for the deep tier — it resolves against a *traced* tree and the deep tier
   never traces. Caught before spending 1 h 45 m, not after.
2. A width probe driven through `run_fullfid` instead of the ladder's own `build_assets` +
   `run_meso`: 676 calls where the ladder recorded 37,911, and a `mean|live|` that would have
   **acquitted the candidate outright**.
3. A trend rule that called a settled quadratic "converging" and demoted it to the bottom of its
   ranking — see [[a-fitted-exponent-cannot-see-its-own-shape]].
4. Two vacuous ranking tests, same cause both times: `_severity_sort` keys on `projected` before the
   trend, so a pair whose magnitudes agree with the verdict sorts identically under both rules.
5. An equivalence fixture that could not observe the ordering property its own source comment called
   load-bearing — `sorted(tied)` and `reversed(tied)` sabotages differed on **0 of 2,400 boards**,
   because the ids were inserted in sorted order and the prefs were near-continuous.
6. Three wrong expected counts in one complexity guard, the last because the harness described the
   board *after* the call and charged its own 2n hashes to the body under test.
7. `per_arm_total_s` keyed on the arm name while `runtime_metrics` is unique on
   `(cell, pair, config, channel, arm)` — 136 rows, 34 names, and a dict comprehension kept one row
   in four, silently. It printed 34 directly beneath a rollup saying 136.

**Why:** a measurement that agrees with expectation gets believed. Every one of these was caught
only by a cross-check against an independently known number — the ladder's own call count, a
sabotage that should have failed, a formula verified at three sizes, a per-arm sum that must
reconcile with `total_s_sum`.

**How to apply:** before trusting a new measurement, write down the independent quantity it must
agree with, and check it. Before trusting a guard, sabotage the thing it guards and confirm it
fails. A reconciliation identity (parts sum to a total you already have) is worth more than any
amount of care.
