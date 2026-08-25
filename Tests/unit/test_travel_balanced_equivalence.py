"""
test_travel_balanced_equivalence.py — the SKU-run-cached `_travel_balanced_impl` selects the
byte-identical placement sequence the original per-unit scan selected.

The ORACLE below is the pre-optimization loop preserved VERBATIM (only renamed): every float
expression, iteration order, strict-< tie-break, and commit is the shipped 2026-08 code. The
production function is then exercised against it on:

  - seeded random waves (multi-unit SKUs, shared and unshared aisles), cart OFF and ON;
  - crafted exact ties (equal expected_labor across SKUs; equal bracket costs within an
    aisle; equal scores across aisles) — the first-seen-wins semantics;
  - aisle exhaustion mid-wave (the (unit, None) spill + a winner's _aisle_best going None);
  - an empty-candidates wave (all None);
  - two Order OBJECTS sharing one SKU (lead-queue double-arrival shape);
  - two CONSECUTIVE waves sharing manager dicts (cross-wave state flows only through them).

Equality is EXACT — including float `==` on every mutated manager sum. That deliberately
tightens the repo's floats-with-tolerance rule: both paths must run the *same arithmetic in
the same order*, so any difference is a behavior change, not rounding (the precedent is
test_placement_fastpath_equivalence.py's exact-equality tiers).

Run: python -m pytest Tests/unit/test_travel_balanced_equivalence.py -q
"""
from __future__ import annotations

import random
from collections import deque

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement.Assignment_Functions import (
    _D_map, _travel_balanced_impl)
from Warehouse.kernel.cost_model import height_multiplier, per_pick, sec_per_inch
from Warehouse.inventory.inventory_common import _wp_for

# ── the frozen oracle: the shipped loop, verbatim (renamed only) ─────────────


def _oracle_travel_balanced_impl(units, candidates_fn, affinity, wp,
                                 aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                                 aisle_pick_load_sum, sku_pick_load_product,
                                 freq_by_sku, qty_by_sku, cart=None):
    wp = _wp_for(wp, units[0]) if units else wp
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)
    intercept = wp.pick_intercept
    brackets = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]

    cart_on = cart is not None
    if cart_on:
        aisle_vol_sum, sku_vol_product, expected_batch_skus, total_freq = cart
        cart_coef = wp.cart_swap_coef
        cap_raw = wp.cart_capacity * total_freq / max(expected_batch_skus, 1e-9)

    D_of = _D_map(cands, x_pace, y_pace)
    M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
    by_aisle: dict[int, dict] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], {}).setdefault(M_of[id(b)], []).append(b)
    for groups in by_aisle.values():
        for m, lst in list(groups.items()):
            lst.sort(key=lambda bb: D_of[id(bb)])
            groups[m] = deque(lst)
    load = {aid: float(aisle_pick_load_sum.get(aid, 0.0)) for aid in by_aisle}
    vol_load = ({aid: float(aisle_vol_sum.get(aid, 0.0)) for aid in by_aisle}
                if cart_on else None)
    sku_to_idx = affinity._sku_to_idx
    result: list = []

    def _cart_cost(v_raw):
        return cart_coef * max(0.0, v_raw / cap_raw - 1.0)

    def _aisle_best(aid, var):
        best = None
        for m, dq in by_aisle[aid].items():
            if not dq:
                continue
            b = dq[0]
            cost = per_pick(m, intercept, var) + D_of[id(b)]
            if best is None or cost < best[0]:
                best = (cost, m, b)
        return best

    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)
        m_s = sku_vol_product.get(sku, 0.0) if cart_on else 0.0
        best_aid = best_choice = None
        best_score = None
        for aid in by_aisle:
            ab = _aisle_best(aid, var)
            if ab is None:
                continue
            score = load[aid] + fq * ab[0]
            if cart_on:
                add = 0.0 if sku in aisle_sku_sets[aid] else m_s
                score += _cart_cost(vol_load[aid] + add)
            if best_score is None or score < best_score:
                best_score, best_aid, best_choice = score, aid, ab
        if best_aid is None:
            result.append((unit, None))
            continue
        cost, m, chosen = best_choice
        load[best_aid] += fq * cost
        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += fq
            aisle_pick_load_sum[best_aid] += sku_pick_load_product.get(sku, 0.0)
            if cart_on:
                vol_load[best_aid] += m_s
                aisle_vol_sum[best_aid] += m_s
        by_aisle[best_aid][m].popleft()
        result.append((unit, chosen))
    return result


