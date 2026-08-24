"""test_work_events.py — two streams, one axis, and the reconciliation that keeps them there.

`picker_events` is batch-relative, pick-only, and carries nine pick-specific
`NOT NULL DEFAULT 0` columns; a put-away row there would be nine zeros with no way to tell
"this actor carries no cart" from "cart_move was 0.0". So the merged timeline is its own
table, and `picker_events` is untouched — every existing analysis, figure and viewer route
keeps working.

These are IN-MEMORY checks on the row builders: given events and put records, do they
produce the right tuples. They cannot catch a writer that drops, doubles or misorders rows
on the way to disk, so they are not the reconciliation — an earlier version of this
docstring called one of them "THE query", which it is not.

The reconciliation runs SQL against a real database and lives in
`Tests/integration/test_work_events_reconciliation.py`.

Run:  python -m pytest Tests/unit/test_work_events.py -q
"""
from __future__ import annotations

import pytest

from Optimization.metrics.work_events import merged, pick_rows, put_rows
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS
from Warehouse.operations import Crew, Mode, Role
from Warehouse.picking.Pick import PickEvent

# Column offsets into a row tuple — Picking_Data._WORK_EVENT_COLS, minus run_id.
BATCH, SEQ, T_ABS, T_LOCAL, SHIFT, UID, LOCAL, ROLE, MODE, TYPE, AISLE, SKU, QTY, DUR, SRC = range(15)

PICKERS = Crew(Role.PICK, Mode.MACHINE, SpeedProfile(3.0, 2.0), size=2)
PUTTERS = Crew(Role.PUT, Mode.FOOT, SpeedProfile(2.0, 4.0), size=1)
PW = PICKERS.workers(0)
TW = PUTTERS.workers(PICKERS.next_uid(0))

EVENTS = [
    PickEvent(time=100.0, picker_id=0, event_type='task_start', aisle_id=1),
    PickEvent(time=105.0, picker_id=0, event_type='pick', aisle_id=1, sku=7, quantity=3),
    PickEvent(time=112.0, picker_id=1, event_type='pick', aisle_id=2, sku=9, quantity=1),
    PickEvent(time=120.0, picker_id=1, event_type='done', items_picked=1),
]
RECORDS = [(0.0, 4.0, 7, 5, 1, 10.0, 20.0, 'reorder'),
           (4.0, 3.0, 9, 2, 2, 30.0, 0.0, 'intake')]
START = 100.0


# ── the two id spaces ─────────────────────────────────────────────────────────────

def test_the_two_crews_never_share_a_uid():
    rows = pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW)
    by_role = {}
    for r in rows:
        by_role.setdefault(r[ROLE], set()).add(r[UID])
    assert by_role['pick'].isdisjoint(by_role['put'])


def test_local_ids_deliberately_collide_across_crews():
    """Which is exactly why `actor_local` and `actor_uid` are two columns."""
    picks = pick_rows(EVENTS, 0, START, PW)
    puts = put_rows(RECORDS, 0, START, TW)
    assert 0 in {r[LOCAL] for r in picks} and 0 in {r[LOCAL] for r in puts}


def test_an_actor_outside_the_crew_raises_rather_than_borrowing_a_uid():
    bad = [PickEvent(time=1.0, picker_id=9, event_type='pick', sku=1, quantity=1)]
    with pytest.raises(ValueError, match="outside the crew"):
        pick_rows(bad, 0, 0.0, PW)


# ── the signed quantity ───────────────────────────────────────────────────────────

def test_a_pick_is_negative_and_a_put_is_positive():
    """One row shape read in opposite directions, so SUM(qty) is net movement."""
    picks = [r for r in pick_rows(EVENTS, 0, START, PW) if r[QTY] is not None]
    puts = put_rows(RECORDS, 0, START, TW)
    assert all(r[QTY] < 0 for r in picks)
    assert all(r[QTY] > 0 for r in puts)
    assert sum(r[QTY] for r in picks) == -4     # 3 + 1 picked
    assert sum(r[QTY] for r in puts) == 7       # 5 + 2 put away


def test_a_state_change_carries_no_quantity_not_a_zero():
    """`task_start` and `done` move nothing. NULL and 0 are different facts."""
    rows = {r[TYPE]: r for r in pick_rows(EVENTS, 0, START, PW)}
    assert rows['task_start'][QTY] is None
    assert rows['done'][QTY] is None


# ── the axis ──────────────────────────────────────────────────────────────────────

def test_t_local_is_t_abs_minus_the_batch_start():
    for r in pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW):
        assert r[T_LOCAL] == pytest.approx(r[T_ABS] - START)


def test_a_pick_row_carries_the_events_own_absolute_instant():
    """The row builder does not shift a pick event: the arm has already put `PickEvent.time`
    on the absolute axis, so `t_abs` is that value and `t_local` is the offset.

    NOT the reconciliation — this compares rows to the events they were built from, in one
    process. The reconciliation is SQL over a real DB, in
    Tests/integration/test_work_events_reconciliation.py.
    """
    rows = pick_rows(EVENTS, 1, START, PW)
    for row, e in zip(rows, EVENTS):
        assert row[T_ABS] == pytest.approx(e.time)
        assert row[T_ABS] - START == pytest.approx(e.time - START)


def test_every_pick_event_becomes_exactly_one_row():
    """A count check the DB-level reconciliation mirrors: no event dropped, none doubled."""
    assert len(pick_rows(EVENTS, 0, START, PW)) == len(EVENTS)


