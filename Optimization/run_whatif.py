"""run_whatif.py — what-if scenario matrix over aisle-reconstruction × velocity zoning.

Simulates the SAME inventory and the SAME batch stream across a COMBINATORIAL matrix of aisle-layout
cells (aisle_split: k segments × capacity_loss) crossed with velocity zoning (ABC on / off).  The
batch fingerprint depends on the SAMPLED inventory, so the harness samples ONCE (from the tightest
cell, which every roomier cell can hold) and freezes that planned inventory; each cell then reshapes
the warehouse from the frozen inventory and re-simulates it.  Zoning on/off is batch-identical for
free (placement-time only).

Each cell writes its own scenario subtree `comparison_whatif_<ts>/<cell>/<pair>/<pickcfg>/<channel>/`
so `run_analysis <cell>` and the scenario-delta comparator work per scenario.  Reuses the entire
run_simulation inner pipeline (build_shared_assets → _run_workers_flat), and is RESUMABLE.

    # run from the repo root as a module (so the Optimization package imports resolve).
    # all assignment fns × {off, 2-band, 3-band zoning} × split-in-two at {0%, 10%} loss, both regimes:
    python -m Optimization.run_whatif --k 1,2 --loss 0,0.10 --zoning off,abc2,abc3 \
        --arms all --n-batches 100 --workers 20
    # resume a long run that was interrupted (reuses the frozen inventory, skips finished cells/arms):
    python -m Optimization.run_whatif --resume comparison_whatif_20260709_... --arms all \
        --k 1,2 --loss 0,0.10 --zoning off,abc2,abc3 --workers 20
    # compare:
    python -m Optimization.run_whatif_delta <comparison_whatif_...> --reference k1_off
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime

from Optimization import run_simulation as rs
from Optimization.sim_config import CONFIG, regime_sizing_from_config, _setup_logging
from Optimization.sim_assets import build_shared_assets
from Optimization import strategies

CHANNELS = ('store', 'fulfillment')
ARMS_DEFAULT = ('fifo', 'rank_labor', 'comp')   # the what-if compares LAYOUTS, not the full arm grid


def _zoning(mode: str) -> dict:
    """Zoning cell spec.  off | abc2 (2-band hot/cold) | abc3 == abc (3-band A/B/C) | equal.
    Manual mass thresholds (hot band = SKUs holding the top X% of demand mass; aisles then
    allocated by band footprint so the hot band is a small aisle fraction)."""
    if mode == 'abc2':
        return {'enabled': True, 'mode': 'abc', 'n_bands': 2, 'abc': {'mass_thresholds': [0.7]}}
    if mode in ('abc', 'abc3'):
        return {'enabled': True, 'mode': 'abc', 'n_bands': 3, 'abc': {'mass_thresholds': [0.7, 0.9]}}
    if mode == 'equal':
        return {'enabled': True, 'mode': 'equal', 'n_bands': 3}
    return {'enabled': False}


def _build_cells(ks, losses, zmodes):
    """Combinatorial (k × capacity_loss × zoning) cells.  k=1 = no split (loss collapses to 0).
    Returns [(name, aisle_split|None, zoning_dict), …]; the reference is the (k=1, off) cell."""
    cells, seen = [], set()
    for zmode in zmodes:
        z = _zoning(zmode)
        for k in ks:
            for loss in (losses if k > 1 else [0.0]):
                split = None if k <= 1 else {'k': k, 'capacity_loss': loss}
                name = (f'k{k}' + ('' if k <= 1 else f'_l{int(round(loss * 100))}') + f'_{zmode}')
                if name in seen:
                    continue
                seen.add(name)
                cells.append((name, split, z))
    return cells


def _apply_cell(aisle_split, zoning) -> None:
    for ch in CHANNELS:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)


def _tightest_split(cells):
    """The split with the largest capacity_loss (fewest bins).  Freezing the sampled inventory to it
    guarantees every roomier cell holds it (the frozen inventory always fits)."""
    splits = [c[1] for c in cells if c[1] and c[1].get('capacity_loss', 0.0) > 0 and int(c[1].get('k', 1)) > 1]
    return max(splits, key=lambda s: s.get('capacity_loss', 0.0), default=None)


def _cells_arg(s, cast):
    return [cast(x) for x in str(s).split(',') if x != '']


def main():
    ap = argparse.ArgumentParser(description='What-if aisle-reconstruction × velocity-zoning matrix.')
    ap.add_argument('--profiles-dir', default=rs._DEFAULT_PROFILES_DIR)
    ap.add_argument('--k', default='1,2', help='comma list of aisle-split segment counts (1=no split)')
    ap.add_argument('--loss', default='0,0.15', help='comma list of capacity_loss fractions (k>1 only)')
    ap.add_argument('--zoning', default='off,abc', help='comma list of zoning modes: off|abc2|abc3(=abc)|equal')
    ap.add_argument('--arms', default=','.join(ARMS_DEFAULT),
                    help="comma list of restock arms to sweep, or 'all' for the full assignment-function suite")
    ap.add_argument('--n-batches', type=int, default=100)
    ap.add_argument('--max-skus', type=int, default=None)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--ff-min-bins', type=int, default=None)
    ap.add_argument('--s-min-bins', type=int, default=None)
    ap.add_argument('--resume', metavar='BASE_DIR', default=None,
                    help='resume a prior comparison_whatif_* dir: reuse its frozen inventory and skip completed arm-runs')
    args = ap.parse_args()

    cells = _build_cells(_cells_arg(args.k, int), _cells_arg(args.loss, float), _cells_arg(args.zoning, str))
    reference = next((c[0] for c in cells if c[1] is None and not c[2]['enabled']), cells[0][0])
    # --arms all ⇒ None = the FULL restock suite (all 17 assignment fns × uni/opt = 34 arms, _norsl).
    arms = None if args.arms.strip().lower() == 'all' else tuple(_cells_arg(args.arms, str))

    g = CONFIG['global']
    g['n_batches'] = args.n_batches
    g['workers'] = args.workers
    if args.max_skus is not None:
        g['max_skus'] = args.max_skus
    if args.ff_min_bins is not None:
        CONFIG['channels']['fulfillment']['sizing']['min_bins'] = args.ff_min_bins
    if args.s_min_bins is not None:
        CONFIG['channels']['store']['sizing']['min_bins'] = args.s_min_bins
    for ch in CHANNELS:
        strategies.CHANNEL_RESTOCKS[ch] = arms   # None ⇒ full assignment-function suite
        CONFIG['channels'][ch]['restocks'] = strategies.restocks_for(ch)

    if args.resume:
        base_dir = args.resume if os.path.isabs(args.resume) else os.path.join(rs._OUTPUT_DIR, args.resume)
        if not os.path.isdir(base_dir):
            sys.exit(f'Resume directory not found: {base_dir}')
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_dir = os.path.join(rs._OUTPUT_DIR, f'comparison_whatif_{ts}')
        os.makedirs(base_dir, exist_ok=True)
    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    log.info(f'What-if matrix → {base_dir}  ({len(cells)} cells, reference={reference}, '
             f'arms={"all" if arms is None else arms}, n_batches={args.n_batches}, resume={bool(args.resume)})')
    log.info('  cells: ' + ', '.join(c[0] for c in cells))

    pairs = rs.find_latest_db_pairs(args.profiles_dir)
    if not pairs:
        sys.exit(f'No inventory+affinity DB pairs found in: {args.profiles_dir}')

    # ── 1. FREEZE the sampled inventory once (from the tightest cell) per pair ────────
    _apply_cell(_tightest_split(cells), _zoning('off'))
    regime_sizing = regime_sizing_from_config()
    frozen: dict = {}
    for label, inv_db, aff_db in pairs:
        frozen_db = os.path.join(base_dir, '_frozen', label, 'planned_inventory.db')
        if args.resume and os.path.exists(frozen_db):
            frozen[label] = frozen_db
            log.info(f'  reusing frozen inventory[{label}]')
            continue
        log.info(f'\n{"="*64}\n  FREEZE inventory (tightest cell): {label}\n{"="*64}')
        shared = build_shared_assets(
            inv_db, aff_db, log, max_skus=g['max_skus'], regime_sizing=regime_sizing,
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(base_dir, '_frozen', label, 'warehouse.db'))
        frozen[label] = shared['planned_inv_db']

    # ── 2. Each cell: reshape the warehouse from the FROZEN inventory + simulate ─────
    for name, aisle_split, zoning in cells:
        zdesc = zoning.get('mode', 'off') if zoning.get('enabled') else 'off'
        scenario_base = os.path.join(base_dir, name)
        # Resume: skip a cell whose every (pair × pickcfg × channel) already finalized sim_meta.json.
        if args.resume and _cell_complete(scenario_base, pairs):
            log.info(f'  SKIP cell {name} (already complete)')
            continue
        log.info(f'\n{"#"*64}\n  CELL {name}  split={aisle_split}  zoning={zdesc}\n{"#"*64}')
        _apply_cell(aisle_split, zoning)
        regime_sizing = regime_sizing_from_config()
        os.makedirs(scenario_base, exist_ok=True)
        rs.write_run_manifest(scenario_base, pairs, rs.STORE_CONFIGS, rs.FULFILLMENT_CONFIGS, rs.STRATEGIES)
        shared_by_pair = {}
        for label, inv_db, aff_db in pairs:
            shared_by_pair[label] = build_shared_assets(
                inv_db, aff_db, log, max_skus=g['max_skus'], regime_sizing=regime_sizing,
                keyframe_interval=g['keyframe_interval'],
                warehouse_db_path=os.path.join(scenario_base, label, 'warehouse.db'),
                frozen_inventory_db=frozen[label])
        rs._run_workers_flat(pairs, scenario_base, shared_by_pair, args.workers, log,
                             max_tasks_per_child=1, skip_completed=bool(args.resume))
        rs._warn_blank_arms(scenario_base, log)

    log.info(f'\nWhat-if matrix complete → {base_dir}')
    log.info(f'  Per-cell graphs: python run_analysis.py {base_dir}\\<cell>')
    log.info(f'  Compare:         python run_whatif_delta.py {base_dir} --reference {reference}')


def _cell_complete(scenario_base: str, pairs: list) -> bool:
    """A cell is done when it has ≥1 sim_meta.json per pair (all its configs finalized)."""
    if not os.path.isdir(scenario_base):
        return False
    return all(glob.glob(os.path.join(scenario_base, label, '**', 'sim_meta.json'), recursive=True)
               for label, _i, _a in pairs)


if __name__ == '__main__':
    main()
