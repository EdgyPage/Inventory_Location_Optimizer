"""test_actor_space.py — who is allowed to appear in a per-crew reader.

`_group_events_by_picker` used to filter with `if pid is not None and 0 <= pid <
k_pickers` and drop everything else on the floor.  For a single crew of pickers whose
ids come from `assign_tasks`' bucket index that condition is always true, so the branch
never fired and nothing was ever lost.

It fires the moment a second work stream shares the event list.  A putter or an unloader
carries an id from a different space, gets dropped, and every statistic computed from the
grouping — `total_items`, `task_makespan`, `picking_pct` — silently reports a subset of
the batch as if it were the whole batch.  There is no error, no warning, and the numbers
look plausible.

So the boundary is now explicit in both directions: the per-crew readers RAISE on an
actor they do not own, and a second stream is expected to record somewhere else rather
than widen the bound.

Run:  python -m pytest Tests/unit/test_actor_space.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.metrics.Simulation_Analytics import (
    _group_events_by_picker,
    extract_batch_stats,
)
from Warehouse.picking.Pick import PickEvent, _ProgressAPIMixin


def _ev(pid, t=1.0, kind='pick'):
    return PickEvent(time=t, picker_id=pid, event_type=kind)


# ── the crew's own ids still group exactly as before ──────────────────────────────

def test_a_conforming_crew_groups_unchanged():
    evs = [_ev(0), _ev(1), _ev(0, t=2.0), _ev(2)]
    grouped = _group_events_by_picker(evs, 3)
    assert [len(g) for g in grouped] == [2, 1, 1]


def test_an_idle_picker_gets_an_empty_list_not_a_missing_slot():
    grouped = _group_events_by_picker([_ev(0)], 3)
    assert len(grouped) == 3 and grouped[1] == [] and grouped[2] == []


# ── the actor from another crew ───────────────────────────────────────────────────

def test_an_id_past_the_crew_raises_instead_of_vanishing():
    """The putter case: id 5 in a crew of 3."""
    with pytest.raises(ValueError, match=r'picker_id 5 is outside the crew of 3'):
        _group_events_by_picker([_ev(0), _ev(5)], 3)


def test_a_negative_id_raises_rather_than_indexing_from_the_end():
    """`grouped[-1]` is a real picker and would have appended to the wrong one had the
    guard ever been removed carelessly."""
    with pytest.raises(ValueError, match='outside the crew'):
        _group_events_by_picker([_ev(-1)], 3)


def test_a_missing_id_raises():
    with pytest.raises(ValueError, match='carries no picker_id'):
        _group_events_by_picker([_ev(None)], 3)


def test_the_error_says_what_to_do_about_it():
    """A raise that does not explain the two possible causes just moves the confusion."""
    with pytest.raises(ValueError) as exc:
        _group_events_by_picker([_ev(9)], 2)
    msg = str(exc.value)
    assert 'k_pickers does not match' in msg      # cause 1: wrong crew size passed
    assert 'another crew' in msg                  # cause 2: a second stream leaked in


def test_the_batch_reader_surfaces_it_rather_than_reporting_a_short_batch():
    """The whole point — this used to return a BatchStats built from a subset."""
    evs = [_ev(0, kind='done'), _ev(7, kind='done')]
    with pytest.raises(ValueError, match='outside the crew'):
        extract_batch_stats(evs, batch_id=0, k_pickers=2)


# ── the other half of the same contract ───────────────────────────────────────────

def test_progress_reports_the_pick_crew_and_says_so():
    """`progress_at` enumerates range(num_pickers), so it omits any non-picker no matter
    what id it carries.  That is intended, and now documented — it is why a second stream
    gets its own table instead of nine zero-valued pick columns in `picker_events`."""
    src = inspect.getsource(_ProgressAPIMixin.progress_at)
    assert 'range(self._config.num_pickers)' in src
    # Collapse whitespace and case: the claim is what the prose SAYS, not where it wraps
    # or which half of it is shouted.
    doc = ' '.join((inspect.getdoc(_ProgressAPIMixin.progress_at) or '').split()).lower()
    assert 'pick crew and only the pick crew' in doc
    assert 'picker_events' in doc
