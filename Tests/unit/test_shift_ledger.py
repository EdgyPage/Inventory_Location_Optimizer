"""test_shift_ledger.py — the drain-or-cap working day's close-out state.

`_build_leaf` carried this as four closure variables — `_shift_prev_day`, `_shift_cut_today`,
`_shift_last_finish`, `_shift_standing` — updated in one block per batch and read again at
run end for the final day, which has no next-day boundary to close it.

WHY THIS CLUSTER FIRST. Tranche C's brief is to extract by what a TEST needs to address, and
until now nothing here could be addressed without running a simulation. The day-boundary rule
is intricate and has drawn blood before (the working-day plan's corrections; the empty-batch
clock stall being a contract rather than a bug), yet its whole specification is four values
and one ordering rule. That is a unit test's natural size.

THE ORDERING RULE IS THE SPECIFICATION, and the product comment states it:

    the boundary is tested BEFORE this batch's clocks, cut and depths are folded in, so the
    previous day closes on what ITS last batch left behind and this batch — the first of the
    new day — is attributed to the new day.

So `advance_to(day)` returns the snapshot to close (or None) and must be called BEFORE
`note(...)`. Getting that backwards attributes every day's first batch to the wrong day,
which is precisely the defect the product comment was written to prevent — and which no test
could previously catch.

WHAT IS NOT HERE. `_shift_close_out` — the pure function that turns a snapshot into a DB row
and decides `drained` via `equilibrium.is_drained` — is unchanged and stays in
`strategy_runner`. This object holds the STATE it is called with; it makes no judgement about
a day.

FLAG-OFF IS STRUCTURAL. Every site is inside `if _drain_or_cap:`, so a run without the era
flag never constructs one and never calls it. That is what makes this extraction
byte-identical by construction rather than by argument.
"""
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import logging
import pickle

import pytest

from Optimization.simdriver.shift_ledger import ShiftLedger
from Optimization.simdriver.strategy_runner import _shift_close_out


# ── the opening day ───────────────────────────────────────────────────────────────

def test_a_fresh_ledger_has_opened_no_day():
    s = ShiftLedger()
    assert s.prev_day is None
    assert s.cut_today is False
    assert s.last_finish == 0.0
    assert s.standing == (0, 0, 0, 0)


def test_the_first_day_opens_and_closes_nothing():
    """There is no previous day to close, so the first `advance_to` yields no row."""
    s = ShiftLedger()
    assert s.advance_to(0) is None
    assert s.prev_day == 0


def test_staying_within_a_day_closes_nothing():
    s = ShiftLedger()
    s.advance_to(3)
    for _ in range(5):
        assert s.advance_to(3) is None
    assert s.prev_day == 3


# ── the boundary ──────────────────────────────────────────────────────────────────

def test_crossing_a_boundary_returns_the_PREVIOUS_day_snapshot():
    """The row closes the day that ENDED, with the values its own last batch left."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=True, finish=1234.5, standing=(7, 2, 1, 0))

    closed = s.advance_to(1)
    assert closed is not None
    day, standing, last_finish, cut = closed
    assert day == 0, 'the snapshot must close the day that ended, not the one beginning'
    assert standing == (7, 2, 1, 0)
    assert last_finish == pytest.approx(1234.5)
    assert cut is True


def test_crossing_a_boundary_resets_the_new_day_but_keeps_standing():
    """`cut` and `last_finish` are PER-DAY and reset; `standing` is a LEVEL that carries.

    The product code resets exactly `cut_today` and `last_finish` at a boundary and leaves
    `standing` alone — it is re-measured every batch, so the carried value is simply the last
    reading until this day's first batch overwrites it.
    """
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=True, finish=900.0, standing=(5, 1, 2, 3))
    s.advance_to(1)

    assert s.prev_day == 1
    assert s.cut_today is False, 'a new day starts uncut'
    assert s.last_finish == 0.0, 'a new day starts with no finish'
    assert s.standing == (5, 1, 2, 3), 'standing is a level and is not reset at a boundary'


def test_a_skipped_day_still_closes_only_the_day_that_ended():
    """Days need not be contiguous — an empty day consumes a release slot. The ledger closes
    the day it was on and opens the day it was handed, with nothing invented in between."""
    s = ShiftLedger()
    s.advance_to(4)
    s.note(cut=False, finish=10.0, standing=(1, 0, 0, 0))
    closed = s.advance_to(9)
    assert closed[0] == 4
    assert s.prev_day == 9


# ── note() ────────────────────────────────────────────────────────────────────────

def test_cut_is_sticky_within_a_day():
    """`cut_today = cut_today or <this batch cut>` — once a day is cut it stays cut."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=True, finish=1.0, standing=(0, 0, 0, 0))
    s.note(cut=False, finish=2.0, standing=(0, 0, 0, 0))
    assert s.cut_today is True, 'a later uncut batch must not clear the day'


