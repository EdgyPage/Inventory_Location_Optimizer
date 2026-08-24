"""test_arm_clock.py — an arm's batches sit on one absolute axis.

A batch's picker clocks were reborn at `0.0`, so `batch_start_time` was always 0 and `t`
alone could not order two events from different batches. The arm now carries a clock: every
picker starts a batch at the arm's current instant, and the instant advances by that batch's
makespan.

**Uniform across the crew, deliberately.** Batches are sequential waves — batch *i+1*'s work
is released when batch *i* completes — so the whole crew starts together and the axis is the
running sum of the makespans, which is exactly `timeline.epochs`.

A per-picker carry (whoever finishes early starts the next wave early) is a different model:
it removes the barrier, and it has a failure mode this was measured hitting. A picker who
draws no task keeps its clock frozen at 0 while the others advance, so `batch_start_time`
sticks at 0 forever and `duration` silently becomes an absolute END time — on a four-batch
run, batch 3's "duration" read 6504 s against a true makespan of 2262 s. The `start_times`
seam supports per-picker carry when someone wants it; this is not that.

Because the offset is uniform, every batch statistic is a span measured from it and is
UNCHANGED — which is precisely what the offset-invariance work in `extract_batch_stats`
bought, and it is asserted end to end below.

Run:  python -m pytest Tests/unit/test_arm_clock.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.metrics.Simulation_Analytics import extract_batch_stats
from Warehouse.kernel.timeline import epochs
from Warehouse.picking.Pick import PickEvent


def _batch(t0: float, spans: list[float], k: int = 2):
    """One batch of `k` pickers, each starting at `t0` and working `spans[p]` seconds."""
    evs = []
    for pid, span in enumerate(spans):
        evs.append(PickEvent(time=t0, picker_id=pid, event_type='task_start', aisle_id=pid + 1))
        evs.append(PickEvent(time=t0 + span * 0.5, picker_id=pid, event_type='arrive',
                             aisle_id=pid + 1))
        evs.append(PickEvent(time=t0 + span * 0.9, picker_id=pid, event_type='pick',
                             aisle_id=pid + 1, quantity=1, items_picked=1))
        evs.append(PickEvent(time=t0 + span, picker_id=pid, event_type='task_end',
                             aisle_id=pid + 1))
        evs.append(PickEvent(time=t0 + span, picker_id=pid, event_type='done', items_picked=1))
    return evs


SPANS = [[10.0, 6.0], [4.0, 9.0], [7.0, 7.0]]


# ── the axis is contiguous and is the running sum of the makespans ────────────────

def test_each_batch_starts_where_the_previous_ended():
    clock, seen = 0.0, []
    for spans in SPANS:
        bs = extract_batch_stats(_batch(clock, spans), batch_id=len(seen), k_pickers=2)
        seen.append(bs)
        assert bs.batch_start_time == pytest.approx(clock)
        clock = bs.batch_start_time + bs.duration
    for prev, nxt in zip(seen, seen[1:]):
        assert nxt.batch_start_time == pytest.approx(prev.batch_end_time)


def test_the_axis_matches_the_named_epoch_definition():
    """`timeline.epochs` is the definition a what-if already consumes; the runner must not
    invent a second one that drifts from it."""
    clock, durations, starts = 0.0, [], []
    for spans in SPANS:
        bs = extract_batch_stats(_batch(clock, spans), batch_id=0, k_pickers=2)
        starts.append(bs.batch_start_time)
        durations.append(bs.duration)
        clock = bs.batch_start_time + bs.duration
    assert starts == pytest.approx(epochs(durations))


# ── and every statistic is unchanged by the offset ────────────────────────────────

@pytest.mark.parametrize('field', ['duration', 'task_makespan', 'total_items', 'num_tasks',
                                   'thr_batch', 'thr_task', 'avg_concurrent_pickers',
                                   'picking_pct', 'traveling_pct'])
def test_a_batch_scores_the_same_wherever_it_sits_on_the_axis(field):
    at_zero = extract_batch_stats(_batch(0.0, SPANS[0]), batch_id=1, k_pickers=2)
    later = extract_batch_stats(_batch(9_999.0, SPANS[0]), batch_id=1, k_pickers=2)
    assert getattr(at_zero, field) == pytest.approx(getattr(later, field))


def test_the_makespan_is_the_slowest_picker_not_the_absolute_end():
    """The bug a per-picker carry produced: `duration` became an absolute END time."""
    bs = extract_batch_stats(_batch(5_000.0, [10.0, 6.0]), batch_id=0, k_pickers=2)
    assert bs.duration == pytest.approx(10.0)
    assert bs.batch_end_time == pytest.approx(5_010.0)


# ── the runner does it this way, and says why ─────────────────────────────────────

def test_the_runner_carries_one_uniform_clock():
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    assert 'start_times=[arm_clock] * k_pickers' in src, (
        'the arm no longer hands every picker the same batch epoch')
    assert 'arm_clock             = bs.batch_start_time + bs.duration' in src


def test_a_skipped_batch_cannot_advance_the_clock():
    """`if not tasks: skipped += 1; continue` fires BEFORE the sim, so an empty batch
    writes no batch_stats row and moves no clock. That is also why a downstream cumsum over
    rows cannot reconstruct the epoch, and why the worker writes it as a column."""
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    skip = src.index('skipped += 1')
    advance = src.index('arm_clock             = bs.batch_start_time')
    assert skip < advance, 'the skip guard must precede the clock advance'
    assert 'continue' in src[skip:skip + 60]
