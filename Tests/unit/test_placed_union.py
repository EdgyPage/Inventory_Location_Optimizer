"""test_placed_union.py -- the pools' `_all_idx` under the gain evaluator is answered from the
owner's counted inverse, and it answers EXACTLY what the materialized union answered.

Ticket 02 of the phase-2 campaign: `_RankedAssignPool.__init__` (and two sibling pools)
built `set().union(*aisle_idx_sets.values())` at every open; under the evaluator that dict
is a copy-on-write view whose `values()` materializes every aisle, T(T+1) x 12.59 opens per
drain, 2,774 aisles at campaign scale.  Now `AisleLedger.partner_aisles` counts its placed
keys (`_PartnerAisles.n_placed`), `_CowSets.union()` hands back a `_PlacedUnion` over it, and
`_placed_union` in the placement module asks for it.

The oracle everywhere below is the old expression, computed on the same view.  The size
must match exactly and not approximately, because `_delta_lift_from_row` iterates the
shorter side and a wrong `len` changes the fold.

Run:  python -m pytest Tests/unit/test_placed_union.py -q
"""
from __future__ import annotations

import inspect
import random
from collections import defaultdict

import pytest

from Inbound import gain_cow
from Warehouse.inventory.aisle_ledger import AisleLedger, _PartnerAisles
from Warehouse.placement import Assignment_Functions as af


def _by_hand(pa) -> int:
    return sum(1 for s in pa.values() if s)


def _oracle(view) -> set:
    return set().union(*view.values())


def _churned_owner(seed: int, n_ops: int = 400) -> AisleLedger:
    """An owner ledger after a random add/drop history over 12 aisles and 60 indices,
    driven through the three write points the inverse mirrors."""
    rng = random.Random(seed)
    led = AisleLedger()
    live: set = set()
    for _ in range(n_ops):
        aid, idx = rng.randrange(1, 13), rng.randrange(60)
        if (aid, idx) not in live or rng.random() < 0.6:
            if rng.random() < 0.5:
                led.add_bin(aid, idx, rng.random())
            else:
                led.add_sku(aid, idx, idx, demand=0.0)
            live.add((aid, idx))
        else:
            led.drop_sku(aid, idx, idx, last_when_zero=True)
            live.discard((aid, idx))
    return led


# ── the counter ──────────────────────────────────────────────────────────────────────

def test_the_owner_inverse_is_the_counting_kind():
    led = AisleLedger()
    assert isinstance(led.partner_aisles, _PartnerAisles)
    assert led.idx_sets.inverse is led.partner_aisles
    assert led.partner_aisles.n_placed == 0


@pytest.mark.parametrize('seed', [1, 7, 42])
def test_n_placed_tracks_the_inverse_through_churn(seed):
    led = _churned_owner(seed)
    pa = led.partner_aisles
    assert pa.n_placed == _by_hand(pa) == len(_oracle(led.idx_sets))
    assert led.reconcile() == []


def test_a_second_drop_of_the_same_departure_does_not_decrement_twice():
    led = AisleLedger()
    led.add_bin(1, 5, 0.0)
    led.add_bin(2, 5, 0.0)
    assert led.partner_aisles.n_placed == 1
    # aisle 1 releases idx 5 through the inverse twice (drop_sku's guard is the `in` check);
    # the count must reflect the inverse, not the number of calls
    led.add_sku(1, 5, 5, demand=0.0)
    led.drop_sku(1, 5, 5, last_when_zero=True)
    led.drop_sku(1, 5, 5, last_when_zero=True)
    assert led.partner_aisles.n_placed == _by_hand(led.partner_aisles) == 1
    led.add_sku(2, 5, 5, demand=0.0)
    led.drop_sku(2, 5, 5, last_when_zero=True)
    assert led.partner_aisles.n_placed == 0 and 5 in led.partner_aisles   # emptied, kept


def test_reconcile_reports_a_wrong_count():
    """Non-vacuity: the check in `reconcile` fires on a corrupted counter."""
    led = _churned_owner(3)
    led.partner_aisles.n_placed += 1
    out = led.reconcile()
    assert any('n_placed' in line for line in out), out


def test_an_explicit_plain_inverse_is_refused_by_over():
    owner = AisleLedger()
    with pytest.raises(TypeError, match='_PartnerAisles'):
        AisleLedger.over(idx_sets=owner.idx_sets, partner_aisles=defaultdict(set))


