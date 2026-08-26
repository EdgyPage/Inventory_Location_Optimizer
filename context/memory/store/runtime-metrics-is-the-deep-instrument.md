---
name: runtime-metrics-is-the-deep-instrument
description: "the deep ladder's t_* are MEAN seconds per batch per arm, not a share of the wall; runtime_metrics.db has the real per-arm totals"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T19:25:35.115Z
---

`calltree_scenarios.macro_sections()` returns `statistics.fmean` over checkpoint lines. With
`CHECKPOINT_FRAC = 0.1` there is one such line **per batch per arm**, so the deep ladder's `t_*`
values are *mean seconds per batch, averaged over every arm in the run*. They are **not** a
share of the phase wall and must never be summed against one — ~12 s against a 27-minute phase
is a units mismatch, not a coverage gap. That misreading cost a day and produced a wrong
committed claim.

The instrument that answers deep questions is **`runtime_metrics.db`**
(`Optimization/persistence/runtime_metrics.py`, `load_rows(run_root)`): one row per arm with
`total_s`, all seven section **totals**, `peak_rss_mib`, `n_bins`, `n_aisles` and arm identity —
136 rows per rung. `Σ total_s / workers` models the simulation phase; `Σ total_s − Σ sections`
is the batch loop's unattributed tail (measured at 0.8–2.2%, so it is small); per-arm `total_s`
names *which* arm.

Using it located the deep knee immediately: **`save_s`**, the DB-write section, +0.37 / +0.56 /
**+3.62** per doubling, while `reord_s` stayed at +0.92 / +0.96 / +0.98.

**How to apply:** for anything at deep scale, read the DB, not the log. When comparing two
numbers from different instruments, check they are the same KIND of number first — the ladder
now prints its section units and a commensurability line (`Σ total_s / workers` vs measured
wall) for exactly this reason. Also note `_DEEP_LADDER` scales SKUs and bins together, so a
per-bin cost is charged to the SKU exponent unless `n_bins` is carried alongside.
Related: [[knees-hide-from-r-squared]], [[a-count-is-not-a-claim]].
