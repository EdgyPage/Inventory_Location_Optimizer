"""run_whatif.py — what-if scenario matrix over aisle-reconstruction × velocity zoning.

Simulates the SAME inventory and the SAME batch stream across a matrix of aisle-layout cells
(aisle_split) crossed with velocity zoning (ABC on / off).  The batch fingerprint depends on the
SAMPLED inventory, so the harness samples ONCE (from the tightest cell, which every other cell can
hold) and freezes that planned inventory; every cell then reshapes the warehouse from the frozen
inventory and re-simulates it.  Zoning on/off is batch-identical for free (placement-time only).

Each cell writes its own scenario subtree `comparison_whatif_<ts>/<cell>/<pair>/<pickcfg>/<channel>/`
so `run_analysis <cell>` and the scenario-delta comparator work per scenario.  Reuses the entire
run_simulation inner pipeline (build_shared_assets → _run_workers_flat).

    python Optimization/run_whatif.py --n-batches 60 --workers 20
    (then)  python Optimization/run_whatif_delta.py <comparison_whatif_...> --reference base
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from Optimization import run_simulation as rs
from Optimization.sim_config import CONFIG, regime_sizing_from_config, _setup_logging
from Optimization.sim_assets import build_shared_assets
from Optimization import strategies

# ── the scenario matrix (name, aisle_split, velocity_zoning) — edit freely ───────────
_ABC = {'enabled': True, 'mode': 'abc', 'n_bands': 3, 'abc': {'mass_thresholds': [0.7, 0.9]}}
_OFF = {'enabled': False}
CELLS = [
    ('base',            None,                             _OFF),   # reference: no split, no zoning
    ('split2',          {'k': 2, 'capacity_loss': 0.0},   _OFF),
    ('split2_loss15',   {'k': 2, 'capacity_loss': 0.15},  _OFF),
    ('zone_abc',        None,                             _ABC),
    ('split2_zone',     {'k': 2, 'capacity_loss': 0.0},   _ABC),
    ('split2l15_zone',  {'k': 2, 'capacity_loss': 0.15},  _ABC),
]
REFERENCE = 'base'
CHANNELS  = ('store', 'fulfillment')
ARMS      = ('fifo', 'rank_labor', 'comp')   # restrict arms — the what-if compares LAYOUTS


def _apply_cell(aisle_split, zoning) -> None:
    for ch in CHANNELS:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)


def _tightest_split():
    """The aisle_split with the largest capacity_loss (fewest bins) — freezing the sampled
    inventory to it guarantees every other (roomier) cell holds it with no queue growth."""
    splits = [c[1] for c in CELLS if c[1] and c[1].get('capacity_loss', 0.0) > 0 and int(c[1].get('k', 1)) > 1]
    return max(splits, key=lambda s: s.get('capacity_loss', 0.0), default=None)


def main():
    ap = argparse.ArgumentParser(description='What-if aisle-reconstruction × velocity-zoning matrix.')
    ap.add_argument('--profiles-dir', default=rs._DEFAULT_PROFILES_DIR)
    ap.add_argument('--n-batches', type=int, default=60)
    ap.add_argument('--max-skus', type=int, default=None)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--ff-min-bins', type=int, default=None)
    ap.add_argument('--s-min-bins', type=int, default=None)
    args = ap.parse_args()

    g = CONFIG['global']
    g['n_batches'] = args.n_batches
    g['workers'] = args.workers
    if args.max_skus is not None:
        g['max_skus'] = args.max_skus
    if args.ff_min_bins is not None:
        CONFIG['channels']['fulfillment']['sizing']['min_bins'] = args.ff_min_bins
    if args.s_min_bins is not None:
        CONFIG['channels']['store']['sizing']['min_bins'] = args.s_min_bins
    for ch in CHANNELS:                         # restrict arms for tractability
        strategies.CHANNEL_RESTOCKS[ch] = ARMS
        CONFIG['channels'][ch]['restocks'] = strategies.restocks_for(ch)

    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_dir = os.path.join(rs._OUTPUT_DIR, f'comparison_whatif_{ts}')
    os.makedirs(base_dir, exist_ok=True)
    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    log.info(f'What-if matrix → {base_dir}  ({len(CELLS)} cells × {CHANNELS} × arms={ARMS})')

    pairs = rs.find_latest_db_pairs(args.profiles_dir)
    if not pairs:
        sys.exit(f'No inventory+affinity DB pairs found in: {args.profiles_dir}')

    # ── 1. FREEZE the sampled inventory once (from the tightest cell) per pair ────────
    _apply_cell(_tightest_split(), _OFF)
    regime_sizing = regime_sizing_from_config()
    frozen: dict = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  FREEZE inventory (tightest cell): {label}\n{"="*64}')
        shared = build_shared_assets(
            inv_db, aff_db, log, max_skus=g['max_skus'], regime_sizing=regime_sizing,
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(base_dir, '_frozen', label, 'warehouse.db'))
        frozen[label] = shared['planned_inv_db']
        log.info(f'  frozen planned inventory[{label}] = {frozen[label]}')

    # ── 2. Each cell: reshape the warehouse from the FROZEN inventory + simulate ─────
    for name, aisle_split, zoning in CELLS:
        log.info(f'\n{"#"*64}\n  CELL {name}  split={aisle_split}  zoning={zoning.get("mode","off") if zoning.get("enabled") else "off"}\n{"#"*64}')
        _apply_cell(aisle_split, zoning)
        regime_sizing  = regime_sizing_from_config()
        scenario_base  = os.path.join(base_dir, name)
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
                             max_tasks_per_child=1)
        rs._warn_blank_arms(scenario_base, log)

    log.info(f'\nWhat-if matrix complete → {base_dir}')
    log.info(f'  Per-cell graphs: python run_analysis.py {base_dir}\\<cell>')
    log.info(f'  Compare:         python run_whatif_delta.py {base_dir} --reference {REFERENCE}')


if __name__ == '__main__':
    main()
