"""test_assignment_and_labor.py

Confirms the labor-cost computation and the rank_* assignment functions behave as
specified.  Run:
    python -m pytest Tests/test_assignment_and_labor.py -v
"""
from __future__ import annotations

import os
import sys
import math
import random
import types
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Carton import Carton
from Pick import PickConfig, _pick_time, height_multiplier, DEFAULT_HEIGHT_BRACKETS
from Storage_Primitive import viable_storage_units
from Assignment_Functions import (
    build_ranked_labor_fn,
    build_ranked_popularity_fn,
    build_ranked_uniform_assignment_fn,
    build_ranked_minlabor_fn,
    build_ranked_maxlabor_fn,
    build_optmap_fn,
)
from Workload import WorkloadParams, aisle_workload, aisle_workload_components
from Simulation_Analytics import expected_task_labor


def _cfg() -> PickConfig:
    return PickConfig(pick_intercept=1.0, pick_weight_coef=1.1,
                      pick_volume_coef=1e-3, cart_swap_coef=10.0)


# ── labor-cost / precomputed attributes ──────────────────────────────────────

def test_labor_cost_matches_pick_time_qty1():
    """carton.labor_cost must equal the sim's per-unit handling charge (_pick_time
    at qty=1, no cart swap) — so the assignment proxy == what the sim actually bills."""
    cfg = _cfg()
    random.seed(0)
    for _ in range(25):
        c = Carton(('conveyable', 'food'))
        lc = c.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef)
        expect = _pick_time(cfg, c.weight, c.volume(), 1, False)
        assert abs(lc - expect) < 1e-9
        assert abs(c.labor_cost - expect) < 1e-9


def test_expected_popularity_and_labor():
    cfg = _cfg(); random.seed(1)
    c = Carton(('conveyable', 'food'))
    c.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef)
    assert abs(c.expected_popularity - c.demand.frequency * c.demand.quantity_rate) < 1e-12
    assert abs(c.expected_labor - c.expected_popularity * c.labor_cost) < 1e-12


def test_total_labor_cost_is_qty_times_labor():
    cfg = _cfg(); random.seed(2)
    c = Carton(('conveyable', 'food'))
    c.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef)
    units = viable_storage_units(c, 5)
    assert units
    for u in units:
        assert abs(u.total_labor_cost - u.quantity * c.labor_cost) < 1e-9


def test_reorder_propagates_labor_cost():
    cfg = _cfg(); random.seed(3)
    c = Carton(('conveyable', 'food'))
    c.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef)
    r = c.reorder()
    assert r.labor_cost > 0
    assert abs(r.labor_cost - c.labor_cost) < 1e-12


# ── synthetic fixtures for the assignment functions ──────────────────────────

class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')
    def __init__(self, aid, x, y=0):
        self.location = (aid,); self.x_phys = x; self.y_phys = y

class _Cart:
    def __init__(self, sku, lc, f, q, handle_var=None):
        self.sku = sku; self.labor_cost = lc; self._f = f; self._q = q
        # per-unit weight/volume term used by the height-aware labor balancer
        self.handle_var = lc if handle_var is None else handle_var
        self.demand = types.SimpleNamespace(frequency=f, quantity_rate=q)
    @property
    def expected_popularity(self): return self._f * self._q
    @property
    def expected_labor(self): return self._f * self._q * self.labor_cost

class _Unit:
    def __init__(self, c): self.carton = c

def _aff(skus):
    return types.SimpleNamespace(_matrix=None, _sku_to_idx={s: i for i, s in enumerate(skus)})

def _aff_csr(skus, lift_pairs=None):
    """Affinity with a real CSR _matrix (rank_minlabor requires one).  lift_pairs is a
    list of (sku_a, sku_b, lift) entries written symmetrically."""
    import numpy as np
    from scipy.sparse import csr_matrix
    idx = {s: i for i, s in enumerate(skus)}
    n = len(skus)
    M = np.zeros((n, n))
    for a, b, v in (lift_pairs or []):
        M[idx[a], idx[b]] = v
        M[idx[b], idx[a]] = v
    return types.SimpleNamespace(_matrix=csr_matrix(M), _sku_to_idx=idx)

