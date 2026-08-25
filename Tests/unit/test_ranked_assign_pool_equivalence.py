"""test_ranked_assign_pool_equivalence.py — the pool computes what the wave computed.

`_RankedAssignPool` is `_ranked_assign_impl` split along its seam: the candidate snapshot
into `__init__`, the priority sort into `order`, one placement into `take`. Four arms run on
it — `tmin`, `tmax`, `rank_random`, `rank_popularity`.

WHAT THIS FILE CLAIMS, precisely: driven in the SAME order, the pool makes bit-identical
decisions and leaves bit-identical manager state. It does not claim that a reordered drain
reproduces today's run — it cannot, and Phase 2 is where that stops being true on purpose.
So every test here drives the pool through `pool.order(units)`, which is the sort the impl
did internally, and compares against `_ranked_assign_impl` itself as a live oracle. When the
drain later declines that order, this file still pins the half that must not move: the
choice.

Float equality is EXACT here, deliberately against the repo's tolerance convention, because
the claim is byte-identity and not agreement. `aisle_demand_sum` is a running sum whose value
depends on the accumulation order, so `==` is the only assertion that can detect a reordering
that happens to land close.

Run:  python -m pytest Tests/unit/test_ranked_assign_pool_equivalence.py -q
"""
from __future__ import annotations

import copy
import random
from collections import defaultdict

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement import Assignment_Functions as af


# ── fixtures: the smallest shapes the impl actually reads ─────────────────────────
class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class _Order:
    __slots__ = ('sku', 'labor_cost', 'demand', 'expected_popularity')

    def __init__(self, sku, labor_cost, f, q):
        self.sku, self.labor_cost = sku, labor_cost
        self.demand = _Demand(f, q)
        self.expected_popularity = f * q


class _Unit:
    __slots__ = ('order',)

    def __init__(self, order):
        self.order = order


class _Affinity:
    """`_sku_to_idx` plus a real lift matrix, because a null matrix short-circuits
    `_affinity_row` and the co-occurrence term — the one float-summation hazard in the
    priority — never runs."""
    __slots__ = ('_sku_to_idx', '_matrix')

    def __init__(self, skus, pairs=()):
        self._sku_to_idx = {s: i for i, s in enumerate(sorted(skus))}
        n = len(self._sku_to_idx)
        try:
            from scipy.sparse import csr_matrix
            import numpy as np
        except ImportError:                                  # pragma: no cover
            self._matrix = None
            return
        m = np.zeros((n, n), dtype=np.float32)
        for a, b, lift in pairs:
            i, j = self._sku_to_idx[a], self._sku_to_idx[b]
            m[i, j] = m[j, i] = lift                         # symmetric, no self-pairs
        self._matrix = csr_matrix(m)


def _wp():
    return WorkloadParams.from_pick_config(PickConfig(
        num_pickers=2, x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0))


def _state(aisle_ids=()):
    """The four manager dicts, plus a pre-seeded aisle so `all_idx` is non-empty and the
    co-occurrence term is live from the first unit."""
    return {'aisle_sku_sets': defaultdict(set, {a: set() for a in aisle_ids}),
            'aisle_idx_sets': defaultdict(set, {a: set() for a in aisle_ids}),
            'aisle_demand_sum': defaultdict(float)}


def _wave(rng, n_aisles=3, bins_per_aisle=(4, 2, 1), n_units=9, n_skus=4):
    """A fixture that lights up every branch: an aisle that exhausts mid-wave (so the
    `del head_bin/head_D` path runs), a D tie across aisles (so the first-wins tie-break is
    live), repeat SKUs in one aisle (so the `sku not in aisle_sku_sets` no-op runs), and more
    units than bins (so the tail returns None)."""
    bins = []
    for aid in range(1, n_aisles + 1):
        for k in range(bins_per_aisle[aid - 1]):
            # Aisle 3's single bin duplicates aisle 1's first D exactly.
            x, y = (10.0, 20.0) if aid == 3 else (10.0 * k, 20.0 * (aid - 1))
            bins.append(_Bin(aid, x, y))
    rng.shuffle(bins)                       # candidate order drives insertion-order ties
    skus = list(range(1, n_skus + 1))
    orders = {s: _Order(s, 1.0 + 0.25 * s, 0.1 * s, 2.0 + s) for s in skus}
    # Two SKUs share an exact priority so the stable sort's tie behaviour is exercised.
    orders[2].labor_cost = orders[1].labor_cost * orders[1].demand.relative_frequency \
        / orders[2].demand.relative_frequency
    units = [_Unit(orders[rng.choice(skus)]) for _ in range(n_units)]
    aff = _Affinity(skus, [(1, 2, 5.0), (2, 3, 3.0), (1, 4, 2.5)])
    return bins, units, aff, orders


