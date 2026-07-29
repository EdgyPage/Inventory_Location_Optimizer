"""test_channel_strategy_subset.py

Locks in the per-channel strategy-subset + cross-regime isolation work:
  * strategies.strategies_for(restocks) filters the grid by restock rule
  * build_channels(store_restocks=...) restricts the store channel; fulfillment stays full
  * Inventory_Manager._execute_placement fail-fast guard rejects cross-regime placements,
    and mixed stocking never trips it (store and fulfillment never intersect each other's bins)

Run:  python -m pytest Tests/test_channel_strategy_subset.py -v
"""
from __future__ import annotations

import os
import sys
import random

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


from Warehouse.regime import regime_of, STORE, FULFILLMENT
from Warehouse.Storage_Primitive import viable_storage_units
from Warehouse.Inventory_Management import Inventory_Manager, Placement
from Warehouse.Warehouse_Builder import Warehouse_Builder
from Warehouse.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.Affinity_Store import AffinityStore
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.Assignment_Functions import build_cluster_maximizing_assignment_fn
from Optimization.config.strategies import STRATEGIES, strategies_for
from Optimization.config.channels import build_channels
from Warehouse.Pick import PickConfig

# Reuse the mixed-catalog helpers from the sibling channel test.
from test_fulfillment_channels import _mixed_inventory, _ff_order, _plan


# ── strategies_for ───────────────────────────────────────────────────────────

def test_strategies_for_none_is_full_grid():
    assert strategies_for(None) == list(STRATEGIES)


def test_strategies_for_store_subset():
    subset = strategies_for(('fifo', 'rank_labor'))
    keys = {s.key for s in subset}
    assert keys == {'uni_fifo_norsl', 'opt_fifo_norsl',
                    'uni_rank_labor_norsl', 'opt_rank_labor_norsl'}
    # every arm carries its restock rule and it's one of the requested two
    assert {s.restock for s in subset} == {'fifo', 'rank_labor'}
    # and the subset is strictly smaller than the full suite
    assert len(subset) < len(STRATEGIES)


def test_every_strategy_has_a_restock_key():
    # the new dataclass field is populated for the whole grid (used for filtering)
    assert all(s.restock for s in STRATEGIES)


# ── build_channels store_restocks wiring ─────────────────────────────────────

def test_build_channels_store_restocks_scopes_store_only():
    store_cfg = PickConfig(num_pickers=25, x_speed=3.0, y_speed=2.0)
    chans = build_channels(store_cfg, 25, include_fulfillment=True,
                           store_restocks=('fifo', 'rank_labor'))
    store, ff = chans
    assert store.regime == STORE and store.restocks == ('fifo', 'rank_labor')
    assert ff.regime == FULFILLMENT and ff.restocks is None   # fulfillment = full suite

    # resolved arms: store gets 4, fulfillment gets the whole grid
    assert len(strategies_for(store.restocks)) == 4
    assert len(strategies_for(ff.restocks)) == len(STRATEGIES)


def test_build_channels_default_restocks_is_full_suite():
    # No store_restocks kwarg ⇒ store also runs the full suite (backward compatible).
    store_cfg = PickConfig(num_pickers=25, x_speed=3.0, y_speed=2.0)
    chans = build_channels(store_cfg, 25, include_fulfillment=True)
    assert all(c.restocks is None for c in chans)


# ── cross-regime placement guard ─────────────────────────────────────────────

def test_mixed_stocking_never_trips_the_guard():
    # A full mixed stock must place every unit without a cross-regime AssertionError.
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    mgr.enqueue_all(plan.sampled)   # would raise if any unit crossed regimes
    # queues/bins hold only their own regime's units
    for b in wh.bins:
        if b.storage is not None:
            assert regime_of(b.storage) == regime_of(b)


def test_guard_rejects_a_forced_cross_regime_placement():
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    store_bin = next(b for b in wh.bins if regime_of(b) == STORE)
    ff_unit = viable_storage_units(_ff_order(999, random.Random(3)), 1)[0]
    assert regime_of(ff_unit) == FULFILLMENT
    with pytest.raises(AssertionError, match='cross-regime placement'):
        mgr._execute_placement(ff_unit, store_bin)


# ── cohesion opt-stock places fulfillment units (aisle-index size-table regression) ──

def test_opt_cohesion_stock_places_fulfillment_units():
    """Regression for the blank-DB bug: opt/policy-stock via a cohesion (cluster) policy
    must place fulfillment units.  The aisle-index fast path used the pallet-only size
    table, so ff units (sizes ff_*) never matched any ff BinKey and were silently dropped
    -> no bins filled -> no pick tasks -> blank sim DB.  This asserts ff units get placed."""
    plan = _plan(_mixed_inventory())
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    aff = AffinityStore(':memory:')
    # A usable affinity matrix (cohesion refuses to run without one).  Edges span every
    # sampled SKU (store + fulfillment); on an empty warehouse the lift delta is 0 anyway,
    # so cohesion falls back to the D tie-break — exactly the first-unit opt-stock case.
    _skus = [c.sku for c in plan.sampled]
    aff._conn.executemany('INSERT INTO affinity VALUES (?,?,?)',
                          [(_skus[i], _skus[i + 1], 2.0) for i in range(len(_skus) - 1)])
    aff._conn.commit()
    aff._load_matrix()
    assert aff._matrix is not None
    wp = WorkloadParams(x_speed=4.5, y_speed=2.0, pick_intercept=10.0,
                        pick_weight_coef=0.1, pick_volume_coef=0.5)
    mgr._affinity = aff
    mgr.init_travel_costs(wp)                             # builds _aisle_index (incl. ff keys)
    freq_by_sku = {c.sku: c.demand.relative_frequency for c in plan.sampled}
    qty_by_sku  = {c.sku: c.demand.quantity_rate for c in plan.sampled}
    freq_by_idx = {aff._sku_to_idx[c.sku]: c.demand.relative_frequency
                   for c in plan.sampled if c.sku in aff._sku_to_idx}
    fn = build_cluster_maximizing_assignment_fn(
        aff, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0, aisle_index=mgr._aisle_index)
    mgr.placement = Placement('cohesion_max', fn)         # the opt/policy-stock placement
    mgr.enqueue_all(plan.sampled)                         # stock THROUGH the cohesion policy

    ff_used = [b for b in wh.bins if b.storage is not None and regime_of(b) == FULFILLMENT]
    assert ff_used, 'cohesion opt-stock placed ZERO fulfillment units (ff-dropped regression)'
    # and the placed ff units are queryable for pick-task generation (non-blank sim)
    assert any(mgr._sku_pallet_bins.get(b.storage.order.sku) for b in ff_used)
