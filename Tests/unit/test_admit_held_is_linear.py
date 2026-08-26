"""test_admit_held_is_linear.py — the retry costs O(queues + admitted), not O(held).

Was `test_admit_held_early_exit.py`. The rename is the finding: there is no early exit any
more, because the exit was the wrong shape twice and the partition makes the question go away.

  1. The original walked every held item on every call, doing a `PutQueueSet.route()` each
     time. `_stock`'s refill loop calls this once per pass and needs roughly `work / staging`
     passes, so passes and the held list grew together — 27,248,644 route calls at 2,400 SKUs.
  2. An early exit on "every queue has refused" cut that 96x and was reported as the fix. It
     was not. It still copied the whole list into a fresh deque per call, and worse, the exit
     was UNREACHABLE: it fired on `len(blocked) >= len(put_queues)` — every queue in the set —
     while a store-only catalogue routes to two of the split's three and `fulfillment` stays
     empty forever. Touches kept growing at k=1.81 against 1.82 before the exit existed.
  3. `_held` is now partitioned per queue, so a full queue is skipped in O(1).

So this file pins two things, and the second is the one that matters:

  * the WORK is bounded — `admit()` is called O(queues + admitted) times, not O(held). A
    wall-clock assertion would be flaky; a call count is exact and deterministic.
  * the RESULT is unchanged. The partition is compared against a reimplementation of the
    ORIGINAL global-order walk — a genuinely different algorithm, not a paraphrase of the one
    under test — because a faster retry that dropped, reordered or double-admitted an item
    would be far worse than the slowness it cured.

Run:  python -m pytest Tests/unit/test_admit_held_is_linear.py -q
"""
from __future__ import annotations

from collections import deque

import pytest

from Warehouse.inventory import put_queue as pq
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.put_queue import (
    ANY, HeldItems, PALLET, SINGLETON, PutQueueSet, PutQueueSpec,
)


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
    """A manager holding `n_held` items, each partitioned against the queue that takes it.

    Built by hand rather than by running a simulation: the point is to count calls under a
    KNOWN held size against a KNOWN amount of free space, and a simulated backlog gives
    neither reproducibly.

    `fill` maps queue name -> how many items to pre-load; anything unnamed is filled to its
    staging limit, i.e. full. Leaving a queue room is what separates "there is nothing to
    find" from "there is still work to find", and the retry must tell those apart.
    """
    fill = fill or {}
    mgr = object.__new__(Inventory_Manager)
    mgr._put_queues = queues
    held = HeldItems()
    for i in range(n_held):
        u = _Unit(cats[i % len(cats)], i)
        held.append(queues.route(u).name, _Item(u, i))
    mgr._held = held
    for q in queues:
        n = fill.get(q.spec.name, q.spec.staging or 1)
        q.items = deque(_Item(_Unit(q.spec.accepts[0], -1), -1) for _ in range(n))
    return mgr


@pytest.fixture()
def counted(monkeypatch):
    """Count `admit` calls without changing what it returns."""
    calls = {'n': 0}
    orig = pq.PutQueue.admit

    def spy(self, item, arrival=True, _o=orig, _c=calls):
        _c['n'] += 1
        return _o(self, item, arrival)

    monkeypatch.setattr(pq.PutQueue, 'admit', spy)
    return calls


# ── the work is bounded ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('n_held', [50, 500, 5_000])
def test_admit_is_called_per_queue_not_per_held_item(counted, n_held):
    """THE regression, as a call count rather than a stopwatch.

    Every queue is full, so nothing can be admitted and one refusal settles each queue.
    Whatever `n_held` is, the calls must stay near the number of queues — if they track
    `n_held`, the walk is back.
    """
    queues = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=1),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=1)])
    mgr = _mgr_with_held(n_held, queues, (SINGLETON, PALLET))

    admitted = mgr._admit_held()

    assert admitted == 0, 'every queue was full; nothing should have been admitted'
    assert len(mgr._held) == n_held, 'a held item went missing'
    assert counted['n'] <= len(queues) + 1, (
        f'{counted["n"]} admit() calls for {n_held} held items across {len(queues)} queues — '
        f'the retry is walking the list instead of skipping a full queue whole')


