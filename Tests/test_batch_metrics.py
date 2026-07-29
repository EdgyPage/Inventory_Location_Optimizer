"""batch_stats success metrics — the three new columns (task_makespan, thr_task, thr_batch) and the
invariants they rest on.

  * task_makespan (Σ per-picker done-times) EQUALS Σ task_stats.duration — the single-source guard
    that the new batch column and the task-join `ss_prod_hours` can never diverge.
  * thr_batch == items / batch makespan (duration); thr_task == items / task makespan; both guard 0.
  * batch makespan (parallel wall-clock) never exceeds task makespan (the serial total).
  * load_batch_stats stays back-compatible on a legacy DB missing the three columns (defaults, no
    crash), and frames._bdf then yields thr_batch from duration and thr_task = NaN (excluded, not 0).
"""
import math
import sqlite3
import types

from Warehouse.Pick import PickConfig, PickSimulation
from Warehouse.Storage_Primitive import FulfillmentCart
from Optimization.metrics.Simulation_Analytics import (extract_batch_stats, extract_task_stats,
                                              flag_batch_outliers, WorkloadParams)
from Optimization.persistence.Picking_Data import BatchStats, load_batch_stats
from Optimization.Performance_Evaluations.common.frames import _bdf


def _order(sku, vol, wt=5):
    return types.SimpleNamespace(sku=sku, weight=wt, volume=(lambda v=vol: v))


def _bin(aid, x, y, o, qty):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(aid, x, y),
        storage=types.SimpleNamespace(order=o, quantity=qty),
        aisle=types.SimpleNamespace(aisle_width=2400.0))


def _task(aid, npicks, vol=15000, qty=3):
    path, items = [], {}
    for i in range(npicks):
        sku = aid * 100 + i
        path.append(_bin(aid, 100 + i * 40, 30, _order(sku, vol), qty))
        items[sku] = qty
    # x_traversed/y_traversed/carts_required feed the analytical W in extract_task_stats; they are
    # irrelevant to the per-task DURATION this test compares, but must exist so the call doesn't raise.
    return types.SimpleNamespace(aisle_id=aid, path=path, items=items,
                                 x_traversed=0.0, y_traversed=0.0, carts_required=1)


def _tasks(heavy):
    """15 aisles; the given aisles are HEAVY (20 picks), the rest light (3 picks)."""
    return [_task(aid, 20 if aid in heavy else 3) for aid in range(1, 16)]


def _cfg(scheduler='lpt', npickers=3):
    return PickConfig(num_pickers=npickers, cart=FulfillmentCart, cart_swap_coef=240,
                      scheduler=scheduler)


_AFF = types.SimpleNamespace(sum_lift=lambda skus: 0.0)   # lift is irrelevant to duration


def test_task_makespan_equals_sum_task_durations():
    """The single-source invariant: task_makespan (Σ per-picker done-times) == Σ task_stats.duration,
    so the new batch column and the task-join `ss_prod_hours` can never disagree.  Holds for both
    schedulers (the partition changes the split, never the total task time)."""
    for scheduler in ('round_robin', 'lpt'):
        tasks  = _tasks({1, 4, 7})
        cfg    = _cfg(scheduler)
        events = PickSimulation(tasks, cfg).run()
        bs = extract_batch_stats(events, batch_id=0, k_pickers=cfg.num_pickers)
        ts = extract_task_stats(events, tasks, batch_id=0, affinity=_AFF, wp=WorkloadParams())
        assert abs(bs.task_makespan - sum(t.duration for t in ts)) < 1e-6, scheduler


def test_throughput_definitions():
    bs = extract_batch_stats(PickSimulation(_tasks({1, 4, 7}), _cfg()).run(),
                             batch_id=0, k_pickers=3)
    assert bs.duration > 0 and bs.task_makespan > 0 and bs.total_items > 0
    assert abs(bs.thr_batch - bs.total_items / bs.duration) < 1e-12
    assert abs(bs.thr_task - bs.total_items / bs.task_makespan) < 1e-12
    # batch makespan (parallel wall-clock) <= task makespan (serial total) whenever >1 picker is busy
    assert bs.duration <= bs.task_makespan + 1e-9


def test_zero_guards():
    """Empty batch (no events) → zero metrics, no ZeroDivisionError."""
    bs = extract_batch_stats([], batch_id=0, k_pickers=3)
    assert bs.duration == 0 and bs.task_makespan == 0
    assert bs.thr_batch == 0.0 and bs.thr_task == 0.0


def test_legacy_db_without_columns_loads(tmp_path):
    """A batch_stats table predating the 3 new columns still loads (defaults 0.0), no crash —
    mirrors the sigma_fd/sigma_fw legacy-read path in load_batch_stats."""
    db = str(tmp_path / 'legacy.db')
    con = sqlite3.connect(db)
    con.executescript(
        'CREATE TABLE simulation_runs(run_id INTEGER PRIMARY KEY);'
        'INSERT INTO simulation_runs(run_id) VALUES (1);'
        'CREATE TABLE batch_stats('
        ' id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INT, batch_id INT, duration REAL,'
        ' num_tasks INT, total_items INT, avg_concurrent_pickers REAL, picking_pct REAL,'
        ' traveling_pct REAL, is_outlier INT);'
        'INSERT INTO batch_stats(run_id,batch_id,duration,num_tasks,total_items,'
        ' avg_concurrent_pickers,picking_pct,traveling_pct,is_outlier)'
        ' VALUES (1,0,100.0,3,50,1.5,0.6,0.4,0);')
    con.commit()
    con.close()
    rows = load_batch_stats(db, 1)
    assert len(rows) == 1
    b = rows[0]
    assert b.task_makespan == 0.0 and b.thr_task == 0.0 and b.thr_batch == 0.0
    assert b.duration == 100.0 and b.total_items == 50


def test_flag_batch_outliers_preserves_new_metrics():
    """Outlier flagging must carry the new columns through (it uses dataclasses.replace, not a
    field-by-field rebuild) — otherwise a notebook that flags then re-frames zeroes every metric."""
    b = BatchStats(run_id=1, batch_id=0, duration=100.0, num_tasks=3, total_items=50,
                   avg_concurrent_pickers=1.5, picking_pct=0.6, traveling_pct=0.4,
                   task_makespan=180.0, thr_task=50 / 180.0, thr_batch=0.5,
                   queue_depth=7, in_transit_qty=42)
    out = flag_batch_outliers([b, b])[0]
    assert out.task_makespan == 180.0 and out.thr_batch == 0.5
    assert abs(out.thr_task - 50 / 180.0) < 1e-12
    assert out.queue_depth == 7 and out.in_transit_qty == 42     # the pre-existing drop is fixed too


def test_bdf_thr_task_nan_when_task_makespan_absent():
    """frames._bdf yields thr_batch from duration but thr_task = NaN (not 0) when task_makespan is
    unavailable, so legacy rows are excluded from thr_task stats rather than counted as zero."""
    b = BatchStats(run_id=1, batch_id=0, duration=100.0, num_tasks=3, total_items=50,
                   avg_concurrent_pickers=1.5, picking_pct=0.6, traveling_pct=0.4)  # task_makespan=0
    df = _bdf([b])
    assert abs(float(df['thr_batch'][0]) - 0.5) < 1e-12
    assert math.isnan(float(df['thr_task'][0]))
