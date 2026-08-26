"""test_admit_held_early_exit.py — the retry stops once every queue has refused.

Found by the growth ladder, and it was quadratic. `_stock`'s refill loop calls `_admit_held`
once per pass and needs roughly `work / staging` passes, so with a tight floor the passes and
the held list grow together. Without an early exit the retry still walked every remaining held
item on every pass — doing a `PutQueueSet.route()` each time — to reach a conclusion the first
blocked-out queue had already settled.

Measured at 40 batches with `staging=4`, exact call counts:

      SKUs        300        2,400     exponent
    before    677,845   27,248,644     k = 1.784
    after      42,736      283,774     k = 0.914
    unstaged   26,408      174,738     k = 0.912

The defect only became reachable when the split configuration became selectable: with
`staging=None` the held list is always empty and the refill loop runs exactly one pass.

Two things are pinned here, and the second is the one that matters:

  * the WORK is bounded — `route()` is called O(queues), not O(held), once everything is
    blocked. A wall-clock assertion would be flaky; a call count is exact and deterministic;
  * the RESULT is unchanged. An early exit that dropped, reordered or double-admitted an item
    would be far worse than the slowness it cured, so the ordering and the admission decisions
    are compared against the exhaustive walk directly.

Run:  python -m pytest Tests/unit/test_admit_held_early_exit.py -q
"""
from __future__ import annotations

from collections import deque

import pytest

from Warehouse.inventory import put_queue as pq
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.put_queue import ANY, PALLET, SINGLETON, PutQueueSet, PutQueueSpec


class _Unit:
    """A unit is only ever routed and admitted here, so a category is all it needs."""
    __slots__ = ('unit_category', 'quantity', 'order')

    def __init__(self, cat, i):
        self.unit_category = cat
        self.quantity = 1
        self.order = type('O', (), {'sku': i})()


class _Item:
    __slots__ = ('unit', 'source', 'age')

    def __init__(self, unit, age):
        self.unit, self.source, self.age = unit, 'reorder', age


def _mgr_with_held(n_held, queues, cats, fill=None):
    """A manager whose `_held` deque holds `n_held` items, with each queue pre-loaded.

    Built by hand rather than by running a simulation: the point is to count calls under a
    KNOWN held size against a KNOWN amount of free space, and a simulated backlog gives
    neither reproducibly.

    `fill` maps queue name -> how many items to pre-load; anything unnamed is filled to its
    staging limit, i.e. full. Leaving a queue room is what separates "the answer is settled"
    from "there is still work to find", and the early exit must tell those apart.
    """
    fill = fill or {}
    mgr = object.__new__(Inventory_Manager)
    mgr._put_queues = queues
    mgr._held = deque(_Item(_Unit(cats[i % len(cats)], i), i) for i in range(n_held))
    for q in queues:
        n = fill.get(q.spec.name, q.spec.staging or 1)
        q.items = deque(_Item(_Unit(q.spec.accepts[0], -1), -1) for _ in range(n))
    return mgr


@pytest.fixture()
def counted(monkeypatch):
    """Count `route` calls without changing what it returns."""
    calls = {'n': 0}
    orig = pq.PutQueueSet.route

    def spy(self, unit, _o=orig, _c=calls):
        _c['n'] += 1
        return _o(self, unit)

    monkeypatch.setattr(pq.PutQueueSet, 'route', spy)
    return calls


# ── the work is bounded ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('n_held', [50, 500, 5_000])
def test_route_is_called_per_queue_not_per_held_item(counted, n_held):
    """THE regression, as a call count rather than a stopwatch.

    Every queue is full, so nothing can be admitted and the answer is known after the last
    queue refuses. Whatever `n_held` is, the number of routes must stay near the number of
    queues — if it tracks `n_held`, the quadratic is back.
    """
    queues = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=1),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=1)])
    mgr = _mgr_with_held(n_held, queues, (SINGLETON, PALLET))

    admitted = mgr._admit_held()

    assert admitted == 0, 'every queue was full; nothing should have been admitted'
    assert len(mgr._held) == n_held, 'a held item went missing'
    assert counted['n'] <= len(queues) + 1, (
        f'{counted["n"]} route() calls for {n_held} held items across {len(queues)} queues — '
        f'the retry is walking the whole list again after every queue has refused, which is '
        f'the quadratic the early exit removed')


