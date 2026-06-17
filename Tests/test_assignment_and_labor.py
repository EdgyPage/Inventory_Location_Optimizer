"""test_assignment_and_labor.py

Confirms the labor-cost computation and the rank_* assignment functions behave as
specified.  Run:
    python -m pytest Tests/test_assignment_and_labor.py -v
"""
from __future__ import annotations

import os
import sys
import random
import types
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Carton import Carton
from Pick import PickConfig, _pick_time
from Storage_Primitive import viable_storage_units
from Assignment_Functions import (
    build_ranked_labor_fn,
    build_ranked_popularity_fn,
    build_ranked_uniform_assignment_fn,
)


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
    def __init__(self, sku, lc, f, q):
        self.sku = sku; self.labor_cost = lc; self._f = f; self._q = q
        self.demand = types.SimpleNamespace(frequency=f, quantity_rate=q)
    @property
    def expected_popularity(self): return self._f * self._q
    @property
    def expected_labor(self): return self._f * self._q * self.labor_cost

class _Unit:
    def __init__(self, c): self.carton = c

def _aff(skus):
    return types.SimpleNamespace(_matrix=None, _sku_to_idx={s: i for i, s in enumerate(skus)})

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


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
