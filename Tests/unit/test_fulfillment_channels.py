"""test_fulfillment_channels.py

Locks in the Store/Fulfillment mixed-warehouse core:
  * regime_of + FulfillmentBin fit/tiers (Part A bin model)
  * viable_storage_units + _candidates regime routing (Part A placement)
  * plan_warehouse regime-aware sizing (both aisle kinds, no cross-product junk,
    short-shelf ff geometry, store-only unchanged)
  * per-regime cost routing: b._D travel speed + Order.labor_cost + ff height M=1 (Part B)
  * the Channel / PickerProfile abstraction (Optimization/config/channels.py)

The `_store_order` / `_ff_order` / `_mixed_inventory` / `_plan` helpers are SHARED: they are
imported by name from `test_channel_strategy_subset.py` (same directory, see Tests/README.md).
Keep their signatures stable.

Run:  python -m pytest Tests/unit/test_fulfillment_channels.py -v
"""
from __future__ import annotations

import os
import sys
import random

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


from Warehouse.catalog.Order import Order
from Warehouse.kernel.regime import regime_of, STORE, FULFILLMENT
from Warehouse.layout.Storage_Primitive import Pallet, Singleton, FulfillmentBin, viable_storage_units, _can_fit
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.layout.Aisle_Dimensions import aisle_width_for, aisle_height_for, FULFILLMENT_AISLE_HEIGHT
from Warehouse.kernel.cost_model import sec_per_inch, height_multiplier, DEFAULT_HEIGHT_BRACKETS
from Optimization.metrics.Workload import WorkloadParams

_CATS = ['food', 'clothing', 'electronic']
_HANDS = ['conveyable', 'non-conveyable']


# The catalogue carries geometry and demand; a LEVEL is a run's declaration (ADR-0002), so
# these fixtures build the SKU and then declare on it exactly as a run's setup would.  The
# declaration is not decoration here: `plan_warehouse` sizes every bucket from
# `_equilibrium_qty`, which RAISES `UndeclaredStock` on an order no run has declared.
def _store_order(sku, rng):
    return Order.build(sku, _HANDS[sku % 2], _CATS[sku % 3],
                       length=rng.randint(20, 44), width=rng.randint(20, 44),
                       height=rng.randint(20, 44), weight=rng.randint(10, 80),
                       relative_frequency=rng.uniform(0.05, 0.5), qty_rate=rng.randint(1, 6),
                       ).declare_stock(rng.randint(3, 12), 2)


def _ff_order(sku, rng):
    # small dims that fit a 16x16 fulfillment bin footprint and the 18-tall tallest tier
    return Order.build(sku, FULFILLMENT, FULFILLMENT,
                       length=rng.randint(4, 14), width=rng.randint(4, 14),
                       height=rng.randint(4, 16), weight=rng.randint(1, 8),
                       relative_frequency=rng.uniform(0.1, 0.6), qty_rate=rng.randint(1, 4),
                       ).declare_stock(rng.randint(3, 10), 2)


def _mixed_inventory(n_store=60, n_ff=20, seed=0):
    rng = random.Random(seed)
    orders = [_store_order(i + 1, rng) for i in range(n_store)]
    orders += [_ff_order(n_store + i + 1, rng) for i in range(n_ff)]
    return orders


def _plan(orders, seed=1):
    return Inventory_Manager.plan_warehouse(
        orders, categories=_CATS, handlings=_HANDS,
        aisle_width=aisle_width_for(50), aisle_height=aisle_height_for(10),
        target_fill=0.85, rng=random.Random(seed))


# ── Part A: bin model ────────────────────────────────────────────────────────

def test_regime_of_across_entity_kinds():
    rng = random.Random(3)
    st, ff = _store_order(1, rng), _ff_order(2, rng)
    assert regime_of(st) == STORE
    assert regime_of(ff) == FULFILLMENT
    # StorageUnit level
    assert regime_of(viable_storage_units(ff, 3)[0]) == FULFILLMENT
    assert regime_of(viable_storage_units(st, 3)[0]) == STORE


def test_fulfillment_bin_tiers_and_fit():
    rng = random.Random(4)
    ff = _ff_order(1, rng)
    b = FulfillmentBin(ff, 1)
    assert b.unit_category == FULFILLMENT
    assert b.storage_size in dict(FulfillmentBin.TIERS)
    # a pallet-sized item cannot fit the small ff footprint (a fit is pure geometry, so this
    # one needs no stock declaration)
    big = Order.build(2, FULFILLMENT, FULFILLMENT, 40, 40, 40, 50,
                      relative_frequency=0.3, qty_rate=1)
    assert not _can_fit(big, FulfillmentBin, 1)


