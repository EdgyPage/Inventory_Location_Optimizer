"""test_work_day.py — the working day and the release schedule, as arithmetic.

`shift_index` LABELS a continuous clock and never schedules against it. That contract is
restated in five other places, one of them a persisted DDL comment, so the dispatch-affecting
concept needs its own name: a `WorkDay` is a length and an origin, and it answers when a day
ENDS — the instant a picker has to be stopped at.

Nothing imports either class yet. That is deliberate: the commit that wires them has to decide
what a picker overrunning its release does, and that decision does not belong in the same
change as the value objects. Building them unwired means they can be tested exhaustively
without a simulation, which is most of why they are pure.

The two boundary rules below are the ones a caller will get wrong:

  HALF-OPEN     an instant exactly at `end_of(i)` belongs to day i+1, not to day i.
  FULL DAY      `remaining()` at a boundary is a WHOLE day, not zero — the instant belongs to
                the day that is opening, and returning zero would stop a picker that has just
                started rather than one that has run out of time.

Run:  python -m pytest Tests/unit/test_work_day.py -q
"""
from __future__ import annotations

import pytest

from Warehouse.kernel.timeline import (
    DEFAULT_SHIFT_SECONDS, ReleaseSchedule, WorkDay,
)


# ── WorkDay ───────────────────────────────────────────────────────────────────────

def test_the_default_day_is_the_shift_length():
    """One length constant, not two. A day and a reporting shift default to the same eight
    hours; nothing good comes of them drifting apart by default."""
    assert WorkDay().length == DEFAULT_SHIFT_SECONDS == 8 * 3600.0


@pytest.mark.parametrize('bad', [0.0, -1.0, -3600.0])
def test_a_day_that_never_ends_is_refused(bad):
    with pytest.raises(ValueError, match='never ends'):
        WorkDay(length=bad)


def test_days_are_back_to_back():
    """No gap between days. A gap needs a calendar and this model has none — overnight is
    expressed by making the day shorter, not by inserting emptiness."""
    d = WorkDay(length=100.0)
    assert d.end_of(0) == d.start_of(1) == 100.0
    assert d.end_of(4) == d.start_of(5) == 500.0


def test_the_boundary_belongs_to_the_day_that_is_opening():
    """Half-open. Anything else double-counts the boundary instant, and a task starting
    exactly at the whistle would be recorded in the day that just closed."""
    d = WorkDay(length=100.0)
    assert d.index_of(99.999) == 0
    assert d.index_of(100.0) == 1, 'the closing instant was counted in the closing day'
    assert d.index_of(d.end_of(3)) == 4


def test_remaining_at_a_boundary_is_a_whole_day():
    """Zero here would stop a picker that has just started rather than one out of time."""
    d = WorkDay(length=100.0)
    assert d.remaining(0.0) == 100.0
    assert d.remaining(100.0) == 100.0
    assert d.remaining(60.0) == 40.0
    assert d.remaining(199.999) == pytest.approx(0.001)


def test_remaining_is_never_zero_or_negative():
    d = WorkDay(length=100.0)
    for t in (0.0, 1.0, 99.9999, 100.0, 100.0001, 1234.5):
        assert d.remaining(t) > 0.0, t


def test_an_instant_before_the_origin_is_a_negative_day():
    """Not day 0. Folding everything earlier into day 0 would make the first day longer than
    every other one, which is the sort of thing that shows up as a single fat bar in a chart
    and gets explained away."""
    d = WorkDay(length=100.0, origin=500.0)
    assert d.index_of(500.0) == 0
    assert d.index_of(499.0) == -1
    assert d.index_of(399.0) == -2


def test_an_origin_shifts_every_boundary():
    d = WorkDay(length=100.0, origin=37.0)
    assert d.start_of(0) == 37.0 and d.end_of(0) == 137.0
    assert d.index_of(136.999) == 0 and d.index_of(137.0) == 1
    assert d.remaining(37.0) == 100.0


def test_fits_is_remaining_without_the_arithmetic():
    d = WorkDay(length=100.0)
    assert d.fits(60.0, 40.0) is True, 'work ending exactly at the whistle fits'
    assert d.fits(60.0, 40.001) is False
    assert d.fits(0.0, 100.0) is True


def test_a_day_is_a_value():
    """Hashable and comparable, so a sweep can key on one."""
    assert WorkDay(100.0, 5.0) == WorkDay(100.0, 5.0)
    assert WorkDay(100.0) != WorkDay(200.0)
    assert len({WorkDay(100.0), WorkDay(100.0), WorkDay(200.0)}) == 2


# ── ReleaseSchedule: continuous is today ──────────────────────────────────────────

def test_the_default_schedule_is_the_current_runner():
    """`per_day=None` means the schedule has no opinion, so batch i starts when batch i-1
    finished. That is exactly `arm_clock = bs.batch_start_time + bs.duration`, and it is why
    wiring the default schedule in changes nothing."""
    s = ReleaseSchedule()
    assert s.is_continuous
    for ready in (0.0, 77.0, 1e6):
        assert s.release_at(3, ready) == ready
    assert s.missed_by(3, 1e6) == 0.0
    assert s.day_of(99) == 0, 'a continuous schedule has no day structure'


