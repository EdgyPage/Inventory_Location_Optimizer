"""test_whatif_delta.py

Locks Optimization/run_whatif_delta.py's cross-cell diff, and in particular the two things that
were broken:

  - STORE-ONLY runs are scanned.  Their tree has no <channel> level, and the old relpath scan
    (`len(rel) < 4: continue`) skipped every such DB, so a store-only sweep produced an EMPTY
    whatif_delta.csv with no error.
  - whatif_delta.json is EMITTED.  docs/macros.py:whatif_matrix reads this file, but nothing in the
    repo wrote it — the only way to get one was to hand-copy and rename whatif_labor.json.  The
    shape asserted here is exactly what that macro indexes into.

Synthetic sqlite only — deterministic, no real simulation.

Run:  python -m pytest Tests/test_whatif_delta.py -q
"""
from __future__ import annotations

import csv
import json

import sqlite3

from Optimization import run_whatif_delta as rwd
from Optimization.sim_manifest import write_run_layout


def _make_db(path, per_batch):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE batch_stats (id INTEGER PRIMARY KEY, batch_id INTEGER, '
                'duration REAL, total_items INTEGER, task_makespan REAL, '
                'thr_task REAL, thr_batch REAL)')
    for i, (tm, dur, items) in enumerate(per_batch):
        con.execute('INSERT INTO batch_stats (batch_id, duration, total_items, task_makespan) '
                    'VALUES (?,?,?,?)', (i, dur, items, tm))
    con.commit()
    con.close()


def _descriptor(root, cells, channels, reference):
    write_run_layout(
        str(root), spec='test', reference=reference,
        cells=[(c, None, {'enabled': False},
                'lpt' if c.endswith('_lpt') else 'round_robin') for c in cells],
        pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
        ff_cfgs=[], channels=channels, arms=None, created='2026-07-28T00:00:00')


def _tree(root, cell, arm, per_batch, *, channel: str | None):
    """<cell>/<pair>/<config>[/<channel>]/sim_<arm>.db — channel=None is the store-only shape."""
    d = root / cell / 'pairA' / 'store'
    if channel:
        d = d / channel
    d.mkdir(parents=True, exist_ok=True)
    _make_db(str(d / f'sim_{arm}.db'), per_batch)


def _build(tmp_path, name, channel):
    root = tmp_path / name
    root.mkdir()
    # reference cell is slower per batch; the treatment cell moves the same items faster
    for cell, dur in (('k1_off_rr', 2.0e4), ('k1_off_lpt', 1.0e4)):
        _tree(root, cell, 'uni_fifo_norsl', [(3.6e4, dur, 10)] * 100, channel=channel)
    _descriptor(root, ['k1_off_rr', 'k1_off_lpt'],
                ['store', 'fulfillment'] if channel else ['store'], 'k1_off_rr')
    return root


def test_store_only_run_is_not_skipped(tmp_path):
    """The regression: no <channel> segment must still produce rows."""
    root = _build(tmp_path, 'comparison_whatif_store_only', channel=None)
    rwd.run(str(root), reference='k1_off_rr', log=None)
    rows = list(csv.DictReader(open(root / 'whatif_delta.csv')))
    assert rows, 'store-only sweep produced no delta rows — the channel level is OPTIONAL'
    assert {r['channel'] for r in rows} == {'store'}
    assert {r['cell'] for r in rows} == {'k1_off_lpt'}          # reference itself is not a row


def test_mixed_run_keeps_its_channel(tmp_path):
    root = _build(tmp_path, 'comparison_whatif_mixed', channel='fulfillment')
    rwd.run(str(root), reference='k1_off_rr', log=None)
    rows = list(csv.DictReader(open(root / 'whatif_delta.csv')))
    assert {r['channel'] for r in rows} == {'fulfillment'}


def test_delta_json_is_written_in_the_shape_the_site_reads(tmp_path):
    root = _build(tmp_path, 'comparison_whatif_json', channel='store')
    rwd.run(str(root), reference='k1_off_rr', log=None)
    d = json.load(open(root / 'whatif_delta.json'))

    assert d['reference'] == 'k1_off_rr'
    assert [c['name'] for c in d['cells']] == ['k1_off_lpt']    # only the treatment cell
    cell = d['cells'][0]
    # the exact keys docs/macros.py:whatif_matrix indexes
    assert set(cell) >= {'name', 'layout', 'zoning_label', 'by_channel'}
    store = cell['by_channel']['store']
    assert set(store) >= {'dthr_batch', 'dtask_ms', 'dbatch_ms', 'dthr_task'}
    # halving batch duration at identical labor = throughput up, task makespan flat
    assert store['dthr_batch']['med'] > 0
    assert abs(store['dtask_ms']['med']) < 1e-6


def test_reference_defaults_to_the_runs_own_descriptor(tmp_path):
    """Re-analyzing an OLD sweep must use ITS reference, not whatever whatif_config says today."""
    root = _build(tmp_path, 'comparison_whatif_ref', channel='store')
    rwd.run(str(root), reference=None, log=None)                # no explicit reference
    d = json.load(open(root / 'whatif_delta.json'))
    assert d['reference'] == 'k1_off_rr'
