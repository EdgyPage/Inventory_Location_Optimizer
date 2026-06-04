"""bench_sample_to_capacity.py — perf benchmark for warehouse planning.

Times Inventory_Manager.plan_warehouse on increasingly large synthetic
inventories and counts geometry _can_fit calls (the dominant cost in
Pallet._fit / _max_qty_fits).  The batched sampler should keep _can_fit calls
~O(num_skus) — far below total_bins — proving _fit was lifted out of the
per-bin fill loop, while expected_fill stays unchanged.

Not part of the pass/fail suite.  Run directly:
    cd Tests
    python bench_sample_to_capacity.py
"""
from __future__ import annotations

import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

import Storage_Primitive
from Aisle_Dimensions import aisle_width_for, aisle_height_for
from Inventory_Management import Inventory_Manager
from generate_inventory import (
    build_inventory_with_profile, DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
)

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_HANDLINGS  = ['conveyable', 'non-conveyable']
_AW, _AH    = aisle_width_for(50), aisle_height_for(10)   # real dims: 2400 x 480

# ── instrument the geometry test ────────────────────────────────────────────
_fit_calls = 0
_orig_can_fit = Storage_Primitive._can_fit


def _counting_can_fit(*args, **kwargs):
    global _fit_calls
    _fit_calls += 1
    return _orig_can_fit(*args, **kwargs)


Storage_Primitive._can_fit = _counting_can_fit


def _bench(n_skus: int) -> None:
    global _fit_calls
    inv = build_inventory_with_profile(
        num_skus=n_skus, seed=5,
        handling_splits=[0.5, 0.5], category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC, weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0, reorder_safety_batches=2.0,
    )
    _fit_calls = 0
    t0 = time.perf_counter()
    plan = Inventory_Manager.plan_warehouse(
        inv.cartons, categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=_AW, aisle_height=_AH, target_fill=0.85,
        rng=random.Random(1),
    )
    dt = time.perf_counter() - t0
    fits_per_bin = _fit_calls / plan.total_bins if plan.total_bins else 0.0
    fits_per_sku = _fit_calls / n_skus if n_skus else 0.0
    print(f'  n_skus={n_skus:>6,}  '
          f'time={dt:7.2f}s  '
          f'bins={plan.total_bins:>9,}  '
          f'_can_fit={_fit_calls:>9,}  '
          f'fits/bin={fits_per_bin:6.3f}  '
          f'fits/sku={fits_per_sku:5.1f}  '
          f'fill={plan.expected_fill:5.1%}')


if __name__ == '__main__':
    print('\nplan_warehouse benchmark (real aisle dims 2400x480)')
    print('  fits/bin << 1 means _fit scales with SKUs, not bins (the win).\n')
    for n in (1000, 4000, 8000):
        _bench(n)
    print('\nDone.')
