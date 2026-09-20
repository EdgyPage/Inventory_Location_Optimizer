# worker-pool — one flat work pool for every cell of a run

Successor to `handoff.md` (2026-09-19 17:20, folded in below and deleted).  The user's
decision, 2026-09-19: ONE pool architecture for every run -- not a single-cell path beside a
multi-cell one -- with the cell carried on the unit, maximum parallelism, and the same
runtime object pattern for the sim driver and the analysis stage.  Byte-identity was NOT
required (a new era), but the pool change is scheduling-only and was expected to digest
IDENTICAL; see Decisions-so-far for the result.

## Destination

A multi-cell run's wall is the worker-hours over the pool (bounded below by its slowest
unit), not the sum of the cells' slowest units; the analysis stage saturates its pool across
cells; every test that faked the old per-cell executor drives the production path; the
architecture and context layers describe the pool that ships.  Reached at `develop`
`53964420` plus the docs commit that follows it.

## Notes (the measured shape, from the handoff)

- Phase 2 `inbound_unload`: 10 cells x 4 coupled units, 12 workers.  The old driver opened one
  pool per cell (`Flat pool [cell i/N]: 4 job(s)`), so 8 of 12 workers idled for the life of
  every cell.  Priced cell units: 788 s / 798 s (fifo rider), 7,803 s (opt pair), 10,449 s
  (uni pair); the cell wall was its slowest unit, ~2.9 h.  Unpriced cell: ~19 min units.
- Whole matrix as driven then: ~24 h.  Worker-hours in the matrix: ~46 h -> ~3.8 h on 12
  workers flat, bounded below by the single slowest unit (~2.9 h).  Machine: 24 cores,
  128 GB; a worker peaks ~4 GB.  More workers change nothing; scheduling does.
- Parent-side per-cell setup is serial and ~6 min a cell at campaign scale: frozen-inventory
  reload 10 s, warehouse reshape 42 s, staffing derivation ~2 min, batch precompute ~2 min
  (recomputed per cell though the fingerprint is the same), put-crew staffing 50 s, W* floor.
  All ten cells of `inbound_unload` plan the SAME warehouse.
- Per-unit asset loading inside each spawned worker is ~1 min here; dominant only on short
  units and in the analysis stage.
- The campaign run `ILO_phase2_inbound_unload` (root `comparison_whatif_20260919_163818`,
  from snapshot `62d9649e`, cell-serial, ~22 h from 16:38) was left running: it runs from a
  git-archive copy, so the working tree's edits cannot reach it, and its results are valid
  whatever the pool becomes.  A relaunch under the flat pool would take ~4 h.  Stopping it
  is the user's call.

## Decisions so far

- **One pool class, `Optimization/simdriver/workpool.py:WorkPool`** (ticket 01).  Cell-tagged
  jobs, its own pending queue (weight, then submission order; the executor never sees more
  than `max_workers` at once), `absorb` between cell setups, `when_done` continuations on the
  parent thread, the old `_supervise` recovery policy over all cells at once, one Manager and
  QueueListener for the whole run.  The executor factory is REQUIRED, so the recycling pin
  lives in exactly one place (`supervisor._sim_executor`); the draft `_MatrixPool` had
  hardcoded a second copy nothing checked.  A raise in the driver books what landed, cancels
  what never started, and does not wait for the units in flight.
- **Cells are scoped values** (ticket 02): `cells.cell_scope` wraps the unchanged
  `_apply_cell` and restores CONFIG on exit BY REBINDING the saved objects -- never by
  refilling the live dicts, because a payload built inside the scope is pickled hours later
  from the pool's own queue.  The payload copies its zoning dict for the same reason.  The
  union refusal in `_inbound_axis` is gone with its reason.  A config VALUE threaded through
  every accessor was rejected: parent setup is single-threaded by the domain globals
  (`Aisle.next_aisle_id`, `Order.next_sku`, the global `random` seed) regardless, so it would
  buy no parallelism; the cost was the WAIT between cells, not the setup.
- **One driver path** (ticket 03): `scenario._run_cells` streams every cell into the pool;
  `_run_scenario`, `_run_workers_flat`, `_supervise`, `_run_pool` are deleted; a single cell
  is a one-cell matrix.  Four things the design review forced, each a defect the draft had:
  a cell's shared assets live until its last unit lands so a retry never re-runs
  `build_shared_assets` (on a single-cell run that RE-SAMPLES and rewrites
  `planned_inventory.db` under the units in flight); the freeze is scoped; the blank-arm
  scan is per cell (`iter_sim_dbs` from a run root is vacuous on a mixed tree); a cell whose
  setup raises is reported, never unwound into the pool.  A leaf whose prepare raised is now
  reported through `_build_work_units(failed=)` instead of vanishing (pre-existing silence).