def test_last_finish_is_a_running_max_never_the_latest():
    """`max(last_finish, arm, put, recv)` — a batch whose crews finished EARLIER than a
    previous one must not pull the day's finish backwards."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=500.0, standing=(0, 0, 0, 0))
    s.note(cut=False, finish=100.0, standing=(0, 0, 0, 0))
    assert s.last_finish == pytest.approx(500.0)


def test_standing_is_replaced_not_accumulated():
    """A LEVEL, re-measured each batch. Summing it across batches is the `recv_cut` scar."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=1.0, standing=(9, 9, 9, 9))
    s.note(cut=False, finish=2.0, standing=(1, 2, 3, 4))
    assert s.standing == (1, 2, 3, 4)


# ── the final day ─────────────────────────────────────────────────────────────────

def test_final_returns_the_open_day_for_the_run_end_close_out():
    """The last day of a run has no next-day boundary, so `_finish` closes it explicitly.

    Without this the ledger reports one day fewer than the run worked, and the equilibrium
    check's "every day drained" is read over a window missing its last member.
    """
    s = ShiftLedger()
    s.advance_to(2)
    s.note(cut=True, finish=42.0, standing=(1, 1, 0, 0))
    assert s.final() == (2, (1, 1, 0, 0), 42.0, True)


def test_final_is_none_when_no_day_ever_opened():
    """A run that never reached a batch has no final day to close, and must not invent one."""
    assert ShiftLedger().final() is None


# ── the ordering rule, stated as a test ───────────────────────────────────────────

def test_advance_before_note_attributes_the_first_batch_to_the_NEW_day():
    """THE RULE. Called in the product order, day 0's row carries day 0's values only.

    A ledger that folded the batch in first would close day 0 holding day 1's first batch —
    the mis-attribution the product comment exists to prevent.
    """
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=100.0, standing=(1, 0, 0, 0))

    closed = s.advance_to(1)                      # boundary FIRST...
    s.note(cut=True, finish=999.0, standing=(8, 8, 8, 8))   # ...then this batch

    assert closed[2] == pytest.approx(100.0), "day 0 closed holding day 1's finish"
    assert closed[3] is False, "day 0 closed holding day 1's cut"
    assert s.last_finish == pytest.approx(999.0)
    assert s.cut_today is True


