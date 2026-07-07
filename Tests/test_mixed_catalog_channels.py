"""test_mixed_catalog_channels.py

End-to-end proof at the simulation level (no DB-runner multiprocessing) that a MIXED CATALOG
drives a mixed warehouse and PER-CHANNEL simulation:

  * a creation_plan with store families + a fulfillment family builds one mixed catalog
    (store SKUs + small fulfillment cubes), and it round-trips through the inventory DB;
  * planning that catalog yields a warehouse with both store and fulfillment aisles;
  * each Channel generates its OWN batch stream from its OWN SKU subset, whose tasks are
    regime-pure, and simulates with its OWN picker cost — so the same fulfillment tasks cost
    differently under the walker vs the machine regression.

Run:  python -m pytest Tests/test_mixed_catalog_channels.py -v
"""
from __future__ import annotations

import os
import sys
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:0] = [os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization'),
                os.path.join(_ROOT, 'Warehouse', 'generation')]

from Inventory_Builder import Inventory
from Order import Order
from regime import regime_of, STORE, FULFILLMENT
from Inventory_Management import Inventory_Manager
from Warehouse_Builder import Warehouse_Builder
from Aisle_Dimensions import aisle_width_for, aisle_height_for
from Workload_Builder import Batch, Task
from fast_pick import DeferredPickSimulation
from Pick import PickConfig
from channels import build_channels
from generation.generate_inventory import (
    Family, fulfillment_family, build_inventory_from_plan,
    save_inventory_to_db, load_inventory_from_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _store_family(cat, share):
    return Family(category=cat, share=share, handling_split=(0.5, 0.5),
                  length_spec=_DIM, width_spec=_DIM, height_spec=_DIM, weight_spec=_WT)


def _mixed_plan():
    return [_store_family('food', 0.4), _store_family('clothing', 0.3),
            fulfillment_family(share=0.3, cube_sizes=(4, 6, 8))]


def test_mixed_catalog_build_and_db_roundtrip(tmp_path):
    inv = build_inventory_from_plan(num_skus=150, plan=_mixed_plan(), seed=1)
    ff = [c for c in inv.orders if regime_of(c) == FULFILLMENT]
    store = [c for c in inv.orders if regime_of(c) == STORE]
    assert ff and store, 'mixed catalog must contain both regimes'
    # fulfillment SKUs are small cubes drawn from {4,6,8}
    for c in ff:
        assert c.length == c.width == c.height and c.length in (4, 6, 8)
        assert c.storage_handle_config.handling == FULFILLMENT
        assert c.storage_handle_config.category == FULFILLMENT

    # round-trip through the inventory DB — the fulfillment BinKey survives persistence
    db = str(tmp_path / 'mixed_inv.db')
    save_inventory_to_db(inv, db, {'name': 'mixed_test', 'num_skus': 150})
    inv2 = load_inventory_from_db(db)
    ff2 = [c for c in inv2.orders if regime_of(c) == FULFILLMENT]
    assert len(ff2) == len(ff)
    assert all(c.length in (4, 6, 8) and c.length == c.width == c.height for c in ff2)


def _build_mixed_warehouse(seed=2, num_skus=240):
    orders = build_inventory_from_plan(num_skus=num_skus, plan=_mixed_plan(), seed=seed).orders
    plan = Inventory_Manager.plan_warehouse(
        orders, categories=['food', 'clothing'], handlings=['conveyable', 'non-conveyable'],
        aisle_width=aisle_width_for(50), aisle_height=aisle_height_for(10),
        target_fill=0.85, rng=random.Random(seed + 1))
    wh = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    mgr.enqueue_all(plan.sampled)
    return wh, mgr, plan


def test_mixed_plan_has_both_aisle_kinds():
    wh, mgr, plan = _build_mixed_warehouse()
    kinds = {ac.unit_type for ac in plan.warehouse_cfg.aisle_configs}
    assert FULFILLMENT in kinds and 'pallet' in kinds


def test_per_channel_batches_and_simulation():
    wh, mgr, plan = _build_mixed_warehouse()
    store_cfg = PickConfig(num_pickers=4, x_speed=3.0, y_speed=2.0, pick_intercept=12.0,
                           pick_weight_coef=0.58, pick_volume_coef=0.7,
                           pick_weight_fn='pow:1.5', pick_volume_fn='log:2', cart_swap_coef=300.0)
    channels = build_channels(store_cfg, 4, include_fulfillment=True, ff_num_pickers=4)
    assert [c.regime for c in channels] == [STORE, FULFILLMENT]

    ran_regimes = []
    ff_events_by_cfg = {}
    for ch in channels:
        ch_orders = [c for c in plan.sampled if regime_of(c) == ch.regime]
        assert ch_orders, f'no stocked SKUs for channel {ch.name}'
        bc = ch.batch_config(len(ch_orders))
        batch = Batch(bc, Inventory(ch_orders), affinity=None,
                      rng=random.Random(1000 + ch.batch_seed_offset))
        tasks = Task.from_batch(batch, wh, manager=mgr)
        assert tasks, f'channel {ch.name} produced no tasks'
        # tasks are REGIME-PURE: every picked bin belongs to this channel's regime
        for t in tasks:
            assert all(regime_of(b) == ch.regime for b in t.path)

        sim = DeferredPickSimulation(tasks, ch.picker.cost, manager=None)
        events = sim.run()
        assert any(e.event_type == 'pick' for e in events)
        ran_regimes.append(ch.regime)

        if ch.regime == FULFILLMENT:
            # same fulfillment tasks, machine cost vs walker cost → different pick durations
            ff_events_by_cfg['walker'] = max(e.time for e in events)
            store_sim = DeferredPickSimulation(tasks, store_cfg, manager=None)
            ff_events_by_cfg['machine'] = max(e.time for e in store_sim.run())

    assert ran_regimes == [STORE, FULFILLMENT]
    assert ff_events_by_cfg['walker'] != ff_events_by_cfg['machine'], \
        'fulfillment picks should cost differently under the walker vs machine regression'
