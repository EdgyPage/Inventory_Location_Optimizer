"""Regression tests for the aisle_index fast path and its coupling guard.

These lock in the two invariants that closed the profiler-vs-production
divergence (see ASSIGNMENT_DIVERGENCE_PLAN.md):

1. The aisle_index fast path produces IDENTICAL aisle-level placement to the
   candidates scan, so arming init_travel_costs() never changes results.
2. The _stock coupling guard raises if init_travel_costs() (mgr half) and an
   index-consuming assignment_fn (fn half) are armed independently, so the two
   can never silently diverge again.

The stock level these tests field
--------------------------------
`perf_simulation._build_inventory` hands back a CATALOGUE — geometry and demand, no stock level
(ADR-0002: a level is a run's declaration, never a SKU's fact) — so every manager built here
declares one first, through `_declare`, at Q = 1 unit per SKU.  Q = 1 is the level this file has
always fielded (`_equilibrium_qty` used to answer 1 by default) and the only one its warehouse
can hold: `_build_warehouse_cfg` sizes the building at ~1.15 bins per SKU.

The declaration is also what finally makes `_run`'s `check_reorders()` do something.  Until it,
an undeclared order had no `reorder_point` attribute at all, `_notify_pick` read that absence as
"never flag", and no reorder ever fired — so the "full reorder+pick simulation" this file claims
to compare was a pick-only one, and the index fast path was never exercised at reorder time,
which is the one moment it runs on a warehouse that is already half full.  It is now.
"""
import os
import sys
import random

import pytest


from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.inventory.Inventory_Management import Inventory_Manager, Placement
from Warehouse.placement.Assignment_Functions import build_cluster_minimizing_assignment_fn
from Warehouse.picking.Pick import PickConfig, PickSimulation
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
from perf_simulation import _build_inventory, _build_affinity_store, _build_warehouse_cfg

SEED, N_SKUS, BINS_PER_AISLE, N_BATCHES, N_PICKERS = 42, 2000, 100, 60, 5


class _WP:
    x_speed = 1.0
    y_speed = 0.5


def _declare(orders, qty: int = 1):
    """Declare this file's stock level — `qty` units per SKU — on a catalogue.

    Two reads need it and both used to answer from a silent default: `enqueue_all` takes the
    Order-Up-To as the quantity to stock (it defaulted to 1), and `_notify_pick` /
    `_fire_reorders` need a reorder point to trigger on (its absence meant "never fire").
    Both raise `UndeclaredStock` now.

    Q = 1 keeps the stocking identical to what this file always placed — one unit per SKU into a
    warehouse sized for ~1.15 bins per SKU — so the aisle-level state the two paths are compared
    on is built from the same units in the same order.  `declare_stock` clamps rp into [1, Q-1]
    and so takes rp = 1 at Q = 1: a one-unit SKU is emptied by any pick, so every picked SKU is
    flagged and restocked to one unit, one unit per reorder.

    An explicit loop rather than `simconfig.coverage.rescale_section`, and only the four level
    slots are written: this is an equivalence test, so the level must be a hand-checkable
    constant, and `expected_batch_demand` / `lead_time_mean` / `supply_cv` stay unset exactly as
    `Order.__init__` left them (every reader of those on this path is a `getattr` with the same
    default — see `Order.reorder`).
    """
    for o in orders:
        o.declare_stock(qty, 1)
    return orders


def _build_cluster_mgr(wh_cfg, affinity, inventory, wp, arm):
    """Build a cluster-minimising manager — armed (index fast path) or not (scan)."""
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=affinity)
    random.seed(SEED + 1)
    mgr.enqueue_all(inventory.orders)
    mgr.init_lift_state(affinity)
    mgr.init_demand_state(inventory)
    if arm:
        mgr.init_travel_costs(wp)
    freq_by_sku = {c.sku: c.demand.relative_frequency    for c in inventory.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate for c in inventory.orders}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.relative_frequency
                   for c in inventory.orders if c.sku in affinity._sku_to_idx}
    mgr.placement = Placement('cohesion_min', build_cluster_minimizing_assignment_fn(
        affinity, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None)))
    return wh, mgr


