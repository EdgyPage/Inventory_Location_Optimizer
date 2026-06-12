"""Regression tests for the aisle_index fast path and its coupling guard.

These lock in the two invariants that closed the profiler-vs-production
divergence (see ASSIGNMENT_DIVERGENCE_PLAN.md):

1. The aisle_index fast path produces IDENTICAL aisle-level placement to the
   candidates scan, so arming init_travel_costs() never changes results.
2. The _drain coupling guard raises if init_travel_costs() (mgr half) and an
   index-consuming assignment_fn (fn half) are armed independently, so the two
   can never silently diverge again.
"""
import os
import sys
import random

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Optimization'))
sys.path.insert(0, os.path.dirname(__file__))

from Aisle_Storage import Aisle
from Inventory_Management import Inventory_Manager
from Assignment_Functions import build_cluster_minimizing_assignment_fn
from Pick import PickConfig, PickSimulation
from Workload import WorkloadParams
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, BatchConfig, Task
from perf_simulation import _build_inventory, _build_affinity_store, _build_warehouse_cfg

SEED, N_SKUS, BINS_PER_AISLE, N_BATCHES, N_PICKERS = 42, 2000, 100, 60, 5


class _WP:
    x_speed = 1.0
    y_speed = 0.5


def _build_cluster_mgr(wh_cfg, affinity, inventory, wp, arm):
    """Build a cluster-minimising manager — armed (index fast path) or not (scan)."""
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=affinity)
    random.seed(SEED + 1)
    mgr.enqueue_all(inventory.cartons)
    mgr.init_lift_state(affinity)
    mgr.init_demand_state(inventory)
    if arm:
        mgr.init_travel_costs(wp)
    freq_by_sku = {c.sku: c.demand.frequency    for c in inventory.cartons}
    qty_by_sku  = {c.sku: c.demand.quantity_rate for c in inventory.cartons}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.frequency
                   for c in inventory.cartons if c.sku in affinity._sku_to_idx}
    mgr.assignment_fn = build_cluster_minimizing_assignment_fn(
        affinity, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None))
    return wh, mgr


def _run(wh, mgr, pick_cfg, batch_cfg, inventory):
    random.seed(SEED + 100)
    for _ in range(N_BATCHES):
        mgr.check_reorders()
        batch = Batch(batch_cfg, inventory, affinity=None)
        tasks = Task.from_batch(batch, wh, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()
    # Aisle-level placement state: sku->aisle counts (bin-level identity may differ
    # on same-D ties, but only aisle assignment feeds back into reorders).
    return {aid: dict(c) for aid, c in mgr._aisle_sku_counts.items() if c}


@pytest.fixture(scope='module')
def assets():
    random.seed(SEED)
    inventory = _build_inventory(N_SKUS, SEED)
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

    assert getattr(mgr1.assignment_fn, 'uses_aisle_index', None) is False
    assert getattr(mgr2.assignment_fn, 'uses_aisle_index', None) is True

    state_scan  = _run(wh1, mgr1, pick_cfg, batch_cfg, inventory)
    state_index = _run(wh2, mgr2, pick_cfg, batch_cfg, inventory)
    assert state_scan == state_index


def _fresh_mgr(wh_cfg):
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    random.seed(SEED + 1)
    mgr.enqueue_all(_build_inventory(200, SEED).cartons)
    return mgr


def test_guard_raises_when_armed_with_scan_fn(assets):
    """travel costs armed but the fn scans candidates -> divergence guard fires."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    mgr.init_travel_costs(_WP())            # mgr half armed; default fn does NOT read index
    with pytest.raises(RuntimeError, match='Assignment divergence'):
        mgr._drain()


def test_guard_raises_when_index_fn_without_arming(assets):
    """index-consuming fn but travel costs not armed -> divergence guard fires."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    def fn(unit, candidates):
        return None
    fn.uses_aisle_index = True             # fn half armed; mgr half not
    mgr.assignment_fn = fn
    with pytest.raises(RuntimeError, match='Assignment divergence'):
        mgr._drain()


def test_guard_silent_when_consistent(assets):
    """Consistent unarmed scan path drains without raising."""
    _, wh_cfg, *_ = assets
    mgr = _fresh_mgr(wh_cfg)
    mgr._drain()   # ready=False, default fn has no index tag -> no raise
