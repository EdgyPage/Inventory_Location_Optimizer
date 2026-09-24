"""test_sortmatch_pool.py — `rank_sortmatch`: two sorted lists sharing an index.

`build_sortmatch_pool_fn` (Warehouse/placement/Assignment_Functions.py) hands a drain's
packs, most lifetime pick work first, the group's bins in ascending D + hbar M
(`.scratch/placement-sortmatch/`, S02-S06).  What this file pins:

  1. THE TWO LISTS.  `order` sorts packs by the documented key, and successive `take`s
     return the candidates in ascending bin key -- the whole group, every bin exactly
     once -- including across height brackets and with equal keys in different aisles.
  2. THE INDEX IS OPTIMAL WHERE THE ARGUMENT SAYS SO.  When every pack's cost at a bin is
     (pack weight) x (bin key) -- one SKU law for the whole group -- the pairing equals the
     exact assignment optimum (scipy) on random instances, and a shuffled pairing is worse.
  3. hbar IS THE CATALOGUE'S DEMAND-WEIGHTED LINE COST, per regime parameter object.
  4. THE HOOK IS INERT ELSEWHERE: `_RankedAssignPool` without `bin_key` still ranks by D
     (every other arm), and a frozen tier refuses a custom key rather than ignoring it.

Run:  python -m pytest Tests/unit/test_sortmatch_pool.py -q
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.catalog.Demand import Demand
from Warehouse.inventory.aisle_ledger import AisleLedger
from Warehouse.kernel.cost_model import SpeedProfile, height_multiplier
from Warehouse.placement import Assignment_Functions as af

_WP = WorkloadParams()


class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Order:
    __slots__ = ('sku', 'demand', 'handle_var')

    def __init__(self, sku, freq=1.0, rate=2.0, hvar=0.5):
        self.sku, self.demand, self.handle_var = sku, Demand.from_rates(freq, rate), hvar


class _Unit:
    __slots__ = ('order', 'quantity')

    def __init__(self, order, quantity):
        self.order, self.quantity = order, quantity


_AISLES = range(5)


def _books():
    """The three aisle books, one entry per aisle -- the shape the manager's ledger
    hands every ranked builder."""
    return ({a: set() for a in _AISLES}, {a: set() for a in _AISLES},
            {a: 0.0 for a in _AISLES})


def _open(cands, hbar=None, wp=_WP):
    ss, ii, ds = _books()
    ledger = AisleLedger.over(sku_sets=ss, idx_sets=ii, demand_sum=ds)
    aff = SimpleNamespace(_sku_to_idx={})
    fn = af.build_sortmatch_pool_fn(aff, wp, ledger, {}, {}, {},
                                    hbar if hbar is not None else {})
    return fn(cands)


def _keys(wp, hb):
    sp = SpeedProfile(wp.x_speed, wp.y_speed)
    return lambda b: (sp.x_pace * b.x_phys + sp.y_pace * b.y_phys
                      + hb * height_multiplier(wp.height_brackets, b.y_phys))


def _scene(seed, n_bins=30, n_units=12, one_law=False):
    rng = random.Random(seed)
    xs = (0.0, 96.0, 240.0, 480.0)                   # shared columns: cross-aisle ties
    ys = (40.0, 150.0, 300.0)                        # all three height brackets
    bins = [_Bin(rng.choice(_AISLES), rng.choice(xs), rng.choice(ys))
            for _ in range(n_bins)]
    law = _Order(1, rate=3.0, hvar=0.4)
    units = [_Unit(law if one_law else _Order(i + 2, freq=rng.uniform(0.1, 1),
                                              rate=rng.choice((1.0, 3.0, 8.0)),
                                              hvar=rng.uniform(0.1, 2.0)),
                   rng.randint(1, 40)) for i in range(n_units)]
    return bins, units


# ── 1. the two lists ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(6))
def test_takes_walk_the_bins_in_ascending_key_and_exhaust_them(seed):
    bins, units = _scene(seed)
    hb = 21.0
    pool = _open(bins, {id(_WP): hb})
    key = _keys(_WP, hb)
    got = []
    u = units[0]
    while True:
        b, score = pool.take(u)
        if b is None:
            break
        assert score == pytest.approx(key(b))
        got.append(b)
    assert sorted(map(id, got)) == sorted(map(id, bins)), 'every bin exactly once'
    ks = [key(b) for b in got]
    assert ks == sorted(ks), 'the bins must come out cheapest first'


def test_order_is_the_lifetime_work_key_highest_first():
    bins, units = _scene(3)
    pool = _open(bins, {id(_WP): 21.0})
    n = len(bins)
    sp = SpeedProfile(_WP.x_speed, _WP.y_speed)
    d_bar = sum(sp.x_pace * b.x_phys + sp.y_pace * b.y_phys for b in bins) / n
    m_bar = sum(height_multiplier(_WP.height_brackets, b.y_phys) for b in bins) / n

    def want(u):
        eq = u.order.demand.line.mean()
        h = _WP.pick_intercept + eq * (_WP.pick_per_item + u.order.handle_var)
        return max(1.0, u.quantity / eq) * (d_bar + h * m_bar)

    assert [id(u) for u in pool.order(units)] == [
        id(u) for u in sorted(units, key=want, reverse=True)]


# ── 2. the index is optimal where the rearrangement argument says so ───────────────

@pytest.mark.parametrize('seed', range(8))
def test_one_law_pairing_is_the_exact_optimum(seed):
    """With one SKU law, pack weight w_u = visits_u (Dbar + h Mbar) and each pack's cost
    at bin b is visits_u (D_b + h M_b) -- proportional to the bin key when hbar = h -- so
    the index match must reach the assignment optimum."""
    scipy = pytest.importorskip('scipy.optimize')
    import numpy as np
    bins, units = _scene(seed, n_bins=20, n_units=9, one_law=True)
    law = units[0].order
    eq = law.demand.line.mean()
    h = _WP.pick_intercept + eq * (_WP.pick_per_item + law.handle_var)
    pool = _open(bins, {id(_WP): h})
    key = _keys(_WP, h)
    visits = lambda u: max(1.0, u.quantity / eq)
    got = 0.0
    for u in pool.order(units):
        b, _ = pool.take(u)
        got += visits(u) * key(b)
    cost = np.array([[visits(u) * key(b) for b in bins] for u in units])
    r, c = scipy.linear_sum_assignment(cost)
    assert got == pytest.approx(cost[r, c].sum(), rel=1e-12)
    rng = random.Random(seed)
    shuffled = rng.sample(bins, len(units))
    assert got <= sum(visits(u) * key(b) for u, b in zip(units, shuffled)) + 1e-9


# ── 3. hbar ────────────────────────────────────────────────────────────────────────

def test_hbar_is_the_demand_weighted_line_cost():
    orders = [_Order(1, freq=1.0, rate=1.0, hvar=0.2), _Order(2, freq=3.0, rate=8.0, hvar=1.5),
              _Order(3, freq=0.0, rate=2.0, hvar=9.0)]   # f = 0 carries no weight
    got = af.sortmatch_hbar(orders, _WP)
    I, p = _WP.pick_intercept, _WP.pick_per_item
    num = sum(o.demand.relative_frequency
              * (I + o.demand.line.mean() * (p + o.handle_var)) for o in orders)
    den = sum(o.demand.relative_frequency for o in orders)
    assert got == {id(_WP): pytest.approx(num / den)}


# ── 4. the hook is inert elsewhere ─────────────────────────────────────────────────

def test_without_a_bin_key_the_ranked_pool_still_ranks_by_travel():
    bins, units = _scene(5)
    aff = SimpleNamespace(_sku_to_idx={})
    pool = af._RankedAssignPool(bins, aff, _WP, *_books(), {}, {}, {}, 1.0, True,
                                order_key=lambda u: 0.0)
    sp = SpeedProfile(_WP.x_speed, _WP.y_speed)
    d = lambda b: sp.x_pace * b.x_phys + sp.y_pace * b.y_phys
    got = [pool.take(units[0])[0] for _ in bins]
    assert [d(b) for b in got] == sorted(d(b) for b in bins)


def test_a_frozen_tier_refuses_a_custom_bin_key():
    bins, _units = _scene(1)
    tier = af.freeze_tier(bins, _WP)
    from Warehouse.placement.frozen_tier import TierSlice
    with pytest.raises(TypeError, match='bin_key'):
        af._RankedAssignPool(TierSlice(tier, frozenset()), SimpleNamespace(_sku_to_idx={}),
                             _WP, *_books(), {}, {}, {}, 1.0, True,
                             order_key=lambda u: 0.0, bin_key=lambda b: 0.0)


# ── 5. the whole group decides the pairing; the drain decides only who goes first ──

@pytest.mark.parametrize('seed', range(8))
def test_prepared_pairing_under_the_full_order_is_the_greedy_one(seed):
    """Served in its own order, a prepared pool hands out exactly the bins the greedy heap
    does -- the planned global order IS the heap's (key, aisle rank, position)."""
    bins, units = _scene(seed)
    greedy = _open(bins, {id(_WP): 21.0})
    want = [(id(u), id(greedy.take(u)[0])) for u in greedy.order(list(units))]
    planned = _open(bins, {id(_WP): 21.0})
    planned.prepare(units)
    got = [(id(u), id(planned.take(u)[0])) for u in planned.order(list(units))]
    assert got == want


