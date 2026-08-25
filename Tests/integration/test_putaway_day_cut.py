"""test_putaway_day_cut.py — the whistle stops put-away too, and the stop is counted.

"if the work from pick or puts or inbound does not complete, I want the workload to rollover
to the next shift."

The pick cut truncates a task MID-BIN, because a pick path is long and divisible. A put is
not: one unit into one bin, and a putter does not set a pallet down halfway up an aisle. So
the put cut is a **start gate** — a putter already past the whistle takes nothing new, and
the put in progress at the whistle finishes.

That asymmetry is the thing most likely to be "fixed" into a completion gate by someone who
read the pick cut first, so it is pinned explicitly (`test_a_put_may_run_past_the_whistle`)
rather than left to be inferred from the numbers.

What is asserted:

  1. `None` — every run that does not ask for a cut — is byte-identical: same placements,
     same clocks, same counters;
  2. a deadline of 0 stops the queue before its first put, and NOTHING is lost;
  3. the whistle defers rather than drops: what it stops is placed by the next drain;
  4. overtime is bounded by one put per worker, and the gate is on the START;
  5. `cut` counts ITEMS LEFT, once, and is not multiplied by the drain's refill passes --
     the defect `blocked` was fixed for, on the same loop;
  6. the deadline and `budget` bind independently: a warehouse can run out of either first.

The queue is filled DIRECTLY here rather than by running batches. A standing put-away queue
is what the cut acts on, and the reorder machinery only produces one after enough picking to
deplete a SKU — so driving it through `check_reorders` would make the test's subject depend
on the demand sampler. `Tests/unit/test_reorder_phases.py` owns the question of whether the
phase passes its deadline down.

Run:  python -m pytest Tests/integration/test_putaway_day_cut.py -q
"""
from __future__ import annotations

import pathlib
import random
import sys

import pytest

from Warehouse.inventory.put_queue import ANY, PutQueue, PutQueueSet, PutQueueSpec
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.layout.Storage_Primitive import viable_storage_units

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


def _assets(strategy='uni_fifo_norsl', n_skus=200, timed=True, size=1):
    a = cs.build_assets(n_skus=n_skus, bins_per_aisle=20, strategy=strategy,
                        seed=7, coverage=2.0, safety=0.4)
    if timed:
        a.mgr.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=size)
    return a


def _fill(a, n_orders: int = 40, qty: int = 4) -> int:
    """Put a real standing queue on the floor. Returns the items that reached a QUEUE.

    Real reorder units, not stubs: the drain reads geometry (`height`, `max_width`) to pick
    a bin and to cost the put, and a namespace does not have it.

    `_admit` returns None for an item a staging limit refused — it goes to `_held` instead,
    which is a different place with a different counter (`blocked`). So the return here is
    queue depth, not arrivals, and `mgr._held` holds the difference.
    """
    mgr = a.mgr
    admitted = 0
    for order in a.inventory.orders[:n_orders]:
        for unit in viable_storage_units(order.reorder(), qty):
            if mgr._admit(unit, 'reorder') is not None:
                admitted += 1
    return admitted


def _queued(mgr):
    return sum(len(q.items) for q in mgr.put_queues) + len(mgr._held)


def _cut(mgr):
    return sum(r['cut'] for r in mgr.put_queues.snapshot())


# ── 1. no deadline is the run we already have ─────────────────────────────────────

def test_no_deadline_places_exactly_what_it_placed_before():
    """`None` is every run today. Compared against a second manager built identically and
    drained through the OLD call shape, so the reference is the previous behaviour rather
    than a remembered number.

    `random.seed` before each drain is load-bearing, not hygiene. `_uniform_assignment`
    draws from the GLOBAL `random` stream, so the second manager's draws depend on how many
    the first one consumed — build one arm, drain it, and the other arm starts mid-stream
    and picks different bins. Without the reseed this test failed on `putaway_seconds`
    (1394.23 vs 1300.90) while placing the same COUNT into the same depth, which is exactly
    what a different-bins divergence looks like.
    """
    a, b = _assets(), _assets()
    n = _fill(a)
    assert _fill(b) == n

    base_a, base_b = a.mgr._reorder_placements, b.mgr._reorder_placements
    random.seed(99)
    a.mgr._stock(deadline=None)
    random.seed(99)
    b.mgr._stock()

    assert a.mgr._reorder_placements - base_a == b.mgr._reorder_placements - base_b
    assert _queued(a.mgr) == _queued(b.mgr)
    assert round(a.mgr.putaway_seconds, 9) == round(b.mgr.putaway_seconds, 9)
    assert a.mgr._reorder_placements - base_a > 0, 'nothing was placed — the test is vacuous'
    assert _cut(a.mgr) == 0 and _cut(b.mgr) == 0


