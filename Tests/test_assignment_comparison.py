"""test_assignment_comparison.py

Sparse-demand assignment comparison.  Runs N_BATCHES with 5% of inventory
demanded per batch (~80% aisle coverage) so that co-location effects on
n_tasks and makespan are measurable.

The three strategies are passed in as builder callables at call time:

    run(builder_A, builder_B, builder_C, label_A, label_B, label_C)

  builder = None         -> uniform random (Inventory_Manager default)
  builder = <callable>   -> called as builder(affinity, wp, sku_sets,
                            idx_sets, demand_sum, freq_by_idx, freq_by_sku,
                            qty_by_sku, beta=1.0) -> AssignmentFn

Defaults:
  A = None  (uniform)
  B = build_trip_minimizing_assignment_fn
  C = build_trip_maximizing_assignment_fn

Usage:
    cd Tests
    python test_assignment_comparison.py
"""
from __future__ import annotations

import os
import random
import sys
import time
from statistics import mean, stdev
from typing import Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Aisle_Storage import Aisle
from Inventory_Management import (
    Inventory_Manager,
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
)
from Pick import PickConfig, PickSimulation
from Workload import WorkloadParams
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, BatchConfig, Task

from perf_simulation import _build_inventory, _build_affinity_store, _build_warehouse_cfg

# ── parameters ────────────────────────────────────────────────────────────────

SEED            = 42
N_SKUS          = 2_000
BINS_PER_AISLE  = 100
N_BATCHES       = 200
N_PICKERS       = 5
BATCH_MEAN_FRAC = 0.05
CHECK_WINDOW    = 100

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


# ── manager factory ───────────────────────────────────────────────────────────

def _build_manager(wh_cfg, wh_seed, affinity, inventory, wp, builder):
    """Build a warehouse and attach an assignment function for reorders.

    builder=None   -> uniform random (no affinity state initialised)
    builder=<fn>   -> called with (affinity, wp, sku_sets, idx_sets,
                       demand_sum, freq_by_idx, freq_by_sku, qty_by_sku,
                       beta=1.0) to produce the AssignmentFn
    """
    Aisle.next_aisle_id = 1
    random.seed(wh_seed)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=(affinity if builder is not None else None))
    random.seed(wh_seed + 1)
    mgr.enqueue_all(inventory.cartons)

    if builder is not None:
        mgr.init_lift_state(affinity)
        mgr.init_demand_state(inventory)
        freq_by_sku = {c.sku: c.demand.frequency     for c in inventory.cartons}
        qty_by_sku  = {c.sku: c.demand.quantity_rate  for c in inventory.cartons}
        freq_by_idx = {
            affinity._sku_to_idx[c.sku]: c.demand.frequency
            for c in inventory.cartons if c.sku in affinity._sku_to_idx
        }
        mgr.assignment_fn = builder(
            affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
        )

    return wh, mgr


# ── comparison runner ─────────────────────────────────────────────────────────

