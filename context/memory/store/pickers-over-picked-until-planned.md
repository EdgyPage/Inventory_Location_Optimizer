---
name: pickers-over-picked-until-planned
description: "Before commit 0b0d7d7 both picker loops read the whole per-aisle demand at EVERY bin on the path, so a multi-bin SKU was picked once per bin; every absolute pick/throughput number published before it reads high"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T01:45:01.924Z
---

A `Task` carries `items[sku]` = the demand for that **aisle**, and both picker loops used it
as the quantity to take at **each bin** on the path. A SKU spread over three bins was picked
three times. `PickSimulation` did not even clamp to stock, so it reported picking more units
than existed; `fast_pick` clamped and so over-picked only up to what was there.

Repro that isolates it: demand 8, two bins of 5 → `PickSimulation` 16, `fast_pick` 10, truth 8.

Fixed 2026-08-22 by `Task.planned` — a per-bin quantity list built beside `path`, zipped with
it in `pick_lines` and in both loops, and used by the LPT predictor in `_task_static`.

Measured impact on the coverage sweep: `total_items` −9.2%, `duration` −11.3%,
`thr_batch` +8.1%, `traveling_pct` +23.6%.

**Why:** every published absolute pick count, throughput and duration from before this commit
is wrong in that direction, on top of the separate 1000x unit error
([[sim-time-unit-is-seconds-not-ms]]). Ratios between arms are much less affected — all arms
over-picked — but they are not exactly preserved either, because the over-pick scaled with
bins-per-SKU, which placement quality changes.

**How to apply:** treat pre-`0b0d7d7` absolute numbers as unusable and arm-to-arm gaps as
indicative only. The bug was found because `BatchStats.items_demanded` was added and its own
verification (`items_demanded >= total_items`) failed on all 272 rows — a demand-side column
is what makes "did we actually pick what was asked" checkable at all. Keep it populated.
Related: [[determinism-fix-shifted-throughput]], [[real-test-coverage-is-317]].