@pytest.mark.parametrize('seed', range(8))
def test_fifo_service_keeps_the_index_match(seed):
    """Under arrival-order service (a `k_cap = 1` queue) each unit still receives the bin
    its KEY rank earns -- the pairing does not degrade to cheapest-bin-to-oldest-pack."""
    bins, units = _scene(seed)
    ref = _open(bins, {id(_WP): 21.0})
    want = {id(u): id(ref.take(u)[0]) for u in ref.order(list(units))}
    pool = _open(bins, {id(_WP): 21.0})
    pool.prepare(units)
    got = {id(u): id(pool.take(u)[0]) for u in units}          # arrival order
    assert got == want


def test_without_prepare_fifo_service_would_have_paired_differently():
    """Non-vacuity for the test above: an UNprepared pool served in arrival order gives
    the oldest pack the cheapest bin, and that differs from the index match on most
    scenes -- so `prepare` is doing work, not restating the greedy."""
    differ = 0
    for seed in range(8):
        bins, units = _scene(seed)
        ref = _open(bins, {id(_WP): 21.0})
        want = {id(u): id(ref.take(u)[0]) for u in ref.order(list(units))}
        greedy = _open(bins, {id(_WP): 21.0})
        differ += {id(u): id(greedy.take(u)[0]) for u in units} != want
    assert differ >= 6, f'only {differ} of 8 scenes separate FIFO-greedy from the match'


def test_the_drain_prepares_a_pool_that_asks_and_only_that_pool(monkeypatch):
    """`_stock_ranked` calls `prepare` when the pool has one; every other pool family has
    no such attribute, which is what keeps them byte-identical."""
    import Warehouse.placement.Assignment_Functions as A
    for cls in (A._RankedAssignPool, A._TravelBalancedPool, A._MinLaborPool,
                A._CoDemandPool, A._OptMapPool):
        assert not hasattr(cls, 'prepare'), f'{cls.__name__} grew a prepare hook'
    assert hasattr(A._SortMatchPool, 'prepare')


def test_a_missing_line_cost_refuses_rather_than_guessing():
    bins, _units = _scene(0)
    with pytest.raises(ValueError, match='mean line cost'):
        _open(bins, {})