def _wp():
    return types.SimpleNamespace(x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
                                 pick_weight_coef=1.1, pick_volume_coef=1e-3)


def test_rank_labor_is_travel_aware():
    """Near aisle (D=1..6) vs far aisle (D=21..26): travel-aware LPT fills the near
    aisle first and only spills to the far one as the near load builds — returning
    specific, distinct bins.  (A travel-blind balancer would split ~4/4.)"""
    bins = [_Bin(1, 1 + i) for i in range(6)] + [_Bin(2, 21 + i) for i in range(6)]
    c = _Cart(100, 10.0, 1.0, 1.0)
    units = [_Unit(c) for _ in range(8)]
    ass, aix, ads, apl = defaultdict(set), defaultdict(set), defaultdict(float), defaultdict(float)
    spl = {100: 1.0 * 1.0 * 10.0}
    fn = build_ranked_labor_fn(_aff([100]), _wp(), ass, aix, ads, apl, spl,
                               {}, {100: 1.0}, {100: 1.0})
    res = fn(units, lambda u: list(bins))
    placed = [b for _, b in res if b is not None]
    aisles = [b.location[0] for b in placed]
    assert all(b is not None for _, b in res)                  # all placed
    assert len({id(b) for b in placed}) == len(placed)         # distinct bins
    assert aisles.count(1) > aisles.count(2)                   # near preferred
    assert aisles.count(2) >= 1                                # but does spill far


def test_rank_popularity_balances_demand():
    """Equal-distance bins in two aisles: popularity balancer splits demand mass
    evenly (greedy LPT on freq·qty)."""
    bins = [_Bin(1, 5), _Bin(1, 5), _Bin(1, 5), _Bin(2, 5), _Bin(2, 5), _Bin(2, 5)]
    carts = [_Cart(i, 1.0, f, 1.0) for i, f in enumerate([4.0, 3.0, 2.0, 1.0])]
    units = [_Unit(c) for c in carts]
    ass, aix, ads = defaultdict(set), defaultdict(set), defaultdict(float)
    fbs = {c.sku: c._f for c in carts}; qbs = {c.sku: 1.0 for c in carts}
    fn = build_ranked_popularity_fn(_aff([c.sku for c in carts]), _wp(), ass, aix, ads,
                                    {}, fbs, qbs)
    res = fn(units, lambda u: list(bins))
    assert all(b is not None for _, b in res)
    assert abs(ads[1] - ads[2]) <= 1.0                         # demand split evenly (5 vs 5)


def test_rank_random_disperses_across_aisles():
    """Random aisle selection spreads placements across multiple aisles (seeded),
    taking a nearest bin within each; bins are distinct."""
    bins = [_Bin(a, x) for a in (1, 2, 3) for x in range(4)]
    carts = [_Cart(i, 2.0, 1.0, 1.0) for i in range(9)]
    units = [_Unit(c) for c in carts]
    ass, aix, ads = defaultdict(set), defaultdict(set), defaultdict(float)
    fbs = {c.sku: 1.0 for c in carts}; qbs = {c.sku: 1.0 for c in carts}
    fn = build_ranked_uniform_assignment_fn(_aff([c.sku for c in carts]), _wp(),
                                            ass, aix, ads, {}, fbs, qbs,
                                            rng=random.Random(0))
    res = fn(units, lambda u: list(bins))
    aisles = {b.location[0] for _, b in res if b is not None}
    ids = [id(b) for _, b in res if b is not None]
    assert len(aisles) >= 2
    assert len(ids) == len(set(ids))


# ── height brackets ──────────────────────────────────────────────────────────

def _wp_h(brackets):
    return types.SimpleNamespace(x_speed=1.0, y_speed=0.5, pick_intercept=0.0,
                                 pick_weight_coef=1.0, pick_volume_coef=0.0,
                                 height_brackets=brackets)


