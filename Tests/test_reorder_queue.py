"""test_reorder_queue.py

Reorder queue stability — dense warehouse matching run_simulation config.

The warehouse uses the same 24-aisle-type layout as run_simulation:
  12 pallet types  (conveyable + non-conveyable x 6 categories, all 4 sizes)
  12 singleton types (same split)

Reorder policy (OUP equilibrium model)
---------------------------------------
Each Carton carries attributes set at profile generation time:
  expected_batch_demand = demand.frequency * demand.quantity_rate
  equilibrium_qty       = max(1, round(coverage_batches * expected_batch_demand))
  reorder_point         = max(1, min(eq-1, round(demand * (lead_time + safety_batches))))

_notify_pick reads carton.reorder_point directly.  check_reorders fires an
Order-Up-To reorder: qty = equilibrium_qty - current_qty.  Received quantity
is sampled from N(ideal, ideal * supply_cv) floored at 1.

Units that cannot be placed stay in the queue indefinitely (FIFO) until a bin
opens up.  The important property is that the queue remains bounded and
stabilises — it should NOT grow without limit.

Checks
------
  PASS 1  reorder_point and expected_batch_demand set on all cartons
  PASS 2  queue does not grow in the final STABLE_WINDOW batches
  PASS 3  at least N_SKUS * 0.05 reorders triggered total
  PASS 4  fill rate stays above MIN_FILL at the end
  PASS 5  both pallet and singleton units placed (min-pallets packing active)

Usage
-----
    cd Tests
    python test_reorder_queue.py
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Aisle_Storage import Aisle
from Inventory_Management import Inventory_Manager
from Pick import PickConfig, PickSimulation
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig, Task
from generation.generate_inventory import (
    DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
    build_inventory_with_profile,
)

# ── parameters ────────────────────────────────────────────────────────────────

SEED            = 42
N_SKUS          = 500
N_BATCHES       = 50
N_PICKERS       = 5
BATCH_MEAN_FRAC = 0.25
STABLE_WINDOW   = 20
MIN_FILL        = 0.40

# Physical aisle dimensions: 6 pallet-column widths × 4 extra_large-height levels.
# With density expansion, singleton aisles have 3× more X bins and
# small-tier bins create 4× more Y levels than the physical slot count.
from Aisle_Dimensions import aisle_width_for, aisle_height_for
_AISLE_W = aisle_width_for(6)    # 6 × 48 = 288 physical units
_AISLE_H = aisle_height_for(4)   # 4 × 48 = 192 physical units

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_ALL_SIZES  = ['small', 'medium', 'large', 'extra_large']
_PALL_PROBS = [0.25, 0.25, 0.25, 0.25]

# 48 aisle types — 12 pallet + 12 singleton × 2 handling types
_AISLE_CFGS: list[AisleConfig] = []
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES,    _PALL_PROBS))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES,    _PALL_PROBS))
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))

# ── pass/fail helpers ─────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0


def check(label: str, ok: bool, detail: str = '') -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f'  PASS  {label}')
    else:
        _FAIL += 1
        suffix = f'  ({detail})' if detail else ''
        print(f'  FAIL  {label}{suffix}')


# ── warehouse builder ─────────────────────────────────────────────────────────

def _build_wh_cfg(n_skus: int) -> WarehouseConfig:
    """Size the warehouse so 1-bin-per-SKU reaches ~87% fill."""
    from Aisle_Dimensions import SIZE_HEIGHTS, unit_bin_width as _ubw
    n_types = len(_AISLE_CFGS)   # 48
    from Aisle_Dimensions import SINGLETON_BIN_HEIGHT as _SBH
    def _bins(cfg: AisleConfig) -> int:
        n_cols = _AISLE_W // _ubw(cfg.unit_type)
        if cfg.unit_type == 'singleton':
            return n_cols * (_AISLE_H // _SBH)
        probs  = cfg.size_probabilities or [1.0 / len(cfg.storage_sizes)] * len(cfg.storage_sizes)
        n_rows = sum(round(p * _AISLE_H) // SIZE_HEIGHTS[s] for s, p in zip(cfg.storage_sizes, probs))
        return n_cols * n_rows
    bins_per_aisle = _bins(_AISLE_CFGS[0])  # representative pallet aisle
    total_bins   = n_types * bins_per_aisle
    # Extra replicas only if n_skus genuinely exceeds capacity.
    replicas     = max(1, math.ceil(n_skus / total_bins))
    total_aisles = n_types * replicas
    weight       = replicas / total_aisles  # = 1/n_types; all types equal weight
    return WarehouseConfig(
        total_aisles  = total_aisles,
        aisle_splits  = [weight] * n_types,
        aisle_configs = _AISLE_CFGS,
    )


# ── test runner ───────────────────────────────────────────────────────────────

def run() -> bool:
    global _PASS, _FAIL
    _PASS = _FAIL = 0

    print(f'\n{"="*66}')
    print(f'  Reorder queue stability — dense warehouse (run_simulation config)')
    print(f'  SKUs={N_SKUS}  batches={N_BATCHES}  pickers={N_PICKERS}')
    print(f'  aisle_dims={_AISLE_W}×{_AISLE_H}  aisle_types={len(_AISLE_CFGS)}  seed={SEED}')
    print(f'{"="*66}')

    # ── inventory ─────────────────────────────────────────────────────────────
    inventory = build_inventory_with_profile(
        num_skus           = N_SKUS,
        handling_splits    = [0.5, 0.5],
        category_splits    = [1 / 6] * 6,
        singleton_fraction = 0.5,
        dim_spec           = DEFAULT_DIM_SPEC,
        weight_spec        = DEFAULT_WEIGHT_SPEC,
        seed               = SEED,
    )
    n_skus   = len(inventory.cartons)
    eq_qtys  = [c.equilibrium_qty       for c in inventory.cartons]
    rops     = [c.reorder_point         for c in inventory.cartons]
    ebds     = [c.expected_batch_demand for c in inventory.cartons]

    print(f'  equilibrium_qty:       min={min(eq_qtys)}  max={max(eq_qtys)}  '
          f'mean={sum(eq_qtys)/len(eq_qtys):.1f}')
    print(f'  reorder_point:         min={min(rops)}  max={max(rops)}  '
          f'mean={sum(rops)/len(rops):.2f}')
    print(f'  expected_batch_demand: min={min(ebds):.2f}  max={max(ebds):.2f}  '
          f'mean={sum(ebds)/len(ebds):.2f}')

    # ── warehouse ──────────────────────────────────────────────────────────────
    wh_cfg = _build_wh_cfg(n_skus)
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    warehouse  = Warehouse_Builder().from_config(wh_cfg).build()
    total_bins = len(warehouse.bins)

    # ── initial stock: 1 bin per SKU ──────────────────────────────────────────
    random.seed(SEED + 100)
    mgr = Inventory_Manager(warehouse)
    mgr.enqueue_all(inventory.cartons)
    init_fill  = len(mgr.unavailable) / total_bins
    init_queue = mgr.queue_depth

    print(f'  Warehouse : {len(warehouse.aisles)} aisles / {total_bins:,} bins')
    print(f'  Fill      : {len(mgr.unavailable):,} / {total_bins:,} = {init_fill:.1%}')
    print(f'  Queue after initial stock: {init_queue}')

    pick_cfg = PickConfig(
        num_pickers      = N_PICKERS,
        x_speed      = 1.0,
        y_speed      = 0.5,
        pick_intercept   = 1.0,
        pick_weight_coef = 1.1,
        pick_volume_coef = 1e-3,
        cart_swap_coef   = 10.0,
    )
    batch_cfg = BatchConfig(
        inventory_size = n_skus,
        mean_fraction  = BATCH_MEAN_FRAC,
        std_fraction   = 0.03,
    )

    queue_depths:    list[int]   = []
    fill_rates:      list[float] = []
    total_triggered: int         = 0

    random.seed(SEED + 200)
    t0 = time.perf_counter()

    for i in range(N_BATCHES):
        triggered = mgr.check_reorders()
        total_triggered += len(triggered)

        batch = Batch(batch_cfg, inventory, affinity=None)
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()

        q    = mgr.queue_depth
        fill = len(mgr.unavailable) / total_bins
        queue_depths.append(q)
        fill_rates.append(fill)

        if (i + 1) % 10 == 0:
            print(f'  batch {i+1:3d}  queue={q:5d}  fill={fill:.1%}  '
                  f'reorders_so_far={total_triggered}')

    elapsed = time.perf_counter() - t0
    print(f'\n  {N_BATCHES} batches  wall={elapsed:.1f}s  '
          f'total_reorders={total_triggered}')

    max_q        = max(queue_depths)
    stable       = queue_depths[-STABLE_WINDOW:]
    mid          = STABLE_WINDOW // 2
    first_half   = mean(stable[:mid])
    second_half  = mean(stable[mid:])
    growing      = second_half > first_half * 1.2
    final_fill   = fill_rates[-1]
    min_expected = int(n_skus * 0.05)

    print(f'\n  Queue max across all batches : {max_q}  (limit: {n_skus})')
    print(f'  Last {STABLE_WINDOW} batches  first-half mean={first_half:.1f}  '
          f'second-half mean={second_half:.1f}  growing={growing}')
    print(f'  Final fill rate : {final_fill:.1%}  (min: {MIN_FILL:.0%})')
    print(f'  Total reorders  : {total_triggered}  (min expected: {min_expected})')

    # Check that both pallet and singleton bins are occupied — confirms the
    # min-pallets + singleton-remainder packing logic is active.
    pallet_occupied   = sum(
        1 for b in mgr._unavailable.values()
        if b.storage is not None and b.unit_type == 'pallet'
    )
    singleton_occupied = sum(
        1 for b in mgr._unavailable.values()
        if b.storage is not None and b.unit_type == 'singleton'
    )

    print(f'\n  Bin type breakdown: pallet={pallet_occupied}  singleton={singleton_occupied}')
    print(f'\n  Checks:')
    all_eq_set  = all(hasattr(c, 'equilibrium_qty')       for c in inventory.cartons)
    all_rops_set = all(hasattr(c, 'reorder_point')        for c in inventory.cartons)
    all_ebds_set = all(hasattr(c, 'expected_batch_demand') for c in inventory.cartons)
    check(
        'equilibrium_qty, reorder_point and expected_batch_demand set on all cartons',
        all_eq_set and all_rops_set and all_ebds_set,
        f'eq_set={all_eq_set}  rops_set={all_rops_set}  ebds_set={all_ebds_set}',
    )
    check(
        f'queue not growing in last {STABLE_WINDOW} batches',
        not growing,
        f'first_half={first_half:.1f}  second_half={second_half:.1f}',
    )
    check(
        f'at least {min_expected} reorders triggered',
        total_triggered >= min_expected,
        f'triggered={total_triggered}',
    )
    check(
        f'fill rate >= {MIN_FILL:.0%} at end',
        final_fill >= MIN_FILL,
        f'fill={final_fill:.1%}',
    )
    check(
        'both pallet and singleton bins occupied (min-pallets packing active)',
        pallet_occupied > 0 and singleton_occupied > 0,
        f'pallet={pallet_occupied}  singleton={singleton_occupied}',
    )

    print(f'\n  Result: {_PASS} passed  {_FAIL} failed')
    print(f'{"="*66}\n')
    return _FAIL == 0


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
