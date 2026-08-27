"""test_shift_end.py — the drain-or-cap rule: off the clock, or the whistle, whichever first.

The shift is one site-wide working stretch (root `CONTEXT.md`): it ends when no standing
work remains and none is still scheduled to release, or at the global cap.  The arithmetic
lives in `Warehouse.kernel.timeline.shift_end`; the JUDGMENT (what counts as standing work
— put queues, held items, the dock floor, carried demand; never merchandise in transit) is
the runner's, and its wiring is pinned here by source because the mode's two implications
("capping implies the cut", "one boundary for every crew") are single lines a refactor
could silently drop.

Run:  python -m pytest Tests/unit/test_shift_end.py -q
"""
from __future__ import annotations

import inspect

from Warehouse.kernel.timeline import shift_end


CAP = 28_800.0          # an eight-hour day, in the sim's own seconds


def test_a_drained_shift_ends_when_the_crews_do():
    """The drain-early case: everything done by early afternoon — off the clock."""
    assert shift_end(CAP, 20_000.0, drained=True) == 20_000.0


def test_standing_work_holds_the_shift_to_the_cap():
    """The capped case: work remains, the whistle is the end, and the carry takes over."""
    assert shift_end(CAP, 20_000.0, drained=False) == CAP


def test_overtime_past_the_whistle_is_still_a_capped_shift():
    """START-gate overtime can finish past the cap; the CAP still came first — the shift
    ended at the whistle, and the overhang is the bounded one-task-per-worker tail."""
    assert shift_end(CAP, 29_950.0, drained=True) == CAP


def test_the_boundary_is_exact():
    assert shift_end(CAP, CAP, drained=True) == CAP, 'a drain AT the cap is a capped shift'


# ── the mode's two implications, pinned at the wiring ────────────────────────────

def _runner_source() -> str:
    import Optimization.simdriver.strategy_runner as sr
    return inspect.getsource(sr)


def test_the_cap_implies_the_cut():
    """A cap without carry loses demand, so the mode FORCES the cut rather than trusting
    two flags to agree."""
    src = _runner_source()
    assert 'if _drain_or_cap:\n        _cut_at_day_end = True' in src, (
        'the drain-or-cap mode no longer forces the cut — a capped shift without carry '
        'silently loses the demand it cut')


def test_one_boundary_for_every_crew():
    """Mode-on, the receiving crew shares the site-wide cap; its own day knobs are the
    flag-off configuration, by decision."""
    src = _runner_source()
    assert 'if _drain_or_cap:' in src and '_recv_day = _WorkDay(length=_wd.get' in src, (
        'the receiving crew no longer shares the site-wide shift when the mode is on')
