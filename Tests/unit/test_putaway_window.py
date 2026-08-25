"""test_putaway_window.py — FIFO with exactly K units of tolerance.

The correction this branch exists for. An assignment function used to return
`[(unit, bin)] in priority order`, so 14 of 17 restock rules chose WHO was put away first as
well as WHERE — the put-away queue freely re-sorted by whatever score maximised the
function. Real put-away is FIFO with a little tolerance.

`Inventory_Manager.putaway_window` is that tolerance, measured in units:

    None   the policy's whole request is granted (the default, and the old behaviour)
    1      strict FIFO — the policy chooses the bin and has no say in the order
    K      the policy may take its favourite of the K OLDEST units still waiting

The two endpoints are what make the middle trustworthy, and both are asserted here rather
than assumed: K >= len(units) must reproduce `pool.order` EXACTLY, tie-breaking included,
and K = 1 must reproduce queue order exactly. If either drifts, no intermediate K means
anything.

Run:  python -m pytest Tests/unit/test_putaway_window.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.placement.Assignment_Functions import _Pool


class _Unit:
    """A unit identified by its arrival index, so a serve order is readable as a list."""
    __slots__ = ('i',)

    def __init__(self, i):
        self.i = i

    def __repr__(self):
        return f'u{self.i}'


class _KeyPool(_Pool):
    """A pool that only has opinions — `take` is irrelevant to ordering."""
    __slots__ = ('_keys',)

    def __init__(self, keys):
        self._keys = keys

    def sort_key(self, unit):
        return self._keys[unit.i]

    def take(self, unit):
        return None, None


class _MutePool(_Pool):
    """A pool with no precedence at all (optmap is the real one)."""
    __slots__ = ()

    def take(self, unit):
        return None, None


class _Drain:
    """`_serve_order` unbound from the rest of the manager.

    The window is a parameter rather than an attribute read: with several queues the drain
    resolves a different tolerance per queue (`_window_for`), so the ordering step has to be
    told which one applies. It is REQUIRED — a default of None would mean "unbounded", and a
    caller that forgot it would silently get the pre-window behaviour.
    """

    def __init__(self, window):
        self.putaway_window = window

    def order(self, pool, units):
        return Inventory_Manager._serve_order(self, pool, units, self.putaway_window)


def _units(n):
    return [_Unit(i) for i in range(n)]


def _idx(served):
    return [u.i for u in served]


# ── the two endpoints ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(8))
def test_a_window_wider_than_the_queue_is_exactly_the_policys_order(seed):
    """Including ties: `order` is a stable sort, and the window breaks ties on arrival
    index, which is the same rule. A window of K >= N must therefore be indistinguishable
    from granting the whole request — that equivalence is what makes `None` and a large K
    interchangeable, and it is the claim the byte-identity fingerprint rests on."""
    rng = random.Random(seed)
    n = 25
    units = _units(n)
    # Deliberately few distinct keys, so ties are the common case rather than the exception.
    pool = _KeyPool([float(rng.randrange(4)) for _ in range(n)])
    want = _idx(pool.order(units))
    for k in (n, n + 1, 500):
        assert _idx(_Drain(k).order(pool, units)) == want, f'K={k}'
    assert _idx(_Drain(None).order(pool, units)) == want


@pytest.mark.parametrize('seed', range(8))
def test_a_window_of_one_is_strict_fifo(seed):
    rng = random.Random(seed)
    n = 20
    units = _units(n)
    pool = _KeyPool([rng.random() for _ in range(n)])
    assert _idx(_Drain(1).order(pool, units)) == list(range(n))
    # ...and the policy did have an opinion, so this is not vacuous.
    assert _idx(pool.order(units)) != list(range(n))


# ── the middle ────────────────────────────────────────────────────────────────────

def test_the_window_is_the_k_oldest_and_slides_by_one():
    """Worked by hand, so the mechanism is pinned and not just self-consistent.

    Keys (higher is better): u0=1 u1=9 u2=2 u3=8 u4=3, K=3.
      window u0,u1,u2 -> u1 (9).  slide in u3
      window u0,u2,u3 -> u3 (8).  slide in u4
      window u0,u2,u4 -> u4 (3)
      window u0,u2     -> u2 (2)
      window u0        -> u0
    """
    units = _units(5)
    pool = _KeyPool([1.0, 9.0, 2.0, 8.0, 3.0])
    assert _idx(_Drain(3).order(pool, units)) == [1, 3, 4, 2, 0]


def test_every_served_unit_was_among_the_k_oldest_still_waiting():
    """THE definition of the window, checked directly at every step.

    Replayed independently of the implementation: at each output position, recompute the
    set of K oldest unserved units and assert the one served is in it. This is the property
    the whole design rests on, and it is checkable without trusting the heap.
    """
    rng = random.Random(11)
    for seed in range(6):
        rng = random.Random(200 + seed)
        n, k = 40, 6
        units = _units(n)
        pool = _KeyPool([rng.random() for _ in range(n)])
        served = _idx(_Drain(k).order(pool, units))
        waiting = list(range(n))
        for i in served:
            assert i in waiting[:k], (
                f'served u{i} while the {k} oldest waiting were {waiting[:k]}')
            waiting.remove(i)
        assert not waiting


def test_the_window_bounds_LOOKAHEAD_and_not_staleness():
    """A K-oldest window does NOT bound how long a unit waits, and that is worth stating
    out loud because "FIFO with a little tolerance" sounds like it should.

    The window holds the K oldest UNSERVED units. When the policy's preference runs
    opposite to arrival order, the head units stay in the window and lose every single
    round: with ascending keys and K=5, units 0-3 sit in the window for the whole run and
    are served last, overtaken by 56 younger units. The bound is on how far the policy may
    SEE past the head (K-1 units), not on how long the head may be passed over.

    Bounding staleness instead needs a deadline rule — "a unit passed over K times must be
    served next" — which is a different policy, not a tuning of this one. Recorded here so
    the choice is visible rather than discovered later in a result.
    """
    n, k = 60, 5
    units = _units(n)
    pool = _KeyPool([float(i) for i in range(n)])       # policy wants the exact reverse
    served = _idx(_Drain(k).order(pool, units))
    assert sorted(served) == list(range(n)), 'a unit was dropped or duplicated'
    assert served[-4:] == [3, 2, 1, 0], f'expected the head to be stranded, got {served[-6:]}'
    overtaken = sum(1 for j in served[:served.index(0)] if j > 0)
    assert overtaken == n - 1, overtaken
    # It is still a real constraint: the FIRST unit served is inside the window.
    assert served[0] == k - 1
    # And it is not the same as no window at all.
    assert served != _idx(_Drain(None).order(pool, units)) == list(reversed(range(n)))


@pytest.mark.parametrize('k', [1, 2, 3, 7, 19])
@pytest.mark.parametrize('seed', range(6))
def test_every_unit_is_served_exactly_once(k, seed):
    rng = random.Random(100 + seed)
    n = 30
    units = _units(n)
    pool = _KeyPool([rng.random() for _ in range(n)])
    served = _idx(_Drain(k).order(pool, units))
    assert sorted(served) == list(range(n))
    assert len(served) == n


def test_a_wider_window_never_serves_a_unit_later_than_the_policy_would():
    """Sanity on the direction of the knob: widening the window moves the result toward
    the policy's request and away from FIFO, monotonically at the endpoints."""
    n = 40
    units = _units(n)
    pool = _KeyPool([float(n - i) for i in range(n)])       # policy wants queue order...
    for k in (1, 4, 40, None):
        assert _idx(_Drain(k).order(pool, units)) == list(range(n))
    pool2 = _KeyPool([float(i) for i in range(n)])          # ...and now the reverse
    fifo_like = _idx(_Drain(2).order(pool2, units))
    wide = _idx(_Drain(n).order(pool2, units))
    assert fifo_like[0] < wide[0], 'a narrow window should start nearer the queue head'


