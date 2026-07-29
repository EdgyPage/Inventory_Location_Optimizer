"""test_reorder_accounting.py

Locks the standardized reorder/stock order-unit accounting (Phase 3):
  - Inventory_Manager.units_ordered tracks Σ reorder qty ORDERED this batch (U), resets each
    check_reorders() call, and is distinct from placed (P) / in-transit;
  - batch_stats persists the new skus_reordered (N) + units_ordered (U) columns (save/load round-trip).

Run:  python -m pytest Tests/test_reorder_accounting.py -q
"""
from __future__ import annotations

import random

from Warehouse.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.Aisle_Storage import Aisle
from Warehouse.Order import Order, StorageHandleConfig
from Warehouse.Demand import Demand
from Warehouse.Inventory_Management import Inventory_Manager
from Warehouse.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Optimization.persistence.Picking_Data import (
    BatchStats, init_run_db, save_batch_stats, load_batch_stats,
)


def _small_warehouse(seed=0):
    Aisle.next_aisle_id = 1
    random.seed(seed)
    w, h = aisle_width_for(4), aisle_height_for(6)
    cfg = WarehouseConfig(total_aisles=4, aisle_splits=[0.25] * 4, aisle_configs=[
        AisleConfig('conveyable',     'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
        AisleConfig('non-conveyable', 'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
        AisleConfig('conveyable',     'food', 'singleton', w, h, ['singleton'], None),
        AisleConfig('non-conveyable', 'food', 'singleton', w, h, ['singleton'], None)])
    return Inventory_Manager(Warehouse_Builder().from_config(cfg).build())


def _carton(sku, eq_qty=20, rp=8, lt=0.0):
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height, c.weight = 8, 8, 6, 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty = eq_qty
    c.reorder_point = rp
    c.lead_time_mean = lt
    c.supply_cv = 0.0
    c.expected_batch_demand = 3.2
    return c


def test_units_ordered_tracks_qty_and_resets():
    mgr = _small_warehouse(0)
    c = _carton(1, eq_qty=20, rp=8, lt=0.0)
    mgr.enqueue(c)
    mgr._current_quantities[1] = 5          # drained below rp
    mgr._depleted_skus.add(1)
    triggered = mgr.check_reorders()
    assert triggered == [1]                 # N = 1 sku reordered
    assert mgr.units_ordered == 15          # U = eq(20) - position(5) = 15 units ordered
    # a subsequent batch with nothing depleted orders nothing → resets to 0
    mgr.check_reorders()
    assert mgr.units_ordered == 0


def test_units_ordered_sums_across_skus():
    mgr = _small_warehouse(1)
    for sku, eq in ((2, 20), (3, 40)):      # lead>0 defers placement, so units_ordered == Σ deferred
        c = _carton(sku, eq_qty=eq, rp=8, lt=5.0)
        mgr._originals[sku] = c
        mgr._current_quantities[sku] = 5
        mgr._depleted_skus.add(sku)
    mgr.check_reorders()
    total_deferred = sum(mgr._deferred_qty.values())
    assert mgr.units_ordered == total_deferred > 0    # U sums the two SKUs' orders


def test_batch_stats_persists_reorder_counts(tmp_path):
    db = str(tmp_path / 'sim.db')
    init_run_db(db)
    bs = BatchStats(run_id=1, batch_id=0, duration=100.0, num_tasks=3, total_items=50,
                    avg_concurrent_pickers=2.0, picking_pct=0.6, traveling_pct=0.4,
                    skus_reordered=7, units_ordered=123, reorder_placements=90)
    save_batch_stats(db, 1, [bs])
    got = load_batch_stats(db, 1)
    assert len(got) == 1
    assert got[0].skus_reordered == 7
    assert got[0].units_ordered == 123
    assert got[0].reorder_placements == 90
