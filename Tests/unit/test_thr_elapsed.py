"""test_thr_elapsed.py — throughput against the DAY, not against the makespan.

`thr_batch = total_items / duration` divides by the batch MAKESPAN: first picker starting to
last finishing. Under the continuous default that is also the whole elapsed time, because the
next batch is released the instant this one ends — so "how fast did the crew work" and "how
much did the day produce" have one answer and nothing ever had to tell them apart.

A paced schedule separates them. `ReleaseSchedule` releases batch i at its slot, and a crew
that finishes early WAITS. That gap is real elapsed time in which nothing was picked, and
`duration` does not contain it, so a paced run's `thr_batch` is the rate the crew worked AT
while being read as the rate the day DELIVERED.

`thr_elapsed` is the second number. Both are legitimate, and a scheduling change moves them
in OPPOSITE directions — fewer, fuller waves raise what a day produces while leaving the
working rate alone — which is precisely why this is an addition and never a replacement.

What is pinned:

  1. under the continuous default the two are EQUAL TO THE LAST BIT, so no published figure
     moves — asserted with `==` on floats, not a tolerance, because the construction is
     exact and a tolerance would hide a real drift;
  2. under a paced schedule with slack they diverge, and in the correct direction;
  3. a legacy DB (every epoch stamped 0.0, before the absolute clock) falls back to the
     makespan instead of emitting a negative or infinite rate;
  4. DB row order does not matter — the gap is measured between batch_ids, not between
     adjacent rows;
  5. the last batch has no successor and uses its own makespan.

Run:  python -m pytest Tests/unit/test_thr_elapsed.py -q
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from Optimization.Performance_Evaluations.common.frames import _bdf


def _row(batch_id, start, duration, items):
    """A BatchStats-shaped stand-in: `_bdf` reads attributes, never the type."""
    return SimpleNamespace(
        batch_id=batch_id, duration=duration, num_tasks=1, total_items=items,
        task_makespan=duration, avg_concurrent_pickers=1.0,
        picking_pct=0.5, traveling_pct=0.5, sigma_fd=0.0,
        reload_moves=0, reorder_placements=0, queue_depth=0,
        lead_queue_depth=0, in_transit_qty=0, is_outlier=0,
        batch_start_time=start)


def _continuous(durations, items):
    """Epochs laid end to end — the default, where a batch starts as the last one ends."""
    rows, t = [], 0.0
    for i, (d, n) in enumerate(zip(durations, items)):
        rows.append(_row(i, t, d, n))
        t += d
    return rows


def _paced(durations, items, slot):
    """Epochs on a fixed cadence — a crew that finishes early waits for its slot."""
    return [_row(i, i * slot, d, n)
            for i, (d, n) in enumerate(zip(durations, items))]


# ── 1. the default does not move ──────────────────────────────────────────────────

def test_a_continuous_schedule_makes_the_two_throughputs_identical():
    """The byte-identity guarantee. `==` on floats deliberately: the runner sets
    `arm_clock = batch_start_time + duration` and `release_at` returns it unchanged with no
    cadence, so consecutive epochs differ by EXACTLY the makespan. A tolerance here would
    pass while a real drift crept in."""
    df = _bdf(_continuous([100.0, 250.0, 175.5, 90.25], [400, 900, 610, 300]))
    assert list(df['elapsed']) == list(df['duration'])
    for got, want in zip(df['thr_elapsed'], df['thr_batch']):
        assert got == want


def test_the_metric_is_not_vacuous_on_the_default():
    """A column of NaN would satisfy the equality above by being equally absent."""
    df = _bdf(_continuous([100.0, 250.0], [400, 900]))
    assert df['thr_elapsed'].notna().all()
    assert (df['thr_elapsed'] > 0).all()


# ── 2. a paced schedule separates them ────────────────────────────────────────────

def test_slack_in_the_schedule_lowers_what_the_day_produced():
    """The correction this metric exists for: with a 400 s slot and a 100 s wave, the crew
    works a quarter of the day and `thr_batch` reports four times what the day delivered."""
    df = _bdf(_paced([100.0] * 4, [400] * 4, slot=400.0))
    # every batch but the last measures against its slot...
    assert list(df['elapsed'][:3]) == [400.0, 400.0, 400.0]
    assert list(df['thr_batch'][:3]) == [4.0, 4.0, 4.0]
    assert list(df['thr_elapsed'][:3]) == [1.0, 1.0, 1.0]
    # ...and the direction is the one that matters: the day produced LESS than the crew's
    # working rate suggests, never more.
    assert (df['thr_elapsed'] <= df['thr_batch']).all()


def test_a_schedule_the_crew_cannot_keep_up_with_has_no_slack():
    """`release_at` clamps to the instant the arm is free — the model has no picker
    contention, so a wave that overruns its slot simply starts late. There is then no idle
    gap and the two metrics converge again, which is the correct answer rather than a
    negative one."""
    df = _bdf(_continuous([500.0, 500.0, 500.0], [400, 400, 400]))   # overrunning a 400 s slot
    for got, want in zip(df['thr_elapsed'], df['thr_batch']):
        assert got == want


def test_fewer_fuller_waves_raise_the_day_and_leave_the_working_rate_alone():
    """Why this is an addition and not a replacement: a scheduling change moves the two
    metrics differently, so neither can stand in for the other.

    Two schedules move the same 1,600 items at the same working rate of 4 items/s. The
    four-wave version wastes three quarters of each slot; the one-wave version wastes
    nothing. `thr_batch` cannot tell them apart — that is the point.
    """
    slack = _bdf(_paced([100.0] * 4, [400] * 4, slot=400.0))
    full = _bdf(_paced([400.0], [1600], slot=400.0))
    assert slack['thr_batch'].iloc[0] == full['thr_batch'].iloc[0] == 4.0
    assert full['thr_elapsed'].iloc[0] > slack['thr_elapsed'].iloc[0]


# ── 3. legacy and edge shapes ─────────────────────────────────────────────────────

def test_a_legacy_db_with_no_absolute_clock_falls_back_to_the_makespan():
    """Every `batch_start_time` was stamped 0.0 before the absolute clock landed, so the
    gaps are all zero and a naive division would emit inf. Such a run predates the axis this
    metric measures, and its two throughputs genuinely ARE one number."""
    rows = [_row(i, 0.0, 100.0, 400) for i in range(3)]
    df = _bdf(rows)
    assert list(df['elapsed']) == [100.0, 100.0, 100.0]
    for got, want in zip(df['thr_elapsed'], df['thr_batch']):
        assert got == want


def test_a_non_monotonic_epoch_does_not_produce_a_negative_rate():
    """Defensive, and cheap: a rate below zero is not a number any figure can carry, and it
    would propagate silently through a mean."""
    rows = [_row(0, 500.0, 100.0, 400), _row(1, 100.0, 100.0, 400)]
    df = _bdf(rows)
    assert (df['elapsed'] > 0).all()
    assert (df['thr_elapsed'] > 0).all()


def test_the_last_batch_measures_against_its_own_makespan():
    """Nothing follows the final wave, so there is no gap after it to attribute — the day
    does not keep costing time once the work is done."""
    df = _bdf(_paced([100.0, 100.0], [400, 400], slot=400.0))
    assert df['elapsed'].iloc[-1] == 100.0
    assert df['thr_elapsed'].iloc[-1] == df['thr_batch'].iloc[-1]


def test_row_order_does_not_change_the_answer():
    """The gap is between batch_ids, not between adjacent ROWS. A DB read that returns them
    unsorted would otherwise produce a different metric for the same run."""
    rows = _paced([100.0, 150.0, 120.0], [400, 600, 500], slot=400.0)
    forward = _bdf(list(rows))
    shuffled = _bdf([rows[2], rows[0], rows[1]])
    by_id = {int(b): e for b, e in zip(shuffled['batch_id'], shuffled['elapsed'])}
    assert by_id == {int(b): e for b, e in zip(forward['batch_id'], forward['elapsed'])}


def test_a_zero_duration_batch_is_unmeasured_rather_than_zero():
    """A skipped batch writes a zero-duration row. Counting it as zero throughput drags
    every mean toward zero — the same rule `thr_batch` and `thr_task` already apply."""
    rows = [_row(0, 0.0, 0.0, 0), _row(1, 0.0, 100.0, 400)]
    df = _bdf(rows)
    assert math.isnan(df['thr_elapsed'].iloc[0])
    assert math.isnan(df['thr_batch'].iloc[0])


def test_an_empty_frame_still_carries_the_columns():
    """A consumer that selects `thr_elapsed` must not KeyError on a run with no batches."""
    df = _bdf([])
    assert 'elapsed' in df.columns and 'thr_elapsed' in df.columns


# ── 4. it is a declared quantity, not an ad-hoc column ────────────────────────────

def test_the_metric_is_registered_and_reads_only_the_guaranteed_surface():
    """An ad-hoc frame column is invisible to the era gate and to the figure registry. This
    one is a `Quantity`, so both see it — including the check that `batch_start_time` is in
    every vetted vintage's guaranteed surface, which it is."""
    import Optimization.persistence.Picking_Data                    # registers the family
    from Schema import compat
    from Optimization.Performance_Evaluations.core import quantities as Q

    q = Q.BY_KEY['throughput_elapsed']
    assert q.source.per_batch == ('batch', 'thr_elapsed')
    table, cols = q.source.db_reads
    surface = compat.guaranteed_surface('sim_db')[table]
    for col in cols:
        assert col in surface, f'{col} is not in every vetted vintage'
    assert 'batch_start_time' in cols, (
        'the declaration does not name the column the gap is measured from, so the era '
        'gate would validate a read this metric does not make')
