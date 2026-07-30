---
name: build-inventory-tests-no-reorders
description: Tests built on perf_simulation._build_inventory never fire reorders unless you set reorder_point on the orders
metadata: 
  node_type: memory
  type: project
  originSessionId: f4e9d22d-0c30-4c80-8cf2-5010571c9a6a
---

`Tests/perf_simulation._build_inventory()` builds bare `Order((handling, category))` objects that carry **no `reorder_point` attribute** and stock only ~1 unit/SKU. So `_notify_pick` reads `reorder_point=None` and never flags depletion → `check_reorders()` fires nothing.

**Why:** Any test that loops `check_reorders()` + a pick batch to compare *reorder-time* placement (e.g. `test_index_equivalence.test_cluster_index_matches_scan`) is therefore **vacuous on the reorder path** — it only compares the initial (default uniform) stock, which is identical regardless of the placement policy set afterward.

**How to apply:** To actually exercise reorder placement in a test, set `o.reorder_point = 0` on every order before `enqueue_all` (with 1-unit stock, one pick depletes → reorder fires; ~3000 placements over 40 batches). See `Tests/unit/test_placement_fastpath_equivalence.py::_build_map_mgr`. This is a pre-existing coverage gap in `test_index_equivalence`, not specific to the map/cluster_map fast-path work.
