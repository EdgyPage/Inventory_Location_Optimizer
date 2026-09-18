"""test_partner_aisles.py — the ledger's inverse book, and the cohesion fold that reads it.

W8 stage 2 (2026-09-18). `cohesion_min` / `cohesion_max` scored EVERY live aisle for every
placed unit: measured on the meso skus ladder, live aisles per unit grow linearly with the
catalogue (k 0.96, 8 -> 120 over 500 -> 8,000 SKUs) and total aisle scoring is quadratic
(k 1.98). `AisleLedger.partner_aisles` is the inverse of `idx_sets` (matrix index -> aisles
holding it), mirrored at the ledger's three `idx_sets` write points, and `_co_by_aisle` folds a
unit's cohesion over the aisles that hold a partner instead of all of them.

THE CLAIM UNDER TEST IS EXACTNESS, NOT CLOSENESS. The placement oracles compare exact floats,
so the fold must reproduce `_delta_lift_from_row` bit for bit per aisle -- same terms, same
order, same starting int 0 -- and read the same int 0 / float 0.0 the old code read for aisles
it never touches. Every comparison below is `==` AND `type(...) is type(...)`.

Run:  python -m pytest Tests/unit/test_partner_aisles.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.inventory.aisle_ledger import AisleLedger, _UNBOUND
from Warehouse.placement.Assignment_Functions import _co_by_aisle, _delta_lift_from_row


# ── the inverse book ──────────────────────────────────────────────────────────────

def _churn(led: AisleLedger, rng: random.Random, n: int = 600, aisles: int = 12,
           idxs: int = 40) -> None:
    """Random add_bin / add_sku / drop_sku traffic through the ledger's own API."""
    for _ in range(n):
        aid, idx, sku = rng.randrange(aisles), rng.randrange(idxs), rng.randrange(idxs)
        op = rng.random()
        if op < 0.4:
            led.add_bin(aid, idx, rng.random())
            led.count_bin(aid, sku)
        elif op < 0.7:
            led.add_sku(aid, sku, idx, demand=0.0)
            led.count_bin(aid, sku)
        else:
            led.drop_sku(aid, sku, idx, last_when_zero=True)


@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5])
def test_partner_aisles_inverts_idx_sets_under_churn(seed):
    led = AisleLedger()
    _churn(led, random.Random(seed))
    assert led.reconcile() == [], led.reconcile()
    # NON-VACUITY: the check must actually read the book -- a planted extra and a planted
    # miss are both named.
    idx = next(iter(led.partner_aisles))
    led.partner_aisles[idx].add(10_000)
    assert any('which left' in f for f in led.reconcile())
    led.partner_aisles[idx].discard(10_000)
    aid = next(iter(led.partner_aisles[idx]))
    led.partner_aisles[idx].discard(aid)
    assert any('misses aisle' in f for f in led.reconcile())


def test_a_drop_that_empties_the_set_leaves_it_empty():
    led = AisleLedger()
    led.add_sku(3, 7, 7, demand=0.0)
    led.count_bin(3, 7)
    assert led.partner_aisles == {7: {3}}
    assert led.drop_sku(3, 7, 7) is True
    assert not led.partner_aisles.get(7), 'the aisle was not discarded from the inverse'
    assert led.reconcile() == []


def test_a_view_over_the_owners_idx_sets_binds_and_mirrors_the_inverse():
    """The pools build their own view over the owner's forward dict (a loose parameter);
    that view must keep the owner's inverse in step, or the owner's book goes stale on every
    pooled arm.  It does, because the forward dict carries the inverse."""
    owner = AisleLedger()
    view = AisleLedger.over(sku_sets=owner.sku_sets, idx_sets=owner.idx_sets,
                            member_pos=owner.member_pos)
    assert view.partner_aisles is owner.partner_aisles
    view.add_sku(1, 5, 5)
    view.add_bin(1, 6, 0.0)
    assert owner.partner_aisles[5] == {1} and owner.partner_aisles[6] == {1}
    assert owner.reconcile() == []


def test_a_view_over_a_copy_binds_nothing_and_never_touches_the_live_inverse():
    """The gain evaluator hands a pool a copy-on-write VIEW of idx_sets, not the owner's
    dict.  Such a view has no inverse: the mirror is skipped, nothing raises, and the live
    inverse is not advanced by a placement that did not happen."""
    from Inbound.gain_cow import AISLE_VIEWS
    owner = AisleLedger()
    owner.add_bin(2, 9, 0.0)
    cow = AISLE_VIEWS['aisle_idx_sets'](owner.idx_sets)
    view = AisleLedger.over(sku_sets=owner.sku_sets, idx_sets=cow, member_pos=owner.member_pos)
    assert view.partner_aisles is _UNBOUND
    assert 'partner_aisles' not in view.bound
    view.add_sku(3, 9, 9)
    assert 9 in cow[3] and 3 not in owner.idx_sets, 'the virtual write escaped the overlay'
    assert owner.partner_aisles[9] == {2}, 'the live inverse moved on a virtual placement'