def test_height_multiplier_brackets():
    b = DEFAULT_HEIGHT_BRACKETS   # ((96,1.0),(240,1.2),(inf,1.4))
    assert height_multiplier(b, 0) == 1.0
    assert height_multiplier(b, 95.9) == 1.0
    assert height_multiplier(b, 96.0) == 1.2
    assert height_multiplier(b, 239.0) == 1.2
    assert height_multiplier(b, 240.0) == 1.4
    assert height_multiplier(b, 9999.0) == 1.4


def test_pick_time_height_scaling():
    cfg = PickConfig(pick_intercept=2.0, pick_weight_coef=0.5, pick_volume_coef=0.1,
                     cart_swap_coef=10.0, height_brackets=((96.0, 1.0), (float('inf'), 2.0)))
    var = 0.5 * math.log(20) + 0.1 * math.log(27000)
    ground = _pick_time(cfg, 20, 27000, 3, False, 10.0)    # mult 1.0
    high   = _pick_time(cfg, 20, 27000, 3, False, 300.0)   # mult 2.0
    # height scales the ENTIRE at-location pick: M*(intercept + qty*var)
    assert abs(ground - 1.0 * (2.0 + 3 * var)) < 1e-9
    assert abs(high   - 2.0 * (2.0 + 3 * var)) < 1e-9
    # the whole pick (intercept + handling) is height-scaled (cart stays flat)
    assert abs((high - ground) - (2.0 - 1.0) * (2.0 + 3 * var)) < 1e-9


def test_aisle_workload_matches_pick_time_with_height():
    """Analytical W (P term) must equal the sim's Σ _pick_time handling, incl. height."""
    cfg = PickConfig(pick_intercept=2.0, pick_weight_coef=0.5, pick_volume_coef=0.1,
                     cart_swap_coef=10.0, height_brackets=((96.0, 1.0), (float('inf'), 2.0)))
    wp = WorkloadParams.from_pick_config(cfg)
    stops = [(20, 27000, 3, 10.0), (20, 27000, 2, 300.0)]   # (w, v, qty, y_phys)
    W = aisle_workload(0, 0, 1, stops, wp)                  # D=0, C=0 → W == Σ handling
    expect = sum(_pick_time(cfg, w, v, q, False, y) for (w, v, q, y) in stops)
    assert abs(W - expect) < 1e-9


def test_rank_labor_height_vs_travel_tradeoff():
    """A costly (high handle_var) SKU prefers a LOW ground bin even if farther; a cheap
    SKU tolerates a high bin to save travel."""
    brackets = ((96.0, 1.0), (float('inf'), 1.9))

    def place(handle_var):
        bin_low  = _Bin(1, 200, 10)    # far (high D), ground (mult 1.0)
        bin_high = _Bin(1, 10, 300)    # near (low D), high (mult 1.9)
        c = _Cart(7, 1.0, 1.0, 1.0, handle_var=handle_var)
        ass, aix, ads, apl = (defaultdict(set), defaultdict(set),
                              defaultdict(float), defaultdict(float))
        fn = build_ranked_labor_fn(_aff([7]), _wp_h(brackets), ass, aix, ads, apl,
                                   {7: 0.0}, {}, {7: 1.0}, {7: 1.0})
        res = fn([_Unit(c)], lambda u: [bin_low, bin_high])
        return res[0][1]

    assert place(100.0).y_phys == 10     # costly → ground bin
    assert place(1.0).y_phys == 300      # cheap → near (high) bin


# ── rank_minlabor (objective minimiser) ──────────────────────────────────────