def _tables(orders, aff):
    freq_by_sku = {s: o.demand.relative_frequency for s, o in orders.items()}
    qty_by_sku  = {s: o.demand.quantity_rate      for s, o in orders.items()}
    freq_by_idx = {aff._sku_to_idx[s]: f for s, f in freq_by_sku.items()}
    return freq_by_idx, freq_by_sku, qty_by_sku


ARMS = ['tmin', 'tmax', 'rank_random', 'rank_popularity']


def _run(arm, bins, units, aff, orders, st, seed, use_pool):
    """Both paths over the SAME deep-copied state. The pool is driven through
    `pool.order(units)` — the impl's own sort — so only the CHOICE is under test."""
    wp = _wp()
    fbi, fbs, qbs = _tables(orders, aff)
    common = dict(affinity=aff, wp=wp, aisle_sku_sets=st['aisle_sku_sets'],
                  aisle_idx_sets=st['aisle_idx_sets'],
                  aisle_demand_sum=st['aisle_demand_sum'],
                  freq_by_idx=fbi, freq_by_sku=fbs, qty_by_sku=qbs, beta=1.0)
    rng = random.Random(seed)
    extra: dict = {'minimize': arm != 'tmax'}
    if arm == 'rank_random':
        extra['aisle_selector'] = lambda bw, bb: rng.choice(list(bb.keys()))
    elif arm == 'rank_popularity':
        ads = st['aisle_demand_sum']
        extra['aisle_selector'] = (
            lambda hd, hb: min(hd, key=lambda a: (ads.get(a, 0.0), hd[a])))
        extra['order_key'] = af._score_expected_popularity

    if use_pool:
        pool = af._RankedAssignPool(list(bins), minimize=extra.pop('minimize'),
                                    **{k: v for k, v in common.items()}, **extra)
        return [(u, pool.take(u)[0]) for u in pool.order(units)], pool
    got = af._ranked_assign_impl(units, lambda _u: list(bins), **common, **extra)
    return got, None


def _key(pairs):
    """Placements as reproducible geometry, never id(bin)."""
    return [(id(u), None if b is None else (b.location[0], b.x_phys, b.y_phys))
            for u, b in pairs]


@pytest.mark.parametrize('arm', ARMS)
@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4, 5, 6, 7])
def test_pool_matches_the_impl_exactly(arm, seed):
    """Same order in, same placements out, same manager state — with `==` on the floats."""
    rng = random.Random(1000 + seed)
    bins, units, aff, orders = _wave(rng)
    ids = [b.location[0] for b in bins]
    s1, s2 = _state(ids), _state(ids)
    # Pre-seat a partner so the co-occurrence term is non-zero from the first unit.
    for st in (s1, s2):
        st['aisle_sku_sets'][1].add(2)
        st['aisle_idx_sets'][1].add(aff._sku_to_idx[2])
        st['aisle_demand_sum'][1] = 0.3

    ref, _ = _run(arm, bins, units, aff, orders, s1, seed, use_pool=False)
    got, _ = _run(arm, bins, units, aff, orders, s2, seed, use_pool=True)

    assert _key(got) == _key(ref)
    assert dict(s2['aisle_sku_sets']) == dict(s1['aisle_sku_sets'])
    assert dict(s2['aisle_idx_sets']) == dict(s1['aisle_idx_sets'])
    # EXACT: a running sum whose value encodes the accumulation order.
    assert dict(s2['aisle_demand_sum']) == dict(s1['aisle_demand_sum'])


@pytest.mark.parametrize('arm', ARMS)
def test_the_fixture_is_not_vacuous(arm):
    """Every branch this file claims to cover must actually fire, or the equality above is
    an agreement about nothing."""
    rng = random.Random(7)
    bins, units, aff, orders = _wave(rng)
    st = _state([b.location[0] for b in bins])
    got, _ = _run(arm, bins, units, aff, orders, st, 7, use_pool=True)
    placed = [b for _u, b in got if b is not None]
    assert len(placed) == len(bins), 'not every bin was consumed'
    assert any(b is None for _u, b in got), 'the exhaustion path never ran'
    assert len({b.location[0] for b in placed}) > 1, 'only one aisle was used'
    assert len({u.order.sku for u, _b in got}) > 1, 'only one SKU — no repeat-SKU no-op'
    # Two aisles carry a bin at the same travel cost, so the first-wins tie-break is live.
    wp = _wp()
    dm = af._D_map(bins, af.sec_per_inch(wp.x_speed), af.sec_per_inch(wp.y_speed))
    ds = sorted(dm.values())
    assert any(a == b for a, b in zip(ds, ds[1:])), 'no exact D tie in the fixture'


