# Build the space timeline

Type: task
Status: open
Blocked by: 09

## Question

Build everything "Design the space timeline" (03) decided — its `## Answer` is the spec. The
pieces:

- `Inbound/space.py`: `SpaceTimeline`, constructed by the driver when `INBOUND_STANDING_YARD`
  is on, attached to the manager instance (the `BinRecorder.attach` precedent, no listener
  registry); flag-off it is never constructed and every hook is an `is None` no-op —
  byte-identity by construction.
- The extracted drain rule: `Task.from_batch`'s pure drain loop
  (`Warehouse/picking/Workload_Builder.py` — singleton before pallet, location order,
  min(remaining, quantity)) hoisted into one shared function; the task builder and the
  projection both call it. NOT `_rederive_plan` (lower fidelity by its own docstring).
- Four touchpoints: driver injects the released batch demand + rollover carry before
  `check_reorders`; reclaim-harvest in `_reclaim_empty_bins` (capture `_emptied_at` stamps
  before the wipe); fill in `_execute_placement`; freeze (projection + view build) at
  ctx-freeze inside `_receive`. One projection per drain.
- `SpaceView` frozen per drain: `empties` (per-BinKey copied snapshot of `_index`) /
  `emptied_at` (actual stamps only) / `predicted` (per-BinKey, untimed) / `released_at` /
  `versions=(demand_v, reclaim_v, fill_v)` / `frozen_at`; `DockContext` gains the `space`
  slot, default None.
- Version counters: per-event bumps (per injection; per harvested bin; per placement);
  equality is the only legal operation, documented as the invalidation contract ticket 06
  keys on.
- Tests: the degenerate lockstep with the timeline ON; the purity pin (no manager-state
  mutation, no RNG consumption when building a view); the drain-rule equivalence pin
  (extracted rule ≡ `Task.from_batch`'s `bin_pick` on identical state); REPLACE the
  `_emptied_at` AST guard in `Tests/unit/test_bin_empty_timing.py` with a pin naming
  reclaim-harvest the one legal reader; fix the stale "picker-local" docstrings in
  `Warehouse/inventory/inventory_reorder.py`, `Warehouse/inventory/Inventory_Management.py`,
  and `Warehouse/picking/fast_pick.py` (the stamps are absolute).
