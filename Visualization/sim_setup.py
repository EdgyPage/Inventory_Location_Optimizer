from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))

from Aisle_Storage import Aisle
from Carton import Carton
from Inventory_Builder import Inventory_Builder, InventoryConfig
from Inventory_Management import Inventory_Manager
from Pick import PickConfig, PickSimulation
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig, Task

_GRID_COLS = 6


def build_simulation(seed: int = 42) -> dict:
    Carton.next_sku = 1
    Aisle.next_aisle_id = 1
    random.seed(seed)

    warehouse_config = WarehouseConfig(
        total_aisles=24,
        aisle_splits=[1 / 24] * 24,
        aisle_configs=[
            AisleConfig('conveyable',     'food',       'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'food',       'singleton', 240, 288, ['singleton']),
            AisleConfig('conveyable',     'clothing',   'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'clothing',   'singleton', 240, 288, ['singleton']),
            AisleConfig('conveyable',     'electronic', 'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'electronic', 'singleton', 240, 288, ['singleton']),
            AisleConfig('conveyable',     'furniture',  'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'furniture',  'singleton', 240, 288, ['singleton']),
            AisleConfig('conveyable',     'seasonal',   'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'seasonal',   'singleton', 240, 288, ['singleton']),
            AisleConfig('conveyable',     'chemical',   'pallet',    240, 288, ['medium']),
            AisleConfig('conveyable',     'chemical',   'singleton', 240, 288, ['singleton']),
            AisleConfig('non-conveyable', 'food',       'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'food',       'singleton', 192, 288, ['singleton']),
            AisleConfig('non-conveyable', 'clothing',   'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'clothing',   'singleton', 192, 288, ['singleton']),
            AisleConfig('non-conveyable', 'electronic', 'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'electronic', 'singleton', 192, 288, ['singleton']),
            AisleConfig('non-conveyable', 'furniture',  'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'furniture',  'singleton', 192, 288, ['singleton']),
            AisleConfig('non-conveyable', 'seasonal',   'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'seasonal',   'singleton', 192, 288, ['singleton']),
            AisleConfig('non-conveyable', 'chemical',   'pallet',    192, 288, ['large']),
            AisleConfig('non-conveyable', 'chemical',   'singleton', 192, 288, ['singleton']),
        ],
    )
    warehouse = Warehouse_Builder().from_config(warehouse_config).build()

    inv_config = InventoryConfig(
        num_skus=120,
        handling_splits=[0.5, 0.5],
        category_splits=[1 / 6] * 6,
    )
    inventory = Inventory_Builder().from_config(inv_config).build()
    manager = Inventory_Manager(warehouse)
    manager.enqueue_all(inventory.cartons, quantity=5)

    # Capture initial bin states — PickSimulation is non-mutating so this is stable
    initial_bins: dict[str, dict] = {}
    sku_volumes: dict[int, int] = {}
    for b in warehouse.bins:
        key = f"{b.location[0]},{b.location[1]},{b.location[2]}"
        if b.storage is not None:
            sku = b.storage.carton.sku
            initial_bins[key] = {'sku': sku, 'qty': b.storage.quantity}
            sku_volumes[sku] = b.storage.carton.volume()

    affinity = inventory.affinity_matrix()
    batch_cfg = BatchConfig(
        inventory_size=len(inventory.cartons),
        mean_fraction=0.30,
        std_fraction=0.05,
    )
    batch = Batch(batch_cfg, inventory, affinity=affinity)
    tasks = Task.from_batch(batch, warehouse)

    pick_cfg = PickConfig(
        num_pickers=3,
        x_speed=1.0,
        y_speed=0.5,
        pick_intercept=1.0,
        pick_weight_coef=0.02,
        pick_volume_coef=1e-4,
        cart_swap_coef=5.0,
    )
    sim = PickSimulation(tasks, pick_cfg)
    events = sim.run()

    # Serialize aisle layout
    aisles_data = []
    for idx, aisle in enumerate(warehouse.aisles):
        bins_data = []
        for b in aisle.bins:
            key = f"{b.location[0]},{b.location[1]},{b.location[2]}"
            info = initial_bins.get(key)
            bins_data.append({
                'x':    b.bayX,
                'y':    b.bayY,
                'size': b.storage_size,
                'key':  key,
                'sku':  info['sku'] if info else None,
                'qty':  info['qty'] if info else 0,
            })
        aisles_data.append({
            'aisle_id':      aisle.aisle_id,
            'handling_type': aisle.handling_type,
            'storage_type':  aisle.storage_type,
            'unit_type':     aisle.unit_type,
            'bay_x':         aisle.bayXPerAisle,
            'bay_y':         aisle.bayYPerAisle,
            'grid_col':      idx % _GRID_COLS,
            'grid_row':      idx // _GRID_COLS,
            'bins':          bins_data,
        })

    # Serialize events
    events_data = [
        {
            'time':           round(e.time, 4),
            'picker_id':      e.picker_id,
            'event_type':     e.event_type,
            'aisle_id':       e.aisle_id,
            'sku':            e.sku,
            'quantity':       e.quantity,
            'location':       list(e.location) if e.location else None,
            'bins_completed': e.bins_completed,
            'total_bins':     e.total_bins,
            'items_picked':   e.items_picked,
            'total_items':    e.total_items,
        }
        for e in events
    ]

    max_time = max((e.time for e in events), default=0.0)
    print(f'  Aisles: {len(aisles_data)}, stocked bins: {len(initial_bins)}, '
          f'tasks: {len(tasks)}, events: {len(events_data)}, max_time: {max_time:.1f}')

    return {
        'max_time':     round(max_time, 4),
        'num_pickers':  pick_cfg.num_pickers,
        'aisles':       aisles_data,
        'events':       events_data,
        'sku_volumes':  {str(k): v for k, v in sku_volumes.items()},
        'initial_bins': initial_bins,
    }
