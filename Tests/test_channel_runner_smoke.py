"""test_channel_runner_smoke.py — mixed-catalog fan-out through the REAL run_simulation wiring.

Generates a small mixed catalog (store families + a fulfillment cube family), builds the shared
assets (a mixed warehouse), runs `_prepare_config_run` + `_run_strategy_worker` per channel, and
asserts each channel writes its OWN DB subtree with batch stats and the right `channel` identity.

This exercises the production seam (`_prepare_config_run` fan-out + the one-worker-per-channel
worker + per-channel persistence) without the full multiprocessing pool.

Run:  python -m pytest Tests/test_channel_runner_smoke.py -q
"""
from __future__ import annotations

import os
import sys
import glob
import queue
import sqlite3
import logging

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:0] = [os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization'),
                os.path.join(_ROOT, 'Warehouse', 'generation')]

import run_simulation as rs               # noqa: E402
import strategy_runner as sr              # noqa: E402
import generate_affinity as ga           # noqa: E402
from Picking_Data import load_batch_stats  # noqa: E402
from generation.generate_inventory import (  # noqa: E402
    Family, fulfillment_family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _mixed_dbs(tmp_path):
    plan = [
        Family('food', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        Family('clothing', 0.3, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        fulfillment_family(share=0.3, cube_sizes=(4, 6, 8)),
    ]
    inv = build_inventory_from_plan(num_skus=300, plan=plan, seed=1)
    inv_db = str(tmp_path / 'mixed_inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'mixed', 'num_skus': 300})
    # Seed a small REAL affinity matrix (consecutive-SKU pairs) so affinity-driven strategies
    # (rank_minlabor / cohesion) have a usable matrix — they refuse to run on an empty one.
    aff_db = str(tmp_path / 'mixed_aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit(); conn.close()
    return inv_db, aff_db


def test_mixed_fanout_writes_per_channel_dbs(tmp_path):
    log = logging.getLogger('chan-smoke'); log.setLevel(logging.ERROR)
    rs.N_BATCHES = 3

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=300, max_bins=40000, min_bins=3000,
        keyframe_interval=1, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    cfg = rs.REGRESSION_CONFIGS[0]
    pair_dir = str(tmp_path / 'run' / 'mixed'); os.makedirs(pair_dir, exist_ok=True)
    strategy_args, sim_skeletons = rs._prepare_config_run(cfg, shared, pair_dir, log, workers=1)

    # fan-out produced one run subtree per channel
    channels = {sk['channel'] for sk in sim_skeletons}
    assert channels == {'store', 'fulfillment'}, channels
    for sk in sim_skeletons:
        assert sk['run_dir'].rstrip('/\\').endswith(sk['channel'])   # <config>/<channel>/

    # run ONE worker per channel (first strategy of each) and confirm per-channel output
    ran: dict = {}
    for a in strategy_args:
        ck = a['channel_key']
        if ck in ran:
            continue
        assert a['channel_regime'] == ck          # worker filters inventory to this regime
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
        ran[ck] = (a['db_path'], a['run_id'])

    assert set(ran) == {'store', 'fulfillment'}
    for ch, (db_path, run_id) in ran.items():
        assert os.path.exists(db_path)
        assert (os.sep + ch + os.sep) in db_path            # DB lives under the channel subdir
        assert len(load_batch_stats(db_path, run_id)) >= 1  # the channel actually simulated
        con = sqlite3.connect(db_path)
        row = con.execute('SELECT channel FROM simulation_runs WHERE run_id=?', (run_id,)).fetchone()
        con.close()
        assert row and row[0] == ch                         # persisted channel identity


def test_mixed_analysis_replicates_per_channel(tmp_path):
    """run_analysis discovers the per-channel run subtrees and replicates the whole graph
    suite for each channel (store + fulfillment) — no plot-module changes required."""
    import run_analysis as ra
    log = logging.getLogger('chan-an'); log.setLevel(logging.ERROR)
    rs.N_BATCHES = 2

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=200, max_bins=40000, min_bins=2000,
        keyframe_interval=1, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    cfg = rs.REGRESSION_CONFIGS[0]
    base_dir = str(tmp_path / 'run')
    pair_dir = os.path.join(base_dir, 'mixed'); os.makedirs(pair_dir, exist_ok=True)
    strategy_args, sim_skeletons = rs._prepare_config_run(cfg, shared, pair_dir, log, workers=1)

    for a in strategy_args:                       # run every strategy of every channel
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
    for sk in sim_skeletons:                      # write each channel's sim_meta.json
        rs._finalize_config_run(sk)

    ra.run_analysis(base_dir, log, workers=1, preset='NO_STATS')

    for ch in ('store', 'fulfillment'):
        ch_dir = os.path.join(pair_dir, cfg['name'], ch)
        assert os.path.exists(os.path.join(ch_dir, 'sim_meta.json'))
        pngs = glob.glob(os.path.join(ch_dir, '**', '*.png'), recursive=True)
        assert pngs, f'no graphs generated for channel {ch}'
