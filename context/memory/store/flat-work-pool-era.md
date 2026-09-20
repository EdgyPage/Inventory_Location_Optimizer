---
name: flat-work-pool-era
description: "since 2026-09-19 every cell of a run goes through ONE WorkPool; the matrix wall is worker-hours over the pool bounded below by the slowest unit, not the sum of the cells' slowest units"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:07.806Z
---

Since 2026-09-19 (commits `9bad3a97`, `8c156641`, `70633401`, `3ca743ca`, `99982d90`,
`53964420`) every cell of a run goes through ONE `Optimization/simdriver/workpool.py:WorkPool`,
both on the sim side (`scenario._run_cells`) and the analysis side
(`run_analysis.analyze_cells`). Before that date the driver opened one `ProcessPoolExecutor` PER
CELL and waited for the cell's last unit before the next cell's setup began, so on a coupled
spec with few workers per cell, workers idled for the life of every cell and the matrix wall was
the SUM of the cells' slowest units. Under the pool the wall is the worker-hours over the pool,
bounded below only by the single slowest unit.

Toy digest on `_toy_priced` (2 cells, 40 arms): IDENTICAL against the pre-change baseline
(run root `comparison_whatif_20260919_170759`); the candidate (`comparison_whatif_20260919_184523`)
shows the first cell-2 arm finishing before the last cell-1 arm finishes -- direct evidence the
pool is flat -- with one log listener for the whole run instead of one per cell.

Phase-2 `inbound_unload` (10 cells x 4 coupled units, 12 workers) was measured at ~24 h wall
under the old per-cell driver (8 of 12 workers idle per cell); ~4 h is expected flat.

**Why:** the coupled inbound spec is the shape that exposes the old design (few, expensive units
per cell); the pick side had the same shape from the start but it was hidden by many short units
per cell.

**How to apply:** a wall-clock measurement on a multi-cell run is now worker-hours plus one unit,
not per-cell sums. The `ILO_phase2_inbound_unload` run launched 2026-09-19 16:38 (root
`comparison_whatif_20260919_163818`, snapshot `62d9649e`) predates this pool and is cell-serial --
do not use its wall time as a baseline for a post-pool run, and do not resume it expecting the
new scheduling (it was built by the old driver). See [[cell-scope-restores-by-rebinding]],
[[a-retry-never-rebuilds-shared-assets]], [[cell-record-overlay-at-job-build-time]].
