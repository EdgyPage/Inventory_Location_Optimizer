"""The expected-travel closed form (`Optimization/simconfig/expected_travel.py`) against
hand computations on a toy warehouse.

The module reproduces the simulator's charging rules as an expectation; these tests pin the
identities the rules guarantee (two-way x travel is x_max, one-way x travel is the aisle
length, a put priced at one known bin is `put_cost`), the demand model's moments, the
seam's two adapters, and the fixed point's balance (.scratch/department-calibration,
"Derive the expected-travel closed form").
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from Optimization.simconfig import expected_travel as et
from Warehouse.catalog.Order import Order
from Warehouse.kernel.cost_model import (SpeedProfile, handle_var, height_multiplier,
                                         per_pick, sec_per_inch)
from Warehouse.layout.Storage_Primitive import StoreCart
from Warehouse.operations.putaway import PutawayCost, put_cost
from Warehouse.picking.Pick import PickConfig


# ── fixtures ────────────────────────────────────────────────────────────────────────────

KEY = ('conveyable', 'food', 'medium', 'pallet')


def _geometry(n_aisles=1, C=4, R=3, key=KEY):
    rows = [dict(aisle_id=i + 1, handling_type=key[0], category=key[1], storage_size=key[2],
                 unit_type=key[3], bay_x=C, bay_y=R) for i in range(n_aisles)]
    return et.Geometry.from_layout_rows(rows)


def _order(sku, freq, qty_rate, weight=10, dims=(10, 10, 10)):
    return Order.from_rates(sku, freq, qty_rate, weight=weight, dims=dims) \
        if hasattr(Order, 'from_rates') else _order_manual(sku, freq, qty_rate, weight, dims)


def _order_manual(sku, freq, qty_rate, weight, dims):
    from Warehouse.catalog.Demand import Demand
    c = object.__new__(Order)
    c._sku = sku
    c.length, c.width, c.height = dims
    c.weight = weight
    c.demand = Demand.from_rates(freq, qty_rate)
    c.storage_handle_config = type('SHC', (), {'handling': KEY[0], 'category': KEY[1]})()
    return c


@pytest.fixture
def cfg():
    return PickConfig(x_speed=2.0, y_speed=1.0, pick_intercept=10.0,
                      pick_per_item=0.5, cart_swap_coef=100.0, cart=StoreCart)


# ── geometry ────────────────────────────────────────────────────────────────────────────

def test_geometry_positions_match_the_bin_centre_rule():
    g = _geometry(C=4, R=3)
    a = g.aisles[0]
    assert a.x_step == 48 and a.y_step == 24                # a medium pallet aisle
    assert a.x_of(1) == 24 and a.x_of(4) == 3 * 48 + 24     # (bayX-1)*step + step//2
    assert a.y_of(1) == 12 and a.y_of(3) == 2 * 24 + 12
    assert a.length == 4 * 48
    assert g.class_bins(KEY) == 12


def test_class_means_are_uniform_over_every_bin_of_the_class():
    g = _geometry(n_aisles=2, C=2, R=2)
    xp, yp = sec_per_inch(2.0), sec_per_inch(1.0)
    a = g.aisles[0]
    xs = [a.x_of(1), a.x_of(2)]; ys = [a.y_of(1), a.y_of(2)]
    expect = np.mean(xs) * xp + np.mean(ys) * yp
    assert math.isclose(g.class_mean_travel(KEY, xp, yp), expect, rel_tol=1e-12)
    assert g.class_mean_height_mult(KEY, ((96.0, 1.0), (float('inf'), 2.0))) == 1.0


# ── the demand model ────────────────────────────────────────────────────────────────────

def test_units_per_line_is_the_mean_of_max_one_poisson():
    g = _geometry()
    lam = 3.0
    c = _order_manual(1, 0.5, lam, 10, (10, 10, 10))
    dist = et.PlacementDist.initial({1: [(1, 1, 1, 100)]}, g)
    r = et.accumulate([c], PickConfig(pick_intercept=1.0), dist, g)
    assert math.isclose(r.units_per_line, lam + math.exp(-lam), rel_tol=1e-9)
    assert math.isclose(r.visits_per_line, 1.0)


def test_a_second_bin_is_reached_with_the_poisson_tail_past_the_first_bins_stock():
    g = _geometry()
    lam = 5.0
    c = _order_manual(1, 1.0, lam, 10, (10, 10, 10))
    dist = et.PlacementDist.initial({1: [(1, 1, 1, 2), (1, 2, 1, 100)]}, g)
    r = et.accumulate([c], PickConfig(pick_intercept=1.0), dist, g)
    p_gt2 = 1.0 - math.exp(-lam) * (1 + lam + lam ** 2 / 2)     # P(X > 2)
    assert math.isclose(r.visits_per_line, 1.0 + p_gt2, rel_tol=1e-9)
    # the two rates are the SKU's whole line share split by reach
    assert math.isclose(float(r.aisle_rates[1][0, 0]), 1.0)
    assert math.isclose(float(r.aisle_rates[1][1, 0]), p_gt2, rel_tol=1e-9)


def test_handling_is_the_catalogue_priced_at_the_bins_height(cfg):
    g = _geometry(C=1, R=1)
    lam = 4.0
    c = _order_manual(1, 1.0, lam, 20, (10, 10, 10))
    dist = et.PlacementDist.initial({1: [(1, 1, 1, 1000)]}, g)
    r = et.accumulate([c], cfg, dist, g)
    eu = lam + math.exp(-lam)
    var = handle_var(20, 1000, cfg.pick_weight_coef, cfg.pick_volume_coef,
                     cfg.pick_weight_fn, cfg.pick_volume_fn)
    M = height_multiplier(cfg.height_brackets, g.aisles[0].y_of(1))
    assert math.isclose(r.handling_s_per_line,
                        per_pick(M, cfg.pick_intercept, var, eu, cfg.pick_per_item), rel_tol=1e-9)


# ── the routing identities ──────────────────────────────────────────────────────────────

def test_a_certain_single_visit_travels_to_the_bin_and_back_only_on_one_way(cfg):
    g = _geometry(C=4, R=3)
    a = g.aisles[0]
    m = np.zeros((4, 3)); m[2, 1] = 60.0            # bin (col 3, row 2), visited surely
    p, x, y = et._aisle_routing(m, a, one_way=False)
    assert math.isclose(p, 1.0, abs_tol=1e-12)
    assert math.isclose(x, a.x_of(3), rel_tol=1e-9)           # two-way: x_max
    assert math.isclose(y, a.y_of(2), rel_tol=1e-9)           # up to the bin
    p, x, y = et._aisle_routing(m, a, one_way=True)
    assert math.isclose(x, a.length, rel_tol=1e-9)            # one-way: the far end
    assert math.isclose(y, 2 * a.y_of(2), rel_tol=1e-9)       # ... and back down


def test_two_certain_visits_walk_x_max_and_the_y_span_toward_the_nearer_end():
    g = _geometry(C=4, R=3)
    a = g.aisles[0]
    m = np.zeros((4, 3)); m[0, 2] = 60.0; m[3, 0] = 60.0     # (col1,row3) then (col4,row1)
    p, x, y = et._aisle_routing(m, a, one_way=False)
    assert math.isclose(x, a.x_of(4), rel_tol=1e-9)
    assert math.isclose(y, a.y_of(3) + (a.y_of(3) - a.y_of(1)), rel_tol=1e-9)


def test_an_unvisited_aisle_costs_nothing_and_a_rarely_visited_one_costs_its_visit():
    g = _geometry(C=2, R=1)
    a = g.aisles[0]
    assert et._aisle_routing(np.zeros((2, 1)), a, False) == (0.0, 0.0, 0.0)
    m = np.zeros((2, 1)); m[1, 0] = 0.01
    p, x, y = et._aisle_routing(m, a, False)
    assert math.isclose(p, 1 - math.exp(-0.01), rel_tol=1e-9)
    assert math.isclose(x, p * a.x_of(2), rel_tol=1e-9)


def test_averaging_over_the_line_count_lowers_the_task_count(cfg):
    g = _geometry(n_aisles=3, C=5, R=2)
    orders = [_order_manual(i, 1.0, 2.0, 10, (10, 10, 10)) for i in range(1, 31)]
    bin_map = {i: [((i - 1) % 3 + 1, (i - 1) % 5 + 1, (i - 1) % 2 + 1, 50)] for i in range(1, 31)}
    dist = et.PlacementDist.initial(bin_map, g)
    r = et.accumulate(orders, cfg, dist, g)
    fixed = et.routing(r, g, 6.0, 0.0, False)['tasks']
    spread = et.routing(r, g, 6.0, 0.5, False)['tasks']
    assert 0 < spread < fixed          # Jensen: the visit count is concave in N


# ── the seam: initial vs uniform ────────────────────────────────────────────────────────

def test_uniform_smears_one_class_evenly_and_one_bin_classes_agree_with_initial(cfg):
    g = _geometry(C=1, R=1)                 # a class of exactly one bin
    c = _order_manual(1, 1.0, 3.0, 10, (10, 10, 10))
    unit = type('U', (), {'quantity': 40, 'storage_size': 'medium', 'unit_category': 'pallet',
                          'order': c})()
    uni = et.accumulate([c], cfg, et.PlacementDist.uniform({1: [unit]}), g)
    ini = et.accumulate([c], cfg, et.PlacementDist.initial({1: [(1, 1, 1, 40)]}, g), g)
    assert uni.kind == 'uniform' and ini.kind == 'initial'
    assert math.isclose(uni.class_rates[KEY], 1.0)
    a = et.expected_pick(uni, g, cfg, 5.0, 0.0)
    b = et.expected_pick(ini, g, cfg, 5.0, 0.0)
    for k in ('units', 'visits', 'tasks', 'travel_x_s', 'travel_y_s', 'handling_s', 's_pick'):
        assert math.isclose(a[k], b[k], rel_tol=1e-9), k


def test_a_sku_with_nothing_placed_is_counted_not_priced(cfg):
    g = _geometry()
    orders = [_order_manual(1, 1.0, 2.0, 10, (10, 10, 10)), _order_manual(2, 1.0, 2.0, 10, (10, 10, 10))]
    r = et.accumulate(orders, cfg, et.PlacementDist.initial({1: [(1, 1, 1, 10)]}, g), g)
    assert r.unplaced_skus == 1
    assert math.isclose(r.visits_per_line, 0.5)


# ── swaps, the day and the fixed point ──────────────────────────────────────────────────

def test_swaps_are_the_renewal_count_and_never_below_one_per_cart_of_volume():
    assert et.expected_swaps(0.0, 1.0, 1.0, 100.0) == 0.0
    # constant per-visit volume v: E[v²] = v², denominator = cap - v/2
    v = 10.0
    assert math.isclose(et.expected_swaps(1000.0, v, v * v, 100.0), 1000.0 / (100.0 - v / 2))


def test_expected_pick_sums_the_four_terms_over_the_expected_units(cfg):
    g = _geometry(C=2, R=1)
    c = _order_manual(1, 1.0, 3.0, 10, (10, 10, 10))
    r = et.accumulate([c], cfg, et.PlacementDist.initial({1: [(1, 2, 1, 1000)]}, g), g)
    d = et.expected_pick(r, g, cfg, 10.0, 0.0)
    assert math.isclose(d['total_s'], d['travel_x_s'] + d['travel_y_s'] + d['swap_s'] + d['handling_s'])
    assert math.isclose(d['s_pick'], d['total_s'] / d['units'])
    assert math.isclose(d['units'], 10.0 * (3.0 + math.exp(-3.0)))
    assert math.isclose(d['travel_x_s'], (1 - math.exp(-10.0)) * g.aisles[0].x_of(2) * sec_per_inch(2.0), rel_tol=1e-9)


def test_solve_n_balances_the_capacity_and_clamps_when_saturated(cfg):
    g = _geometry(n_aisles=2, C=3, R=2)
    orders = [_order_manual(i, 1.0, 2.0, 10, (10, 10, 10)) for i in range(1, 13)]
    bin_map = {i: [((i - 1) % 2 + 1, (i - 1) % 3 + 1, (i - 1) % 2 + 1, 500)] for i in range(1, 13)}
    r = et.accumulate(orders, cfg, et.PlacementDist.initial(bin_map, g), g)
    cap = 150.0
    d = et.solve_n(r, g, cfg, 0.0, capacity_s=cap, n_max=12)
    assert not d['saturated']
    assert math.isclose(d['total_s'], cap, rel_tol=1e-5)
    assert math.isclose(d['s_pick'] * d['units'], cap, rel_tol=1e-5)
    big = et.solve_n(r, g, cfg, 0.0, capacity_s=1e9, n_max=12)
    assert big['saturated'] and math.isclose(big['lines'], 12.0)


# ── put-away ────────────────────────────────────────────────────────────────────────────

def test_a_put_priced_at_one_known_bin_is_put_cost_exactly(cfg):
    g = _geometry(C=3, R=2)
    c = _order_manual(7, 1.0, 2.0, 30, (12, 12, 12))
    dist = et.PlacementDist.initial({7: [(1, 3, 2, 8)]}, g)
    pc = PutawayCost.from_pick(cfg)
    speed = SpeedProfile(2.0, 4.0)
    unit = type('U', (), {'quantity': 8, 'storage_size': 'medium', 'unit_category': 'pallet',
                          'order': c})()
    travel, M = et.put_site_pricer(g, dist, pc, speed)(unit)
    a = g.aisles[0]
    var = handle_var(30, c.volume(), pc.weight_coef, pc.volume_coef, pc.weight_fn, pc.volume_fn)
    priced = travel + per_pick(M, pc.intercept, var, 8, pc.per_item)
    assert math.isclose(priced, put_cost(a.x_of(3), a.y_of(2), 30, c.volume(), 8, speed, pc), rel_tol=1e-12)
