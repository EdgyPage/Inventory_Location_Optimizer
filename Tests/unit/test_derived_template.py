"""test_derived_template.py — a template DERIVED from a sibling is the eager build.

Under the gain evaluator every pool opens over a `TierSlice`, and a round's slices differ
from each other by a few dozen bins: the now side's `taken`, the defer side's `B`, one
`B - hole` per candidate holding a bin uniquely, the odd spill.  Since S10 of
`.scratch/inbound-fullscale-perf/` a slice's bucket template is DERIVED from the nearest
one already in the round's store (only the buckets holding a bin of the symmetric
difference are rebuilt; every other cursor is reused), and a one-off slice derives
without being stored (`derive=`).  The pools' head matrices (`_TravelVec`,
`_MinLabVec`) are likewise patched from the parent's -- rows re-gathered when the aisle
order moved, only the touched aisles rebuilt.

What this file pins, against the eager build (`tier.slice(E)` with no store), exactly:

  1. STRUCTURE: the aisle order, each aisle's bracket order, and every cursor's full pop
     sequence from the front (`popleft`) and from the back (`[-1]` / `pop`) and its `len`
     -- for stored derivations, one-off derivations, and chains of them.
  2. DECISIONS: travel-balanced (cart on and off) and min-labour (minimising and
     maximising) pools opened over derived slices take the same bins with the same float
     scores and commit the same aisle state as the eager open.
  3. NON-VACUITY: derivations happened with the aisle order held AND moved, the matrix
     patch ran in both layouts, and a derivation that skips the rebuild is caught.

Run:  python -m pytest Tests/unit/test_derived_template.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.placement import Assignment_Functions as af
from Warehouse.placement import frozen_tier as ft

from Tests.unit.test_frozen_tier import (
    _FAMILY_STATE, _aff, _bins, _drive, _filtered, _frozen_state, _open_family, _tier,
    _units, _wp)

_FAMS = [('travel', False, False), ('travel', True, False),
         ('minlabor', False, False), ('minlabor', False, True)]


def _seq(cursor):
    """(front pops, back pops, len) of CLONES, so the template cursor never moves."""
    a = cursor.clone()
    front = []
    while a:
        front.append(id(a[0]))
        a.popleft()
    b = cursor.clone()
    back = []
    while True:
        try:
            back.append(id(b[-1]))
        except IndexError:
            break
        b.pop()
    return front, back, len(cursor)


def _structure(buckets) -> list:
    return [(aid, [(m, _seq(buckets[aid][m])) for m in buckets[aid]]) for aid in buckets]


def _children(rng, bins, E0, n):
    """Exclusion sets near E0: a few bins added, a few released -- and on some, an
    aisle's first live bin taken, so the aisle order moves."""
    ids = [id(b) for b in bins]
    out = []
    for _ in range(n):
        E = set(E0)
        for _k in range(rng.randint(1, 4)):
            if E and rng.random() < 0.4:
                E.discard(rng.choice(sorted(E)))
            else:
                E.add(rng.choice(ids))
        if rng.random() < 0.3:
            for b in bins:
                if id(b) not in E:
                    E.add(id(b))
                    break
        out.append(E)
    return out


def _check_structure(seed: int) -> None:
    rng = random.Random(seed)
    bins = _bins(rng, n_aisles=12, per_aisle=10)
    tier = _tier(bins, _wp())
    E0 = {id(b) for b in bins if rng.random() < 0.25}
    store: dict = {}
    tier.slice(E0, store).aisle_buckets()                      # the round's root template
    for i, E in enumerate(_children(rng, bins, E0, 12)):
        want = _structure(tier.slice(E).aisle_buckets())
        if i % 2:
            got = tier.slice(E, store).aisle_buckets()         # stored: later ones chain
        else:
            got = tier.slice(E, derive=store).aisle_buckets()  # one-off: never stored
        assert _structure(got) == want, f'seed {seed} child {i}'
    assert sum(1 for k in store if k[0] == 'buckets') == 1 + 6, (
        'a one-off derivation was memoised, or a stored one was not')


