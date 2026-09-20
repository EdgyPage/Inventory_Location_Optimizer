---
name: a-retry-never-rebuilds-shared-assets
description: the flat-pool retry path is cell_scope + _build_work_units(kept) over the SAME shared_by_pair; calling build_shared_assets again on a retry would re-sample and rewrite planned_inventory.db under units already in flight
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:19.208Z
---

`Optimization/simdriver/scenario.py:_run_cells` keeps a cell's `shared_by_pair` alive (in
`kept: dict`) until its last unit lands. When the pool recovers from a broken worker pool, the
rebuild path re-runs `_build_work_units` over the SAME kept assets with `mid_flight=True` (the
coupled reconciler's contract) and resubmits -- it never calls `build_shared_assets` again. An
early draft (`_MatrixPool`) did call `build_shared_assets` on retry, and on a single-cell run
with no frozen inventory (`sample=True`, nothing forcing determinism across calls),
`build_shared_assets` would RE-SAMPLE the inventory and delete/rewrite `planned_inventory.db`
while units built from the FIRST sample were still in flight in the pool's queue.

**Why:** `build_shared_assets` is a generator, not an idempotent accessor -- it is only safe to
call once per cell. This was caught before merge, not from a production failure.

**How to apply:** the retry path is `cell_scope(cell)` + `_build_work_units(kept[cell], ...,
mid_flight=True)`, exactly as the old per-cell supervisor did it; never `build_shared_assets`
inside a retry/rebuild callback. See [[flat-work-pool-era]] for the pool this retry runs under.