def run(
    builder_A: Callable | None = None,
    builder_B: Callable | None = build_trip_minimizing_assignment_fn,
    builder_C: Callable | None = build_trip_maximizing_assignment_fn,
    label_A:   str             = 'Uniform',
    label_B:   str             = 'Trip-Min',
    label_C:   str             = 'Trip-Max',
) -> bool:
    global _PASS, _FAIL
    _PASS = _FAIL = 0

    w_col = max(len(label_A), len(label_B), len(label_C)) + 2

    print(f'\n{"="*64}')
    print(f'  Sparse assignment comparison  (batch_frac={BATCH_MEAN_FRAC:.0%})')
    print(f'  SKUs={N_SKUS}  bins/aisle={BINS_PER_AISLE}  '
          f'batches={N_BATCHES}  pickers={N_PICKERS}  seed={SEED}')
    print(f'  A={label_A}  B={label_B}  C={label_C}')
    print(f'{"="*64}')

    random.seed(SEED)
    inventory = _build_inventory(N_SKUS, SEED)
    wh_cfg    = _build_warehouse_cfg(N_SKUS, BINS_PER_AISLE)
    affinity  = _build_affinity_store(inventory, top_k=20, seed=SEED)

    pick_cfg = PickConfig(
        num_pickers      = N_PICKERS,
        x_speed      = 1.0,
        y_speed      = 0.5,
        pick_intercept   = 1.0,
        pick_weight_coef = 1.1,
        pick_volume_coef = 1e-3,
        cart_swap_coef   = 10.0,
    )
    wp = WorkloadParams.from_pick_config(pick_cfg)

    batch_cfg = BatchConfig(
        inventory_size = N_SKUS,
        mean_fraction  = BATCH_MEAN_FRAC,
        std_fraction   = 0.01,
    )

    print(f'  Building warehouses...')
    t0 = time.perf_counter()
    wh_A, mgr_A = _build_manager(wh_cfg, SEED, affinity, inventory, wp, builder_A)
    wh_B, mgr_B = _build_manager(wh_cfg, SEED, affinity, inventory, wp, builder_B)
    wh_C, mgr_C = _build_manager(wh_cfg, SEED, affinity, inventory, wp, builder_C)
    print(f'  Ready ({time.perf_counter()-t0:.1f}s)  '
          f'bins={len(wh_A.bins):,}  filled={len(mgr_A.unavailable):,}')

    durations_A, durations_B, durations_C = [], [], []
    ntasks_A,    ntasks_B,    ntasks_C    = [], [], []
    skipped = 0

    def _makespan(events):
        return max((e.time for e in events if e.event_type == 'done'), default=0.0)

    random.seed(SEED + 100)
    t_loop = time.perf_counter()

    for i in range(N_BATCHES):
        mgr_A.check_reorders()
        mgr_B.check_reorders()
        mgr_C.check_reorders()

        batch = Batch(batch_cfg, inventory, affinity=None)

        ta = Task.from_batch(batch, wh_A, manager=mgr_A)
        tb = Task.from_batch(batch, wh_B, manager=mgr_B)
        tc = Task.from_batch(batch, wh_C, manager=mgr_C)

        if not ta or not tb or not tc:
            skipped += 1
            continue

        ea = PickSimulation(ta, pick_cfg, manager=mgr_A).run()
        eb = PickSimulation(tb, pick_cfg, manager=mgr_B).run()
        ec = PickSimulation(tc, pick_cfg, manager=mgr_C).run()

        durations_A.append(_makespan(ea)); ntasks_A.append(len(ta))
        durations_B.append(_makespan(eb)); ntasks_B.append(len(tb))
        durations_C.append(_makespan(ec)); ntasks_C.append(len(tc))

        if (i + 1) % 50 == 0:
            elapsed = time.perf_counter() - t_loop
            print(f'  batch {i+1:3d}  '
                  f'dur  A={durations_A[-1]:7.1f} B={durations_B[-1]:7.1f} C={durations_C[-1]:7.1f}'
                  f'  tasks  A={ntasks_A[-1]:3d} B={ntasks_B[-1]:3d} C={ntasks_C[-1]:3d}'
                  f'  ({elapsed:.1f}s)')

    n      = len(durations_A)
    elapsed = time.perf_counter() - t_loop
    print(f'\n  {n} valid batches  skipped={skipped}  wall={elapsed:.1f}s')

    w  = min(CHECK_WINDOW, n)
    mA_d = mean(durations_A[-w:]); mB_d = mean(durations_B[-w:]); mC_d = mean(durations_C[-w:])
    mA_t = mean(ntasks_A[-w:]);    mB_t = mean(ntasks_B[-w:]);    mC_t = mean(ntasks_C[-w:])

    ds_B  = list(mgr_B._aisle_demand_sum.values())
    ds_C  = list(mgr_C._aisle_demand_sum.values())
    std_B = stdev(ds_B) if len(ds_B) > 1 else 0.0
    std_C = stdev(ds_C) if len(ds_C) > 1 else 0.0
    max_B = max(ds_B, default=0.0)
    max_C = max(ds_C, default=0.0)

    def pct(x, ref): return (x - ref) / max(ref, 1e-9) * 100

    print(f'\n  Duration (last {w} batches):')
    print(f'    A {label_A:<{w_col}}{mA_d:8.2f}')
    print(f'    B {label_B:<{w_col}}{mB_d:8.2f}  ({pct(mB_d,mA_d):+.1f}% vs A)')
    print(f'    C {label_C:<{w_col}}{mC_d:8.2f}  ({pct(mC_d,mA_d):+.1f}% vs A)')

    print(f'\n  Aisles visited per batch (last {w}):')
    print(f'    A {label_A:<{w_col}}{mA_t:5.1f}')
    print(f'    B {label_B:<{w_col}}{mB_t:5.1f}  ({pct(mB_t,mA_t):+.1f}% vs A)')
    print(f'    C {label_C:<{w_col}}{mC_t:5.1f}  ({pct(mC_t,mA_t):+.1f}% vs A)')

    print(f'\n  Aisle demand_sum  B std={std_B:.3f} max={max_B:.2f}'
          f'  |  C std={std_C:.3f} max={max_C:.2f}')

    print(f'\n  Checks:')
    check(f'B ({label_B}) duration <= A ({label_A})',
          mB_d <= mA_d, f'B={mB_d:.2f}  A={mA_d:.2f}')
    check(f'C ({label_C}) duration >= A ({label_A})',
          mC_d >= mA_d, f'C={mC_d:.2f}  A={mA_d:.2f}')
    check('B duration < C',
          mB_d < mC_d, f'B={mB_d:.2f}  C={mC_d:.2f}')
    check(f'B ({label_B}) n_tasks <= A ({label_A})',
          mB_t <= mA_t, f'B={mB_t:.1f}  A={mA_t:.1f}')
    check(f'C ({label_C}) n_tasks >= A ({label_A})',
          mC_t >= mA_t, f'C={mC_t:.1f}  A={mA_t:.1f}')
    check('B n_tasks < C',
          mB_t < mC_t, f'B={mB_t:.1f}  C={mC_t:.1f}')
    check('B demand_sum std >= C  (B hotspot aisles heavier than C)',
          std_B >= std_C, f'B={std_B:.3f}  C={std_C:.3f}')
    check('B demand_sum max >= C',
          max_B >= max_C, f'B={max_B:.3f}  C={max_C:.3f}')

    print(f'\n  Result: {_PASS} passed  {_FAIL} failed')
    print(f'{"="*64}\n')
    return _FAIL == 0


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