# ── lightweight fixtures (no RNG leaks: no Order.__init__, no Aisle build) ───

class _Bin:
    """Duck-typed stand-in: _travel_balanced_impl touches location/x_phys/y_phys/id only."""
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, bay_x, bay_y, x_phys, y_phys):
        self.location = (aid, bay_x, bay_y)
        self.x_phys = x_phys
        self.y_phys = y_phys


class _Order:
    __slots__ = ('sku', 'handle_var', 'expected_labor', 'by_regime')

    def __init__(self, sku, handle_var, expected_labor):
        self.sku = sku
        self.handle_var = handle_var
        self.expected_labor = expected_labor


class _Unit:
    __slots__ = ('order',)

    def __init__(self, order):
        self.order = order


class _Affinity:
    def __init__(self, skus):
        self._sku_to_idx = {s: i for i, s in enumerate(sorted(skus))}


def _wp():
    cfg = PickConfig(num_pickers=4, x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
                     pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0)
    return WorkloadParams.from_pick_config(cfg)   # default brackets: 96/240/inf


def _mk_state(aids):
    from collections import defaultdict
    return {
        'aisle_sku_sets': defaultdict(set, {a: set() for a in aids}),
        'aisle_idx_sets': defaultdict(set, {a: set() for a in aids}),
        'aisle_demand_sum': defaultdict(float),
        'aisle_pick_load_sum': defaultdict(float),
    }


def _rand_wave(rng: random.Random, n_skus, units_per_sku, n_aisles, bins_per_aisle,
               tie_labor=False):
    skus = list(range(1, n_skus + 1))
    orders = {}
    units = []
    for s in skus:
        labor = 5.0 if tie_labor else rng.uniform(0.5, 20.0)
        o = _Order(s, rng.uniform(0.1, 3.0), labor)
        orders[s] = o
        for _ in range(rng.randint(1, units_per_sku)):
            units.append(_Unit(o))
    rng.shuffle(units)                      # queue order the stable sort must respect
    bins = []
    for a in range(1, n_aisles + 1):
        for i in range(bins_per_aisle):
            bins.append(_Bin(a, i + 1, rng.randint(1, 12),
                             float(rng.randint(10, 400)), float(rng.choice(
                                 (40.0, 90.0, 150.0, 260.0)))))
    rng.shuffle(bins)                       # cands order drives insertion-order tie-breaks
    freq = {s: rng.uniform(0.001, 1.0) for s in skus}
    qty = {s: float(rng.randint(1, 20)) for s in skus}
    plp = {s: rng.uniform(0.0, 4.0) for s in skus}
    vol = {s: rng.uniform(100.0, 90_000.0) for s in skus}
    return units, bins, freq, qty, plp, vol


def _pool_as_impl(units, candidates_fn, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                  aisle_demand_sum, aisle_pick_load_sum, sku_pick_load_product,
                  freq_by_sku, qty_by_sku, cart=None):
    """`_TravelBalancedPool` driven with the impl's own signature, so the harness below
    compares three things instead of two.

    The pool is served through `pool.order(units)` — the LPT sort the impl does internally.
    That is deliberate and it is the whole claim: driven in the SAME order, the pool makes
    bit-identical decisions. A drain that later declines that order gets a different (and
    intentionally different) answer, which is not what this file is for."""
    from Warehouse.placement.Assignment_Functions import _TravelBalancedPool
    if not units:
        return []
    pool = _TravelBalancedPool(
        list(candidates_fn(units[0])), affinity, wp, aisle_sku_sets, aisle_idx_sets,
        aisle_demand_sum, aisle_pick_load_sum, sku_pick_load_product,
        freq_by_sku, qty_by_sku, cart=cart)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


