"""test_channel_runner_smoke.py — mixed-catalog fan-out through the REAL run_simulation wiring.

Generates a small mixed catalog (store families + a fulfillment cube family), builds the shared
assets (a mixed warehouse), plans the independent per-channel sweep with `_channel_runs_for`,
runs `_prepare_channel_run` + `_run_strategy_worker` per channel, and asserts each channel writes
its OWN DB subtree with batch stats and the right `channel` identity.

This exercises the production seam (`_channel_runs_for` + `_prepare_channel_run` per channel-run
+ the one-worker-per-channel worker + per-channel persistence) without the full multiprocessing
pool.

Run:  python -m pytest Tests/test_channel_runner_smoke.py -q
"""
from __future__ import annotations

import os
import glob
import queue
import sqlite3
import logging

from Optimization import run_simulation as rs
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Optimization.persistence.Picking_Data import load_batch_stats
from Warehouse.generation.generate_inventory import (
    Family, fulfillment_family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _prepare_all_channels(shared, pair_dir, log, workers=1):
    """New independent-sweep seam: plan every channel-run (store configs + fulfillment
    configs via _channel_runs_for) and concatenate their strategy_args + skeletons —
    mirroring what _run_workers_flat does across the union."""
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    all_args, all_sk = [], []
    for ch, cfg in channel_runs:
        a, sk = rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=workers)
        all_args.extend(a)
        all_sk.extend(sk)
    return all_args, all_sk


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


def test_mixed_fanout_writes_per_channel_dbs(tmp_path, monkeypatch):
    log = logging.getLogger('chan-smoke'); log.setLevel(logging.ERROR)
    rs.CONFIG['global']['n_batches'] = 3
    # One store config keeps the smoke run fast; fulfillment keeps its single default config.
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=300, max_bins=40000, min_bins=3000,
        keyframe_interval=1, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    pair_dir = str(tmp_path / 'run' / 'mixed'); os.makedirs(pair_dir, exist_ok=True)
    strategy_args, sim_skeletons = _prepare_all_channels(shared, pair_dir, log, workers=1)

    # the independent sweep produced one run subtree per channel
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


