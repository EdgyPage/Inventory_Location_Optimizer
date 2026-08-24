"""test_batch_stat_invariance.py — a batch statistic measures a SPAN, not a timestamp.

Every picker's clock is reborn at `0.0` at the start of every batch, so for as long as
that held, "the last done event's time" and "how long the batch took" were the same
number.  They are not the same thing, and three statistics were reading the timestamp:

  * `extract_batch_stats`   — `duration` (and `thr_batch` through it) and `task_makespan`
    (and `thr_task` through it)
  * `_picker_time_breakdown_grouped` — `picking_pct` / `traveling_pct`
  * `avg_concurrent_pickers` — anchored both `prev_t` and its divisor at a literal `0.0`

A shared clock removes the assumption: a picker carries its time across batches, and an
inbound stream starts wherever it starts.  These tests pin the property that makes that
safe — **shifting every event by a constant changes nothing** — and pin it against the
same event stream that produced today's numbers, so the fix is byte-identical rather than
merely defensible.

Run:  python -m pytest Tests/unit/test_batch_stat_invariance.py -q
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from Optimization.metrics.Simulation_Analytics import (
    _group_events_by_picker,
    _picker_time_breakdown_grouped,
    avg_concurrent_pickers,
    extract_batch_stats,
)
from Warehouse.picking.Pick import PickEvent


# ── a two-picker batch, hand-built so the expected numbers are readable ──────────

def _one_picker(pid: int, t0: float, aisles: list[tuple[int, float, float, float]]):
    """Emit one picker's event stream starting at `t0`.

    Each aisle is `(aisle_id, travel_in, handle, travel_out)` — the shape the sim
    produces: `task_start` at the clock's current instant, `arrive` after travel,
    `pick` after handling, `task_end` after the exit.
    """
    evs, t, picked = [], t0, 0
    evs.append(PickEvent(time=t, picker_id=pid, event_type='task_start',
                         aisle_id=aisles[0][0], total_bins=len(aisles)))
    for i, (aid, tin, handle, tout) in enumerate(aisles):
        if i:
            evs.append(PickEvent(time=t, picker_id=pid, event_type='task_start', aisle_id=aid))
        t += tin
        evs.append(PickEvent(time=t, picker_id=pid, event_type='arrive', aisle_id=aid))
        t += handle
        picked += 1
        evs.append(PickEvent(time=t, picker_id=pid, event_type='pick', aisle_id=aid,
                             quantity=1, items_picked=picked))
        t += tout
        evs.append(PickEvent(time=t, picker_id=pid, event_type='task_end', aisle_id=aid))
    evs.append(PickEvent(time=t, picker_id=pid, event_type='done', items_picked=picked))
    return evs


def _batch(t0_a: float = 0.0, t0_b: float = 0.0) -> list[PickEvent]:
    """Two pickers, deliberately unequal, so makespan != labor and the split is not 50/50."""
    return (_one_picker(0, t0_a, [(1, 2.0, 3.0, 1.0), (2, 4.0, 5.0, 1.0)])
            + _one_picker(1, t0_b, [(3, 1.0, 2.0, 1.0)]))


K = 2


# ── the invariance property ──────────────────────────────────────────────────────

@pytest.mark.parametrize('shift', [1.0, 3600.0, 1e6])
def test_batch_stats_are_unchanged_by_a_constant_shift(shift):
    """The whole point: an absolute clock origin must not move a single statistic."""
    base    = _batch()
    shifted = [replace(e, time=e.time + shift) for e in base]

    a = extract_batch_stats(base,    batch_id=0, k_pickers=K)
    b = extract_batch_stats(shifted, batch_id=0, k_pickers=K)

    for field in ('duration', 'task_makespan', 'num_tasks', 'total_items',
                  'thr_batch', 'thr_task', 'avg_concurrent_pickers',
                  'picking_pct', 'traveling_pct'):
        assert getattr(a, field) == pytest.approx(getattr(b, field)), field


@pytest.mark.parametrize('shift', [1.0, 3600.0, 1e6])
def test_the_start_and_end_stamps_are_the_only_things_that_move(shift):
    """`batch_start_time`/`batch_end_time` are timestamps and SHOULD track the origin —
    they are how a consumer recovers the epoch."""
    base    = _batch()
    shifted = [replace(e, time=e.time + shift) for e in base]

    a = extract_batch_stats(base,    batch_id=0, k_pickers=K)
    b = extract_batch_stats(shifted, batch_id=0, k_pickers=K)

    assert b.batch_start_time == pytest.approx(a.batch_start_time + shift)
    assert b.batch_end_time   == pytest.approx(a.batch_end_time   + shift)
    # ...and the span between them is what `duration` is measured against.
    assert b.batch_end_time - b.batch_start_time == pytest.approx(
        a.batch_end_time - a.batch_start_time)


def test_pickers_starting_at_different_instants_score_their_own_spans():
    """The case that only exists once clocks persist: picker 1 starts late.  Labor is the
    sum of the two spans and must not absorb picker 1's head start as work."""
    together = extract_batch_stats(_batch(0.0, 0.0),   batch_id=0, k_pickers=K)
    staggered = extract_batch_stats(_batch(0.0, 50.0), batch_id=0, k_pickers=K)

    # Labor is unchanged — the same two pickers did the same two spans of work.
    assert staggered.task_makespan == pytest.approx(together.task_makespan)
    # Makespan grows by exactly the stagger, because picker 1 now finishes later than
    # picker 0 (its 4.0 span pushed out by 50 exceeds picker 0's 16.0).
    assert staggered.duration == pytest.approx(54.0)
    assert together.duration  == pytest.approx(16.0)


# ── the three statistics, pinned individually ────────────────────────────────────

@pytest.mark.parametrize('shift', [0.0, 7.5, 1e6])
def test_the_picking_split_is_unchanged_by_a_shift(shift):
    grouped = _group_events_by_picker(
        [replace(e, time=e.time + shift) for e in _batch()], K)
    out = _picker_time_breakdown_grouped(grouped)
    # picking = 3+5+2 = 10; spans = 16 + 4 = 20.
    assert out['picking_pct']   == pytest.approx(0.5)
    assert out['traveling_pct'] == pytest.approx(0.5)


@pytest.mark.parametrize('shift', [0.0, 7.5, 1e6])
def test_concurrency_is_unchanged_by_a_shift(shift):
    base  = avg_concurrent_pickers(_batch())
    moved = avg_concurrent_pickers(
        [replace(e, time=e.time + shift) for e in _batch()])
    assert moved == pytest.approx(base)
    assert 0.0 < base <= K


def test_a_picker_that_drew_no_tasks_contributes_no_span():
    """Its only event is `done` at its own clock start, so the span is zero — the guard
    that used to be `done_t <= 0.0` and only worked because that clock start was zero."""
    evs = _one_picker(0, 0.0, [(1, 2.0, 3.0, 1.0)]) + [
        PickEvent(time=900.0, picker_id=1, event_type='done', items_picked=0)]
    bs = extract_batch_stats(evs, batch_id=0, k_pickers=K)
    assert bs.task_makespan == pytest.approx(6.0)   # picker 0 only
    # ...but the idle picker still sets the batch's end, because it clocked out last.
    assert bs.duration == pytest.approx(900.0)


def test_an_empty_batch_is_still_all_zeros():
    bs = extract_batch_stats([], batch_id=0, k_pickers=K)
    assert (bs.duration, bs.task_makespan, bs.total_items, bs.num_tasks) == (0.0, 0.0, 0, 0)
    assert bs.batch_start_time == 0.0
    assert avg_concurrent_pickers([]) == 0.0