@pytest.mark.parametrize('bad', [0, -1])
def test_a_schedule_with_no_releases_per_day_is_refused(bad):
    with pytest.raises(ValueError, match='per_day'):
        ReleaseSchedule(per_day=bad)


# ── ReleaseSchedule: paced ────────────────────────────────────────────────────────

def test_a_paced_day_is_cut_into_equal_slots():
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=4)
    assert s.cadence == 25.0
    assert [s.scheduled_at(i) for i in range(5)] == [0.0, 25.0, 50.0, 75.0, 100.0]


def test_an_empty_batch_still_consumes_its_slot():
    """The whole reason a schedule exists. A batch that produced no tasks has a zero
    makespan, so a clock driven by makespans cannot advance past it — and the axis
    under-counts, cumulatively. A slot advances whether or not any work happened."""
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=4)
    ready = 0.0                                  # batch 0 did nothing at all
    assert s.release_at(1, ready) == 25.0, 'the empty batch did not consume its slot'
    assert s.release_at(2, ready) == 50.0


def test_a_schedule_never_starts_work_before_the_arm_is_free():
    """A schedule says when work MAY begin, not when a picker teleports. If the previous
    batch overran, this one starts late."""
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=4)
    assert s.release_at(2, 90.0) == 90.0, 'a batch was released while the arm was busy'
    assert s.release_at(2, 10.0) == 50.0, 'a batch was released before its slot'


def test_a_missed_slot_is_reported_rather_than_hidden():
    """`release_at` clamps to the ready instant, which erases the fact that a slot was
    missed. That difference is the only record of it."""
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=4)
    assert s.missed_by(2, 90.0) == 40.0
    assert s.missed_by(2, 50.0) == 0.0
    assert s.missed_by(2, 10.0) == 0.0, 'being early is not being late'


def test_slots_run_across_day_boundaries():
    """Releases do not stop at the whistle; the day only says which day a slot is in."""
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=4)
    assert s.scheduled_at(4) == 100.0 and s.day_of(4) == 1
    assert s.scheduled_at(9) == 225.0 and s.day_of(9) == 2
    assert [s.day_of(i) for i in range(9)] == [0, 0, 0, 0, 1, 1, 1, 1, 2]


def test_one_release_a_day_is_a_release_at_each_day_start():
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=1)
    assert s.cadence == 100.0
    assert [s.scheduled_at(i) for i in range(3)] == [0.0, 100.0, 200.0]
    assert [s.day_of(i) for i in range(3)] == [0, 1, 2]


def test_an_origin_carries_into_the_schedule():
    s = ReleaseSchedule(WorkDay(length=100.0, origin=1000.0), per_day=2)
    assert s.scheduled_at(0) == 1000.0 and s.scheduled_at(3) == 1150.0
    assert s.release_at(0, 0.0) == 1000.0, 'work started before the schedule opened'


@pytest.mark.parametrize('idx', [-1, -5])
def test_a_negative_batch_index_raises(idx):
    """`durations[-1]` is a real batch and would produce a plausible wrong answer — the same
    reason `batch_epoch` guards it."""
    s = ReleaseSchedule(WorkDay(length=100.0), per_day=2)
    with pytest.raises(IndexError):
        s.release_at(idx, 0.0)
    with pytest.raises(IndexError):
        s.scheduled_at(idx)


# ── the thing that must not happen ────────────────────────────────────────────────

def test_nothing_in_the_repo_imports_these_yet():
    """Deliberately unwired. The commit that turns the schedule on has to say what a picker
    overrunning its release does — a modelling decision — and shipping the wiring in the same
    change as the value objects would bury it.

    When that commit lands it deletes this test, and the deletion is the signal that the
    decision was made somewhere.
    """
    import ast as _ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    hits = []
    for sub in ('Warehouse', 'Optimization', 'Diagnostics', 'Visualization'):
        for p in (root / sub).rglob('*.py'):
            if p.name == 'timeline.py':
                continue
            try:
                tree = _ast.parse(p.read_text(encoding='utf-8'))
            except SyntaxError:                      # not ours to police
                continue
            # Strip docstrings before scanning: put_policy.py NAMES WorkDay in its prose to
            # say a clock-aware rule will need one, and a plain substring search called that
            # a usage. Comments vanish through unparse on their own.
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                                     _ast.AsyncFunctionDef)) and _ast.get_docstring(node):
                    node.body = node.body[1:]
            body = _ast.unparse(tree)
            if 'WorkDay' in body or 'ReleaseSchedule' in body:
                hits.append(str(p.relative_to(root)))
    assert not hits, (
        f'{hits} now use WorkDay/ReleaseSchedule — delete this test in the commit that '
        f'wires them, and say there what an overrunning picker does')
