"""test_crew_clock.py — the five rules every timed work stream shares, in one place.

Put-away had these as five `PutQueue` methods. Receiving needs the identical five, and two
copies of `min(clocks) < deadline` will drift: one of them says `<=` some day, and the number
it produces is plausible forever. So they moved to the kernel and `PutQueue` delegates.

Two things are pinned that the extraction could quietly have broken:

  * **The START-gate semantics.** `can_start` asks whether anyone is free to BEGIN, so a job
    running when the whistle blows finishes and overtime is bounded by one job per worker. A
    completion gate would need the job's duration, which is known only after its destination
    is chosen — so it would mean choosing a placement and then un-choosing it, past the single
    bin-mutation commit point. The strict `<` matters: a crew free exactly AT the whistle is
    stopped.
  * **`reset` mutates in place.** The `PutQueue` version rebound `self.clocks` to a fresh
    list. In-place is what a second stream needs — a caller holding the list keeps the one the
    owner uses — and it is the one semantic change the extraction made, so it is asserted on
    identity rather than on value.

Run:  python -m pytest Tests/unit/test_crew_clock.py -q
"""
from __future__ import annotations

import ast
import inspect

import pytest

from Warehouse.kernel import crew_clock as cc


# ── new_clocks ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('size', [1, 2, 17])
def test_a_new_crew_is_all_idle(size):
    assert cc.new_clocks(size) == [0.0] * size
    assert cc.size_of(cc.new_clocks(size)) == size


@pytest.mark.parametrize('size', [0, -1])
def test_a_crew_of_nobody_is_a_configuration_error(size):
    """Not "a crew that does nothing slowly" — a stream configured with no workers would
    silently do infinite work in zero time, since `can_start` returns True on an empty list."""
    with pytest.raises(ValueError, match='does no work'):
        cc.new_clocks(size)


def test_the_error_names_the_stream():
    """In a multi-stream configuration, "size >= 1" without the owner sends the reader to the
    wrong queue."""
    with pytest.raises(ValueError, match='store_pallet'):
        cc.new_clocks(0, 'store_pallet')


def test_size_of_is_zero_when_unbound():
    assert cc.size_of(None) == 0
    assert cc.size_of([]) == 0


# ── can_start: the START gate ─────────────────────────────────────────────────────

def test_the_gate_reads_the_EARLIEST_free_worker():
    """Per worker, not per crew: one who is still out does not stop a colleague who is free.
    `min`, not `max`."""
    assert cc.can_start([100.0, 5.0, 100.0], 10.0) is True
    assert cc.can_start([100.0, 50.0, 100.0], 10.0) is False


def test_a_worker_free_exactly_AT_the_whistle_is_stopped():
    """Strict `<`. Pinned because `<=` is the single most likely accidental edit here and it
    would be invisible: the run would just do slightly more work per day, forever."""
    assert cc.can_start([10.0], 10.0) is False
    assert cc.can_start([9.999999], 10.0) is True


def test_no_deadline_and_no_crew_both_mean_yes():
    """`None` is every run that does not ask for a cut. An unbound stream has no clock to be
    past — and this is the branch that made a dock held outside `_bind_put_crews` ignore the
    whistle forever, so it is asserted rather than assumed."""
    assert cc.can_start([100.0], None) is True
    assert cc.can_start(None, 10.0) is True
    assert cc.can_start([], 10.0) is True


def test_a_negative_deadline_stops_everyone():
    """A deadline is a REMAINDER on the batch-local clock. Negative means the day was already
    over when the batch started, which must stop the crew rather than wrap around."""
    assert cc.can_start([0.0], -1.0) is False


# ── charge: greedy list scheduling ────────────────────────────────────────────────

def test_work_goes_to_whoever_is_free_earliest():
    clocks = [7.0, 2.0, 5.0]
    t0, w = cc.charge(clocks, 3.0)
    assert (t0, w) == (2.0, 1)
    assert clocks == [7.0, 5.0, 5.0], 'the clock was not advanced in place'


