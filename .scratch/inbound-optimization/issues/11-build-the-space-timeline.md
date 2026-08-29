# Build the space timeline

Type: task
Status: resolved
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

## Answer

BUILT, commit `72bbffb` (2026-08-29) — everything ticket 03 decided is code, and every
neutrality obligation is a passing test.

What landed, piece by piece against the spec:

- **`Inbound/space.py`**: `SpaceTimeline` + `SpaceView`, constructed by the driver whenever
  the standing yard binds (always on with the flag, no knob), attached by
  `SpaceTimeline.attach(mgr)` — one rebound attribute (`mgr.space_timeline`, default None
  in `Inventory_Manager.__init__`), no listener registry. Flag-off nothing is constructed
  and all three manager-side hooks are `is None` no-ops.
- **The extracted drain rule**: `Workload_Builder.drain_sku` — `Task.from_batch`'s per-SKU
  loop hoisted verbatim (singleton before pallet, location order, min(remaining,
  quantity), `_SortedBins` skip-sort preserved); `from_batch`'s manager branch now calls
  it. One deviation from the ticket's letter, forced by the architecture: the boundary
  `forbid: [inbound, wh_picking]` means `Inbound/space.py` cannot import the rule, so the
  DRIVER injects it at construction (`SpaceTimeline(_drain_sku)`) — the repo's own broker
  idiom, and `space.py` imports nothing from Warehouse at all.
- **Four touchpoints**: driver injects released-batch demand + rollover carry (`_pending`)
  right before `check_reorders` — the batch is a pure function of
  (inventory, affinity, config, seed_batches+i), so fetching it early consumes no shared
  RNG, and it is stashed and reused so the flag-off sampling site is untouched;
  reclaim-harvest at the top of `_reclaim_empty_bins` (stamps captured before the wipe);
  fill in `_execute_placement` beside the sigma/put-cost observer seams; freeze
  immediately after `transit.freeze_ctx()` in `_receive_standing`, one projection per
  drain, delivered as `ctx.space`.
- **`SpaceView`**: `empties` (per-BinKey copied tuples of `_index`) / `emptied_at` (actual
  stamps only, expired on fill) / `predicted` (per-BinKey, untimed, take == quantity) /
  `released_at` / `versions=(demand_v, reclaim_v, fill_v)` / `frozen_at`; `DockContext`
  gained the `space` slot, default None, no signature change.
- **Versions**: per-event bumps exactly as designed, equality-only, documented as the
  invalidation contract. One honest gap surfaced and routed to the cache ticket (comment
  on "Draw the cache-sharing boundary"): `requeue_bin` evictions ride none of the three
  classes, so version-equal freezes can straddle an eviction-only change (views stay
  correct — the staleness is confined to version-keyed reuse).
- **Tests** (`Tests/unit/test_space_timeline.py`, 11 tests): the degenerate merged
  lockstep vs v1 with the timeline ON and injecting; timeline-on vs timeline-off on the
  standing path end to end (receive, placements, picks, reclaims — all four touchpoints
  fire, fingerprints identical); the purity pin (no manager mutation, no RNG); the
  drain-rule equivalence pin (bin_pick AND shortfall); predicted-clear exactness;
  version/stamp lifecycle; frozen-copy contract; ctx.space arrival + None default. The
  `_emptied_at` AST guard in `test_bin_empty_timing.py` is REPLACED by
  `test_reclaim_harvest_is_the_one_legal_reader_of_the_stamp`; the stale "picker-local"
  docstrings in `inventory_reorder.py`, `Inventory_Management.py` and `fast_pick.py` (and
  the boundary test's description string) now say absolute.

Verification: full `Tests/unit` (1388) + `Tests/integration` (395) green; the standing
e2e (which drives `_run_strategy_worker` flag-on, so the injection block ran through the
production seam) green; path/docref guards and `verify_context` clean; preflight canaries
re-validated the run-tree shape (source fingerprint refreshed, rides the commit).
Maintainer agents (architecture, context, memory) were dispatched post-commit and leave
their sync uncommitted for review, per their contract.
