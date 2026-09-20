"""simdriver.scenario — the cell-matrix driver over ONE work pool.

Every run is a cell matrix (a plain run is the single cell k1_off).  `_run_whatif_matrix`
builds the cells from a spec, freezes the sampled inventory once when there is more than one
cell, and then streams EVERY cell's units into one `workpool.WorkPool` (`_run_cells`): each
cell's setup -- manifest, shared assets reshaped from the frozen inventory, work units -- runs
in the parent, in spec order, INSIDE that cell's `cells.cell_scope`, and its units go into the
pool the moment they exist, while the previous cells' units are still running.  No worker
waits for a cell boundary, and there is no second path: a single cell is a one-cell matrix
through the same code (2026-09-19; until then one executor was opened per cell and the matrix
wall was the sum of the cells' slowest units)."""
from __future__ import annotations

import logging
import os

from Optimization.config import strategies
# The spec's OWN accessors, as a module: `_run_whatif_matrix` installs an arm set, it
# never authors one, and every shape question (flat / 'all' / rule pairs) is answered
# beside the specs rather than re-derived here.
from Optimization.config import whatif_config as _wc
from Optimization.simdriver.sim_assets import build_shared_assets
from Optimization.config.sim_config import (
    CONFIG, FULFILLMENT_CONFIGS, STORE_CONFIGS, regime_sizing_from_config,
)
from Optimization.runschema.sim_manifest import write_run_manifest
from Optimization.config.strategies import STRATEGIES
from Optimization.runschema.runlayout import iter_sim_dbs
from Optimization.simdriver.cells import (
    Cell, _build_cells, _cell_complete, _tightest_split, cell_scope, reference_cell,
)
from Optimization.simdriver.strategy_runner import _peak_rss_mib
from Optimization.simdriver.supervisor import SimBooks, _sim_executor, sim_jobs
from Optimization.simdriver.workpool import WorkPool
from Optimization.simdriver.workunits import _build_work_units, _record_coverage


def _warn_blank_arms(base_dir: str, log: logging.Logger) -> list:
    """Scan every sim_*.db under ONE CELL dir and log a prominent WARNING for any that
    recorded zero batches (blank result DB).  Returns the list of blank db paths.

    A blank arm means the whole strategy produced no metrics — almost always a
    placement/stocking failure (units never binned → no pick tasks → all batches
    skipped).  Downstream analysis silently omits such arms, so we flag them here.

    A CELL dir, never the run root: `iter_sim_dbs` walks `<pair>/<config>[/<channel>]/`
    from the directory it is handed, so from a run root it would take the cell level for the
    pair level and, on a mixed catalogue, find nothing at all -- a vacuous "no blanks".
    """
    import sqlite3
    blank = []
    for _run, db in iter_sim_dbs(base_dir):
        try:
            con = sqlite3.connect(db)
            n = con.execute('SELECT COUNT(*) FROM batch_stats').fetchone()[0]
            con.close()
        except Exception:                       # noqa: BLE001 — never block on the scan
            continue
        if n == 0:
            blank.append(db)
    if blank:
        log.warning(f'{"!"*64}')
        log.warning(f'  {len(blank)} BLANK sim DB(s) — recorded ZERO batches (no metrics):')
        for db in sorted(blank):
            log.warning(f'    {os.path.relpath(db, base_dir)}')
        log.warning('  These arms produced no data (likely a placement/stocking failure).')
        log.warning(f'{"!"*64}')
    else:
        log.info('  Metric check: every sim DB recorded ≥1 batch (no blanks).')
    return blank


def _cell_dir(base_dir: str, cell: Cell) -> str:
    """`<run_root>/<cell>` -- or the run root itself for the unnamed cell a harness drives
    (`Tests/e2e/test_coupled_resume_e2e.py` runs one pair straight under its base dir)."""
    return os.path.join(base_dir, cell.name) if cell.name else base_dir