def test_ties_break_to_the_lowest_index():
    """What makes a size-1 stream byte-identical to the single serial clock that preceded
    this, and what makes a size-N stream deterministic."""
    clocks = [0.0, 0.0, 0.0]
    assert cc.charge(clocks, 1.0) == (0.0, 0)
    assert cc.charge(clocks, 1.0) == (0.0, 1)
    assert cc.charge(clocks, 1.0) == (0.0, 2)
    assert cc.charge(clocks, 1.0) == (1.0, 0)


def test_a_crew_of_one_is_exactly_a_serial_clock():
    clocks = cc.new_clocks(1)
    starts = [cc.charge(clocks, d)[0] for d in (3.0, 4.0, 5.0)]
    assert starts == [0.0, 3.0, 7.0]
    assert cc.finish(clocks) == 12.0


def test_a_bigger_crew_finishes_the_same_work_sooner():
    """The only thing crew SIZE means in this model, so it is worth one assertion."""
    one, three = cc.new_clocks(1), cc.new_clocks(3)
    for _ in range(6):
        cc.charge(one, 10.0)
        cc.charge(three, 10.0)
    assert cc.finish(three) < cc.finish(one)
    assert cc.finish(three) == 20.0 and cc.finish(one) == 60.0


# ── finish and reset ──────────────────────────────────────────────────────────────

def test_finish_is_the_last_worker_and_zero_when_unbound():
    assert cc.finish([3.0, 9.0, 1.0]) == 9.0
    assert cc.finish(None) == 0.0
    assert cc.finish([]) == 0.0


def test_reset_mutates_in_place():
    """THE one semantic change the extraction made, asserted on identity rather than value:
    a caller holding the list must keep the one the owner uses, or a second stream's drain
    silently stops resetting the clock anyone actually charges."""
    clocks = [4.0, 9.0]
    same = clocks
    cc.reset(clocks)
    assert clocks == [0.0, 0.0]
    assert same is clocks and same == [0.0, 0.0]


def test_reset_tolerates_an_unbound_stream():
    cc.reset(None)          # must not raise — a drain runs before a crew may be bound


# ── the kernel stays a leaf ───────────────────────────────────────────────────────

def test_crew_clock_imports_nothing():
    """`architecture.yml` forbids `wh_kernel -> *`. That is what lets both wh_inventory and
    wh_operations reach this without inverting a dependency, so it is checked here rather
    than trusted to the graph — the graph is regenerated, this runs every suite."""
    tree = ast.parse(inspect.getsource(cc))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = []
    for n in imports:
        if isinstance(n, ast.ImportFrom):
            if n.module == '__future__':
                continue
            names.append(n.module)
        else:
            names.extend(a.name for a in n.names)
    assert not names, f'crew_clock imports {names}; it must import nothing at all'


# ── PutQueue really delegates ─────────────────────────────────────────────────────

def test_put_queue_has_no_second_copy_of_the_rules():
    """The whole point of the extraction. A re-inlined `min(self.clocks)` would work
    perfectly and drift silently."""
    from Warehouse.inventory import put_queue as pq
    src = ast.unparse(ast.parse(inspect.getsource(pq)))
    for expr in ('min(self.clocks)', 'max(self.clocks)', '[0.0] * size', '[0.0] * len('):
        assert expr not in src, (
            f'put_queue.py still computes {expr!r} itself; there are two copies of the '
            f'whistle rule again and they will drift')
    assert 'crew_clock.' in src


def test_the_delegation_preserves_the_start_gate():
    """End to end through the real object: a bound queue answers exactly as the kernel does."""
    from Warehouse.inventory.put_queue import ANY, PutQueue, PutQueueSpec
    from Warehouse.kernel.cost_model import SpeedProfile
    q = PutQueue(PutQueueSpec('q', accepts=ANY))
    assert q.can_start(0.0) is True, 'an unbound queue must ignore the whistle'
    q.bind_crew(SpeedProfile(2.0, 4.0), None, size=2)
    assert q.crew_size == 2 and q.clocks == [0.0, 0.0]
    assert q.charge(5.0) == (0.0, 0)
    assert q.can_start(3.0) is True          # worker 1 is still at 0.0
    q.charge(5.0)
    assert q.can_start(3.0) is False         # both are at 5.0
    assert q.finish == 5.0
    q.reset_clocks()
    assert q.clocks == [0.0, 0.0]