def test_a_loose_view_over_the_owner_dict_keeps_the_count_on_the_owner():
    owner = AisleLedger()
    view = AisleLedger.over(sku_sets=owner.sku_sets, idx_sets=owner.idx_sets,
                            sku_counts=owner.sku_counts, member_pos=owner.member_pos,
                            demand_sum=owner.demand_sum)
    view.add_bin(1, 9, 0.0)
    view.add_sku(2, 9, 9, demand=0.0)
    assert owner.partner_aisles.n_placed == 1
    view.drop_sku(2, 9, 9, last_when_zero=True)
    assert owner.partner_aisles.n_placed == 1          # aisle 1 still holds it
    assert owner.reconcile() == []


# ── the lazy union ───────────────────────────────────────────────────────────────────

def _same(u, oracle: set, universe=range(70)) -> None:
    assert isinstance(u, gain_cow._PlacedUnion)
    assert len(u) == len(oracle)
    assert bool(u) == bool(oracle)
    assert set(u) == oracle
    for i in universe:
        assert (i in u) == (i in oracle), i


@pytest.mark.parametrize('seed', [2, 11])
def test_cow_union_over_a_fresh_view_equals_the_materialized_union(seed):
    led = _churned_owner(seed)
    view = gain_cow._CowSets(led.idx_sets)
    u = view.union()
    assert view._over == {}, 'union() must not materialize any aisle'
    _same(u, _oracle(view))          # the oracle materializes; taken AFTER the check above


def test_cow_union_reconciles_the_overlay_exactly():
    led = AisleLedger()
    for aid, idx in [(1, 10), (1, 11), (2, 11), (2, 12), (3, 13)]:
        led.add_bin(aid, idx, 0.0)
    view = gain_cow._CowSets(led.idx_sets)
    view[1].add(50)             # EXTRA: a virtual placement of an idx placed nowhere live
    view[1].add(12)             # not extra: 12 lives in aisle 2
    view[3].discard(13)         # GONE: aisle 3 was its only holder and the override drops it
    view[2].discard(11)         # not gone: aisle 1 still holds 11 (and is overridden WITH it)
    view[1].discard(10)         # GONE: aisle 1 was 10's only holder
    view[4].add(60)             # EXTRA in an aisle that does not exist live
    u = view.union()
    oracle = _oracle(view)
    assert oracle == {11, 12, 50, 60}
    _same(u, oracle)
    assert set(u._extra) == {50, 60} and set(u._gone) == {10, 13}


def test_cow_union_over_a_plain_dict_falls_back_to_a_set():
    live = {1: {3, 4}, 2: {4, 5}}
    view = gain_cow._CowSets(live)
    view[2].add(9)
    u = view.union()
    assert isinstance(u, set) and u == {3, 4, 5, 9}


def test_placed_union_helper_dispatches_on_the_dict_kind():
    led = _churned_owner(5)
    assert isinstance(af._placed_union(gain_cow._CowSets(led.idx_sets)), gain_cow._PlacedUnion)
    owner_way = af._placed_union(led.idx_sets)
    assert isinstance(owner_way, set) and owner_way == _oracle(led.idx_sets)
    assert af._placed_union({}) == set() and af._placed_union(None) == set()
    loose = {1: {2, 3}}
    assert af._placed_union(loose) == {2, 3}


def test_the_three_pools_take_their_union_through_the_helper():
    """The site list: `_CoDemandPool`, `_RankedAssignPool`, `_ClusterMapPool`.  A fourth
    `set().union(*aisle_idx_sets.values())` written into a pool constructor would reopen
    the O(warehouse) open under the evaluator with every test green."""
    src = inspect.getsource(af)
    helper_src = inspect.getsource(af._placed_union)
    outside = src.replace(helper_src, '')
    assert 'set().union(*aisle_idx_sets.values())' not in outside
    for cls in (af._CoDemandPool, af._RankedAssignPool):
        assert '_placed_union(aisle_idx_sets)' in inspect.getsource(cls.__init__), cls
    cm = inspect.getsource(af.build_cluster_map_pool_fn) \
        if hasattr(af, 'build_cluster_map_pool_fn') else src
    assert '_placed_union(aisle_idx_sets)' in cm


def test_delta_lift_reads_the_lazy_union_as_it_read_the_set():
    """The consumer, end to end: the same float from the lazy object and from the set."""
    led = _churned_owner(9)
    view = gain_cow._CowSets(led.idx_sets)
    row = {i: 1.0 + i / 10.0 for i in range(0, 60, 3)}       # 20 partners, lift > 1
    freq = {i: 0.01 * (i + 1) for i in range(60)}
    lazy = af._delta_lift_from_row(row, view.union(), freq)
    eager = af._delta_lift_from_row(row, _oracle(view), freq)
    assert lazy == eager                                    # exact: same side iterated, same order