def test_the_count_does_not_grow_with_the_held_list(counted):
    """The exponent, stated directly: a 100x longer list must not cost 100x the calls."""
    seen = []
    for n in (50, 5_000):
        counted['n'] = 0
        queues = PutQueueSet([PutQueueSpec('one', accepts=ANY, staging=1)])
        mgr = _mgr_with_held(n, queues, (PALLET,))
        mgr._admit_held()
        seen.append(counted['n'])
    assert seen[1] <= seen[0] + 2, (
        f'calls grew from {seen[0]} to {seen[1]} for a 100x longer held list')


def test_an_idle_queue_does_not_make_the_retry_walk(counted):
    """THE defect the partition exists for, and the one an exit condition could not fix.

    A queue that exists but never receives anything is the normal case for a store-only run
    of the three-queue split. The old exit counted refusals against EVERY queue in the set, so
    with `fulfillment` permanently empty it could never fire and the retry walked the whole
    list forever. Here the empty queue simply is not in `loaded()`.
    """
    queues = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=1),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=1),
                          PutQueueSpec('never', accepts=('fulfillment',), staging=1)])
    # only two categories, so `never` holds nothing -- exactly the store-only shape
    mgr = _mgr_with_held(2_000, queues, (SINGLETON, PALLET))

    mgr._admit_held()

    assert counted['n'] <= 3, (
        f'{counted["n"]} admit() calls with an idle third queue — this is the configuration '
        f'the old early exit could never fire in')


# ── the result is unchanged ───────────────────────────────────────────────────────

@pytest.mark.parametrize('n_held,staging', [(40, 1), (40, 7), (200, 3), (13, 50)])
def test_the_partition_matches_the_original_global_walk(n_held, staging):
    """The change must be a speed-up and NOTHING else.

    The reference is a reimplementation of the ORIGINAL algorithm — one global age-ordered
    pass, routing each item, stopping a queue at its first refusal. That is a genuinely
    different procedure from the one under test, so agreement is evidence rather than
    restatement.

    Order matters as much as membership WITHIN a queue: items are drained oldest-first and
    the age stamp is what stops backpressure becoming a priority inversion. Across queues
    there is no ordering to preserve — an item competes for floor space only with others
    bound for its own queue — so the comparison is per queue, which is where the invariant
    actually lives.
    """
    def build():
        qs = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=staging),
                          PutQueueSpec('pallet', accepts=(PALLET,), staging=staging)])
        # Half-full, so the walk both admits and refuses -- comparing two runs that only ever
        # refuse would make the equality trivially true.
        return _mgr_with_held(n_held, qs, (SINGLETON, PALLET),
                              fill={'cart': staging // 2, 'pallet': staging // 2})

    def original(mgr):
        """One global pass in age order, first-refusal-per-queue. The pre-partition code."""
        flat = sorted(mgr._held, key=lambda it: it.age)
        blocked, survivors, admitted = set(), {}, 0
        for item in flat:
            q = mgr.put_queues.route(item.unit)
            if q.name in blocked or not q.admit(item, arrival=False):
                blocked.add(q.name)
                survivors.setdefault(q.name, []).append(item.age)
                continue
            admitted += 1
        return admitted, survivors

    fast, slow = build(), build()
    got_fast = fast._admit_held()
    got_slow, want_survivors = original(slow)

    assert got_fast == got_slow, f'admitted {got_fast} vs {got_slow}'

    have_survivors = {name: [it.age for it in items]
                      for name, items in fast._held.loaded()}
    assert have_survivors == want_survivors, (
        'the survivors differ in identity or ORDER within a queue — the retry drains '
        'oldest-first and the age stamp is what keeps backpressure from becoming a priority '
        'inversion')
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


def test_nothing_is_held_against_a_queue_that_did_not_refuse_it():
    """The partition's own invariant. If an item were filed under the wrong queue it would be
    retried against a queue that cannot take it — refused forever, with the conservation
    ledger unable to see it because it never reached a bin."""
    qs = PutQueueSet([PutQueueSpec('cart', accepts=(SINGLETON,), staging=1),
                      PutQueueSpec('pallet', accepts=(PALLET,), staging=1)])
    mgr = _mgr_with_held(60, qs, (SINGLETON, PALLET))
    for name, items in mgr._held.loaded():
        for it in items:
            assert qs.route(it.unit).name == name, (
                f'an item routing to {qs.route(it.unit).name!r} is held under {name!r}')
