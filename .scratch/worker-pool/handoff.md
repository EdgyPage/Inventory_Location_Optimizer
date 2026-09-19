# Worker pooling -- handoff for the redesign plan (2026-09-19 17:20)

The question: a multi-cell run keeps most of its workers idle, because the driver builds ONE
pool PER CELL and waits for the cell's last unit before the next cell's setup begins. The user
wants the flattest pool possible, on the pick side too (cells were always segregated there),
and a fresh plan with its own test suite. This note is what a new context needs; the code
draft named below is UNCOMMITTED and UNTESTED and may be folded in or discarded.

## The measured shape (phase 2, `inbound_unload`, 12 workers, campaign catalogue)

- 10 cells x 4 coupled units. `supervisor._supervise` opens a pool per cell
  (`Flat pool [cell i/N]: 4 job(s)`), so 8 of 12 workers idle for the life of every cell.
- Priced cell (any gain policy): units 788 s / 798 s (fifo rider), 7,803 s (opt pair),
  10,449 s (uni pair) after ticket 03's cuts; the cell wall is its slowest unit, ~2.9 h.
  Unpriced cell: ~19 min winner units, ~0.4 h wall.
- Whole matrix as driven today: ~24 h (sum of the cells' slowest units). Worker-hours in the
  matrix: ~46 h -> ~3.8 h on 12 workers if the pool were flat, bounded below by the single
  slowest unit (~2.9 h). Machine: 24 cores, 128 GB; a worker peaks ~4 GB. So more workers
  change nothing; scheduling does.
- Parent-side per-cell setup is SERIAL and ~6 min a cell at campaign scale (probe log,
  `comparison_whatif_20260919_123309`, 13:21-13:27): frozen-inventory reload 10 s, warehouse
  reshape 42 s, staffing derivation ~2 min, batch precompute ~2 min (recomputed per cell
  though the fingerprint is the same), put-crew staffing 50 s, W* floor. All ten cells of this
  spec plan the SAME warehouse (same split, zoning, scheduler; only the inbound policy
  differs), so most of that is repeated work. `inb_off` differs in the coverage lead.
- Per-unit asset loading inside each spawned worker (catalogue load, labour-cost pass,
  affinity store, warehouse build) is ~1 min here (plan item 3.7); dominant only on short
  units and in the analysis stage.

## Why a flat pool is byte-identical (verified by reading, not yet by digest)

- A unit's payload carries everything its cell decides: `workunits._build_work_units` resolves
  `inbound_spec()` and every CONFIG-derived value into the unit's shared args at build time
  (`workunits.py`, the `_shared` dict); `strategy_runner` never reads CONFIG; workers are
  spawned fresh (recycle pinned at 1 -- memory `worker-recycling-pinned-at-one`).
- `cells._apply_cell` mutates the process-global CONFIG, so per-cell setup cannot overlap in
  the parent; it CAN run cell after cell while the pool works, provided each cell's
  `_build_work_units` runs right after its own `_apply_cell`, in spec order (an
  `inbound=None` cell inherits the previous cell's inbound keys either way -- same as today).
- Shared files: `_batches_<fp>.pkl` per pair dir per cell (parent-written), `_frozen/<pair>/`
  once per run, `runtime_metrics.db` at the run root written by the parent only, `_site` DBs
  per unit. 12 concurrent writers was already the factorial's regime; save time did not move.

## What changes in flight (working tree, uncommitted, untested)

`git status` shows three files:

1. `Optimization/simdriver/supervisor.py`
   - `_run_pool`'s success bookkeeping and finalize loop lifted into `_absorb_success` and
     `_finalize_unit_groups` (moved text, comments intact) so two pools share one bookkeeping.
   - New `_MatrixPool`: one executor across every cell; `submit(cell, units, meta, ...)`
     stamps the job/cell/global-index fields `_supervise` stamped; `absorb()` non-blocking
     between cell setups; `drain()`; `finish(rebuild)` with `_supervise`'s recovery policy
     over all cells (broken pool -> `_unblock_broken_pool` + shutdown -> fresh pool ->
     `rebuild(cell)` for every cell with units left, `mid_flight=True` -> resubmit, up to
     `max_retries`; `_explain_worker_death` once; per-cell finalize sweep; unfinished
     returned per cell for `run_simulation._refuse_incomplete`). Bookkeeping is per cell in
     `_run_pool`'s uid/group-keyed shape, because uids and group keys repeat across cells.
     `executor_factory(max_workers)` is injectable (tests use a thread pool + fake worker).
     The Manager + QueueListener live on the pool for the whole matrix (one listener, not
     one per cell).
2. `Optimization/simdriver/scenario.py`
   - `_run_whatif_matrix`: `if n_cells > 1: _run_cells_flat(...)` else the old per-cell
     `_run_scenario` loop (single cell untouched).
   - New `_run_cells_flat`: per cell in spec order -> `_apply_cell`, manifest,
     `build_shared_assets` (frozen inventory), `_build_work_units` -> `mp.submit` ->
     `mp.absorb()`; then `mp.finish(rebuild=...)` where rebuild re-derives the cell's assets
     from disk (the shared dict is dropped after each cell's units are built so ten
     inventories are not held in the parent); `_warn_blank_arms` per cell at the end.
3. `Tests/integration/test_matrix_pool.py` (never run): a barrier sized to the whole matrix
   that only a shared pool can pass (fails, not hangs, on a cell-serial driver); its
   non-vacuity twin; broken-pool rebuild of only the cells with units left; a cell with
   nothing left not rebuilt; retries exhausted -> unfinished per cell; ordinary failure
   quarantined; the driver streams every cell under its own CONFIG (records the scheduler
   CONFIG carries at each `_build_work_units`); a single-cell spec never opens the matrix
   pool.

Not in the draft: any change to the parent's serial per-cell setup, asset sharing across
cells, per-unit asset caching, the analysis stage, or unit granularity (a coupled unit is one
worker for ~3 h; the slowest unit bounds a flat pool's wall).

## Gates the new plan inherits

- Toy digest baseline, pre-change, from snapshot `62d9649e`: run root
  `comparison_whatif_20260919_170759` (`_toy_priced`, `--n-batches 6 --max-skus 8000
  --coverage-days 1 --workers 6`, two cells, 40 arms). Candidate: the same command from the
  changed tree, then `python Tests/bench/run_digest.py <baseline> <candidate>` -> IDENTICAL.
- Campaign-scale references for `run_digest.py --cell`: the stopped campaign root
  `comparison_whatif_20260919_002111` (cells `k1_off_fifo`, `k1_off_lifo`, `k1_off_gmyopic`)
  and the probe `comparison_whatif_20260919_123309` (`k1_off_fifo`, `k1_off_gmyopic`).
- Existing tests that fake the pool at function level and must keep passing:
  `Tests/integration/test_crash_recovery.py` (`_supervise`, `_run_pool`, `_run_scenario`,
  `_run_whatif_matrix` on `SPECS['single']`), `test_coupled_resume_reconciler.py`
  (`mid_flight` flags), `test_supervisor_broken_pool.py` (real spawn pool, in the CLAUDE.md
  gate), `Tests/unit/test_worker_recycling_pin.py` (pins `recycle = 1` by source).
- A wall measurement belongs on a two-cell toy, per-arm from `runtime_metrics`, never the
  wall of a contended host (memory `toy-run-noise-floor-is-three-percent`).

## The run in flight

Phase 2 `inbound_unload` is running as task `ILO_phase2_inbound_unload` from snapshot
`62d9649e`, root `comparison_whatif_20260919_163818`, cell-serial, ~22 h from 16:38. Cell 1
done 17:15; cell 2 (`k1_off_lifo`) in progress. Its results are valid whatever the pool
becomes; a relaunch under a flat pool would take ~4 h. Stopping it is the user's call
(`Stop-ScheduledTask` then `Unregister-ScheduledTask -TaskName 'ILO_phase2_inbound_unload'`).