def test_an_untimed_queue_ignores_the_deadline():
    """A queue with no crew has no clock to be past. Without this, turning the cut on in a
    run that never enabled put-away timing would silently stop ALL put-away."""
    a = _assets(timed=False)
    assert not any(q.timed for q in a.mgr.put_queues)
    _fill(a)
    base = a.mgr._reorder_placements
    a.mgr._stock(deadline=0.0)
    assert a.mgr._reorder_placements - base > 0
    assert _cut(a.mgr) == 0


# ── 2. a zero deadline stops it dead, and loses nothing ───────────────────────────

def test_a_zero_deadline_places_nothing_and_drops_nothing():
    """The whistle has already blown when the crew arrives. Every unit the manager was
    handed must still be countable — a cut that loses work is a leak wearing a feature's
    name."""
    a = _assets()
    n = _fill(a)
    base = a.mgr._reorder_placements

    a.mgr._stock(deadline=0.0)

    assert a.mgr._reorder_placements == base, 'a put started after the whistle'
    assert n > 0, 'nothing arrived — the assertion above is vacuous'
    assert _queued(a.mgr) == n
    assert _cut(a.mgr) == n


def test_what_the_whistle_stops_is_placed_the_next_day():
    """ROLLOVER, which is the point. The first drain is cut to nothing; the second gets the
    whole day and must clear exactly what the first could not."""
    a = _assets()
    n = _fill(a)
    a.mgr._stock(deadline=0.0)
    stopped = _queued(a.mgr)
    assert stopped == n > 0

    base = a.mgr._reorder_placements
    a.mgr.drain_putaway_records()          # a drain is a batch boundary: clocks reset
    a.mgr._stock(deadline=None)

    assert a.mgr._reorder_placements - base == stopped - _queued(a.mgr)
    assert _queued(a.mgr) < stopped, 'the carried work was not re-offered'


# ── 3. the gate is on the START ───────────────────────────────────────────────────

def test_a_put_may_run_past_the_whistle():
    """Overtime is bounded by one put per worker, BY DESIGN.

    A completion gate ("refuse anything that would END late") needs the duration, which is
    known only after the bin is chosen — so it would mean choosing a placement and then
    un-choosing it, and `_execute_placement` is the single bin-mutation commit point
    precisely so that no path does that. A start gate needs nothing but the clock.

    Asserted on the crew clock rather than on a count: with one worker and a deadline just
    above zero, the queue takes work and its clock ends up PAST the deadline.
    """
    a = _assets()
    _fill(a)
    q = a.mgr.put_queues.queues[0]
    assert q.crew_size == 1, 'the "one put per worker" bound needs a known crew size'

    base = a.mgr._reorder_placements
    a.mgr._stock(deadline=1e-9)

    assert a.mgr._reorder_placements > base, 'nothing started — this is not a start gate'
    assert max(q.clocks) > 1e-9, (
        'the put did not run past the whistle, so this is a completion gate rather than '
        'the start gate the model wants')


def test_can_start_reads_the_earliest_free_worker():
    """With several putters the gate is per WORKER, not per crew: one who is still out does
    not stop a colleague who is free. `min(clocks)` is what says so."""
    q = PutQueue(PutQueueSpec('c', accepts=ANY))
    q.bind_crew(SpeedProfile(2.0, 4.0), None, size=3)
    q.clocks = [100.0, 5.0, 100.0]
    assert q.can_start(10.0) is True          # worker 1 is free at 5
    q.clocks = [100.0, 50.0, 100.0]
    assert q.can_start(10.0) is False
    assert q.can_start(None) is True          # no deadline, ever


def test_a_bigger_crew_gets_more_done_before_the_same_whistle():
    """The gate is per worker, so three putters clear more of the same queue in the same day
    than one does. If the gate were on the crew this would be flat."""
    placed = {}
    for size in (1, 3):
        a = _assets(size=size)
        _fill(a)
        base = a.mgr._reorder_placements
        a.mgr._stock(deadline=200.0)
        placed[size] = a.mgr._reorder_placements - base
    assert placed[3] > placed[1], f'crew size did not change the day\'s work: {placed}'