def test_minlabor_picks_golden_zone_and_front_bin():
    """The minimiser places a high-handle_var SKU in the LOW (golden-zone) and NEAR
    (front) bin — not the high bin (handling penalty) nor the far bin (travel)."""
    brackets = ((96.0, 1.0), (float('inf'), 1.9))
    low_near  = _Bin(1, 10, 10)     # ground (mult 1.0), near
    low_far   = _Bin(1, 200, 10)    # ground, far
    high_near = _Bin(1, 10, 300)    # high (mult 1.9), near
    c = _Cart(100, 10.0, 1.0, 1.0, handle_var=10.0)
    aff = _aff_csr([100])
    ass, aix, ads, amp = (defaultdict(set), defaultdict(set),
                          defaultdict(float), defaultdict(list))
    fn = build_ranked_minlabor_fn(aff, _wp_h(brackets), ass, aix, ads, amp,
                                  {}, {100: 1.0}, {100: 1.0})
    res = fn([_Unit(c)], lambda u: [high_near, low_far, low_near])
    b = res[0][1]
    assert b is not None
    assert (b.x_phys, b.y_phys) == (10, 10)        # low AND near


def test_maxlabor_picks_high_far_bin():
    """The maximiser (sanity control) is the exact mirror: it puts a high-handle_var SKU in
    the WORST bin — far AND high — the opposite of the minimiser's golden-zone/front choice."""
    brackets = ((96.0, 1.0), (float('inf'), 1.9))
    near_low = _Bin(1, 10, 10)      # near, ground  -> minimiser's pick
    far_high = _Bin(1, 200, 300)    # far, high      -> maximiser's pick
    c = _Cart(100, 10.0, 1.0, 1.0, handle_var=10.0)
    aff = _aff_csr([100])
    ass, aix, ads, amp = (defaultdict(set), defaultdict(set),
                          defaultdict(float), defaultdict(list))
    fn = build_ranked_maxlabor_fn(aff, _wp_h(brackets), ass, aix, ads, amp,
                                  {}, {100: 1.0}, {100: 1.0})
    res = fn([_Unit(c)], lambda u: [near_low, far_high])
    b = res[0][1]
    assert b is not None
    assert (b.x_phys, b.y_phys) == (200, 300)      # far AND high (worst)


def test_minlabor_compacts_codemanded_into_one_aisle():
    """Two equal-geometry aisles; two strongly co-demanded SKUs.  The affinity reward
    pulls the second SKU into the SAME aisle as the first (consolidation), vs the
    balancer which would disperse them."""
    brackets = ((float('inf'), 1.0),)              # flat height → isolate affinity
    bins = [_Bin(1, 10, 0), _Bin(1, 20, 0), _Bin(2, 10, 0), _Bin(2, 20, 0)]
    c1 = _Cart(1, 5.0, 2.0, 1.0, handle_var=5.0)   # higher expected_labor → placed first
    c2 = _Cart(2, 1.0, 1.0, 1.0, handle_var=1.0)
    aff = _aff_csr([1, 2], [(1, 2, 3.0)])
    idx = aff._sku_to_idx
    freq_by_idx = {idx[1]: 2.0, idx[2]: 1.0}
    ass, aix, ads, amp = (defaultdict(set), defaultdict(set),
                          defaultdict(float), defaultdict(list))
    fn = build_ranked_minlabor_fn(aff, _wp_h(brackets), ass, aix, ads, amp,
                                  freq_by_idx, {1: 2.0, 2: 1.0}, {1: 1.0, 2: 1.0}, beta=100.0)
    res = fn([_Unit(c1), _Unit(c2)], lambda u: list(bins))
    placed = [b for _, b in res]
    assert all(b is not None for b in placed)
    assert placed[0].location[0] == placed[1].location[0]    # same aisle


# ── analytical objective evaluator ───────────────────────────────────────────

def test_workload_components_handling_matches_pick_time():
    """aisle_workload_components' P (handling) term equals Σ _pick_time handling,
    incl. the height multiplier — the decomposition expected_task_labor reports."""
    cfg = PickConfig(pick_intercept=2.0, pick_weight_coef=0.5, pick_volume_coef=0.1,
                     cart_swap_coef=10.0, height_brackets=((96.0, 1.0), (float('inf'), 2.0)))
    wp = WorkloadParams.from_pick_config(cfg)
    lines = [(20, 27000, 3, 10.0), (20, 27000, 2, 300.0)]   # (w, v, qty, y_phys)
    D, P, C = aisle_workload_components(0, 0, 1, lines, wp)
    expect = sum(_pick_time(cfg, w, v, q, False, y) for (w, v, q, y) in lines)
    assert abs(P - expect) < 1e-9
    assert D == 0.0 and C == 0.0


