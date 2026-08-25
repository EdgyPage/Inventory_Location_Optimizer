"""test_task_realized.py — a task reports what it DID, not only what it was planned to do.

`TaskStats.total_items` is `sum(task.items.values())` and `num_bins_visited` is
`len(task.path)`. Both are the PLAN. Neither is what happened, and they diverge already —
both picker loops clamp every pick to what the bin actually holds
(`min(task.planned[i], bin_.storage.quantity)`), so any task whose stock moved between task
creation and the sim over-reports.

A day cut makes that badly wrong rather than slightly: a task stopped a third of the way
through would report its full planned workload against a third of the elapsed time and read
as three times as efficient as it was.

`items_realized` and `bins_realized` come off the EVENT STREAM, because the task object only
ever knows the plan.

Run:  python -m pytest Tests/unit/test_task_realized.py -q
"""
from __future__ import annotations

import types

import pytest

from Optimization.metrics.Simulation_Analytics import extract_task_stats
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.picking.Workload_Builder import Task
from Warehouse.picking.fast_pick import DeferredPickSimulation


def _order(sku, vol=100):
    return types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol)


def _bin(aisle, x, sku, qty):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=0.0, location=(aisle, x, 0),
        storage=types.SimpleNamespace(order=_order(sku), quantity=qty))


def _cfg(pickers=1):
    return PickConfig(num_pickers=pickers, x_speed=4.0, y_speed=2.0, pick_intercept=5.0,
                      pick_weight_coef=0.2, pick_volume_coef=0.001, cart_swap_coef=0.0,
                      cart=FulfillmentCart)


def _wp():
    return WorkloadParams.from_pick_config(_cfg())


class _NoAffinity:
    def sum_lift(self, skus):
        return 0.0


def _stats(tasks, events):
    return extract_task_stats(events, tasks, 0, _NoAffinity(), _wp(), run_id=1)


def test_a_fully_stocked_task_realizes_its_plan():
    """The baseline: when nothing clamps, realized equals planned. Without this the tests
    below could pass on a realized number that is simply always low."""
    tasks = [Task(1, [_bin(1, 50, 1, 10), _bin(1, 150, 2, 10)], {1: 3, 2: 4})]
    st, = _stats(tasks, PickSimulation(tasks, _cfg()).run())
    assert st.total_items == 7 and st.items_realized == 7
    assert st.num_bins_visited == 2 and st.bins_realized == 2


def test_a_short_bin_makes_realized_fall_below_planned():
    """The pre-existing case, live today and reported as the plan until now: the loops clamp
    each pick to what the bin holds, so a task whose stock moved picks less than it planned."""
    tasks = [Task(1, [_bin(1, 50, 1, 1), _bin(1, 150, 2, 10)], {1: 6, 2: 4})]
    st, = _stats(tasks, PickSimulation(tasks, _cfg()).run())
    assert st.total_items == 10, 'the plan should still be reported as the plan'
    assert st.items_realized == 5, f'expected 1 + 4 picked, got {st.items_realized}'
    assert st.items_realized < st.total_items


def test_an_empty_bin_is_planned_but_not_realized():
    """`num_bins_visited` counts the path; `bins_realized` counts bins actually picked
    from. A bin that emptied between task creation and the sim is on the path and is not
    picked."""
    b = _bin(1, 50, 1, 5)
    b.storage = None                      # emptied after the task was built
    tasks = [Task(1, [b, _bin(1, 150, 2, 10)], {1: 3, 2: 4})]
    st, = _stats(tasks, PickSimulation(tasks, _cfg()).run())
    assert st.num_bins_visited == 2 and st.bins_realized == 1
    assert st.items_realized == 4


def test_realized_items_are_per_task_not_per_session():
    """`items_picked` on an event is the picker's SESSION total, so a second task on the same
    picker would inherit the first task's items if the difference were not taken."""
    tasks = [Task(1, [_bin(1, 50, 1, 10)], {1: 3}),
             Task(2, [_bin(2, 50, 2, 10)], {2: 5})]
    stats = {s.aisle_id: s for s in _stats(tasks, PickSimulation(tasks, _cfg()).run())}
    assert stats[1].items_realized == 3
    assert stats[2].items_realized == 5, (
        f'the second task inherited the first: {stats[2].items_realized}')


def test_both_loops_report_the_same_realized_numbers():
    """The production loop clamps against a snapshot rather than live stock, so its realized
    count is computed by a different route and has to agree."""
    def mk():
        return [Task(1, [_bin(1, 50, 1, 1), _bin(1, 150, 2, 10)], {1: 6, 2: 4}),
                Task(2, [_bin(2, 80, 3, 10)], {3: 2})]
    a = {s.aisle_id: (s.items_realized, s.bins_realized)
         for s in _stats(mk(), PickSimulation(mk(), _cfg()).run())}
    b = {s.aisle_id: (s.items_realized, s.bins_realized)
         for s in _stats(mk(), DeferredPickSimulation(mk(), _cfg()).run())}
    assert a == b and a


def test_realized_never_exceeds_planned():
    """The invariant that makes the pair meaningful. A task cannot pick more than it asked
    for — the over-pick that made this false was fixed by `Task.planned`, and this is the
    per-task statement of it."""
    for qty in (1, 3, 7, 20):
        tasks = [Task(1, [_bin(1, 50, 1, qty), _bin(1, 150, 1, qty)], {1: 9})]
        st, = _stats(tasks, PickSimulation(tasks, _cfg()).run())
        assert st.items_realized <= st.total_items, (qty, st.items_realized, st.total_items)
        assert st.bins_realized <= st.num_bins_visited


def test_the_columns_round_trip(tmp_path):
    from Optimization.persistence.Picking_Data import (
        TaskStats, create_run, init_run_db, save_task_stats,
    )
    import sqlite3
    path = str(tmp_path / 'sim_ts.db')
    init_run_db(path)
    rid = create_run(path, 'test')
    r = TaskStats(run_id=rid, batch_id=0, aisle_id=4, picker_id=1, task_start_time=0.0,
                  task_end_time=10.0, duration=10.0, W=1.0, lift_sum=0.0,
                  num_bins_visited=9, total_items=40)
    r.items_realized, r.bins_realized = 13, 3
    save_task_stats(path, rid, [r])
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        got = con.execute('SELECT total_items, items_realized, num_bins_visited, '
                          'bins_realized FROM task_stats WHERE run_id=?', (rid,)).fetchall()
    finally:
        con.close()
    assert got == [(40, 13, 9, 3)]