def test_the_fixtures_declare_a_level_that_build_alone_does_not():
    """Guards the fixture itself. `plan_warehouse` sizes every bucket from `_equilibrium_qty`,
    so a helper that stopped declaring would size the mixed warehouse from nothing -- except
    that `_equilibrium_qty` now RAISES instead of answering 1 (ADR-0002). Pin both halves:
    `Order.build` declares nothing, and these fixtures do."""
    rng = random.Random(21)
    bare = Order.build(1, FULFILLMENT, FULFILLMENT, 10, 10, 10, 5,
                       relative_frequency=0.3, qty_rate=1)
    assert not bare.stock_declared()
    with pytest.raises(AttributeError):
        bare.equilibrium_qty
    for o in (_store_order(2, rng), _ff_order(3, rng)):
        assert o.stock_declared()
        assert o.equilibrium_qty >= 1
        assert 1 <= o.reorder_point <= max(1, o.equilibrium_qty - 1)


def test_viable_storage_units_routing():
    rng = random.Random(5)
    ff, st = _ff_order(1, rng), _store_order(2, rng)
    ff_units = viable_storage_units(ff, 6)
    assert ff_units and all(isinstance(u, FulfillmentBin) for u in ff_units)
    st_units = viable_storage_units(st, 6)
    assert st_units and all(isinstance(u, (Pallet, Singleton)) for u in st_units)
    assert not any(isinstance(u, FulfillmentBin) for u in st_units)


# ── Part A: planner ──────────────────────────────────────────────────────────

def test_plan_warehouse_mixed_has_both_regimes_no_junk():
    plan = _plan(_mixed_inventory())
    units = {ac.unit_type for ac in plan.warehouse_cfg.aisle_configs}
    assert 'pallet' in units and 'singleton' in units and FULFILLMENT in units
    # no cross-product junk: ff never paired with a store handling/category, and store
    # aisles never carry the fulfillment handling/category.
    for ac in plan.warehouse_cfg.aisle_configs:
        if ac.unit_type == FULFILLMENT:
            assert ac.handling_type == FULFILLMENT and ac.storage_type == FULFILLMENT
            assert ac.bin_width == FulfillmentBin.max_width
            assert ac.aisle_height == FULFILLMENT_AISLE_HEIGHT
        else:
            assert ac.handling_type in _HANDS and ac.storage_type in _CATS


def test_plan_warehouse_store_only_has_no_fulfillment():
    rng = random.Random(7)
    store_only = [_store_order(i + 1, rng) for i in range(40)]
    plan = _plan(store_only)
    units = {ac.unit_type for ac in plan.warehouse_cfg.aisle_configs}
    assert FULFILLMENT not in units
    assert units <= {'pallet', 'singleton'}


def test_mixed_stocking_regime_isolation_and_ff_geometry():
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    ff_bins = [b for b in wh.bins if b.unit_type == FULFILLMENT]
    store_bins = [b for b in wh.bins if b.unit_type != FULFILLMENT]
    assert ff_bins and store_bins
    # ff bins sit on short shelves → every y_phys below the first height bracket → M=1
    assert max(b.y_phys for b in ff_bins) < DEFAULT_HEIGHT_BRACKETS[0][0]
    assert {b.x_step for b in ff_bins} == {FulfillmentBin.max_width}

    mgr = Inventory_Manager(wh, affinity=None)
    mgr.enqueue_all(plan.sampled)
    # regime isolation: ff bins hold only ff units, store bins hold only store units
    assert all(regime_of(b.storage) == FULFILLMENT for b in ff_bins if b.storage)
    assert all(regime_of(b.storage) == STORE for b in store_bins if b.storage)
    assert any(b.storage for b in ff_bins), 'some fulfillment bins should be filled'


def test_candidates_regime_isolation():
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    # index the empty warehouse's bins so _candidates can find them
    for b in wh.bins:
        mgr._index_add(b)
    rng = random.Random(9)
    ff_unit = viable_storage_units(_ff_order(999, rng), 1)[0]
    cands = mgr._candidates(ff_unit)
    assert cands and all(b.unit_type == FULFILLMENT for b in cands)


# ── Part B: per-regime cost routing ──────────────────────────────────────────

def _wp(x_speed, y_speed):
    return WorkloadParams(x_speed=x_speed, y_speed=y_speed, pick_intercept=5.0,
                          pick_weight_coef=0.2, pick_volume_coef=0.3)


