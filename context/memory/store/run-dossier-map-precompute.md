---
name: run-dossier-map-precompute
description: "Map precompute costs ~18-19s per inventory pair (not per channel); per-arrival scoring is not a capacity concern; precomp_src marks measurement provenance and the two sources don't form a ratio"
metadata:
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T19:15:00.000Z
---

On the Experiment-8 catalogue, the Map family's offline precompute (`build_optimal_map`, backed
by `_optimal_work_assign` in `Warehouse/inventory/inventory_optimal.py`) costs **~18-19s once per
inventory pair**, not per channel. Per-arrival placement scoring costs **0.013 ms/unit for Map**
and **0.054 ms/unit for Rank_labor** in store — neither is a capacity or latency concern at this
scale.

The runtime table (`Optimization/persistence/runtime_metrics.py`) records `precomp_src` as
either `inline` (measured live during the sweep, on a contended 20-worker pool) or `backfill`
(re-measured afterwards by `Optimization/run_map_precompute.py`, uncontended, single-arm). **The
two do not form a ratio** — an uncontended backfill number is not "how much faster it would run
alone" evidence against an inline number, because the inline number already reflects the pool
contention the sweep actually ran under. Report each with its own `precomp_src`, never divide one
by the other.

**Ordering trap:** `run_map_precompute.py` must run **AFTER** `analyze_run` finishes for a given
run root, never before — the per-class solver-split detail lands under the dossier tree
(`<run_root>/_dossier/`, contract `6c44b3ce7341`), and `analyze_run`'s dossier stage calls
`driver.prepare_run_dir(out_dir)` which wipes that tree on every analysis pass (it's a derived
directory, so a stale figure is treated as worse than a missing one). Backfill-then-analyze
silently erases the census before the site ever stages it.

Related: [[map-exact-solver-rarely-fires]], [[run-scope-dossier]].
