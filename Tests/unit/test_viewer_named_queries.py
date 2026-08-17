"""test_viewer_named_queries.py — the viewer's publisher-side vocabulary holds together.

The viewer composes these queries ONCE per (family, query, vintage) via `dataset.sql_for` and
executes them on its own connections — skipping `Dataset.query`'s per-row validation.  So the
validation lives HERE, once per suite: servability across every vetted vintage, golden
execution through a real `bind` on writer-built fixtures, the twin `bin_history` contract,
the rollup-columns-equal-DDL tie, the repo's FIRST production override, and EXPLAIN QUERY
PLAN parity for the json_each-scoped forms (the gate on the JSON1 adoption — a regression
here demotes that site back to a raw fragment, per the plan).

Run:  python -m pytest Tests/unit/test_viewer_named_queries.py -q
"""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from Schema import dataset
from Optimization.persistence import Picking_Data as PD
from Optimization.persistence import Warehouse_Data as WD
from Visualization import cache_schema as CS

_VIEWER_QUERIES = {
    'sim_db': ('batch_timing', 'picker_events_timeline', 'task_rows_at_batch',
               'task_rows_all', 'pick_load_by_aisle', 'task_load', 'sku_rank_live',
               'bin_scores_scoped', 'sku_series_live'),
    'keyframes_db': ('keyframe_batches', 'bin_history', 'keyframe_bins_nonzero',
                     'keyframe_state_scoped'),
    'viz_cache_db': ('aisle_rollup', 'bin_history', 'sku_rank_top', 'final_home',
                     'cache_meta_value', 'bin_span_scoped'),
    'warehouse_db': ('aisle_layout_geometry', 'warehouse_fingerprint'),
}

_FAMILIES = {'sim_db': PD.SIM_DB_FAMILY, 'keyframes_db': PD.KEYFRAME_DB_FAMILY,
             'viz_cache_db': CS.VIZ_CACHE_DB_FAMILY, 'warehouse_db': WD.WAREHOUSE_DB_FAMILY}


# ── servability: every viewer query composes for every vetted vintage of its family ──

@pytest.mark.parametrize('family,name',
                         [(f, n) for f, names in _VIEWER_QUERIES.items() for n in names])
def test_every_query_serves_every_vetted_vintage(family, name):
    for sid in _FAMILIES[family].supported_ids():
        sql = dataset.sql_for(family, name, sid)          # raises UnsupportedQuery on a gap
        assert 'SELECT' in sql.upper(), (family, name, sid)


# ── structural ties ──────────────────────────────────────────────────────────────

def test_bin_history_twins_share_one_column_contract():
    kf = dataset._QUERIES[('keyframes_db', 'bin_history')]
    vc = dataset._QUERIES[('viz_cache_db', 'bin_history')]
    assert kf.columns == vc.columns == ('t_from', 't_to', 'sku', 'qty_at_from')


def test_aisle_rollup_columns_equal_the_ddl_in_order():
    """AISLE_ROLLUP_COLS replaced a comment that merely ASKED the cache payload and the live
    fallback to agree — this is the mechanism."""
    ddl_cols = re.findall(r'^\s*(\w+)\s+(?:INTEGER|REAL|TEXT)',
                          CS._CREATE_AISLE_ROLLUP, re.M)
    assert tuple(ddl_cols) == CS.AISLE_ROLLUP_COLS
    assert dataset._QUERIES[('viz_cache_db', 'aisle_rollup')].columns == CS.AISLE_ROLLUP_COLS


def test_viewer_queries_declare_no_optional_fill():
    """Viewer queries bypass Dataset.query's optional-fill, so their variants must carry the
    complete column set in SQL — an `optional` declaration here would silently never fire."""
    for family, names in _VIEWER_QUERIES.items():
        for name in names:
            q = dataset._QUERIES[(family, name)]
            assert not q.optional, f'{family}.{name} declares optional-fill the viewer skips'


# ── golden execution through a real bind on writer-built fixtures ────────────────

