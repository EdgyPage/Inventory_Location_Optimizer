"""test_putaway_age.py — a put-away item's arrival stamp, and what it proves about FIFO.

Queue POSITION used to be the only record of how long a unit had waited. That is enough
while nothing ever re-enters the queue, and three things do: the repack rescue, the singleton
rescue, and a whole group requeued when the put-away budget runs out. Once the drain got a
K-oldest window, "the deque is in FIFO order" stopped being a comment and had to become
something checkable.

`PutawayItem.age` is that stamp — monotonic, never reset, inherited by `respawn` so a repack
does not make a unit newer.

The interesting result is the last test. The plan this work follows predicted that the two
rescues invert priority because they `appendleft`. They do not: `appendleft` returns the
children to the head their parent was just popped from, which is exactly age-preserving. The
prediction was wrong, and it is checked here rather than fixed.

Run:  python -m pytest Tests/integration/test_putaway_age.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from Warehouse.inventory.inventory_common import PutawayItem

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


# ── the stamp itself ──────────────────────────────────────────────────────────────

def test_respawn_inherits_the_parents_age():
    """A repack does not make a unit newer. The pallet that arrived first and had to be
    broken into three should still go before a pallet that arrived after it."""
    parent = PutawayItem(object(), 'reorder', 7)
    child = parent.respawn(object())
    assert child.age == 7 and child.source == 'reorder'
    assert child.unit is not parent.unit


def test_an_unstamped_item_is_minus_one_and_not_zero():
    """A hand-built item in a test must not read as the oldest thing in the warehouse."""
    assert PutawayItem(object(), 'intake').age == -1


def test_the_stamp_is_monotonic_across_batches(tmp_path):
    """Never reset. Restarting per batch would make a fresh arrival look older than
    something that has been waiting since batch 0 — which is precisely the unit a
    staleness report exists to surface."""
    a = cs.build_assets(n_skus=300, bins_per_aisle=30, strategy='uni_rank_labor_norsl',
                        seed=42, coverage=2.0, safety=0.4)
    seen = []
    orig = a.mgr._admit

    def cap(unit, source, _o=orig, _s=seen):
        it = _o(unit, source)
        _s.append(it.age)
        return it
    a.mgr._admit = cap
    cs.run_meso(a, n_batches=5, seed=42)
    assert len(seen) > 50, f'only {len(seen)} admissions — the fixture is too small'
    assert seen == sorted(seen), 'admission stamps went backwards'
    assert len(set(seen)) == len(seen), 'two items share a stamp'


# ── what the queue order actually is ──────────────────────────────────────────────

@pytest.mark.parametrize('strategy', ['uni_rank_labor_norsl', 'uni_fifo_norsl',
                                      'uni_comp_norsl'])
def test_the_queue_is_age_ordered_whenever_the_drain_reads_it(strategy):
    """THE invariant the K-oldest window depends on: `units` reaches `_serve_order` in age
    order, so position IS age and the window's "K oldest" is really the K oldest.

    Checked by instrumenting the drain over a real run rather than by reasoning about the
    three re-entry paths, because reasoning about them is what produced the wrong prediction
    this file corrects.
    """
    a = cs.build_assets(n_skus=400, bins_per_aisle=30, strategy=strategy, seed=42,
                        coverage=2.0, safety=0.4)
    mgr = a.mgr
    violations, groups, n_real, n_unknown = [], 0, 0, 0
    orig = mgr._stock_ranked if mgr.placement.is_ranked else None
    orig_serve = type(mgr)._serve_order

    def spy(self, pool, units, k, _o=orig_serve):
        nonlocal groups, n_real, n_unknown
        groups += 1
        ages = [self._age_of.get(id(u), -1) for u in units]
        n_real += sum(1 for a in ages if a >= 0)
        n_unknown += sum(1 for a in ages if a < 0)
        if ages != sorted(ages):
            violations.append(ages[:12])
        return _o(self, pool, units, k)

    # The drain hands `_serve_order` bare units, so map unit -> age at admission time.
    mgr._age_of = {}
    orig_admit = mgr._admit

    def cap(unit, source, _o=orig_admit):
        it = _o(unit, source)
        mgr._age_of[id(it.unit)] = it.age
        return it
    mgr._admit = cap
    type(mgr)._serve_order = spy
    try:
        cs.run_meso(a, n_batches=6, seed=42)
    finally:
        type(mgr)._serve_order = orig_serve

    if orig is None:
        pytest.skip(f'{strategy} has no group path')
    assert groups > 0, 'the drain never grouped anything — nothing was checked'
    # Non-vacuity: the comparison has to be over real stamps, not a column of -1s, and the
    # groups have to be big enough for an ordering to exist at all.
    assert n_real > 100, f'only {n_real} stamped units reached the drain'
    assert n_unknown == 0, (
        f'{n_unknown} units reached the drain with no stamp — a producer bypassed _admit, '
        f'or a rescue lost the mapping')
    assert n_real / groups > 1.5, 'every group was a single unit; order was never tested'
    assert not violations, (
        f'{strategy}: a group reached the drain out of age order, so the K-oldest window '
        f'would not be selecting the K oldest. First offenders: {violations[:3]}')


def test_the_rescues_do_not_invert_priority():
    """The prediction this corrects.

    A repack pops the head, splits it, and `appendleft`s the children — back to the head
    their parent just vacated, in order. That is age-preserving, and `respawn` carries the
    stamp so it stays age-preserving even after the queue is re-grouped in a later batch.
    Exercised directly on a deque rather than through a warehouse, so the claim is about the
    mechanism and not about whether a particular fixture happens to trigger a rescue.
    """
    from collections import deque
    q = deque([PutawayItem(object(), 'reorder', age) for age in (10, 11, 12)])
    head = q.popleft()                                   # age 10, fails to place
    children = [head.respawn(object()) for _ in range(3)]
    for c in reversed(children):                         # exactly what the rescues do
        q.appendleft(c)
    ages = [it.age for it in q]
    assert ages == [10, 10, 10, 11, 12], ages
    assert ages == sorted(ages), 'a rescued child overtook a unit older than its parent'
