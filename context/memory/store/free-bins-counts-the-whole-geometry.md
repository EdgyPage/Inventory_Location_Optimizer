---
name: free-bins-counts-the-whole-geometry
description: "batch_stats.free_bins is the whole geometry, so a leaf reads the other channel's section as free (57% was really 15-18%); and the era's free index drains every day toward a fragmentation nothing derives yet"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cdc9c7f-4688-4f40-be6b-1b41c63602c6
  modified: 2026-09-10T14:18:59.729Z
---

`batch_stats.free_bins` (from `Inventory_Manager.free_bin_depth`) sums the free index over the
WHOLE geometry, and a leaf simulates only its own section. So the store leaf counts every
untouched fulfillment bin as free and vice versa: the "57-59% free" that ticket 32 was charted on
was really 18.0% (store) / 15.2% (fulfillment), the declared 0.85 fill plus aisle rounding. The
truthful setup number is per bucket in the run spec, `coverage.final.<ch>.fielded.buckets[]`
(`requirement`, `capacity`, `free`).

Found 2026-09-10 on `comparison_20260909_204522`. The same run's series FALLS on every one of 40
days (store -6,763, fulfillment -18,279): under base stock a partial line leaves a remnant, the
top-up opens an empty bin (ADR-0003), and smallest-first drain clears the remnant only later, so a
picked SKU settles at ~2 bins. The store's slide runs at the first-partial-pick rate (a store SKU
sees a line every ~400 days) and the window never reaches its end.

**Why:** a leaf-level "free share" looks reassuring and is meaningless twice over: it includes a
section the leaf never uses, and it is a level mid-slide, not a steady state.

**How to apply:** never quote `free_bins` as a headroom; read the `fielded` block per bucket and
the drawdown over the window. Decisions on the instrument: [[cut-is-a-level-not-a-flow]] (the
column keeps its name and meaning); the fix is department-calibration 33 (per-bucket table, tier
spill judged at zero), the closed form 34, the derived fill 35. Related:
[[empty-bin-preference-is-structural]], [[drain-order-is-smallest-first]],
[[a-count-is-not-a-claim]].
