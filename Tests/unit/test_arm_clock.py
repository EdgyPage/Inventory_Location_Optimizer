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


def test_a_skipped_batch_still_does_not_advance_the_clock():
    """Half of the old contract, and it is unchanged: the skip guard fires before the sim,
    so an empty batch moves no clock. Deliberate — there is no principled duration for a
    batch that produced no tasks until a release schedule exists to say what a day-slot
    costs. The commit that ships the schedule is the one that reverses this."""
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr.run_strategy) if hasattr(sr, 'run_strategy') \
        else inspect.getsource(sr)
    skip = src.index('skipped += 1')
    advance = src.index('arm_clock             = bs.batch_start_time')
    assert skip < advance, 'the skip guard must precede the clock advance'


def _skip_mgr(n_records=3):
    """A manager holding undrained put records — the state a skipped batch is really in,
    because `check_reorders` runs ABOVE the skip guard."""
    import types

    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    from Warehouse.inventory.put_queue import single_queue
    from Warehouse.kernel.cost_model import SpeedProfile

    m = Inventory_Manager.__new__(Inventory_Manager)
    m._put_speed = m._put_cost = None
    m._put_size = 1
    m._put_clock = 0.0
    m._put_seconds = 0.0
    m._put_records = []
    m._put_queues = single_queue()
    m._held = []
    m._lead_queue = []
    m.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=1)
    unit = types.SimpleNamespace(
        quantity=2, unit_category='pallet',
        order=types.SimpleNamespace(sku=5, weight=10, volume=lambda: 100))
    bin_ = types.SimpleNamespace(x_phys=100.0, y_phys=48.0, location=(7, 1, 1))
    for _ in range(n_records):
        m._cost_putaway(unit, bin_, 'reorder')
    return m


def _close(mgr, arm_clock=1000.0, put_clock=0.0, put_workers=None):
    from Warehouse.kernel.cost_model import SpeedProfile
    from Warehouse.operations import Crew, Mode, Role

    from Optimization.simdriver.strategy_runner import close_skipped_batch
    crew = Crew(Role.PUT, Mode.FOOT, SpeedProfile(2.0, 4.0), size=1)
    workers = put_workers if put_workers is not None else crew.workers(0)
    return close_skipped_batch(
        batch_id=4, mgr=mgr, arm_clock=arm_clock, put_clock=put_clock, k_pickers=2,
        run_id=1, demanded=0, sigma_fd=0.0, reload_moves=0, reorder_placements=7,
        skus_reordered=3, units_ordered=11, put_workers=workers,
        put_crews={'all': workers}, shift_seconds=28800.0)


# `put_rows` returns positional tuples, not records. Named here so the assertions below
# read as claims about time and batch rather than about column 2.
_WE_BATCH, _WE_T_ABS, _WE_KIND = 0, 2, 9


def test_a_skipped_batch_writes_a_zero_duration_row_at_the_arm_clock():
    """So the absolute axis is recoverable downstream by a cumsum. The runner's comment used
    to say a skipped batch writes no row at all and call that the design; it was the leak."""
    bs, _rows, _pc = _close(_skip_mgr(0), arm_clock=1234.5)
    assert bs.batch_start_time == 1234.5
    assert bs.duration == 0.0 and bs.total_items == 0 and bs.num_tasks == 0
    # The bookkeeping a skipped batch still owes: reorders happened above the guard.
    assert (bs.reorder_placements, bs.skus_reordered, bs.units_ordered) == (7, 3, 11)


def test_a_skipped_batch_drains_its_put_records():
    """`check_reorders` runs above the skip guard and produces put records — measured at
    66-426 per batch from batch 1 on. The only drain call used to be BELOW the guard, so
    they carried into the next batch and were stamped against its epoch."""
    mgr = _skip_mgr(3)
    assert len(mgr._put_records) == 3, 'the fixture holds no records; nothing is tested'
    _bs, rows, put_clock = _close(mgr, arm_clock=1000.0)
    assert mgr._put_records == [], 'the records were not drained'
    assert len(rows) == 3, f'expected one work-event row per record, got {len(rows)}'
    assert put_clock > 1000.0, 'the put clock did not advance past the skipped batch'


def test_the_drained_records_are_stamped_against_THIS_batch():
    """The whole point. Left undrained they would carry into the next batch and be offset by
    the next batch's epoch, which is a silent misattribution of real work."""
    mgr = _skip_mgr(2)
    _bs, rows, _pc = _close(mgr, arm_clock=5000.0)
    assert rows and all(r[_WE_BATCH] == 4 for r in rows)
    assert all(r[_WE_T_ABS] >= 5000.0 for r in rows), (
        'a put row landed before the batch it belongs to')
    assert {r[_WE_KIND] for r in rows} == {'put'}


def test_the_put_crew_carries_across_a_skipped_batch():
    """A crew that overran the previous batch does not restart at this batch's release: it
    picks up whichever is later. Same rule the non-skipped path uses."""
    mgr = _skip_mgr(2)
    _bs, rows, _pc = _close(mgr, arm_clock=1000.0, put_clock=9000.0)
    assert all(r[_WE_T_ABS] >= 9000.0 for r in rows), (
        'the crew restarted at the batch epoch while it was still busy')


def test_a_run_without_put_timing_is_unaffected():
    mgr = _skip_mgr(0)
    bs, rows, pc = _close(mgr, put_workers=None)
    assert rows == [] and pc == 0.0 and bs.duration == 0.0


# ── a batch that did no work is UNMEASURED, not zero-throughput ───────────────────

def test_a_zero_duration_batch_reports_nan_throughput_not_zero():
    """A skipped batch now writes a row, which made this distinction reachable.

    `thr_batch` was `0.0` when there was no makespan to divide by. Zero is a measurement —
    it drags every throughput mean toward the floor in proportion to how many empty batches
    a run had. NaN is excluded from summaries instead, which is what the SAME function
    already does for `thr_task`, with the reason stated in its own comment. The two were
    inconsistent; nothing noticed because no batch could have a zero duration.
    """
    import numpy as np

    from Optimization.Performance_Evaluations.common.frames import _bdf

    class _S:
        def __init__(self, batch_id, duration, items):
            self.batch_id, self.duration, self.total_items = batch_id, duration, items
            self.batch_start_time = 0.0
            self.num_tasks = 0
            self.avg_concurrent_pickers = 0.0
            self.picking_pct = self.traveling_pct = 0.0
            self.sigma_fd = self.reload_moves = self.reorder_placements = 0

    df = _bdf([_S(0, 10.0, 100), _S(1, 0.0, 0), _S(2, 20.0, 400)])
    assert df['thr_batch'].tolist()[0] == 10.0
    assert np.isnan(df['thr_batch'].tolist()[1]), 'an empty batch reported a real zero'
    assert np.isnan(df['completion_rate'].tolist()[1])
    # And the point of it: the mean skips the unmeasured batch rather than being dragged.
    assert df['thr_batch'].mean() == 15.0, (
        f"the empty batch entered the mean: {df['thr_batch'].mean()}")
