"""test_standing_yard_e2e.py — the standing yard's byte-identity layers, on the real DB.

The unit tier (`Tests/unit/test_standing_yard.py`) pins the event STREAMS at manager
scale.  This file pins the same two claims where they were actually made — against the
run DATABASE, through the production seam (`_prepare_channel_run` + `_run_strategy_worker`,
the `test_receiving_e2e` harness shape):

  * THE DEGENERATE LOCKSTEP: standing-on, FIFO policies, doors >= every trailer that
    ever stands, no receiving cap, allocation='merged' writes a DB **byte-identical**
    to the v1 drain-everything trailer pipeline — every table, every row, every column
    (`simulation_runs.created` excepted: it is wall clock, and a probe of two identical
    v1 runs showed it is the ONLY nondeterministic column in the file).
  * THE CONTAINMENT PROPERTY: the same configuration with allocation='split' writes a DB
    identical EXCEPT the receive rows' labor stamps — t_abs / t_local / shift_index /
    actor_uid / actor_local — proving crew allocation is labor-only: same packs, same
    order, same durations, same placements, same picks.

Under a receiving cap the two allocations legitimately diverge in the unloaded set
(split makes partial progress everywhere; merged completes top-ranked trailers first).
That divergence is the feature, documented at the design ticket, and deliberately not
equality-tested anywhere.

Run:  python -m pytest Tests/e2e/test_standing_yard_e2e.py -q
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3

import pytest

from Optimization import run_simulation as rs
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}

#: The one column two byte-identical runs may not share: wall clock at row creation.
_VOLATILE = {'simulation_runs': {'created'}}
#: What the containment property lets a receive row move: labor stamps, never work.
_LABOR_STAMPS = {'t_abs', 't_local', 'shift_index', 'actor_uid', 'actor_local'}


def _store_dbs(tmp_path, n_skus=250):
    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    inv = build_inventory_from_plan(num_skus=n_skus, plan=plan, seed=3)
    inv_db = str(tmp_path / 'inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'yard', 'num_skus': n_skus})
    aff_db = str(tmp_path / 'aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit()
    conn.close()
    return inv_db, aff_db


def _run_one_arm(tmp_path, monkeypatch, *, standing, allocation='split', n_batches=6):
    """One store arm through the production seam, trailer pipeline ON.

    The degenerate knobs are the test's whole point: doors far beyond any batch's
    trailer count, FIFO everywhere, and no receiving day — so the yard drains whole
    every batch and the ONLY difference between the arms is the standing machinery.
    """
    log = logging.getLogger('yard-e2e')
    log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', n_batches)
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'recv_day_seconds', None)
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_dock_doors', 500)
    monkeypatch.setitem(g, 'inbound_standing_yard', standing)
    monkeypatch.setitem(g, 'inbound_crew_allocation', allocation)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _store_dbs(tmp_path)
    build_pair = str(tmp_path / 'build'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=250, max_bins=40000, min_bins=3000,
        keyframe_interval=0, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    pair_dir = str(tmp_path / 'run'); os.makedirs(pair_dir, exist_ok=True)
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = channel_runs[0]
    args, _sk = rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=1)

    a = args[0]
    a['log_queue'] = queue.Queue()
    sr._run_strategy_worker(a)
    return a['db_path'], a['run_id']


def _dump(db):
    """{table: (columns, rows-in-insertion-order)} for every table in the file."""
    con = sqlite3.connect(db)
    out = {}
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info({t})')]
            try:
                rows = con.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall()
            except sqlite3.OperationalError:      # WITHOUT ROWID: PK order is natural
                rows = con.execute(f'SELECT * FROM {t}').fetchall()
            out[t] = (cols, rows)
    finally:
        con.close()
    return out


def _masked(cols, rows, dropped):
    if not dropped:
        return rows
    keep = [i for i, c in enumerate(cols) if c not in dropped]
    return [tuple(r[i] for i in keep) for r in rows]


# ── layer 2: the degenerate lockstep, on the file itself ─────────────────────────

def test_the_degenerate_merged_run_writes_a_byte_identical_db(tmp_path, monkeypatch):
    db_v1, _ = _run_one_arm(tmp_path / 'v1', monkeypatch, standing=False)
    db_yd, _ = _run_one_arm(tmp_path / 'yd', monkeypatch, standing=True,
                            allocation='merged')
    d1, d2 = _dump(db_v1), _dump(db_yd)
    assert set(d1) == set(d2)
    for t in sorted(d1):
        cols, rows1 = d1[t]
        cols2, rows2 = d2[t]
        assert cols == cols2, t
        drop = _VOLATILE.get(t, set())
        assert _masked(cols, rows1, drop) == _masked(cols2, rows2, drop), (
            f'table {t!r} diverged — the degenerate standing run is not the v1 drain')
    # non-vacuity: the arms actually received through the trailer source
    con = sqlite3.connect(db_v1)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive'").fetchone()[0]
    finally:
        con.close()
    assert n > 0, 'no receiving happened; the lockstep proved nothing'


# ── layer 3: containment — split moves labor stamps only ─────────────────────────

def test_the_split_run_differs_only_in_receive_labor_stamps(tmp_path, monkeypatch):
    db_v1, _ = _run_one_arm(tmp_path / 'v1', monkeypatch, standing=False)
    db_sp, _ = _run_one_arm(tmp_path / 'sp', monkeypatch, standing=True,
                            allocation='split')
    d1, d2 = _dump(db_v1), _dump(db_sp)
    assert set(d1) == set(d2)
    for t in sorted(d1):
        cols, rows1 = d1[t]
        cols2, rows2 = d2[t]
        assert cols == cols2, t
        drop = set(_VOLATILE.get(t, set()))
        if t == 'work_events':
            role_i = cols.index('role')
            recv1 = [r for r in rows1 if r[role_i] == 'receive']
            recv2 = [r for r in rows2 if r[role_i] == 'receive']
            assert (_masked(cols, recv1, _LABOR_STAMPS)
                    == _masked(cols, recv2, _LABOR_STAMPS)), (
                'a receive row moved something besides its labor stamps — that is '
                'placement physics leaking out of the allocation mode')
            assert recv1 != recv2, (
                'no receive stamp differed anywhere — the containment test is vacuous '
                '(the scenario never staged two trailers against the crew)')
            rest1 = [r for r in rows1 if r[role_i] != 'receive']
            rest2 = [r for r in rows2 if r[role_i] != 'receive']
            assert rest1 == rest2, 'a NON-receive work_events row moved'
            continue
        assert _masked(cols, rows1, drop) == _masked(cols2, rows2, drop), (
            f'table {t!r} diverged — split allocation must be labor-only')