def _run_both(units, bins, freq, qty, plp, vol=None, cart_on=False, waves=1,
              vanish_bins_after_wave=True):
    """Run the oracle, the production impl and the POOL on deep-copied state."""
    import copy

    aids = sorted({b.location[0] for b in bins})
    aff = _Affinity([u.order.sku for u in units])
    wp = _wp()
    outs = []
    for impl in (_oracle_travel_balanced_impl, _travel_balanced_impl, _pool_as_impl):
        st = _mk_state(aids)
        avs = {a: 0.0 for a in aids}
        remaining_bins = list(bins)
        seq = []
        for _w in range(waves):
            cart = ((avs, dict(vol), 50.0, sum(freq.values())) if cart_on else None)
            res = impl(list(units), lambda _u: list(remaining_bins), aff, wp,
                       st['aisle_sku_sets'], st['aisle_idx_sets'],
                       st['aisle_demand_sum'], st['aisle_pick_load_sum'],
                       plp, freq, qty, cart=cart)
            seq.append([(id(u), id(b) if b is not None else None) for u, b in res])
            if vanish_bins_after_wave:
                taken = {id(b) for _u, b in res if b is not None}
                remaining_bins = [b for b in remaining_bins if id(b) not in taken]
        outs.append({
            'seq': seq,
            'sku_sets': {a: set(s) for a, s in st['aisle_sku_sets'].items()},
            'idx_sets': {a: set(s) for a, s in st['aisle_idx_sets'].items()},
            'demand': dict(st['aisle_demand_sum']),
            'pick_load': dict(st['aisle_pick_load_sum']),
            'vol_sum': copy.deepcopy(avs),
        })
    return outs


def _assert_equal(*outs):
    a = outs[0]
    for b in outs[1:]:
        _assert_pair(a, b)


def _assert_pair(a, b):
    assert a['seq'] == b['seq'], 'placement sequences diverged'
    assert a['sku_sets'] == b['sku_sets']
    assert a['idx_sets'] == b['idx_sets']
    # exact float equality — same arithmetic path is the claim under test
    assert a['demand'] == b['demand']
    assert a['pick_load'] == b['pick_load']
    assert a['vol_sum'] == b['vol_sum']


# ── the tests ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('cart_on', (False, True), ids=('rank_labor', 'rank_cartlabor'))
@pytest.mark.parametrize('seed', range(8))
def test_random_waves_equivalent(seed, cart_on):
    rng = random.Random(1000 + seed)
    units, bins, freq, qty, plp, vol = _rand_wave(
        rng, n_skus=rng.randint(5, 30), units_per_sku=4,
        n_aisles=rng.randint(2, 8), bins_per_aisle=rng.randint(3, 12))
    a, b, c = _run_both(units, bins, freq, qty, plp, vol, cart_on=cart_on)
    # non-vacuity: something actually got placed
    assert any(x[1] is not None for x in a['seq'][0])
    _assert_equal(a, b, c)


@pytest.mark.parametrize('cart_on', (False, True), ids=('rank_labor', 'rank_cartlabor'))
def test_crafted_exact_ties(cart_on):
    """Equal expected_labor everywhere + equal-cost bins: first-seen must win in both."""
    rng = random.Random(77)
    units, bins, freq, qty, plp, vol = _rand_wave(
        rng, n_skus=10, units_per_sku=3, n_aisles=4, bins_per_aisle=6, tie_labor=True)
    # force exact score ties: identical geometry in every aisle, identical freq/qty
    for b in bins:
        b.x_phys, b.y_phys = 100.0, 90.0
    for s in freq:
        freq[s], qty[s], plp[s], vol[s] = 0.5, 2.0, 1.0, 1000.0
    a, b, c = _run_both(units, bins, freq, qty, plp, vol, cart_on=cart_on)
    placed = sum(1 for x in a['seq'][0] if x[1] is not None)
    assert placed > 0
    _assert_equal(a, b, c)