- **Precompute** (ticket 04): a cell copies a sibling cell's same-fingerprint batch file
  before computing; the precompute pool takes only the cores the sim pool leaves free.
- **Two latent defects** (ticket 05): the worker DID import `sim_config` at run time through
  `sim_assets.load_run_inventory` (a function-body import the import-time guard could not
  see; the loader moved to `Warehouse.generation.generate_inventory` and the guard walks
  function bodies now); `build_shared_assets` snapshotted CONFIG in a default argument.
- **The analysis stage on the same pool** (ticket 06): `run_analysis.analyze_cells` drives
  every cell through one pool with per-cell continuations replacing the three stage barriers;
  `run_analysis(cell_dir)` is the one-cell case; the analysis executor is deliberately
  unpinned (context caches, evicted per cell in the worker).  The cell record from
  `run_layout.json` is applied while a cell's jobs are built, which fixes two pre-existing
  wrongs: `inb_off` reported a dock ceiling (cell-axis inbound keys read off run-level
  CONFIG) and a `ks` sweep was analyzed under the LAST cell's split.  This CHANGES analysis
  output for split sweeps -- the correct direction, part of the era.
- **Gates**: `Tests/integration/test_work_pool.py` joined the CLAUDE.md pytest selection (a
  cell-serial regression is silent in the healthy direction).  The real-spawn broken-pool
  driver now exercises `WorkPool.finish` and `_unblock_broken_pool` in the pool that ships.

## Measurements

- Toy digest (`_toy_priced`, `--n-batches 6 --max-skus 8000 --coverage-days 1 --workers 6`,
  two cells, 40 arms): baseline `comparison_whatif_20260919_170759` (snapshot `62d9649e`);
  candidate from snapshot `53964420` (`comparison_whatif_20260919_184523`).  Result: IDENTICAL on the
  comparable surface, 40 arms; the first cell-2 arm finished before the last cell-1 arm and the run
  started one log listener (ticket 07).
- Throughput, quiet host, same toy and six workers: matrix wall 118 s (cell-serial) ->
  93 s (flat pool), utilisation 0.16 -> 0.19. The toy is the SMALL end of the effect --
  20 units a cell on six workers was never starved, and its wall is dominated by the
  parent's serial setup. The campaign shape (4 units a cell, 12 workers, 3 h units) is
  where the 24 h -> 4 h claim lives, and no toy can stand in for it (ticket 07).
- The parent's memory, from the new per-cell log line: 665 M peak holding one cell's
  assets, 707 M holding two. That ~42 MB is the price of the retry path on an 8,000-SKU
  catalogue; it scales with the catalogue (ticket 07).

## Fog (follow-ons, each its own ticket when taken)

- **`_cell_complete` skips a torn coupled pair on a resumed multi-cell run** (ticket 08).  It
  answers "complete" on ONE `sim_meta.json` per pair, so a cell whose coupled pair is torn
  (one leaf finalized) is skipped by `_run_whatif_matrix` before the reconciler could repair
  it.  Pre-existing; the e2e that proves the repair drives `_run_cells` directly and never
  meets the skip.
- **A duration-weighted dispatch order** (ticket 09).  `Job.weight` is the seam; an estimate
  from a reference run's `runtime_metrics.db` would put the 3 h units first on a wide,
  shallow matrix and cut the tail.  Not built: the wall is already the worker-hours plus one
  unit, and FIFO in spec order is what the campaign shapes need.
- Cross-cell reuse of the whole shared-asset build and parallel per-cell setup in worker
  processes: ~5 min/cell of parent time the pool already hides.  Not built.
- **Pre-existing, not this effort's:** `Tests/architecture/test_architecture_coverage.py`
  fails on HEAD because `Tests/bench/coverage_e2e.py` (2026-08-22) asks for a bin cap the
  planner has refused since 2026-09-07 (`UnfieldableRequirement` before any hot path runs).
  Spawned as its own task; memory `arch-tier-is-red-on-head` already says the tier's count
  moves with tree state.