def test_the_wrong_order_would_be_visible():
    """SABOTAGE: if noting first were harmless, the test above would prove nothing."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=100.0, standing=(1, 0, 0, 0))

    s.note(cut=True, finish=999.0, standing=(8, 8, 8, 8))   # WRONG: batch before boundary
    closed = s.advance_to(1)

    assert closed[2] == pytest.approx(999.0) and closed[3] is True, (
        'noting before advancing did NOT mis-attribute, so the ordering rule is not '
        'load-bearing and the test above is vacuous')


# ── the object itself ─────────────────────────────────────────────────────────────

def test_it_is_slotted_so_a_typo_cannot_shadow_a_field():
    s = ShiftLedger()
    with pytest.raises(AttributeError):
        s.cut_tody = True


def test_it_survives_the_worker_process_boundary():
    s = ShiftLedger()
    s.advance_to(1)
    s.note(cut=True, finish=7.0, standing=(2, 0, 1, 0))
    back = pickle.loads(pickle.dumps(s))
    assert back.final() == (1, (2, 0, 1, 0), 7.0, True)


# ── the day close, end to end ─────────────────────────────────────────────────────
# Every test above drives the LEDGER and stops at the snapshot it hands back. The thing that
# turns a snapshot into the DB row -- `_shift_close_out` -- was an `ArmAssembly` method until
# ticket 07, so reaching it meant building a 76-slot assembly, and a method can read all 76
# whether or not it does. It is a module-level pure function of its arguments now, and its two
# durable names are parameters, so the boundary is drivable with a stub and a logger.


class _Day:
    """`release.day` — `end_of(day)` is the whole surface the close-out uses."""

    def __init__(self, cap: float):
        self.cap = cap

    def end_of(self, day: int) -> float:
        return (day + 1) * self.cap


class _Release:
    def __init__(self, cap: float = 28_800.0):
        self.day = _Day(cap)


def _close(closed, cap=28_800.0):
    return _shift_close_out(*closed, release=_Release(cap),
                            log=logging.getLogger('shift-close-test'))


def test_the_ledger_snapshot_is_the_close_outs_parameter_order():
    """`advance_to` documents its tuple as "`_shift_close_out`'s exact parameter order". That
    is a claim about two functions in two modules, and nothing checked it -- so it is checked
    by CALLING one with the other's output rather than by comparing signatures."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=100.0, standing=(0, 0, 0, 0))
    closed = s.advance_to(1)
    assert closed is not None

    row = _close(closed)
    assert row[0] == 0, 'the row is not the day that ended'
    assert len(row) == 11, 'the shift_days row shape moved'


def test_a_quiet_day_drains_and_a_standing_queue_caps_it():
    """The verdict, through the ledger rather than around it. `drained` is
    `equilibrium.is_drained`'s call and this asserts it MOVES -- a close-out that returned a
    constant would pass every other assertion here."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=100.0, standing=(0, 0, 0, 0))
    quiet = _close(s.advance_to(1))

    s2 = ShiftLedger()
    s2.advance_to(0)
    s2.note(cut=False, finish=100.0, standing=(4, 0, 0, 0))   # a put queue still standing
    busy = _close(s2.advance_to(1))

    assert quiet[3] is True and busy[3] is False, (
        f'drained did not move with the standing put queue ({quiet[3]}, {busy[3]})')


def test_the_supply_carry_is_not_labour_and_does_not_cap_the_day():
    """The distinction the close-out's docstring spends a paragraph on: stock that was never
    delivered is not work that did not fit. `missed_share` judges it; the day still drained."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=100.0, standing=(0, 0, 0, 7))     # SUPPLY carry only
    row = _close(s.advance_to(1))
    assert row[3] is True, 'an undelivered stock carry capped the day'
    assert row[9] == 7 and row[8] == 0, 'the supply carry landed in the labour column'


def test_overtime_caps_a_day_with_nothing_standing():
    """The fifth term, and the one a standing-count test cannot reach: the last task started
    before the whistle and finished after it. Nothing is standing, so only the finish can cap
    this day -- `_cap_end` for day 0 is one cap."""
    s = ShiftLedger()
    s.advance_to(0)
    s.note(cut=False, finish=30_000.0, standing=(0, 0, 0, 0))
    row = _close(s.advance_to(1), cap=28_800.0)
    assert row[3] is False, 'a task finishing past the cap did not count as overtime'
    assert row[10] == 30_000.0, 'last_finish is not reported as measured'


def test_the_final_day_closes_the_same_way_as_a_boundary():
    """The run-end path: `final()` returns the open day and it goes through the same function.
    That equivalence is why the run end needs no second close-out -- and it is the shape that
    lost the last day twice when the run-end WRITER was conditional (ticket 07)."""
    s = ShiftLedger()
    s.advance_to(3)
    s.note(cut=True, finish=500.0, standing=(1, 0, 1, 0))
    row = _close(s.final())
    assert row[0] == 3 and row[3] is False, 'the final day did not close on its own state'
