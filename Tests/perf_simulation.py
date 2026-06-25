"""
perf_simulation.py — end-to-end simulation performance benchmark.

Builds a small in-memory warehouse and inventory (no DB required), runs N
batch iterations, and reports per-phase timing so bottlenecks are visible.

Usage
-----
    cd Tests
    python perf_simulation.py              # 200 batches, default size
    python perf_simulation.py --batches 50 --skus 500 --bins-per-aisle 50

Phases timed
------------
  check_reorders   manager.check_reorders() for all 3 warehouses
  batch            Batch() construction (SKU sampling)
  tasks            Task.from_batch() × 3
  sim_A/B/C        PickSimulation.run() per strategy
  stats            extract_batch_stats + extract_task_stats × 3
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import defaultdict

# ── path setup ──────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Optimization'))

import math
import numpy as np

from Aisle_Storage import Aisle
from Affinity_Store import AffinityStore
from Order import Order
from Demand import Demand
from Inventory_Builder import Inventory
from Inventory_Management import Inventory_Manager, LoadParams, Placement
from Assignment_Functions import (
    build_load_minimizing_assignment_fn,
    build_load_maximizing_assignment_fn,
)
from Pick import PickConfig, PickSimulation
from Storage_Primitive import Storage_Size
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig, Task
from Simulation_Analytics import extract_batch_stats, extract_task_stats


def _build_affinity_store(inventory: Inventory, top_k: int = 20, seed: int = 0) -> AffinityStore:
    """Build a small in-memory AffinityStore with synthetic demand-weighted affinity."""
    import sqlite3, math
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE affinity (
            sku_i INTEGER NOT NULL,
            sku_j INTEGER NOT NULL,
            lift  REAL    NOT NULL,
            PRIMARY KEY (sku_i, sku_j)
        );
        CREATE INDEX idx_affinity_sku_i ON affinity(sku_i);
    ''')
    rng = random.Random(seed)
    # Group by storage_type; within each group assign Pareto rank scores
    from collections import defaultdict
    groups: dict[tuple, list] = defaultdict(list)
    for c in inventory.orders:
        groups[c.storage_type].append(c)
    rows = []
    for group_cartons in groups.values():
        n = len(group_cartons)
        sorted_g = sorted(group_cartons, key=lambda c: c.demand.relative_frequency * c.demand.quantity_rate, reverse=True)
        rank_score = {c.sku: 1.0 / (r + 1) ** 0.5 for r, c in enumerate(sorted_g)}
        cand_k = min(top_k + 10, n - 1)
        for c_i in sorted_g:
            candidates = [c for c in sorted_g[:cand_k + 1] if c is not c_i][:cand_k]
            scored = []
            for c_j in candidates:
                geo  = (rank_score[c_i.sku] * rank_score[c_j.sku]) ** 0.5
                lift = max(1.0, min(5.0, 1.0 + 4.0 * geo + rng.gauss(0, 0.15)))
                scored.append((lift, c_j.sku))
            scored.sort(reverse=True)
            for lift_val, sku_j in scored[:top_k]:
                rows.append((c_i.sku, sku_j, lift_val))
                rows.append((sku_j, c_i.sku, lift_val))
    conn.executemany('INSERT OR IGNORE INTO affinity VALUES (?,?,?)', rows)
    conn.commit()
    store = AffinityStore.__new__(AffinityStore)
    store._conn = conn
    store._rng  = random.Random(seed)
    store._load_matrix()
    return store


# ── synthetic inventory builder ──────────────────────────────────────────────

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_ALL_SIZES  = ['small', 'medium', 'large', 'extra_large']


def _build_inventory(n_skus: int, seed: int = 42) -> Inventory:
    rng = random.Random(seed)
    # Reset the class-level SKU counter for reproducibility across runs
    Order.next_sku = 1
    random.seed(seed)   # Order.__init__ uses the global random state
    orders = []
    handlings  = ['conveyable', 'non-conveyable']
    for _ in range(n_skus):
        handling = rng.choice(handlings)
        category = rng.choice(_CATEGORIES)
        c = Order((handling, category))
        orders.append(c)
    return Inventory(orders)


def _build_warehouse_cfg(
    n_skus        : int,
    bins_per_aisle: int,
    extra_pct     : float = 0.15,
) -> WarehouseConfig:
    n_pallet_types   = 48
    n_singleton_types = 12
    total_bins_needed = math.ceil(n_skus * (1 + extra_pct))
    replicas = max(1, math.ceil(
        total_bins_needed / ((n_pallet_types + n_singleton_types) * bins_per_aisle)
    ))
    total_aisles = (n_pallet_types + n_singleton_types) * replicas
    # Weight per aisle TYPE (not per aisle) — must sum to 1.0 across all types
    pw = replicas / total_aisles
    sw = replicas / total_aisles

    # Convert old bin-count slots to physical dimensions (1 slot = 48 units)
    n_cols = max(1, bins_per_aisle // 20)
    n_rows = 20
    aisle_w = n_cols * 48   # physical width
    aisle_h = n_rows * 48   # physical height

    aisle_cfgs = []
    for size in _ALL_SIZES:
        for cat in _CATEGORIES:
            aisle_cfgs.append(AisleConfig('conveyable',     cat, 'pallet', aisle_w, aisle_h, [size], None))
            aisle_cfgs.append(AisleConfig('non-conveyable', cat, 'pallet', aisle_w, aisle_h, [size], None))
    for cat in _CATEGORIES:
        aisle_cfgs.append(AisleConfig('conveyable',     cat, 'singleton', aisle_w, aisle_h, ['singleton'], None))
        aisle_cfgs.append(AisleConfig('non-conveyable', cat, 'singleton', aisle_w, aisle_h, ['singleton'], None))

    return WarehouseConfig(
        total_aisles  = total_aisles,
        aisle_splits  = [pw] * n_pallet_types + [sw] * n_singleton_types,
        aisle_configs = aisle_cfgs,
    )


# ── benchmark runner ─────────────────────────────────────────────────────────

def run_benchmark(
    n_skus        : int  = 2_000,
    bins_per_aisle: int  = 100,
    n_batches     : int  = 200,
    n_pickers     : int  = 10,
    seed          : int  = 42,
) -> None:
    print(f'\n{"="*62}')
    print(f'  Simulation benchmark')
    print(f'  SKUs={n_skus:,}  bins/aisle={bins_per_aisle}  '
          f'batches={n_batches}  pickers={n_pickers}')
    print(f'{"="*62}')

    # ── build assets ───────────────────────────────────────────────────────
    t0 = time.perf_counter()
    random.seed(seed)
    np.random.seed(seed)
    inventory = _build_inventory(n_skus, seed)

    wh_cfg = _build_warehouse_cfg(n_skus, bins_per_aisle)

    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse_A = Warehouse_Builder().from_config(wh_cfg).build()
    manager_A   = Inventory_Manager(warehouse_A)
    random.seed(seed + 1)
    manager_A.enqueue_all(inventory.orders, quantity=1)

    from Workload import WorkloadParams
    pick_cfg = PickConfig(
        num_pickers      = n_pickers,
        x_speed      = 1.0,
        y_speed      = 0.5,
        pick_intercept   = 1.0,
        pick_weight_coef = 1.1,
        pick_volume_coef = 1e-3,
        cart_swap_coef   = 10.0,
    )
    wp          = WorkloadParams.from_pick_config(pick_cfg)
    load_params = LoadParams(lambda_=1.0, k=1.0, gamma=1.5)

    print(f'  Building affinity store...')
    affinity_store = _build_affinity_store(inventory, top_k=20, seed=seed)

    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse_B = Warehouse_Builder().from_config(wh_cfg).build()
    manager_B   = Inventory_Manager(warehouse_B, affinity=affinity_store)
    random.seed(seed + 1)
    manager_B.enqueue_all(inventory.orders, quantity=1)
    manager_B.init_lift_state(affinity_store)
    manager_B.placement = Placement('load_min', build_load_minimizing_assignment_fn(
        load_params, affinity_store, wp,
        manager_B._aisle_sku_sets, manager_B._aisle_lift_sum,
        manager_B._aisle_idx_sets,
    ))

    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse_C = Warehouse_Builder().from_config(wh_cfg).build()
    manager_C   = Inventory_Manager(warehouse_C, affinity=affinity_store)
    random.seed(seed + 1)
    manager_C.enqueue_all(inventory.orders, quantity=1)
    manager_C.init_lift_state(affinity_store)
    manager_C.placement = Placement('load_max', build_load_maximizing_assignment_fn(
        load_params, affinity_store, wp,
        manager_C._aisle_sku_sets, manager_C._aisle_lift_sum,
        manager_C._aisle_idx_sets,
    ))

    placed_A = len(manager_A.unavailable)
    total_bins = len(warehouse_A.bins)
    print(f'  Warehouse : {total_bins:,} bins  filled={placed_A:,} ({placed_A/total_bins:.1%})')
    print(f'  Setup     : {time.perf_counter()-t0:.2f}s')

    batch_cfg   = BatchConfig(
        inventory_size = n_skus,
        mean_fraction  = 0.25,
        std_fraction   = 0.03,
    )

    random.seed(seed + 100)

    # ── timing accumulators ────────────────────────────────────────────────
    _t: dict[str, float] = {k: 0.0 for k in
        ('reorders', 'batch', 'tasks', 'sim_A', 'sim_B', 'sim_C', 'stats')}
    skipped = 0

    print(f'\n  Running {n_batches} batches...\n')
    t_loop = time.perf_counter()

    for i in range(n_batches):
        _t0 = time.perf_counter()
        manager_A.check_reorders()
        manager_B.check_reorders()
        manager_C.check_reorders()
        _t['reorders'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        batch = Batch(batch_cfg, inventory, affinity=None)
        _t['batch'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        ta = Task.from_batch(batch, warehouse_A, manager=manager_A)
        tb = Task.from_batch(batch, warehouse_B, manager=manager_B)
        tc = Task.from_batch(batch, warehouse_C, manager=manager_C)
        _t['tasks'] += time.perf_counter() - _t0

        if not ta or not tb or not tc:
            skipped += 1
            continue

        _t0 = time.perf_counter()
        ea = PickSimulation(ta, pick_cfg, manager=manager_A).run()
        _t['sim_A'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        eb = PickSimulation(tb, pick_cfg, manager=manager_B).run()
        _t['sim_B'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        ec = PickSimulation(tc, pick_cfg, manager=manager_C).run()
        _t['sim_C'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        extract_batch_stats(ea, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(eb, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(ec, batch_id=i, k_pickers=n_pickers)
        extract_task_stats(ea, ta, batch_id=i, affinity={}, wp=wp)
        extract_task_stats(eb, tb, batch_id=i, affinity={}, wp=wp)
        extract_task_stats(ec, tc, batch_id=i, affinity={}, wp=wp)
        _t['stats'] += time.perf_counter() - _t0

        if (i + 1) % 50 == 0:
            wall = time.perf_counter() - t_loop
            print(f'  Batch {i+1:4d}/{n_batches}  {(i+1)/wall:.2f} batches/s')

    wall  = time.perf_counter() - t_loop
    total = sum(_t.values())
    done  = n_batches - skipped

    print(f'\n{"="*62}')
    print(f'  {done} batches in {wall:.1f}s  ({done/wall:.2f} batches/s)  '
          f'skipped={skipped}')
    print(f'\n  Phase breakdown (total {total:.1f}s across {done} batches):')
    print(f'  {"Phase":<14}  {"Total":>8}  {"Per batch":>10}  {"Share":>7}')
    print(f'  {"-"*14}  {"--------":>8}  {"----------":>10}  {"-------":>7}')
    for phase, t in _t.items():
        pct = t / total * 100 if total > 0 else 0
        per = t / max(done, 1) * 1000
        print(f'  {phase:<14}  {t:>8.2f}s  {per:>9.1f}ms  {pct:>6.1f}%')
    print(f'{"="*62}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Simulation performance benchmark')
    parser.add_argument('--skus',           type=int, default=2_000)
    parser.add_argument('--bins-per-aisle', type=int, default=100)
    parser.add_argument('--batches',        type=int, default=200)
    parser.add_argument('--pickers',        type=int, default=10)
    parser.add_argument('--seed',           type=int, default=42)
    args = parser.parse_args()

    run_benchmark(
        n_skus         = args.skus,
        bins_per_aisle = args.bins_per_aisle,
        n_batches      = args.batches,
        n_pickers      = args.pickers,
        seed           = args.seed,
    )