def _check_pools(seed: int, family: str, cart: bool, maximize: bool) -> None:
    rng = random.Random(100 + seed)
    bins = _bins(rng, n_aisles=12, per_aisle=10)
    units, orders, skus = _units(rng, n=10)
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    plp = {s: 0.7 * s for s in skus}
    vol = {s: 300.0 * s for s in skus}
    wp = _wp()
    fixtures = (aff, idx, fbs, qbs, fbi, plp, vol, wp, skus)
    tier = _tier(bins, wp)
    mk = _FAMILY_STATE[family]
    E0 = {id(b) for b in bins if rng.random() < 0.25}
    store: dict = {}

    def drive(cands):
        st = mk(range(1, 13))
        pool = _open_family(family, cands, st, fixtures, maximize=maximize, cart=cart)
        return _drive(pool, units), _frozen_state(st)

    # The root open builds the template AND the head matrix the children patch from.
    assert drive(tier.slice(E0, store)) == drive(_filtered(bins, E0))
    for i, E in enumerate(_children(rng, bins, E0, 8)):
        want = drive(_filtered(bins, E))
        sl = tier.slice(E, store) if i % 2 else tier.slice(E, derive=store)
        assert drive(sl) == want, f'{family} cart={cart} max={maximize} child {i}'


@pytest.fixture
def counters(monkeypatch):
    c = {'derived': 0, 'order_same': 0, 'order_moved': 0,
         'gather_same': 0, 'gather_moved': 0}
    real_d, real_g = ft.TierSlice._aisle_buckets_derived, af._gather_rows

    def derived(self, parent):
        got = real_d(self, parent)
        if got is not None:
            c['derived'] += 1
            c['order_same' if got.order_same else 'order_moved'] += 1
        return got

    def gather(pv, by_aisle, affected, order_same):
        c['gather_same' if order_same else 'gather_moved'] += 1
        return real_g(pv, by_aisle, affected, order_same)

    monkeypatch.setattr(ft.TierSlice, '_aisle_buckets_derived', derived)
    monkeypatch.setattr(af, '_gather_rows', gather)
    # These scenes are small, so most diffs would trip the production share guard.  The
    # guard only ever DECLINES to derive, so lifting it tests more derivations, not
    # different ones.
    monkeypatch.setattr(ft.TierSlice, 'DERIVE_SHARE', 1.0)
    return c


# ── 1. structure ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(8))
def test_a_derived_template_is_the_eager_build(seed, counters):
    _check_structure(seed)
    assert counters['derived'] >= 10


# ── 2. decisions ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('family, cart, maximize', _FAMS)
def test_pools_over_derived_slices_decide_what_the_eager_open_decides(
        seed, family, cart, maximize, counters):
    _check_pools(seed, family, cart, maximize)


# ── 3. non-vacuity ──────────────────────────────────────────────────────────────────

def test_the_derivations_and_the_patches_were_exercised(counters):
    for seed in range(8):
        _check_structure(seed)
    for seed in range(3):
        for fam in _FAMS:
            _check_pools(seed, *fam)
    for k in ('order_same', 'order_moved', 'gather_same', 'gather_moved'):
        assert counters[k] > 0, f'{k} never happened: {counters}'


def test_a_derivation_that_skips_the_rebuild_is_caught(monkeypatch):
    """Sabotage: a "derivation" that hands back its parent as it stands -- every cursor
    reused, the touched buckets included.  The structure oracle must fail on it."""
    monkeypatch.setattr(ft.TierSlice, 'DERIVE_SHARE', 1.0)

    def stale(self, parent):
        out = ft._BucketTemplate(parent)
        out.firsts, out.per_aisle, out.excl = parent.firsts, parent.per_aisle, self.excluded
        out.parent, out.affected, out.order_same = parent, frozenset(), True
        return out

    monkeypatch.setattr(ft.TierSlice, '_aisle_buckets_derived', stale)
    failed = 0
    for seed in range(8):
        try:
            _check_structure(seed)
        except AssertionError:
            failed += 1
    assert failed, 'a stale derivation passed every scene -- the oracle compares nothing'
