---
name: reloader-cap-floors-to-zero
description: "Capacity_Reloader.per_aisle_cap floors to 0 TWO independent ways on test warehouses (move_limit_pct AND a ref_size that isn't in the fixture), so requeue_bin never fires and eviction paths look covered when they are not"
metadata: 
  node_type: memory
  type: project
  originSessionId: f8316019-3a49-404c-ad0d-e5f670ed885b
  modified: 2026-09-01T00:20:24.048Z
---

`Capacity_Reloader.per_aisle_cap` (`Warehouse/placement/Capacity_Reloader.py`) floors to **0** on
test-scale warehouses, so `requeue_bin` is never called and every eviction path silently does
nothing. There are **two independent routes to zero**, and closing one does not close the other:

1. **`move_limit_pct`** — `int()` TRUNCATES, so the production default `0.005` over a small aisle
   (0.005 × 100 bins = 0.5) floors to 0. Raising it (`0.5` works) is the known fix.
2. **No bin of the REFERENCE KIND** — the cap counts only bins matching a conjunction, `storage_size
   == ref_size` (default `'extra_large'`) AND `unit_type == ref_unit_type` (default `'pallet'`), so
   either field alone can empty the match. `max(per.values()) if per else 0` then yields 0 **at any
   `move_limit_pct`**, silently. This bit `Tests/unit/test_space_timeline.py`'s fixture, whose
   pallet aisle holds mediums and larges and no extra-larges.

The cap is NOT "move_limit_pct × bins in the largest aisle" — it is × bins in the largest aisle
*of the reference kind*. That imprecision is what hid route 2 for two weeks.

**Why:** the same failure shape as [[build-inventory-tests-no-reorders]] — a knob whose default is
fine at production scale and degenerate at test scale, producing no error either way. Route 2 is
worse than route 1: a test written against this memory's earlier one-route version would raise
`move_limit_pct`, believe itself covered, and still exercise zero evictions.

**How to apply:** never infer the cap from the knobs. Assert it directly —
`assert reloader.per_aisle_cap(mgr.warehouse) >= 1` — then assert the eviction COUNT (`reload()`
returns it) before asserting anything about the result. An end-state assertion cannot tell "worked
correctly" from "never ran"; see [[a-count-is-not-a-claim]].