def _build_assets(scenario_base: str, pairs, log: logging.Logger, frozen_by_pair) -> dict:
    """ONE cell's shared assets, under the caller's `cell_scope`: the manifest and, per pair,
    the warehouse reshaped from the frozen inventory when the run has one (multi-cell) or
    sampled fresh (a single cell).  Returns `{label: shared}`."""
    write_run_manifest(scenario_base, pairs, STORE_CONFIGS, FULFILLMENT_CONFIGS, STRATEGIES)
    g = CONFIG['global']
    shared_by_pair = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
        shared_by_pair[label] = build_shared_assets(
            inv_db, aff_db, log,
            max_skus=g['max_skus'], regime_sizing=regime_sizing_from_config(),
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(scenario_base, label, 'warehouse.db'),
            frozen_inventory_db=(frozen_by_pair or {}).get(label),
        )
    return shared_by_pair


def _run_cells(base_dir, pairs, cells, log, *, workers, assets_for, skip_completed=False,
               max_retries=2, resume_granularity='strategy') -> dict:
    """Every cell's units through ONE pool.  Returns {cell: [unfinished uids]} for the cells
    that left units unrecovered (a cell whose SETUP raised reports `('setup',)`; a leaf whose
    prepare raised reports `('prepare', tag)`), empty on a clean matrix.

    THE SHAPE.  For each cell in spec order: `cell_scope(cell)` -> `assets_for(cell,
    cell_dir)` (the shared assets; `_run_whatif_matrix` builds them, a harness may hand in
    ones it built) -> `_build_work_units` -> `pool.submit` -> `pool.absorb()`.  The pool
    absorbs and finalizes what landed between one cell's setup and the next, so a cell's
    units are running while the next cell is being set up; only the WAIT between cells is
    gone.  At campaign scale setup is ~6 min a cell against units of 15 min to 3 h, so the
    pool is fed continuously after the first cell.

    A CELL'S SHARED ASSETS LIVE UNTIL ITS LAST UNIT LANDS (`kept`), not until its units are
    submitted: a rebuild after a broken pool re-runs `_build_work_units` over the SAME
    assets with `mid_flight=True` (the coupled reconciler's contract), exactly as the old
    per-cell supervisor did, and never `build_shared_assets` again -- on a single-cell run
    that would RE-SAMPLE and rewrite the planned inventory under the units in flight.  The
    affinity CSR is dropped at submit (the unit builder reads only its path).  On a wide,
    shallow spec every cell's inventory can be alive at once; the parent's memory is the
    price of the retry path, and it is logged per cell.

    A cell whose setup RAISES (a campaign pin that does not match, a derivation that
    disagrees with the record) is reported and skipped, and the pool keeps running the
    others: an exception must never unwind into the pool with units in flight.
    """
    n_cells = len(cells)
    by_name = {c.name: c for c in cells}
    books = SimBooks(log, run_root=base_dir)
    kept: dict = {}                                   # cell name -> shared_by_pair
    unfinished: dict = {}
    # THE BATCH PRECOMPUTE'S POOL, sized to the cores the sim pool leaves free.  `ensure_batches`
    # opens a transient spawn pool of its own whenever a cell's script is not already on disk
    # (`batch_precompute.precompute_batches`), and since every cell after the first is set up
    # while the sim pool is running, sizing it to the SIM pool's width -- what the old per-cell
    # driver did, when nothing else was running -- would run twice the machine's cores.  Chunk
    # count changes no byte (`Tests/e2e/test_batch_precompute.py`, serial == parallel).
    precompute_workers = max(1, (os.cpu_count() or 1) - int(workers or 1))

    def _units(cell, log_queue, *, mid_flight=False):
        """This cell's `(units, meta)`, under its scope, over its kept assets."""
        scenario_base = _cell_dir(base_dir, cell)
        failed: list = []
        with cell_scope(cell):
            if cell.name not in kept:
                os.makedirs(scenario_base, exist_ok=True)
                kept[cell.name] = assets_for(cell, scenario_base)
            units, meta = _build_work_units(
                pairs, scenario_base, kept[cell.name], log, log_queue, precompute_workers,
                # `skip_completed` on a resume AND on a mid-flight rebuild.
                skip_completed=(skip_completed or mid_flight),
                resume_granularity=resume_granularity, mid_flight=mid_flight, failed=failed)
        for shared in kept[cell.name].values():
            shared.pop('affinity_store', None)        # 41 MB per pair, unread from here on
        if failed:
            unfinished.setdefault(cell.name, []).extend(('prepare', tag) for tag in failed)
        return units, meta

    def _rebuild(name):
        cell = by_name[name]
        units, meta = _units(cell, pool.log_queue, mid_flight=True)
        books.register(name, meta)
        return sim_jobs(name, units, cell_index=cells.index(cell) + 1, cell_total=n_cells)

    hint = f'python -m Optimization.run_simulation --resume {base_dir}'
    with WorkPool(workers, log, run_root=base_dir, executor_factory=_sim_executor,
                  max_retries=max_retries, on_success=books.on_success,
                  on_failure=books.on_failure, resume_hint=hint) as pool:
        for ci, cell in enumerate(cells, start=1):
            try:
                units, meta = _units(cell, pool.log_queue)
            except Exception as exc:                   # noqa: BLE001 -- reported, never unwound
                log.error(f'  [{cell.name}] cell setup FAILED: {exc}', exc_info=True)
                unfinished.setdefault(cell.name, []).append(('setup',))
                kept.pop(cell.name, None)
                continue
            books.register(cell.name, meta)
            pool.submit(cell.name, sim_jobs(cell.name, units, cell_index=ci, cell_total=n_cells))
            pool.absorb()                          # log + finalize what landed during this setup
            for settled in pool.settled_cells():
                kept.pop(settled, None)
            # THE PARENT'S SIDE OF THE TRADE, once a cell.  A cell's assets live until its last
            # unit lands (so a retry never rebuilds them), which on a wide, shallow spec can
            # mean several cells' inventories alive at once -- the one cost the flat pool adds
            # to the parent.  Peak RSS, not current: the high-water mark is what would have to
            # fit, and it is the number a reader needs before widening a matrix.  Reuses the
            # worker's stdlib probe (`strategy_runner._peak_rss_mib`); None on a platform that
            # will not answer, and then the line simply says so.
            _rss = _peak_rss_mib()
            log.info(f'  [pool] parent holds {len(kept)} cell(s) of shared assets  '
                     f'peak_rss={f"{_rss:,.0f}M" if _rss is not None else "n/a"}')
        left = pool.finish(rebuild=_rebuild)
    books.sweep()                                  # safety sweep, per cell
    for name, uids in left.items():
        unfinished.setdefault(name, []).extend(uids)
    return unfinished


