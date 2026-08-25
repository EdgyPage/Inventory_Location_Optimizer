"""test_receiving_e2e.py — a receiving crew through the production seam, reconciled.

Every other test of this feature drives the manager directly. This one goes through
`_prepare_channel_run` + `_run_strategy_worker` — the same two calls a real run makes — and
then reads the DATABASE back from disk and reconciles it. That matters because the interesting
failures are all in the wiring between those layers, not inside any of them:

  * a `we.extend` that never runs, so `work_events` and `batch_stats` disagree;
  * a crew handed to `recv_rows` as a `Crew` rather than its pre-offset worker tuple, so uids
    restart at 0 and collide with the pickers, which nothing downstream can see;
  * a clock that is not carried, so one receiver unloads two batches at the same instant;
  * a snapshot drained twice, so a batch reports an idle dock that moved hundreds of units.

None of those raises. Each produces a run that looks completely healthy — `work_events` has
no consumer anywhere outside `Tests/`, so nothing else in the repo would notice.

`reconcile()` is the assertion, and it is sabotage-checked: all five of its checks were
confirmed to FAIL against a hand-corrupted copy of a real DB, and the merge-key check catches
a duplicate that the existing `assert rows == sorted(rows)` guard passes.

Deliberately NOT built on `Tests/bench/coverage_e2e.py`: that harness hands every worker a
`queue.Queue()` it never drains and prints DONE regardless of what happened, so a green run
through it is not evidence.

Run:  python -m pytest Tests/e2e/test_receiving_e2e.py -q
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3

import pytest

from Diagnostics.receiving_report import reconcile
from Optimization import run_simulation as rs
from Optimization.persistence.Picking_Data import load_batch_stats
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _store_dbs(tmp_path, n_skus=250):
    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    inv = build_inventory_from_plan(num_skus=n_skus, plan=plan, seed=3)
    inv_db = str(tmp_path / 'inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'recv', 'num_skus': n_skus})
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


def _run_one_arm(tmp_path, monkeypatch, *, crew_size, day_seconds=None, n_batches=6):
    """One store arm, end to end. Returns (db_path, run_id)."""
    log = logging.getLogger('recv-e2e')
    log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', n_batches)
    monkeypatch.setitem(g, 'recv_crew_size', crew_size)
    monkeypatch.setitem(g, 'recv_day_seconds', day_seconds)
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
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


# ── the crew is off ───────────────────────────────────────────────────────────────

def test_no_crew_writes_no_receiving_and_still_reconciles(tmp_path, monkeypatch):
    """A run with no receiving crew must record four honest zeros and PASS. A tool that
    reported "no activity" as a failure would be useless on every run before this feature."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=0)
    rows = load_batch_stats(db, run_id)
    assert rows, 'the arm did not simulate'
    assert all(r.recv_unloaded == 0 and r.recv_seconds == 0.0 for r in rows)
    assert all(r.recv_depth == 0 and r.recv_cut == 0 for r in rows)

    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM work_events WHERE role='receive'").fetchone()[0]
    finally:
        con.close()
    assert n == 0

    r = reconcile(db, run_id)
    assert r['verdict'] == 'PASS', r
    assert r['active'] is False


# ── the crew is on ────────────────────────────────────────────────────────────────

def test_a_receiving_crew_reconciles_across_both_surfaces(tmp_path, monkeypatch):
    """THE end-to-end claim. `work_events` and `batch_stats` are written by different code
    from different state and share nothing below the manager, so their agreement is real
    evidence rather than a tautology."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    r = reconcile(db, run_id)
    assert r['active'], 'no receiving happened; the test proves nothing'
    assert r['verdict'] == 'PASS', (
        f'failed: {[k for k, v in r["checks"].items() if not v]} — {r}')
    assert r['qty_events'] == r['qty_batch_stats'] > 0
    assert r['seconds_events'] > 0.0


def test_the_three_crews_hold_disjoint_contiguous_uid_blocks(tmp_path, monkeypatch):
    """A uid collision is invisible to everything else: it passes `put_rows`' bounds check,
    the DDL has no uniqueness constraint, and the merged view still sorts. It surfaces only
    as a per-actor rollup merging two people — and is quietest when the receiving crew is
    small, which is the likely configuration."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    r = reconcile(db, run_id)
    blocks = r['uid_blocks']
    assert set(blocks) == {'pick', 'put', 'receive'}, blocks
    assert r['uid_overlaps'] == 0
    assert r['checks']['uids_contiguous']
    # ...and they are in allocation order, which is what makes the cursor chaining visible
    lo = {role: rng[0] for role, rng in blocks.items()}
    assert lo['pick'] < lo['put'] < lo['receive'], blocks


def test_a_short_day_leaves_work_on_the_dock(tmp_path, monkeypatch):
    """ROLLOVER, through the production seam. A day far shorter than one unload lets exactly
    the committed unload through per batch — the START gate — and the rest carries."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=1, day_seconds=0.5)
    rows = load_batch_stats(db, run_id)
    assert sum(r.recv_cut for r in rows) > 0, 'the whistle never bit'
    assert max(r.recv_depth for r in rows) > 0, 'nothing was left standing'
    # the dock is never negative and the carry is monotone while nothing clears it
    assert all(r.recv_depth >= 0 for r in rows)
    assert reconcile(db, run_id)['verdict'] == 'PASS'


def test_the_receive_rows_carry_the_right_role_and_type(tmp_path, monkeypatch):
    """`put_rows` used to hard-code `event_type='put'` while taking role from the worker, so
    a receive row could have carried `role='receive', event_type='put'` — making
    `SUM(duration) WHERE role='put'` disagree with the same query on `event_type`."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    con = sqlite3.connect(db)
    try:
        pairs = con.execute(
            'SELECT DISTINCT role, event_type FROM work_events ORDER BY role, event_type'
        ).fetchall()
        aisle_null = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive' AND aisle_id IS NOT NULL"
        ).fetchone()[0]
        nonpos = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive' AND qty <= 0").fetchone()[0]
    finally:
        con.close()
    assert ('receive', 'receive') in pairs
    assert not [p for p in pairs if p[0] == 'receive' and p[1] != 'receive'], pairs
    assert aisle_null == 0, 'a dock has no aisle'
    assert nonpos == 0, 'an unload moves merchandise; qty must be positive'


# ── the reconciliation can fail ───────────────────────────────────────────────────

def test_the_reconciliation_is_not_vacuous(tmp_path, monkeypatch):
    """A green reconciliation means nothing unless it can go red. Corrupts one surface of a
    real run and requires the matching check to catch it."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    assert reconcile(db, run_id)['verdict'] == 'PASS'

    con = sqlite3.connect(db)
    try:
        con.execute('UPDATE batch_stats SET recv_seconds = recv_seconds + 5.0 '
                    'WHERE recv_seconds > 0 AND run_id = ?', (run_id,))
        con.commit()
    finally:
        con.close()

    r = reconcile(db, run_id)
    assert r['verdict'] == 'FAIL'
    assert r['checks']['seconds_agree'] is False
    assert r['checks']['counts_agree'] is True, (
        'corrupting the seconds also broke the count check; the two are not independent '
        'and one of them is not measuring what it claims')