def test_per_regime_travel_cost_bakes_by_regime():
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    store_wp, ff_wp = _wp(3.0, 2.0), _wp(4.5, 2.0)
    store_wp.by_regime = {STORE: store_wp, FULFILLMENT: ff_wp}
    mgr.init_travel_costs(store_wp)

    b_ff = next(b for b in wh.bins if b.unit_type == FULFILLMENT)
    b_st = next(b for b in wh.bins if b.unit_type != FULFILLMENT)
    exp_ff = sec_per_inch(4.5) * b_ff.x_phys + sec_per_inch(2.0) * b_ff.y_phys
    exp_st = sec_per_inch(3.0) * b_st.x_phys + sec_per_inch(2.0) * b_st.y_phys
    assert abs(b_ff._D - exp_ff) < 1e-9   # fulfillment bin uses the WALKER's x speed
    assert abs(b_st._D - exp_st) < 1e-9   # store bin uses the machine's x speed


def test_per_regime_travel_cost_single_regime_unchanged():
    # No by_regime map → every bin uses the single wp (byte-identical store behavior).
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    wp = _wp(3.0, 2.0)
    mgr.init_travel_costs(wp)
    b = wh.bins[0]
    assert abs(b._D - (sec_per_inch(3.0) * b.x_phys + sec_per_inch(2.0) * b.y_phys)) < 1e-9


def test_per_regime_labor_cost():
    rng = random.Random(11)
    ff = _ff_order(1, rng)
    store_cost = ff.compute_labor_cost(5.0, 0.5, 0.5, pick_per_item=0.5)   # machine coefs
    ff_cost = ff.compute_labor_cost(10.0, 0.1, 0.5, pick_per_item=0.5)     # walker coefs (diff intercept)
    assert store_cost != ff_cost                             # regime-specific coefficients apply
    assert ff.labor_cost == ff_cost


def test_fulfillment_bin_height_multiplier_is_one():
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    for b in wh.bins:
        if b.unit_type == FULFILLMENT:
            assert height_multiplier(DEFAULT_HEIGHT_BRACKETS, b.y_phys) == 1.0


# ── Channel abstraction ──────────────────────────────────────────────────────

def test_channels_module():
    from Warehouse.picking.Pick import PickConfig
    from Optimization.config.channels import build_channels, wp_by_regime, fulfillment_pick_config, Channel

    store_cfg = PickConfig(num_pickers=25, x_speed=3.0, y_speed=2.0)
    chans = build_channels(store_cfg, 25, include_fulfillment=True, ff_num_pickers=30)
    assert [c.regime for c in chans] == [STORE, FULFILLMENT]
    assert chans[0].picker.num_pickers == 25 and chans[1].picker.num_pickers == 30
    # independent batch streams (distinct seed offsets)
    assert chans[0].batch_seed_offset != chans[1].batch_seed_offset

    wpbr = wp_by_regime(chans)
    assert set(wpbr) == {STORE, FULFILLMENT}
    assert wpbr[STORE].x_speed == 3.0
    assert wpbr[FULFILLMENT].x_speed == fulfillment_pick_config().x_speed

    # store-only channel list omits fulfillment
    solo = build_channels(store_cfg, 25, include_fulfillment=False)
    assert [c.regime for c in solo] == [STORE]

    # BatchConfig is built per channel SKU-subset size
    bc = chans[1].batch_config(inventory_size=500)
    assert bc.inventory_size == 500 and bc.mean_fraction == chans[1].batch_mean_fraction


def test_per_channel_cart_type():
    """Store keeps the standard 125k cart; fulfillment gets the 25k tote, and the smaller
    cart yields a strictly larger carts_required for the same picked volume."""
    from math import ceil
    from Warehouse.picking.Pick import PickConfig
    from Warehouse.layout.Storage_Primitive import StoreCart, FulfillmentCart
    from Optimization.config.channels import fulfillment_pick_config

    assert StoreCart.capacity() == 125_000
    assert FulfillmentCart.capacity() == 25_000
    assert PickConfig().cart is StoreCart                       # default unchanged (store standard)
    assert fulfillment_pick_config().cart is FulfillmentCart    # ff channel uses the small tote

    # carts_required scales inversely with cart volume for the same total picked volume.
    total_vol = 90_000
    assert ceil(total_vol / StoreCart.capacity()) < ceil(total_vol / FulfillmentCart.capacity())
