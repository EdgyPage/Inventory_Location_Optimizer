---
name: per-batch-series-are-autocorrelated
description: "Per-batch metric series carry real serial correlation, so an iid bootstrap understates every CI — the suite uses a moving-block bootstrap"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T15:21:33.292Z
---

Per-batch metric series in this simulator are **not** independent draws. Measured on
`uni_rank_labor` (store, `bell_lt0`, 75 batches): lag-1 **+0.259**, lag-2 **+0.330**, lag-3
**+0.278**, against a white-noise band of **±0.231** — three consecutive lags outside the band.

**Why:** the inventory state carries across batches. A batch that drains the near bins leaves the
next batch reaching farther, so consecutive batches share a slowly-moving layout condition on top
of their independent demand draw. This is a property of the sim, not of one arm.

**How to apply:** never compute a confidence interval on a per-batch series with an iid
(resample-rows) bootstrap — it treats 75 correlated points as 75 independent ones and returns an
interval that is too narrow. The analysis suite's `common/stats_core.py` uses a **moving-block
bootstrap** (`_boot_ci(..., block=...)`, block length `round(n**(1/3))`, overlapping
non-wrapping blocks, 2000 resamples, seed 0) for exactly this reason. If you add a new interval
anywhere in `Performance_Evaluations/`, call `_boot_ci` rather than writing a fresh percentile
bootstrap. Paired tests (Wilcoxon on the per-batch difference) are less affected but still
report a Holm-corrected p — see [[real-test-coverage-is-317]] for the general "a passing number
is not a checked number" habit.

Related: [[auc-degenerate-on-volume-curves]], [[batch-95-flat-spot-is-shared-demand]].
