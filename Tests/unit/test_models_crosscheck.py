"""The composed models against the simulator code and the hand computations already pinned
elsewhere (the plan's M3 cross-checks): the pick model reproduces `test_expected_travel`'s
routing identity under both drain orders, LEVELS' line mean is the chain's units per line, and
the trailer law counts the trailers the real loader (`Inbound.transit.TrailerTransit`) opens."""
from __future__ import annotations

import math
import random

import pytest

from Inbound.transit import TrailerTransit
from Optimization.simconfig import expected_travel as et
from Optimization.simconfig.models import inbound, levels, pick
from Warehouse.kernel.cost_model import sec_per_inch
from Warehouse.layout.Storage_Primitive import StoreCart
from Warehouse.picking.Pick import PickConfig

KEY = ('conveyable', 'food', 'medium', 'pallet')


def _geometry(C=4, R=1):
    rows = [dict(aisle_id=1, handling_type=KEY[0], category=KEY[1], storage_size=KEY[2],
                 unit_type=KEY[3], bay_x=C, bay_y=R)]
    return et.Geometry.from_layout_rows(rows)


def _order(sku, freq, qty_rate):
    from Warehouse.catalog.Demand import Demand
    from Warehouse.catalog.Order import Order
    c = object.__new__(Order)
    c._sku = sku
    c.length, c.width, c.height = (10, 10, 10)
    c.weight = 10
    c.demand = Demand.from_rates(freq, qty_rate)
    c.storage_handle_config = type('SHC', (), {'handling': KEY[0], 'category': KEY[1]})()
    return c


@pytest.fixture
def cfg():
    return PickConfig(x_speed=2.0, y_speed=1.0, pick_intercept=10.0,
                      pick_per_item=0.5, cart_swap_coef=100.0, cart=StoreCart)


def test_pick_day_reproduces_the_pinned_routing_identity(cfg):
    # test_expected_travel: one certain-ish SKU at column 2 walks (1 - e^-n) x_of(2) in x
    g = _geometry(C=2)
    c = _order(1, 1.0, 3.0)
    d = pick.pick_day([c], cfg, {1: [(1, 2, 1, 1000)]}, g, 10.0)
    assert math.isclose(d['travel_x_s'],
                        (1 - math.exp(-10.0)) * g.aisles[0].x_of(2) * sec_per_inch(2.0),
                        rel_tol=1e-9)
    # LEVELS' line mean IS the chain's units per line
    Eq = levels.LEVELS.evaluate({'lam': 3.0, 'n': 1.0, 'pi': 1.0, 'f_L': 1.3, 'supplier': 0.0,
                                 'unit': 1.0, 'transit': 0.0, 'C': 10.0, 'safety': 2.0})['Eq']
    assert math.isclose(d['units'], 10.0 * Eq, rel_tol=1e-12)


def test_the_drain_order_moves_travel_when_the_small_bin_is_far(cfg):
    # a SKU with a big pack at column 1 and a one-unit pack at column 4: the recorded order
    # serves the near bin first; the simulator's smallest-first serves the far one first
    g = _geometry(C=4)
    c = _order(1, 1.0, 1.0)
    bm = {1: [(1, 1, 1, 50), (1, 4, 1, 1)]}
    rec = pick.pick_day([c], cfg, bm, g, 5.0, drain='recorded')
    small = pick.pick_day([c], cfg, bm, g, 5.0, drain='smallest')
    assert small['travel_x_s'] > rec['travel_x_s']


def test_trailer_law_counts_what_the_loader_opens():
    rng = random.Random(4)
    t = TrailerTransit()
    days, shipped, per_day = 120, [], []
    for d in range(days):
        vol = 0
        for s in range(rng.randint(200, 500)):
            q, v = rng.randint(1, 12), int(rng.lognormvariate(math.log(900), 0.8)) + 1
            v = min(v, 40_000)
            t.dispatch(s, q, 0, unit_volume=v, now_s=float(d))
            shipped.append((q, v))
            vol += q * v
        per_day.append(vol)
        t.release(now_s=float(d) + 0.5)
    m = sum(per_day) / days
    cv = (sum((v - m) ** 2 for v in per_day) / days) ** 0.5 / m
    law = inbound.trailers_per_day(shipped, days=days, cv=cv)['trailers_per_day']
    assert law == pytest.approx(t._seq / days, rel=0.05)