def test_the_put_crews_clock_is_offset_onto_the_arms_axis():
    """Records run from 0 on the crew's own clock; the batch epoch places them."""
    rows = put_rows(RECORDS, 3, START, TW)
    assert [r[T_ABS] for r in rows] == pytest.approx([START + 0.0, START + 4.0])
    assert all(r[BATCH] == 3 for r in rows)
    assert [r[DUR] for r in rows] == pytest.approx([4.0, 3.0])


def test_the_put_source_survives_onto_the_row():
    assert [r[SRC] for r in put_rows(RECORDS, 0, START, TW)] == ['reorder', 'intake']


# ── the shift label ───────────────────────────────────────────────────────────────

def test_the_shift_index_labels_the_absolute_instant():
    late = [PickEvent(time=DEFAULT_SHIFT_SECONDS + 5, picker_id=0, event_type='pick',
                      sku=1, quantity=1)]
    assert pick_rows(late, 0, 0.0, PW)[0][SHIFT] == 1
    assert pick_rows(EVENTS, 0, START, PW)[0][SHIFT] == 0


def test_a_task_spanning_a_boundary_is_recorded_once_under_its_start():
    """The stated contract: shifts LABEL a continuous clock. Work does not pause at the
    whistle and no task is split."""
    b = DEFAULT_SHIFT_SECONDS
    spanning = [PickEvent(time=b - 10, picker_id=0, event_type='task_start', aisle_id=1),
                PickEvent(time=b + 10, picker_id=0, event_type='task_end', aisle_id=1)]
    rows = pick_rows(spanning, 0, 0.0, PW)
    assert len(rows) == 2
    assert rows[0][SHIFT] == 0 and rows[1][SHIFT] == 1


def test_a_shorter_shift_relabels_without_moving_an_instant():
    rows_8h = pick_rows(EVENTS, 0, START, PW, shift_seconds=28800.0)
    rows_1m = pick_rows(EVENTS, 0, START, PW, shift_seconds=60.0)
    assert [r[T_ABS] for r in rows_8h] == [r[T_ABS] for r in rows_1m]
    assert {r[SHIFT] for r in rows_8h} == {0}
    assert {r[SHIFT] for r in rows_1m} != {0}


# ── the declared merge order ──────────────────────────────────────────────────────

def test_the_merged_order_is_total_and_declared():
    """`PickEvent.__lt__` compares time alone, so with two streams the tie-break decides
    whether a pick or a put is read first at the same instant. Stated: instant, role, mode,
    actor, seq."""
    rows = merged(pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW))
    keys = [(r[T_ABS], r[ROLE], r[MODE], r[UID], r[SEQ]) for r in rows]
    assert keys == sorted(keys)


def test_the_merge_is_stable_whichever_stream_is_appended_first():
    a = merged(pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW))
    b = merged(put_rows(RECORDS, 0, START, TW) + pick_rows(EVENTS, 0, START, PW))
    assert a == b


def test_the_tie_at_one_instant_is_broken_by_role_not_by_arrival():
    """Both crews start at the batch epoch, so t=START is a real tie in every run."""
    rows = merged(pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW))
    at_start = [r for r in rows if r[T_ABS] == START]
    assert len(at_start) >= 2
    assert [r[ROLE] for r in at_start] == sorted(r[ROLE] for r in at_start)


def test_the_view_and_the_in_memory_merge_declare_the_same_order():
    """Two definitions of "merged" that disagree is the drift this table invites.

    Compared BEHAVIOURALLY, not by matching a literal: the view's ORDER BY is parsed into
    column names, mapped to row offsets, and used to sort the same rows.  A string match
    passes happily while the two keys diverge, which is what let `batch_id` reach one of
    them and not the other.
    """
    import re

    from Optimization.persistence import Picking_Data as pd

    order = re.search(r"ORDER BY (.+)", pd._CREATE_WORK_EVENTS_MERGED).group(1)
    cols = [c.strip() for c in order.split(",")]
    idx = {name: i for i, name in enumerate(pd._WORK_EVENT_COLS)}
    assert all(c in idx for c in cols), f"the view orders by a column no row carries: {cols}"

    rows = pick_rows(EVENTS, 0, START, PW) + put_rows(RECORDS, 0, START, TW)
    by_view = sorted(rows, key=lambda r: tuple(r[idx[c]] for c in cols))
    assert merged(rows) == by_view


def test_the_merge_orders_a_batch_boundary_by_emission_not_backwards():
    """The tie that occurs in EVERY run, and the reason `batch_id` is in the key.

    The arm advances to `batch_start + duration`, which is exactly the last `done` instant,
    so batch i's final `done` and batch i+1's first `task_start` for the same picker share
    a t_abs, a role, a mode and an actor.  `seq` restarts at 0 each batch, so without the
    batch in the key the tie fell to `seq` -- large for the `done`, near 0 for the
    `task_start` -- placing the next batch's start BEFORE the previous batch's end.
    """
    boundary = 500.0
    last = [PickEvent(time=boundary - 9, picker_id=0, event_type="pick", sku=1, quantity=1),
            PickEvent(time=boundary, picker_id=0, event_type="done", items_picked=1)]
    nxt = [PickEvent(time=boundary, picker_id=0, event_type="task_start", aisle_id=4),
           PickEvent(time=boundary + 9, picker_id=0, event_type="pick", sku=2, quantity=1)]

    rows = merged(pick_rows(last, 7, boundary - 40, PW) + pick_rows(nxt, 8, boundary, PW))
    at_tie = [(r[BATCH], r[TYPE]) for r in rows if r[T_ABS] == boundary]
    assert at_tie == [(7, "done"), (8, "task_start")], (
        f"the batch boundary is ordered backwards: {at_tie}")