def _run(wh, mgr, pick_cfg, batch_cfg, inventory):
    random.seed(SEED + 100)
    base_placements = mgr._reorder_placements     # counts every placement, initial included
    for _ in range(N_BATCHES):
        mgr.check_reorders()
        batch = Batch(batch_cfg, inventory, affinity=None)
        tasks = Task.from_batch(batch, wh, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()
    # Aisle-level placement state: sku->aisle counts (bin-level identity may differ
    # on same-D ties, but only aisle assignment feeds back into reorders).  The reorder
    # placement count rides along so the caller can prove the restock path actually ran.
    return ({aid: dict(c) for aid, c in mgr._aisle_sku_counts.items() if c},
            mgr._reorder_placements - base_placements)


@pytest.fixture(scope='module')
def assets():
    random.seed(SEED)
    inventory = _build_inventory(N_SKUS, SEED)
    _declare(inventory.orders)          # the run's declaration; the catalogue carries none
    wh_cfg    = _build_warehouse_cfg(N_SKUS, BINS_PER_AISLE)
    affinity  = _build_affinity_store(inventory, top_k=20, seed=SEED)
    pick_cfg  = PickConfig(num_pickers=N_PICKERS, x_speed=1.0, y_speed=0.5,
                           pick_intercept=1.0, pick_weight_coef=1.1,
                           pick_volume_coef=1e-3, cart_swap_coef=10.0)
    wp        = WorkloadParams.from_pick_config(pick_cfg)
    batch_cfg = BatchConfig(inventory_size=N_SKUS, mean_fraction=0.05, std_fraction=0.01)
    return inventory, wh_cfg, affinity, pick_cfg, wp, batch_cfg


def test_cluster_index_matches_scan(assets):
    """Armed (aisle_index) and unarmed (candidates scan) cluster placement must be
    identical at aisle level across a full reorder+pick simulation."""
    inventory, wh_cfg, affinity, pick_cfg, wp, batch_cfg = assets
    wh1, mgr1 = _build_cluster_mgr(wh_cfg, affinity, inventory, wp, arm=False)
    wh2, mgr2 = _build_cluster_mgr(wh_cfg, affinity, inventory, wp, arm=True)

    assert mgr1.placement.uses_aisle_index is False
    assert mgr2.placement.uses_aisle_index is True

    state_scan,  scan_reord  = _run(wh1, mgr1, pick_cfg, batch_cfg, inventory)
    state_index, index_reord = _run(wh2, mgr2, pick_cfg, batch_cfg, inventory)
    # Not vacuous: bins are occupied, and reorder-time placement — the one moment the index
    # fast path runs against a half-full warehouse — actually fired on both managers.
    assert state_scan, 'no aisle holds a SKU; the comparison below is between two empty dicts'
    assert scan_reord > 0 and index_reord > 0, (scan_reord, index_reord)
    assert state_scan == state_index


def _fresh_mgr(wh_cfg):
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    random.seed(SEED + 1)
    mgr.enqueue_all(_declare(_build_inventory(200, SEED).orders))
    return mgr


def test_guard_raises_when_armed_with_scan_fn(assets):
    """travel costs armed but the fn scans candidates -> divergence guard fires."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    mgr.init_travel_costs(_WP())            # mgr half armed; default fn does NOT read index
    with pytest.raises(RuntimeError, match='Assignment divergence'):
        mgr._stock()


def test_guard_raises_when_index_fn_without_arming(assets):
    """index-consuming fn but travel costs not armed -> divergence guard fires."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    def fn(unit, candidates):
        return None
    fn.uses_aisle_index = True             # fn half armed; mgr half not
    mgr.placement = Placement('test', fn)
    with pytest.raises(RuntimeError, match='Assignment divergence'):
        mgr._stock()


def test_guard_silent_when_consistent(assets):
    """Consistent unarmed scan path drains without raising."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    mgr._stock()   # ready=False, default fn has no index tag -> no raise