# ── 4. the counter says what it means ─────────────────────────────────────────────

@pytest.mark.parametrize('staging,deadline', [(None, 0.0), (4, 0.0), (4, 60.0),
                                              (32, 60.0), (None, 60.0)])
def test_cut_equals_what_is_standing_on_a_stopped_queue(staging, deadline):
    """THE INVARIANT, across every combination of floor space and day length.

    `_stock`'s outer loop re-admits and re-drains until the floor is clear, and all three
    whistle gates can fire on more than one pass. A counter incremented at any of them would
    report how many passes the drain happened to need rather than how much work the boundary
    left standing — the exact defect `blocked` was fixed for (392 spurious refusals at
    staging 4). So the count is computed ONCE, outside the loop, and this asserts it equals
    the depth of every queue that could not start.

    A tight staging limit is what forces many passes, so it is parametrized in rather than
    assumed: if `cut` ever multiplied, the `staging=4` rows would be the ones to catch it.
    """
    a = _assets()
    a.mgr.put_queues = PutQueueSet([PutQueueSpec('one', accepts=ANY, staging=staging)])
    a.mgr.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=1)
    _fill(a)

    a.mgr._stock(deadline=deadline)
    q = a.mgr.put_queues.queues[0]
    standing = len(q.items) if not q.can_start(deadline) else 0
    assert q.cut == standing
    assert q.cut > 0, 'the whistle never bit; this row proves nothing'


def test_held_items_are_blocked_not_cut():
    """The two counters stay disjoint. A held item was refused FLOOR SPACE and never reached
    a queue, so it is `blocked`; only what is standing ON a queue was stopped by the clock.
    Merged, one number would mean two problems with different fixes."""
    a = _assets()
    a.mgr.put_queues = PutQueueSet([PutQueueSpec('tight', accepts=ANY, staging=4)])
    a.mgr.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=1)

    queued = _fill(a)
    assert queued == 4 and len(a.mgr._held) > 0, 'staging never bound'
    a.mgr._stock(deadline=0.0)
    assert _cut(a.mgr) == 4, 'the cut counted items that never reached a queue'


def test_the_counter_drains_with_its_siblings():
    """`cut` is a FLOW, so it resets when the snapshot is taken. A level that never reset
    would read as a monotonically worsening warehouse."""
    a = _assets()
    _fill(a)
    a.mgr._stock(deadline=0.0)
    assert _cut(a.mgr) > 0
    assert _cut(a.mgr) == 0


def test_depth_and_cut_are_different_questions():
    """A deep queue the whistle never reached and a shallow one it stopped hard are
    different problems. If `cut` were derivable from `depth` the column would be noise."""
    a = _assets()
    _fill(a)
    a.mgr._stock(deadline=None)
    free = [(r['depth'], r['cut']) for r in a.mgr.put_queues.snapshot()]
    assert all(c == 0 for _d, c in free), 'an uncut run reported a cut'

    b = _assets()
    _fill(b)
    b.mgr._stock(deadline=0.0)
    stopped = [(r['depth'], r['cut']) for r in b.mgr.put_queues.snapshot()]
    assert any(c > 0 for _d, c in stopped)
    assert any(d > 0 for d, _c in stopped)


# ── 5. budget and deadline are independent ────────────────────────────────────────

def test_a_budget_and_a_deadline_bind_separately():
    """People and the clock are two constraints, and a warehouse can run out of either
    first. The budget alone caps placements; the deadline alone stops the day; neither
    silently subsumes the other."""
    a = _assets()
    _fill(a)
    base = a.mgr._reorder_placements
    a.mgr._stock(budget=3)
    assert a.mgr._reorder_placements - base == 3, 'the budget alone did not bind'

    b = _assets()
    _fill(b)
    base = b.mgr._reorder_placements
    b.mgr._stock(budget=3, deadline=0.0)
    assert b.mgr._reorder_placements == base, 'the deadline lost to the budget'
    assert _cut(b.mgr) > 0


def test_the_deadline_does_not_leak_into_the_budgets_accounting():
    """A cut queue must not spend budget it never used. Otherwise a run with both set would
    report the crew as exhausted when it was only the clock."""
    a = _assets()
    _fill(a)
    base = a.mgr._reorder_placements
    a.mgr._stock(budget=1000, deadline=0.0)
    assert a.mgr._reorder_placements == base
    a.mgr.drain_putaway_records()
    a.mgr._stock(budget=1000)
    assert a.mgr._reorder_placements - base > 0
