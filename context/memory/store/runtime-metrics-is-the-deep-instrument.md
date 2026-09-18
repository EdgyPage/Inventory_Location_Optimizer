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

**UPDATED 2026-09-18 -- on a COUPLED unit, read `reord_s` and `total_s` with two rules.**
Two channel leaves run through one batch loop, and each writes its own row:

* `total_s` on BOTH rows is the UNIT loop's wall (a leaf's clock starts before the loop and
  stops after it), so it includes the sibling's work and the sections cannot partition it; the
  residual `total_s - sum(sections)` on a coupled row is mostly the sibling's step.
* `reord_s` on EACH row carries the SITE drive (one receive + one put drain for both leaves)
  in full -- the driver times it once and charges every leaf, the reading a one-leaf
  site-docked unit always gave. Summing the two leaves' `reord_s` counts the drive twice.

**Before 2026-09-18 every coupled `reord_s` was inflated by the SIBLING'S STEP.** Each leaf
opened its timer lap in `_replenish` and closed it only at its own `split('reord')` in
`_step`, so the second leaf's lap spanned the first leaf's entire batch. Measured on the e2e
coupled unit (3 batches, 300 SKUs): pre-fix `t_reord` read 0.011 s on leaf A and **0.315 s on
leaf B** (29x, for identical work); post-fix both read 0.018 s and B's residual rose from
0.106 s to 0.425 s -- the sibling's work moved from `reord` to the residual where it belongs.
The invariant is now a test: no leaf's lap contains another leaf's lap event
(`Tests/e2e/test_coupled_unit_e2e.py`, the lap-privacy pair, with the pre-fix choreography
convicted synthetically and on the real seam via stash). Zero coupled runs of record predate
the fix (the phase-2 campaign has not run), so nothing published needs re-reading.
