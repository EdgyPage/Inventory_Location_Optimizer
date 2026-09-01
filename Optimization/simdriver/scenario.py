"""simdriver.scenario — one scenario + the cell-matrix driver.

_run_scenario is the inner pipeline for a SINGLE warehouse cell (manifest + shared
assets + flat pool + blank-arm check); _run_whatif_matrix loops _build_cells over one
frozen inventory so every run is a cell matrix (a plain run is the single cell k1_off)."""
from __future__ import annotations

import os

from Optimization.config import strategies
from Optimization.simdriver.sim_assets import build_shared_assets
from Optimization.config.sim_config import (
    CONFIG, FULFILLMENT_CONFIGS, STORE_CONFIGS, regime_sizing_from_config,
)
from Optimization.runschema.sim_manifest import write_run_manifest
from Optimization.config.strategies import STRATEGIES
from Optimization.runschema.runlayout import iter_sim_dbs
from Optimization.simdriver.cells import (
    _apply_cell, _build_cells, _cell_complete, _tightest_split, reference_cell,
)
from Optimization.simdriver.supervisor import _run_workers_flat


def _warn_blank_arms(base_dir: str, log: logging.Logger) -> list:
    """Scan every sim_*.db under base_dir and log a prominent WARNING for any that
    recorded zero batches (blank result DB).  Returns the list of blank db paths.

    A blank arm means the whole strategy produced no metrics — almost always a
    placement/stocking failure (units never binned → no pick tasks → all batches
    skipped).  Downstream analysis silently omits such arms, so we flag them here.
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


# ── one scenario: manifest + shared-asset build + flat pool run ──────────────────
# The inner run pipeline for a SINGLE warehouse configuration.  Both the normal
# single-config run and each what-if matrix cell call this, so the per-pair glue
# lives in exactly one place.  frozen_by_pair maps label -> an already-planned
# inventory DB to reshape from (what-if freeze); None per label ⇒ sample fresh.
def _run_scenario(base_dir, pairs, regime_sizing, workers, log, *,
                  cell='', cell_index=1, cell_total=1, frozen_by_pair=None, skip_completed=False,
                  max_tasks_per_child=1, max_retries=2, resume_granularity='strategy'):
    # `cell` is the cell name (e.g. k1_off / k1_off_lpt), stamped on every per-arm log line so a
    # multi-cell run's interleaved output is attributable.  cell_index/cell_total drive the GLOBAL
    # run counter (progress across the whole matrix, not just within this cell).
    write_run_manifest(base_dir, pairs, STORE_CONFIGS, FULFILLMENT_CONFIGS, STRATEGIES)
    g = CONFIG['global']
    shared_by_pair = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
        shared_by_pair[label] = build_shared_assets(
            inv_db, aff_db, log,
            max_skus=g['max_skus'], regime_sizing=regime_sizing,
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(base_dir, label, 'warehouse.db'),
            frozen_inventory_db=(frozen_by_pair or {}).get(label),
        )
    _run_workers_flat(pairs, base_dir, shared_by_pair, workers, log, cell=cell,
                      cell_index=cell_index, cell_total=cell_total,
                      max_tasks_per_child=max_tasks_per_child,
                      skip_completed=skip_completed,
                      max_retries=max_retries, resume_granularity=resume_granularity)
    # Loud blank-arm check: surface any sim_*.db that completed with ZERO recorded
    # batches so a blank DB is discovered NOW, not halfway through downstream analysis.
    _warn_blank_arms(base_dir, log)


def _run_whatif_matrix(base_dir, pairs, log, spec, resume=False, max_retries=2,
                       resume_granularity='strategy', max_tasks_per_child=1):
    """Drive a cell-matrix run from a spec (see whatif_config.SPECS): every run is a matrix, so a
    plain run is the single cell ``k1_off``.  A MULTI-cell matrix freezes the sampled inventory once
    (tightest cell) and reshapes it per cell (apples-to-apples); a SINGLE-cell run skips the freeze
    and samples fresh — bit-identical to the old flat run, just nested under its cell dir.  Returns
    {'cells': [names], 'reference': name}."""
    cells = _build_cells(spec)
    reference = reference_cell(cells, spec.get('reference'))
    # An inbound matrix must STATE its arm set.  `arms=None` means "leave CHANNEL_RESTOCKS as
    # committed", which is the full 34-arm suite — 1,360 work units where the funnel budgeted
    # 480 for twelve arms, and not the experiment phase 2 is.  The arm set is phase 1's output,
    # so a matrix that never received one has skipped the selection rather than chosen it.
    if len(cells) > 1 and any(c.inbound for c in cells) and spec.get('arms') is None:
        raise ValueError(
            'an inbound cell matrix has no `arms`: the phase-2 arm set is phase 1\'s output '
            '(see run_restock_selection), not the committed default. Set whatif_config.'
            'PHASE2_ARMS from the selection artifact, including its mandatory `fifo` rider')
    # Arm override: 'all' ⇒ full suite (CHANNEL_RESTOCKS=None); list ⇒ subset; None ⇒
    # leave strategies.CHANNEL_RESTOCKS exactly as committed.  CONFIG['restocks'] was
    # snapshotted at import, so refresh it too.
    if spec.get('arms') is not None:
        arms = None if str(spec['arms']).lower() == 'all' else tuple(spec['arms'])
        # `fifo` is not optional in an explicit subset.  `run_channel_rollup._baseline_entry`
        # prefers `uni_fifo`, falls back to any key containing `fifo`, and then falls back to
        # `strategies[0]` — so a fifo-less arm set silently baselines every row against an
        # arbitrary arm and renders plausible, meaningless savings.  It is also the
        # order-blind negative control: `uni_fifo` and `opt_fifo` are byte-identical runs, so
        # a gradient on that row indicts the machinery.  Refused HERE, at minute zero, rather
        # than discovered at analysis after the simulation has been paid for.
        if arms is not None and 'fifo' not in arms:
            raise ValueError(
                f'the arm subset {arms!r} has no `fifo` rule: it is both the analysis baseline '
                f'(run_channel_rollup falls back to an ARBITRARY arm without it) and the '
                f'order-blind negative control. Add it to the spec\'s `arms`')
        for ch in CONFIG['channels']:
            strategies.CHANNEL_RESTOCKS[ch] = arms
            CONFIG['channels'][ch]['restocks'] = strategies.restocks_for(ch)

    log.info(f'Cell matrix → {base_dir}  ({len(cells)} cell(s), reference={reference}, '
             f'arms={spec.get("arms")!r}, resume={resume})')
    log.info('  cells: ' + ', '.join(c[0] for c in cells))

    # ── 1. FREEZE the sampled inventory once (from the tightest cell) per pair — MULTI-cell only ──
    # (the scheduler is task→picker, not placement, so it doesn't affect the frozen layout).  A
    # single-cell run has nothing to share, so it samples fresh (frozen=None).
    g = CONFIG['global']
    frozen: dict | None = None
    if len(cells) > 1:
        # No inbound argument: the freeze PLANS inventory, it does not simulate, so no yard or
        # dock decision is reachable from here.  Every cell writes its own inbound record
        # immediately below, so leaving CONFIG's alone here cannot leak into one.
        _apply_cell(_tightest_split(cells), {'enabled': False}, 'round_robin')
        frozen = {}
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

    # ── 2. Each cell: reshape the warehouse (from FROZEN inv when multi-cell) + simulate ──
    n_cells = len(cells)
    for ci, (name, aisle_split, zoning, sched, inbound) in enumerate(cells, start=1):
        scenario_base = os.path.join(base_dir, name)
        if resume and _cell_complete(scenario_base, pairs):
            log.info(f'  SKIP cell {ci}/{n_cells} {name} (already complete)')
            continue
        zdesc = zoning.get('mode', 'off') if zoning.get('enabled') else 'off'
        log.info(f'\n{"#"*64}\n  CELL {name}  (cell {ci}/{n_cells})  split={aisle_split}  '
                 f'zoning={zdesc}  scheduler={sched}  inbound={inbound}\n{"#"*64}')
        _apply_cell(aisle_split, zoning, sched, inbound)
        os.makedirs(scenario_base, exist_ok=True)
        # max_tasks_per_child was NOT forwarded here until 2026-08-15, so every matrix run
        # — i.e. every run, since a plain run is the single cell k1_off — silently used the
        # default 1 and the CLI flag was dead.
        _run_scenario(scenario_base, pairs, regime_sizing_from_config(), g['workers'], log,
                      cell=name, cell_index=ci, cell_total=n_cells,
                      frozen_by_pair=frozen, skip_completed=resume,
                      max_tasks_per_child=max_tasks_per_child,
                      max_retries=max_retries, resume_granularity=resume_granularity)

    log.info(f'\nCell matrix complete → {base_dir}')
    return {'cells': [c[0] for c in cells], 'reference': reference}