@pytest.fixture()
def sim_db(tmp_path):
    p = str(tmp_path / 'sim_a.db')
    PD.init_run_db(p)
    run_id = PD.create_run(p, 'test')                            # the sanctioned creator
    con = sqlite3.connect(p)
    con.execute('INSERT INTO batch_stats (run_id, batch_id, duration, num_tasks, total_items,'
                ' avg_concurrent_pickers, picking_pct, traveling_pct, is_outlier,'
                ' reorder_placements) VALUES (?,0,10.0,2,5,1.0,0.5,0.5,0,3)', (run_id,))
    con.execute('INSERT INTO picks (run_id, batch_id, picker_id, aisle_id, bayX, bayY, sku,'
                ' quantity, sim_time) VALUES (?,0,0,1,1,1,42,2,1.0)', (run_id,))
    con.execute('INSERT INTO task_stats (run_id, batch_id, aisle_id, picker_id,'
                ' task_start_time, task_end_time, duration, W, lift_sum, num_bins_visited,'
                ' total_items, is_outlier) VALUES (?,0,1,0,0.0,1.0,1.0,0.5,0.0,1,2,0)',
                (run_id,))
    con.execute('INSERT INTO picker_events (run_id, batch_id, picker_id, time, event_type,'
                ' aisle_id, bayX, bayY, sku, quantity, bins_completed, total_bins,'
                ' items_picked, total_items) VALUES (?,0,0,0.5,"pick",1,1,1,42,2,1,1,2,2)',
                (run_id,))
    con.execute('INSERT INTO bin_scores (run_id, aisle_id, bayX, bayY, travel_d, height_mult,'
                ' layout_score, map_pref) VALUES (?,1,1,1,3.0,1.1,3.3,NULL)', (run_id,))
    con.commit(); con.close()
    return p, run_id


def _rows(path, family, name, **params):
    with dataset.bind(path, family, verify=True) as ds:
        return ds.query(name, **params)


def test_sim_queries_golden(sim_db):
    p, rid = sim_db
    assert _rows(p, 'sim_db', 'batch_timing', run_id=rid) == [
        {'batch_id': 0, 'duration': 10.0, 'num_tasks': 2, 'total_items': 5,
         'reorder_placements': 3}]
    tl = _rows(p, 'sim_db', 'picker_events_timeline', run_id=rid, batch_id=0, aisle_id=None)
    assert [r['sku'] for r in tl] == [42]
    assert set(tl[0]) == set(dataset._QUERIES[('sim_db', 'picker_events_timeline')].columns)
    assert _rows(p, 'sim_db', 'picker_events_timeline', run_id=rid, batch_id=0,
                 aisle_id=99) == []
    at = _rows(p, 'sim_db', 'task_rows_at_batch', run_id=rid, batch_id=0, aisle_id=None)
    al = _rows(p, 'sim_db', 'task_rows_all', run_id=rid, aisle_id=None)
    assert at == al and at[0]['W'] == 0.5
    assert _rows(p, 'sim_db', 'pick_load_by_aisle', run_id=rid, batch_id=0) == [
        {'aisle_id': 1, 'picks': 1, 'units_picked': 2}]
    assert _rows(p, 'sim_db', 'task_load', run_id=rid, batch_id=None) == [
        {'batch_id': 0, 'aisle_id': 1, 'visits': 1, 'task_secs': 1.0}]
    assert _rows(p, 'sim_db', 'sku_rank_live', run_id=rid, n=5) == [
        {'sku': 42, 'picks': 1, 'units': 2, 'first_batch': 0, 'last_batch': 0}]


def test_sim_scoped_queries_golden(sim_db):
    p, rid = sim_db
    unscoped = _rows(p, 'sim_db', 'bin_scores_scoped', run_id=rid, aisles=None)
    assert [r['layout_score'] for r in unscoped] == [3.3]
    assert unscoped[0]['map_pref'] is None                       # NULL, not 0.0
    scoped = _rows(p, 'sim_db', 'bin_scores_scoped', run_id=rid, aisles=json.dumps([1]))
    assert scoped == unscoped
    assert _rows(p, 'sim_db', 'bin_scores_scoped', run_id=rid, aisles=json.dumps([9])) == []
    series = _rows(p, 'sim_db', 'sku_series_live', run_id=rid, skus=json.dumps([42, 7]))
    assert series == [{'sku': 42, 'batch_id': 0, 'picks': 1, 'units': 2}]


