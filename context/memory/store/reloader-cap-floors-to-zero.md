---
name: reloader-cap-floors-to-zero
description: "Capacity_Reloader.per_aisle_cap floors to 0 on small test warehouses, so requeue_bin never fires and eviction paths look covered when they are not"
metadata: 
  node_type: memory
  type: project
  originSessionId: f8316019-3a49-404c-ad0d-e5f670ed885b
  modified: 2026-08-14T09:18:46.941Z
---

`Capacity_Reloader.per_aisle_cap` is computed as `move_limit_pct × (bins in the largest aisle)`. On a
test-scale warehouse that product **floors to 0**, so `requeue_bin` is never called and every
eviction path silently does nothing.

A test that exercises restocking end-to-end will pass while covering zero evictions. Raising
`move_limit_pct` (0.5 worked in `Tests/bench/bin_log_harness.py::make_reloader`) is what actually
makes the path fire.

**Why:** it is the same failure shape as [[build-inventory-tests-no-reorders]] — a knob whose default
is fine at production scale and degenerate at test scale, producing no error either way. Both were
found only by asserting on an event count rather than on an end state.

**How to apply:** when writing a test that should exercise reorder or eviction, assert the event
actually happened (a non-zero placement/eviction count) before asserting anything about the result.
An end-state assertion alone cannot tell "worked correctly" from "never ran".
