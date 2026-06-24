"""test_visualization_data.py

Verifies the persistence added for DB-only reconstruction / visualization:
  - simulation_runs stores run params (num_pickers, speeds, keyframe_interval, …)
  - batch_stats round-trips batch_start_time / batch_end_time
  - warehouse.db aisle_layout round-trips (geometry: handling/category/unit/size + bay dims)
  - keyframe DB round-trips full bin snapshots; keyframe_db_path naming
  - the documented reconstruction SQL (keyframe qty − picks ≤ t) yields qty_at_t

Usage:
    cd Tests
    python test_visualization_data.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Picking_Data import (
    init_run_db, create_run, save_batch_stats, load_batch_stats, BatchStats,
    init_keyframe_db, save_bin_keyframe, keyframe_db_path,
    save_reorder_queue, load_reorder_queue,
)
from Warehouse_Data import init_warehouse_db, save_aisle_layout

_PASS = 0
_FAIL = 0


def check(label, ok, detail=''):
    global _PASS, _FAIL
    if ok:
        _PASS += 1; print(f'  PASS  {label}')
    else:
        _FAIL += 1; print(f'  FAIL  {label}' + (f'  ({detail})' if detail else ''))


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix='vizdata_'), name)


def test_run_params_roundtrip():
    print('\n-- simulation_runs stores run params --')
    db = _tmp('sim.db')
    init_run_db(db)
    params = dict(num_pickers=25, x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
                  pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0,
                  k_pickers=25, n_batches=100, seed_world=42, keyframe_interval=5)
    rid = create_run(db, 'uniform_assignment', params)
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM simulation_runs WHERE run_id=?', (rid,)).fetchone()
    con.close()
    check('num_pickers + keyframe_interval persisted',
          row['num_pickers'] == 25 and row['keyframe_interval'] == 5,
          f"{row['num_pickers']},{row['keyframe_interval']}")
    check('speeds + coefs persisted',
          abs(row['x_speed'] - 1.0) < 1e-9 and abs(row['pick_volume_coef'] - 1e-3) < 1e-12)
    check('create_run with no params still works (NULLs)',
          create_run(db, 'x') > 0)


def test_batch_stats_start_end_roundtrip():
    print('\n-- batch_stats batch_start_time / batch_end_time round-trip --')
    db = _tmp('sim.db')
    init_run_db(db)
    rid = create_run(db, 'uniform_assignment')
    rec = BatchStats(run_id=rid, batch_id=7, duration=120.5, num_tasks=3,
                     total_items=40, avg_concurrent_pickers=2.1,
                     picking_pct=0.6, traveling_pct=0.4,
                     batch_start_time=0.0, batch_end_time=120.5)
    save_batch_stats(db, rid, [rec])
    loaded = {b.batch_id: b for b in load_batch_stats(db, rid)}[7]
    check('batch_end_time round-trips', abs(loaded.batch_end_time - 120.5) < 1e-9,
          f'{loaded.batch_end_time}')
    check('batch_start_time round-trips', loaded.batch_start_time == 0.0)


def test_batch_stats_queue_roundtrip():
    print('\n-- batch_stats queue_depth / lead_queue_depth / in_transit_qty round-trip --')
    db = _tmp('sim_queue.db')
    init_run_db(db)
    rid = create_run(db, 'uniform_assignment')
    rec = BatchStats(run_id=rid, batch_id=3, duration=99.0, num_tasks=2,
                     total_items=20, avg_concurrent_pickers=1.5,
                     picking_pct=0.7, traveling_pct=0.3,
                     queue_depth=14395, lead_queue_depth=812, in_transit_qty=88000)
    save_batch_stats(db, rid, [rec])
    loaded = {b.batch_id: b for b in load_batch_stats(db, rid)}[3]
    check('queue_depth round-trips', loaded.queue_depth == 14395, f'{loaded.queue_depth}')
    check('lead_queue_depth round-trips', loaded.lead_queue_depth == 812, f'{loaded.lead_queue_depth}')
    check('in_transit_qty round-trips', loaded.in_transit_qty == 88000, f'{loaded.in_transit_qty}')


def test_reorder_queue_roundtrip():
    print('\n-- reorder_queue contents round-trip --')
    db = _tmp('sim_rq.db')
    init_run_db(db)
    rid = create_run(db, 'uniform_assignment')
    recs = [(5, 'lead', 101, 30, 2), (5, 'lead', 102, 12, 1), (5, 'stock', 101, 8, 0)]
    save_reorder_queue(db, rid, recs)
    got = {(r['kind'], r['sku']): r for r in load_reorder_queue(db, rid, 5)}
    check('3 queue rows at batch 5', len(got) == 3, f'{len(got)}')
    check('lead sku101 qty/lead', got[('lead', 101)]['qty'] == 30 and got[('lead', 101)]['remaining_lead'] == 2)
    check('stock sku101 qty', got[('stock', 101)]['qty'] == 8)
    check('other batch empty', load_reorder_queue(db, rid, 6) == [])
    # graceful: a DB without the table returns [] (older runs)
    db2 = _tmp('sim_no_rq.db')
    import sqlite3 as _sq
    _c = _sq.connect(db2); _c.execute('CREATE TABLE simulation_runs(run_id INTEGER)'); _c.commit(); _c.close()
    check('missing table -> []', load_reorder_queue(db2, 1, 0) == [])


def test_aisle_layout_roundtrip():
    print('\n-- warehouse.db aisle_layout (geometry) round-trip --')
    db = _tmp('warehouse.db')
    init_warehouse_db(db)
    rows = [
        dict(aisle_id=1, handling_type='conveyable', category='food',
             unit_type='pallet', storage_size='large', bay_x=50, bay_y=13),
        dict(aisle_id=2, handling_type='non-conveyable', category='chemical',
             unit_type='singleton', storage_size='singleton', bay_x=150, bay_y=10),
    ]
    save_aisle_layout(db, rows)
    save_aisle_layout(db, rows)   # rewrite-fresh must not duplicate
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    got = {r['aisle_id']: r for r in con.execute('SELECT * FROM aisle_layout')}
    n = con.execute('SELECT COUNT(*) FROM aisle_layout').fetchone()[0]
    con.close()
    check('rewrite-fresh keeps exactly 2 rows', n == 2, f'{n}')
    check('bay dims + unit_type persisted',
          got[1]['bay_x'] == 50 and got[1]['bay_y'] == 13 and got[2]['unit_type'] == 'singleton')
    check('handling/category persisted',
          got[2]['handling_type'] == 'non-conveyable' and got[1]['category'] == 'food')


def test_keyframe_db_roundtrip():
    print('\n-- keyframe DB round-trip + path naming --')
    run_db = _tmp('sim_A.db')
    kf = keyframe_db_path(run_db)
    check('keyframe path is sibling .keyframes.db', kf.endswith('sim_A.keyframes.db'), kf)
    init_keyframe_db(kf)
    recs = [dict(aisle_id=1, bayX=2, bayY=3, sku=10, unit_type='pallet',
                 storage_size='large', qty=16)]
    save_bin_keyframe(kf, run_id=1, batch_id=0, records=recs)
    save_bin_keyframe(kf, run_id=1, batch_id=5, records=recs)
    con = sqlite3.connect(kf)
    batches = sorted(r[0] for r in con.execute(
        'SELECT DISTINCT batch_id FROM bin_keyframe WHERE run_id=1'))
    qty = con.execute('SELECT qty FROM bin_keyframe WHERE run_id=1 AND batch_id=0 '
                      'AND aisle_id=1 AND bayX=2 AND bayY=3').fetchone()[0]
    con.close()
    check('keyframes recorded at batches {0,5}', batches == [0, 5], f'{batches}')
    check('keyframe qty round-trips', qty == 16, f'{qty}')


def test_reconstruction_query():
    print('\n-- reconstruction: qty_at_t = keyframe_qty - picks(<= t) --')
    # Put bin_keyframe + picker_events in one DB so we can run the join SQL.
    db = _tmp('sim_A.db')
    init_run_db(db)
    init_keyframe_db(db)
    con = sqlite3.connect(db)
    # bin starts batch B=5 with qty 10
    con.execute('INSERT INTO bin_keyframe VALUES (1,5,7,2,3,99,"pallet","large",10)')
    # three pick events in batch 5 on that bin: qty 2 at t=1,2,3
    for t in (1.0, 2.0, 3.0):
        con.execute(
            'INSERT INTO picker_events '
            '(run_id,batch_id,picker_id,time,event_type,aisle_id,bayX,bayY,sku,quantity,'
            ' bins_completed,total_bins,items_picked,total_items) '
            'VALUES (1,5,0,?,?,7,2,3,99,2,0,0,0,0)', (t, 'pick'))
    con.commit()

    def qty_at(t):
        row = con.execute("""
            WITH picks AS (
                SELECT aisle_id,bayX,bayY, SUM(quantity) AS picked
                FROM picker_events
                WHERE run_id=1 AND batch_id=5 AND event_type='pick' AND time <= ?
                GROUP BY aisle_id,bayX,bayY)
            SELECT MAX(0, k.qty - COALESCE(p.picked,0)) AS qty_at_t
            FROM bin_keyframe k LEFT JOIN picks p USING (aisle_id,bayX,bayY)
            WHERE k.run_id=1 AND k.batch_id=5
        """, (t,)).fetchone()[0]
        return row

    r0, r2, r9 = qty_at(0.0), qty_at(2.0), qty_at(9.0)
    con.close()
    check('t=0 -> full keyframe qty (10)', r0 == 10, f'{r0}')
    check('t=2 -> 10 - (2+2) = 6', r2 == 6, f'{r2}')
    check('t>=3 -> 10 - 6 = 4 (all picks applied)', r9 == 4, f'{r9}')


if __name__ == '__main__':
    print('\n' + '=' * 60)
    print('  Visualization reconstruction data-layer tests')
    print('=' * 60)
    test_run_params_roundtrip()
    test_batch_stats_start_end_roundtrip()
    test_aisle_layout_roundtrip()
    test_keyframe_db_roundtrip()
    test_reconstruction_query()
    print('\n' + '=' * 60)
    if _FAIL == 0:
        print(f'  All {_PASS} checks passed.')
    else:
        print(f'  {_PASS} passed  {_FAIL} FAILED')
    print('=' * 60 + '\n')
    sys.exit(0 if _FAIL == 0 else 1)