def test_expected_task_labor_objective_and_split():
    """expected_task_labor averages W per task and splits handling vs travel(+cart)."""
    cfg = PickConfig(pick_intercept=1.0, pick_weight_coef=0.5, pick_volume_coef=0.0,
                     cart_swap_coef=0.0, height_brackets=((float('inf'), 1.0),))
    wp = WorkloadParams.from_pick_config(cfg)

    def _bin(sku, w, v, y):
        cart = types.SimpleNamespace(weight=w, volume=lambda _v=v: _v, sku=sku)
        return types.SimpleNamespace(storage=types.SimpleNamespace(carton=cart), y_phys=y)

    t = types.SimpleNamespace(path=[_bin(1, 20, 100, 0.0)], items={1: 2},
                              x_traversed=4, y_traversed=0, carts_required=1)
    res = expected_task_labor([t], wp)
    P = 1.0 + 2 * 0.5 * math.log(20)               # intercept + qty*weight_coef*ln(w)
    assert abs(res['handling'] - P) < 1e-9
    assert abs(res['travel'] - 4.0) < 1e-9          # D = x_traversed * x_speed (1.0)
    assert abs(res['objective'] - (P + 4.0)) < 1e-9
    assert res['n_tasks'] == 1


# ── optimal-map: minimal-work floor + score-matched reloading ────────────────

def _mk_wh_mgr(seed=0):
    """Small two-aisle warehouse + manager (mirrors test_placement_lifecycle)."""
    from Aisle_Storage import Aisle
    from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
    from Inventory_Management import Inventory_Manager
    Aisle.next_aisle_id = 1
    random.seed(seed)
    W, H = 5 * 48, 4 * 48
    cfg = WarehouseConfig(
        total_aisles=2, aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet',    W, H, ['small'], None),
            AisleConfig('conveyable', 'food', 'singleton', W, H, ['small'], None),
        ])
    wh = Warehouse_Builder().from_config(cfg).build()
    return wh, Inventory_Manager(wh)


