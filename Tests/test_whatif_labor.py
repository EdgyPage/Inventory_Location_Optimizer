"""test_whatif_labor.py

Locks Optimization/run_whatif_labor.py — the cross-cell throughput / labor-HOURS what-if:

  - _hours(): full-run SUMS convert ms -> modeled hours (Σ task_makespan / 3.6e6), plus items/batches.
  - _scheduler_of(): the picker scheduler is decoded from the CELL suffix (k1_off_lpt -> lpt).
  - _parse_arm(): 'uni_rank_labor_norsl' -> ('uni','rank_labor','norsl'); assignment may carry '_'.
  - _pct(): signed % where + is always better.
  - end-to-end main(): on a synthetic 2-cell tree, whatif_labor.csv carries the right
    scheduler/hours columns and labor_saved = labor(fifo) - labor(arm) (same cell/channel/initial),
    and whatif_labor.json is whatif_matrix()-shaped (one treatment cell vs the reference).
  - STORE-ONLY runs are scanned (regression): the tree has no <channel> level, which the old
    relpath scan (`len(rel) < 4: continue`) skipped entirely, silently returning zero rows.

Synthetic sqlite only — deterministic, no D: drive, no real simulation.  Trees carry a real
run_layout.json so the versioned resolver (Optimization/runschema) can walk them.

Run:  python -m pytest Tests/test_whatif_labor.py -q
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3

from Optimization import run_whatif_labor as rwl
from Optimization.runschema import resolver_for
from Optimization.runschema.sim_manifest import write_run_layout


def _descriptor(root, cells, channels, reference=None):
    """Write the run-tree descriptor the resolver selects a version from."""
    write_run_layout(
        str(root), spec='test', reference=reference or cells[0],
        cells=[(c, None, {'enabled': False},
                'lpt' if c.endswith('_lpt') else 'round_robin') for c in cells],
        pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
        ff_cfgs=[], channels=channels, arms=None, created='2026-07-28T00:00:00')


def _make_db(path, per_batch):
    """Write a minimal batch_stats DB. per_batch = list of (task_makespan_ms, duration_ms, items)."""
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE batch_stats (id INTEGER PRIMARY KEY, batch_id INTEGER, '
                'duration REAL, total_items INTEGER, task_makespan REAL, '
                'thr_task REAL, thr_batch REAL)')
    for i, (tm, dur, items) in enumerate(per_batch):
        con.execute('INSERT INTO batch_stats (batch_id, duration, total_items, task_makespan) '
                    'VALUES (?,?,?,?)', (i, dur, items, tm))
    con.commit()
    con.close()


# ── pure helpers ──────────────────────────────────────────────────────────────────────────

def test_hours_ms_to_modeled_hours(tmp_path):
    db = str(tmp_path / 'sim_uni_fifo_norsl.db')
    # two batches summing to exactly 3.6e6 ms labor and 1.8e6 ms makespan -> 1.0 h / 0.5 h
    _make_db(db, [(1.8e6, 0.9e6, 100), (1.8e6, 0.9e6, 100)])
    h = rwl._hours(db)
    assert h['labor_hours'] == 1.0
    assert h['batch_hours'] == 0.5
    assert h['items'] == 200 and h['n_batches'] == 2


def test_scheduler_of():
    assert rwl._scheduler_of('k1_off_lpt') == 'lpt'
    assert rwl._scheduler_of('k1_off_rr') == 'round_robin'
    assert rwl._scheduler_of('k2_l0_off') == 'round_robin'   # no known suffix -> default rr


def test_parse_arm():
    assert rwl._parse_arm('uni_rank_labor_norsl') == ('uni', 'rank_labor', 'norsl')
    assert rwl._parse_arm('opt_cluster_map_rank_norsl') == ('opt', 'cluster_map_rank', 'norsl')
    assert rwl._parse_arm('opt_fifo') == ('opt', 'fifo', '')


def test_pct_sign():
    assert rwl._pct(100, 120, True) == 20.0     # more throughput -> +
    assert rwl._pct(100, 80, False) == 20.0     # less labor -> +
    assert rwl._pct(0, 5, True) != rwl._pct(0, 5, True)   # zero ref -> NaN


def test_scan_labor_key_and_merge(tmp_path):
    root = tmp_path / 'comparison_whatif_scan'
    root.mkdir()
    d = root / 'k1_off_lpt' / 'pairA' / 'store' / 'store'
    d.mkdir(parents=True)
    _make_db(str(d / 'sim_uni_fifo_norsl.db'), [(1.0e6, 5.0e5, 50)] * 60)
    _descriptor(root, ['k1_off_lpt'], ['store', 'fulfillment'])
    got = rwl._scan_labor(resolver_for(str(root)), 'k1_off_lpt')
    assert ('pairA', 'store', 'store', 'uni_fifo_norsl') in got
    row = got[('pairA', 'store', 'store', 'uni_fifo_norsl')]
    assert 'labor_hours' in row and 'task_ms' in row and 'thr_batch' in row   # hours + steady means merged


def test_scan_labor_finds_store_only_runs(tmp_path):
    """Regression: a store-only tree has NO <channel> level.  The old relpath scan required four
    segments and returned nothing at all for these runs."""
    root = tmp_path / 'comparison_store_only'
    root.mkdir()
    d = root / 'k1_off' / 'pairA' / 'store'          # <cell>/<pair>/<config>/  — no channel dir
    d.mkdir(parents=True)
    _make_db(str(d / 'sim_uni_fifo_norsl.db'), [(1.0e6, 5.0e5, 50)] * 60)
    _descriptor(root, ['k1_off'], ['store'])
    got = rwl._scan_labor(resolver_for(str(root)), 'k1_off')
    assert got, 'store-only run produced no rows — the channel level is OPTIONAL'
    # channel is reported as 'store' (the layout has no channel dir, but the channel IS store)
    assert ('pairA', 'store', 'store', 'uni_fifo_norsl') in got


# ── end-to-end main() ───────────────────────────────────────────────────────────────────────

def _arm_tree(root, cell, arm, per_batch):
    d = root / cell / 'pairA' / 'store' / 'store'
    d.mkdir(parents=True, exist_ok=True)
    _make_db(str(d / f'sim_{arm}.db'), per_batch)


def test_main_labor_saved_and_json(tmp_path, monkeypatch):
    root = tmp_path / 'comparison_whatif_test'
    root.mkdir()
    # fifo = 1.0 h labor; map = 0.5 h labor  -> saved(map) = +0.5 h.  lpt gets more throughput.
    for cell, thr in (('k1_off_rr', 1.0), ('k1_off_lpt', 1.4)):
        _arm_tree(root, cell, 'uni_fifo_norsl', [(3.6e4, 3.6e4, 10)] * 100)   # 100*3.6e4 = 3.6e6 ms = 1.0 h
        _arm_tree(root, cell, 'uni_map_norsl', [(1.8e4, 1.8e4, 10 * thr)] * 100)  # 1.8e6 ms = 0.5 h
    _descriptor(root, ['k1_off_rr', 'k1_off_lpt'], ['store', 'fulfillment'], reference='k1_off_rr')
    monkeypatch.setattr('sys.argv',
                        ['run_whatif_labor', str(root), '--baseline', 'fifo', '--reference', 'k1_off_rr'])
    rwl.main()

    rows = list(csv.DictReader(open(root / 'whatif_labor.csv')))
    by = {(r['cell'], r['assignment']): r for r in rows}
    # scheduler decoded from the cell name
    assert by[('k1_off_lpt', 'map')]['scheduler'] == 'lpt'
    assert by[('k1_off_rr', 'fifo')]['scheduler'] == 'round_robin'
    # labor_saved = labor(fifo) - labor(map) = 1.0 - 0.5 = +0.5 h ; fifo baseline itself saves 0
    assert abs(float(by[('k1_off_lpt', 'map')]['labor_saved']) - 0.5) < 1e-6
    assert abs(float(by[('k1_off_lpt', 'fifo')]['labor_saved'])) < 1e-6
    assert abs(float(by[('k1_off_lpt', 'map')]['labor_hours']) - 0.5) < 1e-6

    summary = json.load(open(root / 'whatif_labor.json'))
    assert summary['reference'] == 'k1_off_rr'
    assert [c['name'] for c in summary['cells']] == ['k1_off_lpt']    # only the treatment cell
    store = summary['cells'][0]['by_channel']['store']
    assert store['dthr_batch']['med'] > 0        # lpt throughput up vs rr
    assert abs(store['dtask_ms']['med']) < 1e-6  # labor flat (same task_makespan across schedulers)