def test_the_count_does_not_grow_with_the_held_list(counted):
    """The exponent, stated directly: a 100x longer list must not cost 100x the routes."""
    seen = []
    for n in (50, 5_000):
        counted['n'] = 0
        queues = PutQueueSet([PutQueueSpec('one', accepts=ANY, staging=1)])
        mgr = _mgr_with_held(n, queues, (PALLET,))
        mgr._admit_held()
        seen.append(counted['n'])
    assert seen[1] <= seen[0] + 2, (
        f'routes grew from {seen[0]} to {seen[1]} for a 100x longer held list')


def test_a_partially_blocked_set_still_walks_far_enough(counted):
    """The exit must not fire EARLY. With room in one queue, the retry has to keep going to
    find the items that fit — stopping at the first refusal of a different queue would strand
    admissible merchandise forever."""
    queues = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=1),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=50)])
    # `cart` full, `pallet` empty: 20 of the 40 held items are pallets and all of them fit.
    mgr = _mgr_with_held(40, queues, (SINGLETON, PALLET), fill={'pallet': 0})
    admitted = mgr._admit_held()
    assert admitted == 20, f'only {admitted} of the 20 admissible pallets got in'
    assert len(mgr._held) == 20, 'the singletons should still be held'
    assert all(i.unit.unit_category == SINGLETON for i in mgr._held)


# ── the result is unchanged ───────────────────────────────────────────────────────

@pytest.mark.parametrize('n_held,staging', [(40, 1), (40, 7), (200, 3), (13, 50)])
def test_the_early_exit_matches_an_exhaustive_walk(n_held, staging):
    """The fix must be a speed-up and NOTHING else. Compared against a reimplementation of
    the original loop — the exhaustive one — on the same inputs: same admissions, same
    survivors, in the same order.

    Order matters as much as membership: `_held` is drained oldest-first and the age stamp is
    what stops backpressure becoming a priority inversion, so a reordering here would be a
    correctness bug wearing a performance fix's clothes.
    """
    def exhaustive(mgr):
        blocked, still, admitted = set(), deque(), 0
        for item in mgr._held:
            q = mgr.put_queues.route(item.unit)
            if q.name in blocked or not q.admit(item, arrival=False):
                blocked.add(q.name)
                still.append(item)
                continue
            admitted += 1
        mgr._held = still
        return admitted

    def build():
        qs = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=staging),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=staging)])
        # Half-full, so the walk both admits and refuses -- comparing two runs that only
        # ever refuse would make the equality trivially true.
        return _mgr_with_held(n_held, qs, (SINGLETON, PALLET),
                              fill={'cart': staging // 2, 'pallet': staging // 2})

    fast, slow = build(), build()
    got_fast = fast._admit_held()
    got_slow = exhaustive(slow)

    assert got_fast == got_slow, f'admitted {got_fast} vs {got_slow}'
    assert [i.age for i in fast._held] == [i.age for i in slow._held], (
        'the survivors differ in identity or ORDER — the retry drains oldest-first and the '
        'age stamp is what keeps backpressure from becoming a priority inversion')
    assert ([[i.age for i in q.items] for q in fast.put_queues]
            == [[i.age for i in q.items] for q in slow.put_queues]), (
        'the queues ended up holding different items')


def test_the_comparison_is_not_vacuous():
    """Both sides above must actually admit something in at least one configuration, or the
    equality is between two empty results."""
    qs = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=9),
                      PutQueueSpec('pallet', accepts=(PALLET,), staging=9)])
    mgr = _mgr_with_held(40, qs, (SINGLETON, PALLET), fill={'cart': 0, 'pallet': 0})
    assert mgr._admit_held() > 0