class _ListHandler(logging.Handler):
    """Capture on the 'analysis' logger DIRECTLY: the inline `_run_strategy_worker` calls
    above do `root.handlers = []` (correct in a spawned worker, where the queue handler must
    be the only route), which silently removes pytest's caplog handler from the root — so
    root-based capture sees nothing this test runs after the sim phase."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines: list = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_mixed_analysis_replicates_per_channel(tmp_path, monkeypatch):
    """run_analysis discovers the per-channel run subtrees and replicates the whole graph
    suite for each channel (store + fulfillment) — no plot-module changes required.

    Doubles as the ZERO-WARNING gate for the output map and the access-log gate for the
    request broker: a full inline analysis over real DBs must save every figure inside its
    evaluation's declared out_subdir (io._MAP_WARNINGS stays empty) and must get every
    request granted (no DENIED lines, a 0-denial run summary)."""
    from Optimization import run_analysis as ra
    from Optimization.Performance_Evaluations.common import io as pe_io
    log = logging.getLogger('chan-an'); log.setLevel(logging.ERROR)
    rs.CONFIG['global']['n_batches'] = 2
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=200, max_bins=40000, min_bins=2000,
        keyframe_interval=1, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    base_dir = str(tmp_path / 'run')
    pair_dir = os.path.join(base_dir, 'mixed'); os.makedirs(pair_dir, exist_ok=True)
    strategy_args, sim_skeletons = _prepare_all_channels(shared, pair_dir, log, workers=1)

    for a in strategy_args:                       # run every strategy of every channel
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
    for sk in sim_skeletons:                      # write each channel's sim_meta.json
        rs._finalize_config_run(sk)

    pe_io._MAP_WARNINGS.clear()
    cap = _ListHandler()
    an_log = logging.getLogger('analysis')
    an_log.addHandler(cap)
    an_log.setLevel(logging.INFO)
    try:
        ra.run_analysis(base_dir, log, workers=1, preset='NO_STATS')
    finally:
        an_log.removeHandler(cap)

    # Each channel writes its OWN run subtree (store under its config name, fulfillment under
    # its own) — derive the dir from the skeleton rather than assuming a shared config name.
    assert {sk['channel'] for sk in sim_skeletons} == {'store', 'fulfillment'}
    for sk in sim_skeletons:
        ch_dir = sk['run_dir']
        assert os.path.exists(os.path.join(ch_dir, 'sim_meta.json'))
        pngs = glob.glob(os.path.join(ch_dir, '**', '*.png'), recursive=True)
        assert pngs, f'no graphs generated for channel {sk["channel"]}'

    # THE zero-warning gate: every figure landed inside its evaluation's declared out_subdir.
    assert pe_io._MAP_WARNINGS == set(), (
        f'figures saved outside their declared out_subdir: {sorted(pe_io._MAP_WARNINGS)}')

    # THE access gate: with every resource present, every request is granted — no denials.
    access = [m for m in cap.lines if m.startswith('[access]')]
    assert access, 'the broker logged no [access] lines at all — the choke point is unwired'
    denied = [m for m in access if 'DENIED' in m]
    assert not denied, f'unexpected denials on a complete run: {denied}'
    granted_evals = {m.split()[1] for m in access if '-> granted' in m}
    assert granted_evals, 'no granted lines recorded'


def _mixed_inventory(num_skus=120, seed=2):
    plan = [
        Family('food', 0.5, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        fulfillment_family(share=0.5, cube_sizes=(4, 6, 8)),
    ]
    return build_inventory_from_plan(num_skus=num_skus, plan=plan, seed=seed)


def _store_only_inventory(num_skus=120, seed=3):
    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    return build_inventory_from_plan(num_skus=num_skus, plan=plan, seed=seed)


def test_independent_sweep_is_union_not_cross_product(monkeypatch):
    """A mixed catalog sweeps store and fulfillment configs INDEPENDENTLY: one channel-run per
    store config + one per fulfillment config (a union), never the cross product.  Store runs
    carry the store cost/pool + their own names; fulfillment runs carry the walker cost + theirs."""
    from Warehouse.kernel.regime import STORE, FULFILLMENT
    from Warehouse.layout.Storage_Primitive import FulfillmentCart

    # Asymmetric counts (3 vs 2) so union (5) is distinguishable from a cross product (6).
    store_cfgs = [{'name': 's1'}, {'name': 's2'}, {'name': 's3'}]
    ff_cfgs    = [{'name': 'f1'}, {'name': 'f2', 'cart': 'FulfillmentCart', 'num_pickers': 12}]
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', store_cfgs)
    monkeypatch.setitem(rs.CONFIG['channels']['fulfillment'], 'configs', ff_cfgs)

    mixed, runs = rs._channel_runs_for(_mixed_inventory())
    assert mixed is True
    assert len(runs) == len(store_cfgs) + len(ff_cfgs) == 5      # union, not 3×2

    store_runs = [(ch, c) for ch, c in runs if ch.name == 'store']
    ff_runs    = [(ch, c) for ch, c in runs if ch.name == 'fulfillment']
    assert [c['name'] for _, c in store_runs] == ['s1', 's2', 's3']
    assert [c['name'] for _, c in ff_runs] == ['f1', 'f2']
    for ch, _ in store_runs:
        assert ch.regime == STORE and ch.picker.num_pickers == rs.k_pickers()
    for ch, _ in ff_runs:
        assert ch.regime == FULFILLMENT and ch.picker.cost.cart is FulfillmentCart
    assert ff_runs[1][0].picker.num_pickers == 12               # per-config walker pool override


def test_store_only_catalog_skips_fulfillment_sweep(monkeypatch):
    """A store-only catalog runs ONLY the store config sweep (no fulfillment channel-runs), so it
    stays byte-identical to the pre-fulfillment pipeline regardless of FULFILLMENT_CONFIGS."""
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [{'name': 's1'}, {'name': 's2'}])
    monkeypatch.setitem(rs.CONFIG['channels']['fulfillment'], 'configs', [{'name': 'f1'}, {'name': 'f2'}])
    mixed, runs = rs._channel_runs_for(_store_only_inventory())
    assert mixed is False
    assert [ch.name for ch, _ in runs] == ['store', 'store']
    assert [c['name'] for _, c in runs] == ['s1', 's2']