def test_order_is_the_impls_own_sort():
    """`order` is the priority the impl computed internally — same key, same direction, and
    the same stability, which decides who wins a priority tie."""
    rng = random.Random(11)
    bins, units, aff, orders = _wave(rng)
    st = _state([b.location[0] for b in bins])
    fbi, fbs, qbs = _tables(orders, aff)
    pool = af._RankedAssignPool(
        list(bins), aff, _wp(), st['aisle_sku_sets'], st['aisle_idx_sets'],
        st['aisle_demand_sum'], fbi, fbs, qbs, 1.0, minimize=True)
    got = pool.order(units)
    exp = sorted(units, key=pool._pick_effort_priority, reverse=True)
    assert [id(u) for u in got] == [id(u) for u in exp]
    keys = [pool._pick_effort_priority(u) for u in got]
    assert keys == sorted(keys, reverse=True)
    assert len(set(keys)) < len(keys), 'no priority tie — stability is untested'


def test_order_is_a_request_the_caller_may_decline():
    """The seam itself: a pool serves whatever order it is handed, and every bin it hands
    out is distinct. Placements differ from the sorted order — that is the point — but the
    pool never double-issues a bin or invents one."""
    rng = random.Random(13)
    bins, units, aff, orders = _wave(rng, n_units=6)
    st = _state([b.location[0] for b in bins])
    fbi, fbs, qbs = _tables(orders, aff)
    pool = af._RankedAssignPool(
        list(bins), aff, _wp(), st['aisle_sku_sets'], st['aisle_idx_sets'],
        st['aisle_demand_sum'], fbi, fbs, qbs, 1.0, minimize=True)
    fifo = [pool.take(u)[0] for u in units]              # queue order, NOT pool.order
    assert all(b is not None for b in fifo)
    assert len({id(b) for b in fifo}) == len(fifo), 'a bin was handed out twice'
    assert {id(b) for b in fifo} <= {id(b) for b in bins}


def test_take_returns_the_travel_cost_it_chose_on():
    """The score is the chosen bin's D — the float the aisle argmin compared."""
    rng = random.Random(17)
    bins, units, aff, orders = _wave(rng, n_units=5)
    st = _state([b.location[0] for b in bins])
    fbi, fbs, qbs = _tables(orders, aff)
    wp = _wp()
    pool = af._RankedAssignPool(
        list(bins), aff, wp, st['aisle_sku_sets'], st['aisle_idx_sets'],
        st['aisle_demand_sum'], fbi, fbs, qbs, 1.0, minimize=True)
    D_of = af._D_map(bins, af.sec_per_inch(wp.x_speed), af.sec_per_inch(wp.y_speed))
    n = 0
    for u in units:
        b, score = pool.take(u)
        assert b is not None
        assert score == D_of[id(b)]                       # exact, not approx
        n += 1
    assert n == len(units)


def test_an_exhausted_pool_reports_no_score():
    rng = random.Random(19)
    bins, _units, aff, orders = _wave(rng, n_aisles=1, bins_per_aisle=(1,), n_units=2)
    st = _state([1])
    fbi, fbs, qbs = _tables(orders, aff)
    pool = af._RankedAssignPool(
        list(bins), aff, _wp(), st['aisle_sku_sets'], st['aisle_idx_sets'],
        st['aisle_demand_sum'], fbi, fbs, qbs, 1.0, minimize=True)
    u = _Unit(orders[1])
    assert pool.take(u)[0] is not None
    assert pool.take(u) == (None, None)


def test_two_consecutive_groups_share_state():
    """`all_idx` is snapshotted per pool, so the second group ranks against what the first
    one placed. A port that hoisted it to once-per-drain would pass every single-group test
    above and fail here."""
    rng = random.Random(23)
    bins, units, aff, orders = _wave(rng, n_units=4)
    half = len(bins) // 2
    ids = [b.location[0] for b in bins]
    s1, s2 = _state(ids), _state(ids)
    ref_a, _ = _run('tmin', bins[:half], units[:2], aff, orders, s1, 0, use_pool=False)
    ref_b, _ = _run('tmin', bins[half:], units[2:], aff, orders, s1, 0, use_pool=False)
    got_a, _ = _run('tmin', bins[:half], units[:2], aff, orders, s2, 0, use_pool=True)
    got_b, _ = _run('tmin', bins[half:], units[2:], aff, orders, s2, 0, use_pool=True)
    assert _key(got_a) == _key(ref_a) and _key(got_b) == _key(ref_b)
    assert dict(s2['aisle_demand_sum']) == dict(s1['aisle_demand_sum'])
    assert dict(s2['aisle_idx_sets']) == dict(s1['aisle_idx_sets'])
    assert any(s1['aisle_idx_sets'].values()), 'no index was ever committed'


def test_the_state_is_actually_deep_copied_between_runs():
    """Guard for this file's own harness: if `_state` handed both runs the same dicts, every
    equality above would compare an object with itself."""
    a, b = _state([1]), _state([1])
    a['aisle_demand_sum'][1] = 1.0
    assert dict(b['aisle_demand_sum']) == {}
    assert copy.deepcopy(a) is not a