# ── the pool with no opinion ──────────────────────────────────────────────────────

@pytest.mark.parametrize('k', [None, 1, 4, 1000])
def test_a_pool_without_precedence_is_untouched_by_the_window(k):
    """optmap never had a precedence opinion — it served in queue order by construction —
    so no window can change its result. The short-circuit that recognises this also skips
    building N keys that are all None."""
    units = _units(12)
    pool = _MutePool()
    assert _idx(_Drain(k).order(pool, units)) == list(range(12))


# ── guards ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', [0, -1, -5])
def test_a_window_below_one_is_refused(bad):
    """Zero would mean "the policy may choose from no units", which is not a coherent
    request. Refusing loudly beats silently behaving like 1."""
    with pytest.raises(ValueError, match='putaway_window'):
        _Drain(bad).order(_KeyPool([1.0, 2.0]), _units(2))


@pytest.mark.parametrize('k', [None, 1, 3])
def test_an_empty_or_single_group_is_handled(k):
    pool = _KeyPool([1.0])
    assert _Drain(k).order(pool, []) == []
    assert _idx(_Drain(k).order(pool, _units(1))) == [0]


def test_the_manager_defaults_to_an_unbounded_window():
    """Turning the window on must always be an explicit act: every published comparison so
    far was run with the policy's order granted in full."""
    from Warehouse.inventory.Inventory_Management import DEFAULT_PUTAWAY_WINDOW
    assert DEFAULT_PUTAWAY_WINDOW is None


def test_a_queues_own_k_cap_overrides_the_manager_default():
    """`_window_for` resolves the tolerance per queue. A spec's None means INHERIT, not
    unbounded, so the manager-wide knob still governs every queue with no reason to differ
    — and the pallet queue's `k_cap=1` wins over it."""
    from Warehouse.inventory.put_queue import PutQueue, PutQueueSpec

    mgr = _Drain(12)
    inherit = PutQueue(PutQueueSpec('cart', accepts=('singleton',)))
    strict = PutQueue(PutQueueSpec('pallet', accepts=('pallet',), k_cap=1))
    assert Inventory_Manager._window_for(mgr, inherit) == 12
    assert Inventory_Manager._window_for(mgr, strict) == 1
    mgr.putaway_window = None
    assert Inventory_Manager._window_for(mgr, inherit) is None
    assert Inventory_Manager._window_for(mgr, strict) == 1
