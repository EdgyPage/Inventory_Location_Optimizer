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
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from regime import regime_of, STORE, FULFILLMENT
from Storage_Primitive import viable_storage_units
from Inventory_Management import Inventory_Manager
from Warehouse_Builder import Warehouse_Builder
from Aisle_Dimensions import aisle_width_for, aisle_height_for
from strategies import STRATEGIES, strategies_for
from channels import build_channels
from Pick import PickConfig

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