@pytest.fixture()
def kf_db(tmp_path):
    p = str(tmp_path / 'sim_a.keyframes.db')
    PD.init_keyframe_db(p)
    con = sqlite3.connect(p)
    con.executemany('INSERT INTO bin_keyframe (run_id, batch_id, aisle_id, bayX, bayY, sku,'
                    " unit_type, storage_size, qty) VALUES (?,?,?,?,?,?,'u','s',?)",
                    [(1, 0, 2, 1, 1, 10, 4), (1, 0, 2, 1, 2, 11, 0), (1, 5, 2, 1, 1, 10, 1)])
    con.commit(); con.close()
    return p


def test_keyframe_queries_golden(kf_db):
    assert _rows(kf_db, 'keyframes_db', 'keyframe_batches', run_id=1) == [
        {'batch_id': 0}, {'batch_id': 5}]
    hist = _rows(kf_db, 'keyframes_db', 'bin_history', run_id=1, aisle_id=2, bayX=1, bayY=1)
    assert hist == [{'t_from': 0, 't_to': 0, 'sku': 10, 'qty_at_from': 4},
                    {'t_from': 5, 't_to': 5, 'sku': 10, 'qty_at_from': 1}]
    nz = _rows(kf_db, 'keyframes_db', 'keyframe_bins_nonzero', run_id=1, batch_id=0)
    assert [(r['sku'], r['qty']) for r in nz] == [(10, 4)]       # the qty=0 row filtered
    sc = _rows(kf_db, 'keyframes_db', 'keyframe_state_scoped', run_id=1, batch_id=0,
               aisles=json.dumps([2]))
    assert len(sc) == 2                                          # NO qty filter — stays Python-side


@pytest.fixture()
def cache_db(tmp_path):
    p = str(tmp_path / 'arm.viz.db')
    con = CS.init_cache_db(p)
    con.execute('INSERT INTO bin_span VALUES (1, 3, 1, 1, 0, 4, 10, 6)')
    con.execute('INSERT INTO sku_rank VALUES (1, 10, 1, 3, 6, 0, 4)')
    con.execute('INSERT INTO final_home VALUES (1, 10, 3, 1, 1, 6, 1, "3", 4)')
    con.execute('INSERT INTO aisle_batch_rollup VALUES (1, 0, 3, 1, 25, 6, 1, 1, 2, 1, 1.5, 1)')
    con.execute("INSERT OR REPLACE INTO cache_meta VALUES ('span_source', 'log')")
    con.commit(); con.close()
    return p


def test_cache_queries_golden(cache_db):
    roll = _rows(cache_db, 'viz_cache_db', 'aisle_rollup', run_id=1, batch_id=0)
    assert list(roll[0]) == list(CS.AISLE_ROLLUP_COLS)           # DDL order, all 12
    hist = _rows(cache_db, 'viz_cache_db', 'bin_history', run_id=1, aisle_id=3, bayX=1, bayY=1)
    assert hist == [{'t_from': 0, 't_to': 4, 'sku': 10, 'qty_at_from': 6}]
    assert _rows(cache_db, 'viz_cache_db', 'sku_rank_top', run_id=1, n=1)[0]['sku'] == 10
    fh = _rows(cache_db, 'viz_cache_db', 'final_home', run_id=1)
    assert fh[0]['home_aisles'] == '3'                           # comma string, parsed upstream
    assert _rows(cache_db, 'viz_cache_db', 'cache_meta_value', key='span_source') == [
        {'value': 'log'}]
    span = _rows(cache_db, 'viz_cache_db', 'bin_span_scoped', run_id=1, batch=2,
                 aisles=json.dumps([3]))
    assert len(span) == 1 and span[0]['t_from'] == 0


# ── the first production override ────────────────────────────────────────────────