def _run_whatif_matrix(base_dir, pairs, log, spec, resume=False, max_retries=2,
                       resume_granularity='strategy', max_tasks_per_child=1):
    """Drive a cell-matrix run from a spec (see whatif_config.SPECS): every run is a matrix, so a
    plain run is the single cell ``k1_off``.  A MULTI-cell matrix freezes the sampled inventory once
    (tightest cell) and reshapes it per cell (apples-to-apples); a SINGLE-cell run skips the freeze
    and samples fresh — the flat run as it always was, nested under its cell dir.  Returns
    {'cells': [names], 'reference': name, 'unfinished': {cell: [unit ids]}} -- `unfinished` holds
    only cells that left units unrecovered, so an empty dict is a clean matrix.

    `max_tasks_per_child` is accepted for the saved run specs that pass it and IGNORED: the
    executor is built with recycling pinned at 1 (`supervisor._sim_executor`)."""
    cells = _build_cells(spec)
    reference = reference_cell(cells, spec.get('reference'))
    # THE SHAPE GATE, restated at the driver.  `get_spec` is the single door every run comes
    # through and validates there, but `_run_whatif_matrix` is also reachable with a
    # hand-built spec (a test, a bench harness), and a malformed one would otherwise reach
    # the channel install below and half-apply.  Cheap, total and pure.
    _wc.validate_spec(spec)
    _pairs = _wc.rule_pairs_of(spec)
    # THE "AN INBOUND MATRIX MUST STATE ITS ARM SET" REFUSAL USED TO LIVE HERE, over the built
    # CELLS — neither `arms` nor `rule_pairs` means "leave CHANNEL_RESTOCKS as committed",
    # which is the full 34-arm suite, 1,360 work units where the funnel budgeted 480, and not
    # the experiment phase 2 is.  It moved into `validate_spec` above, which reads the spec's
    # own `inbound` AXIS — and that is STRICTLY STRONGER, not merely equivalent: a cell's
    # `inbound` is built FROM that axis (`cells._inbound_axis`), so the two can never disagree,
    # and the cell form additionally let a SINGLE-cell inbound spec through by also requiring
    # `len(cells) > 1`.  Recorded as a comment rather than kept as a second copy: two refusals
    # for one rule is how one of them ends up saying something slightly different.
    # THE CHANNEL INSTALL.  One accessor answers all four spec shapes — absent ⇒ None (leave
    # it as committed, which is what makes `--spec single` the old flat run), 'all' ⇒ the full
    # suite everywhere, a flat tuple ⇒ the same subset everywhere, and rule PAIRS ⇒ each
    # channel's own COLUMN in rank order.  The derivation lives beside the pairs
    # (`whatif_config.channel_restocks_for`) rather than here, so the driver never authors an
    # arm set — it installs one.  CONFIG['restocks'] was snapshotted at import, so refresh it.
    _installed = _wc.channel_restocks_for(spec, CONFIG['channels'])
    if _installed is not None:
        # `fifo` is not optional in an explicit FLAT subset.  `run_channel_rollup.
        # _baseline_entry` prefers `uni_fifo`, falls back to any key containing `fifo`, and
        # then falls back to `strategies[0]` — so a fifo-less arm set silently baselines every
        # row against an arbitrary arm and renders plausible, meaningless savings.  It is also
        # the order-blind negative control: `uni_fifo` and `opt_fifo` are byte-identical runs,
        # so a gradient on that row indicts the machinery.  Refused HERE, at minute zero,
        # rather than discovered at analysis after the simulation has been paid for.
        #
        # UNCONDITIONAL, AND VACUOUS FOR A PAIR SPEC BY CONSTRUCTION.  `validate_spec` wants
        # the rider as a PAIR — `('fifo', 'fifo')` — which puts `fifo` in BOTH derived columns,
        # so a pair spec that reaches here has already satisfied this.  It was written guarded
        # by `if _pairs is None`, and no input could reach the other side of that branch: a
        # guard nothing can exercise is a guard nobody finds out is wrong.
        for _ch, _rules in _installed.items():
            if _rules is not None and 'fifo' not in _rules:
                raise ValueError(
                    f'the {_ch} arm subset {_rules!r} has no `fifo` rule: it is both the '
                    f'analysis baseline (run_channel_rollup falls back to an ARBITRARY arm '
                    f'without it) and the order-blind negative control. Add it to the '
                    f'spec\'s `arms`')
        for _ch, _rules in _installed.items():
            strategies.CHANNEL_RESTOCKS[_ch] = _rules
            CONFIG['channels'][_ch]['restocks'] = strategies.restocks_for(_ch)

    # PAIR-AWARE, because `spec.get('arms')` is None on a coupled campaign and a log line that
    # said `arms=None` there would read as "the committed default" — the one thing the refusal
    # above exists to prevent.
    _swept = _wc.swept_rules_of(spec)
    log.info(f'Cell matrix → {base_dir}  ({len(cells)} cell(s), reference={reference}, '
             f'{"pairs" if _pairs is not None else "arms"}={_swept!r}, resume={resume})')
    log.info('  cells: ' + ', '.join(c.name for c in cells))

    # ── 1. FREEZE the sampled inventory once (from the tightest cell) per pair — MULTI-cell only ──
    # (the scheduler is task→picker, not placement, so it doesn't affect the frozen layout).  A
    # single-cell run has nothing to share, so it samples fresh (frozen=None).
    g = CONFIG['global']
    frozen: dict | None = None
    if len(cells) > 1:
        # No inbound argument: the freeze PLANS inventory, it does not simulate, so no yard or
        # dock decision is reachable from here.  SCOPED like a cell, so CONFIG is back to the
        # run level before the first cell's scope opens (and the analysis stage, which runs
        # after the matrix in this process, is not sized under the freeze's split).
        frozen = {}
        with cell_scope(Cell('_freeze', _tightest_split(cells), {'enabled': False}, 'round_robin')):
            for label, inv_db, aff_db in pairs:
                frozen_db = os.path.join(base_dir, '_frozen', label, 'planned_inventory.db')
                if resume and os.path.exists(frozen_db):
                    frozen[label] = frozen_db
                    log.info(f'  reusing frozen inventory[{label}]')
                    continue
                log.info(f'\n{"="*64}\n  FREEZE inventory (tightest cell): {label}\n{"="*64}')
                shared = build_shared_assets(
                    inv_db, aff_db, log, max_skus=g['max_skus'],
                    regime_sizing=regime_sizing_from_config(), keyframe_interval=g['keyframe_interval'],
                    warehouse_db_path=os.path.join(base_dir, '_frozen', label, 'warehouse.db'))
                frozen[label] = shared['planned_inv_db']
                # THE FREEZE IS WHERE A MULTI-CELL RUN DECLARES ITS STOCK, so it is where the
                # declaration has to be recorded (ADR-0002).  Every cell after this one loads the
                # frozen inventory with `sample=False` and declares nothing, so `_build_work_units`
                # -- which records the block on a single-cell run -- sees no coverage to record and
                # the run would end up with levels nobody could reproduce.  Its own analysis stage
                # would then refuse to rebuild the warehouse from the catalogue and emit no figures
                # at all, which is exactly how this was found: preflight's 2-cell canary produced
                # a complete run tree and an empty analysis one.
                _record_coverage(base_dir, label, shared.get('coverage'), log)

    # ── 2. Every cell: reshape the warehouse (from FROZEN inv when multi-cell) + simulate,
    #       through ONE pool.  A cell already complete on a resume is not set up at all. ──
    n_cells = len(cells)
    todo = []
    for ci, cell in enumerate(cells, start=1):
        if resume and _cell_complete(_cell_dir(base_dir, cell), pairs):
            log.info(f'  SKIP cell {ci}/{n_cells} {cell.name} (already complete)')
            continue
        todo.append(cell)

    def _assets(cell, scenario_base):
        zdesc = cell.zoning.get('mode', 'off') if cell.zoning.get('enabled') else 'off'
        ci = cells.index(cell) + 1
        log.info(f'\n{"#"*64}\n  CELL {cell.name}  (cell {ci}/{n_cells})  split={cell.split}  '
                 f'zoning={zdesc}  scheduler={cell.scheduler}  inbound={cell.inbound}\n{"#"*64}')
        return _build_assets(scenario_base, pairs, log, frozen)

    unfinished_by_cell = _run_cells(
        base_dir, pairs, todo, log, workers=g['workers'], assets_for=_assets,
        skip_completed=resume, max_retries=max_retries, resume_granularity=resume_granularity)

    # Loud blank-arm check, once per cell after the pool: surface any sim_*.db that completed
    # with ZERO recorded batches so a blank DB is discovered NOW, not halfway through analysis.
    for cell in cells:
        cell_dir = _cell_dir(base_dir, cell)
        if os.path.isdir(cell_dir):
            _warn_blank_arms(cell_dir, log)

    log.info(f'\nCell matrix complete → {base_dir}')
    return {'cells': [c.name for c in cells], 'reference': reference,
            'unfinished': unfinished_by_cell}