def _mk_carton(sku, f=0.8, q=3.0, weight=5, dims=(8, 8, 6), eq=12):
    from Carton import StorageHandleConfig
    from Demand import Demand
    c = object.__new__(Carton)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height = dims
    c.weight = weight
    c.demand = Demand.from_rates(f, q)
    c.equilibrium_qty = eq
    c.reorder_point = max(1, eq // 2)
    c.lead_time_mean = 0.0
    c.expected_batch_demand = f * q
    return c


def _wp_work():
    return WorkloadParams(x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
                          pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0,
                          height_brackets=((96.0, 1.0), (float('inf'), 1.9)))


def _layout_work(mgr, freq, qty, wp):
    """Analytical work of the manager's CURRENT occupied layout (per-bin convention)."""
    br = wp.height_brackets
    def M(y):
        for thr, m in br:
            if y < thr:
                return m
        return br[-1][1] if br else 1.0
    tot = 0.0
    for b in mgr._unavailable.values():
        st = b.storage
        if st is None:
            continue
        c = st.carton
        v = wp.pick_weight_coef * math.log(max(c.weight, 1)) + wp.pick_volume_coef * math.log(max(c.volume(), 1))
        D = wp.x_speed * b.x_phys + wp.y_speed * b.y_phys
        f = freq.get(c.sku, 0.0); qq = qty.get(c.sku, 0.0)
        # height scales the whole pick: f*D + f*M*(intercept + q*v)
        tot += f * D + f * M(b.y_phys) * (wp.pick_intercept + qq * v)
    return tot


def test_optimal_work_is_a_floor_below_uniform():
    wh, mgr = _mk_wh_mgr()
    cartons = [_mk_carton(i, f=0.3 + 0.1 * i, weight=3 + 4 * i) for i in range(1, 6)]
    wp = _wp_work()
    freq = {c.sku: c.demand.frequency for c in cartons}
    qty  = {c.sku: c.demand.quantity_rate for c in cartons}
    w_opt = mgr.optimal_work(cartons, freq, qty, wp)        # pure compute, no mutation
    assert w_opt > 0 and math.isfinite(w_opt)
    mgr.enqueue_all(cartons)                                # uniform random layout
    w_uniform = _layout_work(mgr, freq, qty, wp)
    assert w_opt <= w_uniform + 1e-6                        # the optimum is a floor


def test_build_optimal_map_basis_is_quantity_free():
    wh, mgr = _mk_wh_mgr()
    cartons = [_mk_carton(i, f=0.3 + 0.1 * i, weight=3 + 5 * i) for i in range(1, 6)]
    wp = _wp_work()
    freq = {c.sku: c.demand.frequency for c in cartons}
    qty  = {c.sku: c.demand.quantity_rate for c in cartons}
    mgr.build_optimal_map(cartons, freq, qty, wp)
    assert len(mgr._bin_pref) == len(wh.bins)               # pref for every bin
    assert mgr._map_target                                  # some SKUs got a target
    pref_before = dict(mgr._bin_pref)
    # double every SKU's pick quantity — the bin basis must NOT change (quantity-free)
    qty2 = {k: v * 2 for k, v in qty.items()}
    mgr.build_optimal_map(cartons, freq, qty2, wp)
    assert mgr._bin_pref == pref_before


def test_optmap_placement_is_score_matched_not_greedy():
    """A unit is placed in the bin whose pref matches its target — NOT greedily the
    best (lowest-pref) bin."""
    prime = _Bin(1, 0, 0); mid = _Bin(1, 50, 0); far = _Bin(1, 100, 0)
    mgr = types.SimpleNamespace(
        _bin_pref={id(prime): 1.0, id(mid): 5.0, id(far): 9.0},
        _map_target={7: 5.0})                              # SKU 7 belongs at the mid tier
    fn = build_optmap_fn(mgr)
    chosen = fn(_Unit(_Cart(7, 1.0, 1.0, 1.0)), [prime, mid, far])
    assert chosen is mid                                    # matched tier, not the prime bin
    # an unknown SKU (no target) falls back to the prime (lowest-pref) bin
    chosen2 = fn(_Unit(_Cart(99, 1.0, 1.0, 1.0)), [far, mid, prime])
    assert chosen2 is prime


def test_optmap_capped_saves_prime_spots():
    """map_rank (capped) refuses a prime bin when the SKU's own tier is full — it takes the
    worse bin, leaving the prime one for a higher-ranked SKU.  Plain map (symmetric) grabs it."""
    prime = _Bin(1, 0, 0); bad = _Bin(1, 120, 0)           # pref 1.0 (prime) vs 12.0 (bad)
    mgr = types.SimpleNamespace(
        _bin_pref={id(prime): 1.0, id(bad): 12.0},
        _map_target={7: 5.0})                              # SKU 7 belongs at the mid tier (5.0)
    cand = [prime, bad]
    # symmetric map: |5-1|=4 < |5-12|=7 → grabs the PRIME bin (the failure mode)
    assert build_optmap_fn(mgr, capped=False)(_Unit(_Cart(7, 1, 1, 1)), cand) is prime
    # capped map_rank: prime (1.0 < target 5.0) is off-limits → takes the worse bin, saving prime
    assert build_optmap_fn(mgr, capped=True)(_Unit(_Cart(7, 1, 1, 1)), cand) is bad
    # but when its own tier IS free, capped still takes it (no needless degradation)
    tier = _Bin(1, 50, 0)                                  # pref 5.0 == target
    mgr._bin_pref[id(tier)] = 5.0
    assert build_optmap_fn(mgr, capped=True)(_Unit(_Cart(7, 1, 1, 1)), [prime, tier, bad]) is tier
    # unknown SKU under capped: don't waste a prime bin → least-prime
    assert build_optmap_fn(mgr, capped=True)(_Unit(_Cart(99, 1, 1, 1)), [prime, bad]) is bad


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