@pytest.fixture()
def warehouse_pair(tmp_path):
    """(current, pre-fingerprint) warehouse fixtures — the old one surgically de-evolved and
    ASSERTED to hash to the vintage the override targets, so the fixture cannot drift."""
    cur = str(tmp_path / 'wh_current.db')
    WD.init_warehouse_db(cur)
    WD.save_warehouse_stats(cur, inventory_db='inv.db', n_skus=1, n_pallet=1, n_singleton=0,
                            total_aisles=1, total_bins=25, expected_fill=0.5, target_fill=0.5,
                            max_aisles=None, max_bins=None, avg_eq_qty=1.0, avg_rp=1.0,
                            aisle_rows=[], warehouse_fingerprint='fp123')
    WD.save_aisle_layout(cur, [{'aisle_id': 1, 'handling_type': 'h', 'category': 'c',
                                'unit_type': 'u', 'storage_size': 's',
                                'bay_x': 5, 'bay_y': 5}])

    old = str(tmp_path / 'wh_old.db')
    WD.init_warehouse_db(old)
    con = sqlite3.connect(old)
    con.execute('ALTER TABLE warehouse_stats DROP COLUMN warehouse_fingerprint')
    con.execute('DROP TABLE schema_meta')
    con.execute("INSERT INTO warehouse_stats (inventory_db, timestamp, n_skus,"
                " n_pallet_units, n_singleton_units, total_aisles, total_bins,"
                " expected_fill, target_fill) VALUES ('inv.db','t',1,1,0,1,25,0.5,0.5)")
    con.commit()
    from Schema.shape import observed_id
    sid = observed_id(con)
    con.close()
    assert sid == WD.PRE_FINGERPRINT_WAREHOUSE_SCHEMA_ID, (
        f'the de-evolved fixture hashes to {sid}, not the override target — the historical '
        f'delta is bigger than one column; rebuild the fixture from the committed shape doc')
    return cur, old


def test_warehouse_fingerprint_override_serves_the_old_vintage(warehouse_pair):
    cur, old = warehouse_pair
    assert _rows(cur, 'warehouse_db', 'warehouse_fingerprint') == [
        {'warehouse_fingerprint': 'fp123'}]
    # THE first override in production: same logical set, inline NULL, no consumer edit.
    assert _rows(old, 'warehouse_db', 'warehouse_fingerprint') == [
        {'warehouse_fingerprint': None}]
    geo = _rows(old, 'warehouse_db', 'aisle_layout_geometry')
    assert geo == []                                             # canonical query, old vintage


# ── EXPLAIN QUERY PLAN parity: json_each must not demote a SEARCH to a SCAN ──────

def _plan_kinds(con, sql, params):
    rows = con.execute('EXPLAIN QUERY PLAN ' + sql, params).fetchall()
    return {r[-1].split()[0] for r in rows if 'json_each' not in r[-1].lower()}


@pytest.mark.parametrize('family,name,base_table,params', [
    ('sim_db', 'bin_scores_scoped', 'bin_scores',
     {'run_id': 1, 'aisles': json.dumps([1, 2])}),
    ('sim_db', 'sku_series_live', 'picks',
     {'run_id': 1, 'skus': json.dumps([42])}),
    ('keyframes_db', 'keyframe_state_scoped', 'bin_keyframe',
     {'run_id': 1, 'batch_id': 0, 'aisles': json.dumps([2])}),
    ('viz_cache_db', 'bin_span_scoped', 'bin_span',
     {'run_id': 1, 'batch': 2, 'aisles': json.dumps([3])}),
])
def test_json_each_plan_parity(tmp_path, family, name, base_table, params, request):
    fixture = {'sim_db': 'sim_db', 'keyframes_db': 'kf_db',
               'viz_cache_db': 'cache_db'}[family]
    built = request.getfixturevalue(fixture)
    path = built[0] if isinstance(built, tuple) else built
    q = dataset._QUERIES[(family, name)]
    literal_sql = re.sub(r'\(:\w+ IS NULL OR (\w+) IN \(SELECT value FROM json_each\(:\w+\)\)\)',
                         r'\1 IN (1,2,3)', q.sql)
    literal_sql = re.sub(r'(\w+) IN \(SELECT value FROM json_each\(:\w+\)\)',
                         r'\1 IN (1,2,3)', literal_sql)
    literal_params = {k: v for k, v in params.items() if not k.startswith(('aisles', 'skus'))}
    con = sqlite3.connect(path)
    try:
        json_kinds = _plan_kinds(con, q.sql, params)
        lit_kinds = _plan_kinds(con, literal_sql, literal_params)
    finally:
        con.close()
    if 'SEARCH' in lit_kinds:
        assert 'SEARCH' in json_kinds, (
            f'{family}.{name}: the json_each form demoted the {base_table} access from '
            f'SEARCH to {json_kinds} — demote this site back to a raw fragment (see plan)')