def test_no_family_declares_the_book_and_the_evaluator_needs_no_view():
    """It rides with idx_sets, so it is not a policy book: nothing declares it,
    `POLICY_BOOKS` excludes it, and the evaluator's view table is unchanged."""
    from Optimization.config.strategies import _RESTOCKS
    from Inbound.gain_cow import AISLE_VIEWS, AISLE_COPIERS
    assert all('partner_aisles' not in p.ledger_terms for p in _RESTOCKS)
    assert 'partner_aisles' not in AisleLedger.POLICY_BOOKS
    assert 'partner_aisles' in AisleLedger.BOOKS
    assert 'aisle_partner_aisles' not in AISLE_VIEWS and 'aisle_partner_aisles' not in AISLE_COPIERS


# ── the fold ──────────────────────────────────────────────────────────────────────

def _fixture(rng: random.Random, *, aisles: int, idxs: int, row_len: int,
             members_per_aisle: tuple[int, int]):
    """A ledger with random membership, a random affinity row, and random frequencies."""
    led = AisleLedger()
    for aid in range(aisles):
        for idx in rng.sample(range(idxs), rng.randint(*members_per_aisle)):
            led.add_bin(aid, idx, 0.0)
    row = {ci: 0.5 + 2.0 * rng.random() for ci in sorted(rng.sample(range(idxs), row_len))}
    freq = {ci: rng.random() for ci in range(idxs) if rng.random() < 0.9}
    return led, row, freq


def _old_co(row, members, freq):
    """What `score_of` read before: the per-aisle fold, or its two early answers."""
    return _delta_lift_from_row(row, members, freq)


def _new_co(co_by_aid, aid, members):
    """What `score_of` reads now for a SKU with a row, given the per-unit fold."""
    return 0.0 if not members else co_by_aid.get(aid, 0)


@pytest.mark.parametrize('seed', range(12))
@pytest.mark.parametrize('shape', [
    dict(aisles=30, idxs=60, row_len=4, members_per_aisle=(0, 12)),    # row shorter: row order
    dict(aisles=30, idxs=60, row_len=25, members_per_aisle=(0, 6)),    # row longer: set order
    dict(aisles=40, idxs=50, row_len=12, members_per_aisle=(0, 24)),   # both, per aisle
])
def test_the_fold_matches_the_per_aisle_sum_bit_for_bit(seed, shape):
    rng = random.Random(seed)
    led, row, freq = _fixture(rng, **shape)
    co_by_aid = _co_by_aisle(row, led.idx_sets, led.partner_aisles, freq)
    touched = 0
    for aid in range(shape['aisles']):
        members = led.idx_sets[aid]
        old = _old_co(row, members, freq)
        new = _new_co(co_by_aid, aid, members)
        assert new == old and type(new) is type(old), (aid, old, new, type(old), type(new))
        touched += aid in co_by_aid
    # NON-VACUITY: the fixture produced real work -- some aisles folded, some read exact zero.
    assert 0 < touched < shape['aisles'], (touched, shape)
    assert any(isinstance(v, float) and v != 0.0 for v in co_by_aid.values())


def test_untouched_aisles_are_not_in_the_fold():
    """The whole saving: an aisle holding no partner costs nothing and reads int 0."""
    led = AisleLedger()
    led.add_bin(0, 1, 0.0)
    led.add_bin(1, 2, 0.0)
    led.add_bin(2, 3, 0.0)
    row = {1: 1.5, 2: 0.5}
    freq = {1: 0.2, 2: 0.4, 3: 0.6}
    co = _co_by_aisle(row, led.idx_sets, led.partner_aisles, freq)
    assert set(co) == {0, 1}
    assert co[0] == (1.5 - 1.0) * 0.2 and co[1] == (0.5 - 1.0) * 0.4
    assert _new_co(co, 2, led.idx_sets[2]) == 0 and type(_new_co(co, 2, led.idx_sets[2])) is int
    assert _old_co(row, led.idx_sets[2], freq) == 0
    assert type(_old_co(row, led.idx_sets[2], freq)) is int


def test_the_fold_order_is_the_row_order_not_the_set_order():
    """A three-term sum whose row-order and set-order results differ in the last bit; the
    fold must reproduce whichever order the old code used for that aisle."""
    led = AisleLedger()
    # one aisle with MORE members than the row (row order), one with FEWER (set order)
    for idx in (5, 17, 29, 41, 53, 65):
        led.add_bin(0, idx, 0.0)
    for idx in (5, 17):
        led.add_bin(1, idx, 0.0)
    row = {5: 1.1, 17: 1.0000001, 29: 0.3}
    freq = {5: 0.1, 17: 0.7, 29: 1e-9}
    co = _co_by_aisle(row, led.idx_sets, led.partner_aisles, freq)
    for aid in (0, 1):
        old = _old_co(row, led.idx_sets[aid], freq)
        assert co[aid] == old and type(co[aid]) is type(old), (aid, co[aid], old)