def test_exhaustion_and_spill():
    """More units than bins: the (unit, None) path and winner-goes-None recompute."""
    rng = random.Random(5)
    units, bins, freq, qty, plp, vol = _rand_wave(
        rng, n_skus=12, units_per_sku=6, n_aisles=2, bins_per_aisle=2)
    a, b, c = _run_both(units, bins, freq, qty, plp, vol)
    spilled = sum(1 for x in a['seq'][0] if x[1] is None)
    placed = sum(1 for x in a['seq'][0] if x[1] is not None)
    assert spilled > 0 and placed > 0, 'fixture must exercise BOTH outcomes'
    _assert_equal(a, b, c)


def test_empty_candidates():
    rng = random.Random(6)
    units, bins, freq, qty, plp, vol = _rand_wave(rng, 4, 2, 2, 3)
    aff = _Affinity([u.order.sku for u in units])
    wp = _wp()
    st_a, st_b = _mk_state([1]), _mk_state([1])
    ra = _oracle_travel_balanced_impl(list(units), lambda _u: [], aff, wp,
                                      st_a['aisle_sku_sets'], st_a['aisle_idx_sets'],
                                      st_a['aisle_demand_sum'], st_a['aisle_pick_load_sum'],
                                      plp, freq, qty)
    rb = _travel_balanced_impl(list(units), lambda _u: [], aff, wp,
                               st_b['aisle_sku_sets'], st_b['aisle_idx_sets'],
                               st_b['aisle_demand_sum'], st_b['aisle_pick_load_sum'],
                               plp, freq, qty)
    st_c = _mk_state([1])
    rc = _pool_as_impl(list(units), lambda _u: [], aff, wp,
                       st_c['aisle_sku_sets'], st_c['aisle_idx_sets'],
                       st_c['aisle_demand_sum'], st_c['aisle_pick_load_sum'],
                       plp, freq, qty)
    key = lambda r: [(id(u), b) for u, b in r]           # noqa: E731
    assert key(ra) == key(rb) == key(rc)
    assert all(b is None for _u, b in ra) and len(ra) == len(units)
    # The impl short-circuits on an empty candidate list; the pool arrives at the same
    # answer through its ordinary path (no aisle has a bin, so every take is (None, None)).
    assert len(rc) == len(units)


def test_two_order_objects_one_sku():
    """Lead-queue double-arrival shape: distinct Order objects, identical SKU values."""
    o1 = _Order(7, 1.25, 9.5)
    o2 = _Order(7, 1.25, 9.5)          # same values, different object
    o3 = _Order(8, 0.75, 9.5)          # equal labor — interleaves within the stable sort
    units = [_Unit(o1), _Unit(o3), _Unit(o2), _Unit(o1)]
    bins = [_Bin(a, i + 1, i + 1, 50.0 * (i + 1), 90.0) for a in (1, 2) for i in range(3)]
    freq = {7: 0.4, 8: 0.6}
    qty = {7: 3.0, 8: 2.0}
    plp = {7: 1.1, 8: 0.9}
    vol = {7: 800.0, 8: 1200.0}
    a, b, c = _run_both(units, bins, freq, qty, plp, vol, cart_on=True)
    assert sum(1 for x in a['seq'][0] if x[1] is not None) == 4
    _assert_equal(a, b, c)


@pytest.mark.parametrize('cart_on', (False, True), ids=('rank_labor', 'rank_cartlabor'))
def test_two_consecutive_waves_shared_state(cart_on):
    """Cross-wave coupling flows only through the manager dicts — both paths must agree
    on wave 2 given wave 1's own commits (and consumed bins removed, as _stock_ranked's
    caller does via _execute_placement)."""
    rng = random.Random(99)
    units, bins, freq, qty, plp, vol = _rand_wave(
        rng, n_skus=14, units_per_sku=3, n_aisles=5, bins_per_aisle=8)
    a, b, c = _run_both(units, bins, freq, qty, plp, vol, cart_on=cart_on, waves=2)
    assert any(x[1] is not None for x in a['seq'][1]), 'wave 2 must place something'
    _assert_equal(a, b, c)
