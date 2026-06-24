"""trace_lifecycle.py — instrumented simulation lifecycle tracer.

Runs a small simulation for one or more strategies and records, per batch, the
lifecycle of inventory units:

    intake -> units placed -> stuck-in-queue -> picked -> emptied -> reclaimed

plus per-aisle / per-bucket fill, queue depth, and per-function call timings.
The manager's lifecycle methods are wrapped on the instance (no edits to
Warehouse/ or Optimization/ source — same approach as Tests/profile_lifecycle.py).

The default compares a uniform-stocked strategy (fills to the target ~85%) against
a policy-stocked one (which can leave units stuck in the queue -> lower fill),
reproducing the "some warehouses start at ~70% vs ~85%" discrepancy.

Output: Diagnostics/out/trace_<strategy>.json (one per strategy) plus manifest.json.
Open the dashboard with:

    cd Diagnostics && python -m http.server 8009
    # then browse http://localhost:8009/static/

Usage
-----
    python Diagnostics/trace_lifecycle.py
    python Diagnostics/trace_lifecycle.py --strategies uni_fifo_norsl,opt_map_norsl
    python Diagnostics/trace_lifecycle.py --skus 3000 --batches 40 --pickers 10
    python Diagnostics/trace_lifecycle.py --list      # print available strategy keys
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_OUT_DIR   = os.path.join(_HERE, 'out')
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Optimization'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Tests'))

import numpy as np

from Aisle_Storage import Aisle
from Inventory_Builder import Inventory
from Inventory_Management import Inventory_Manager
from Warehouse_Builder import Warehouse_Builder
from Workload import WorkloadParams
from Workload_Builder import Batch, BatchConfig, Task
from Pick import PickConfig, PickSimulation
from strategies import STRATEGY_BY_KEY, STRATEGIES, StrategyContext

# Reuse the small-sim builders from the perf harness (no DB needed).
from perf_simulation import _build_inventory, _build_affinity_store, _CATEGORIES

_HANDLINGS = ['conveyable', 'non-conveyable']
_GRID_COLS = 6


# ── order equilibrium (OUP) fields — mirrors Tests/profile_lifecycle._set_equilibrium ──

def _set_equilibrium(orders, lead_time: float = 2.0, supply_cv: float = 0.1) -> None:
    for c in orders:
        expected = c.demand.frequency * c.demand.quantity_rate
        eq_qty   = max(1, min(3, round(expected * 2)))
        c.expected_batch_demand = expected
        c.equilibrium_qty       = eq_qty
        c.reorder_point         = 1
        c.lead_time_mean        = lead_time
        c.supply_cv             = supply_cv


# ── per-batch lifecycle tracer ────────────────────────────────────────────────

class Tracer:
    """Per-batch accumulators populated by the wrapped manager methods."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.placed    = 0
        self.picked    = 0
        self.emptied   = 0
        self.reclaimed = 0
        self.fn: dict[str, list] = defaultdict(lambda: [0, 0.0])   # name -> [calls, total_s]


def _install_hooks(mgr: Inventory_Manager, tr: Tracer) -> None:
    """Wrap manager lifecycle methods on the instance to feed the tracer.

    Only manager-level methods are wrapped (never reassigned by strategy build),
    so a strategy swapping mgr.placement does not detach the hooks.
    """
    orig_exec = mgr._execute_placement
    def _execute_placement(unit, bin_):
        t = time.perf_counter()
        r = orig_exec(unit, bin_)
        f = tr.fn['_execute_placement']; f[0] += 1; f[1] += time.perf_counter() - t
        tr.placed += 1
        return r
    mgr._execute_placement = _execute_placement

    orig_apply = mgr._apply_picks_batch
    def _apply_picks_batch(picks, empties):
        t = time.perf_counter()
        r = orig_apply(picks, empties)
        f = tr.fn['_apply_picks_batch']; f[0] += 1; f[1] += time.perf_counter() - t
        tr.picked  += len(picks)
        tr.emptied += len(empties)
        return r
    mgr._apply_picks_batch = _apply_picks_batch

    orig_reclaim = mgr._reclaim_empty_bins
    def _reclaim_empty_bins():
        n = len(mgr._pending_reclaim)
        t = time.perf_counter()
        r = orig_reclaim()
        f = tr.fn['_reclaim_empty_bins']; f[0] += 1; f[1] += time.perf_counter() - t
        tr.reclaimed += n
        return r
    mgr._reclaim_empty_bins = _reclaim_empty_bins

    orig_chk = mgr.check_reorders
    def check_reorders():
        t = time.perf_counter()
        r = orig_chk()
        f = tr.fn['check_reorders']; f[0] += 1; f[1] += time.perf_counter() - t
        return r
    mgr.check_reorders = check_reorders


# ── warehouse geometry / fill helpers ─────────────────────────────────────────

def _aisle_bucket(aisle) -> str:
    size = getattr(aisle, 'storage_size', None)
    if size is None and aisle.bins:
        size = aisle.bins[0].storage_size
    return f'{aisle.handling_type}|{aisle.storage_type}|{size}|{aisle.unit_type}'


def _warehouse_layout(warehouse) -> tuple[list[dict], dict[int, int]]:
    """Serialise aisles (one heatmap cell each) + return per-aisle capacity."""
    aisles, capacity = [], {}
    for idx, a in enumerate(warehouse.aisles):
        cap = len(a.bins)
        capacity[a.aisle_id] = cap
        aisles.append({
            'aisle_id': a.aisle_id,
            'handling': a.handling_type,
            'category': a.storage_type,
            'unit_type': a.unit_type,
            'bucket':   _aisle_bucket(a),
            'capacity': cap,
            'grid_col': idx % _GRID_COLS,
            'grid_row': idx // _GRID_COLS,
        })
    return aisles, capacity


def _fill_snapshot(mgr, warehouse, capacity: dict[int, int]) -> dict:
    """Occupied / capacity → fill_overall, fill_by_aisle, fill_by_bucket."""
    occ_aisle: Counter = Counter(b.location[0] for b in mgr._unavailable.values())
    total_cap = sum(capacity.values()) or 1
    fill_overall = len(mgr._unavailable) / total_cap

    fill_by_aisle = {}
    bucket_occ: Counter = Counter()
    bucket_cap: Counter = Counter()
    bucket_of = {a.aisle_id: _aisle_bucket(a) for a in warehouse.aisles}
    for a in warehouse.aisles:
        cap = capacity[a.aisle_id] or 1
        occ = occ_aisle.get(a.aisle_id, 0)
        fill_by_aisle[a.aisle_id] = round(occ / cap, 4)
        bk = bucket_of[a.aisle_id]
        bucket_occ[bk] += occ
        bucket_cap[bk] += capacity[a.aisle_id]
    fill_by_bucket = {bk: round(bucket_occ[bk] / (bucket_cap[bk] or 1), 4)
                      for bk in bucket_cap}
    return {
        'fill_overall':  round(fill_overall, 4),
        'fill_by_aisle': fill_by_aisle,
        'fill_by_bucket': fill_by_bucket,
    }


def _fn_trace(tr: Tracer) -> list[dict]:
    return [{'name': k, 'calls': v[0], 'total_s': round(v[1], 6)}
            for k, v in sorted(tr.fn.items(), key=lambda kv: -kv[1][1])]


# ── one strategy run ──────────────────────────────────────────────────────────

def trace_strategy(strategy_key: str, *, n_skus: int, bins_per_aisle: int,
                   n_batches: int, n_pickers: int, seed: int, fill: float) -> dict:
    strat = STRATEGY_BY_KEY[strategy_key]
    print(f'\n=== {strategy_key}  ({strat.label})  stock={strat.stock_mode} ===')

    # ── assets (plan_warehouse sizes + samples to target fill, like production) ──
    random.seed(seed); np.random.seed(seed)
    pool = _build_inventory(n_skus, seed)
    _set_equilibrium(pool.orders)
    n_cols = max(1, bins_per_aisle // 20)
    plan = Inventory_Manager.plan_warehouse(
        pool.orders, categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=n_cols * 48, aisle_height=20 * 48,
        target_fill=fill, rng=random.Random(seed + 1))
    inventory = Inventory(plan.sampled)
    affinity  = _build_affinity_store(inventory, top_k=20, seed=seed)
    pick_cfg  = PickConfig(num_pickers=n_pickers, x_speed=1.0, y_speed=0.5,
                           pick_intercept=1.0, pick_weight_coef=1.1,
                           pick_volume_coef=1e-3, cart_swap_coef=10.0)
    wp        = WorkloadParams.from_pick_config(pick_cfg)
    batch_cfg = BatchConfig(inventory_size=len(plan.sampled),
                            mean_fraction=0.15, std_fraction=0.05)
    for c in inventory.orders:
        c.compute_labor_cost(wp.pick_intercept, wp.pick_weight_coef, wp.pick_volume_coef)

    freq_by_sku = {c.sku: c.demand.frequency    for c in inventory.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate for c in inventory.orders}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.frequency
                   for c in inventory.orders if c.sku in affinity._sku_to_idx}
    ctx = StrategyContext(affinity=affinity, wp=wp, freq_by_idx=freq_by_idx,
                          freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku,
                          beta=1.0, orders=inventory.orders)

    # ── warehouse + manager ────────────────────────────────────────────────────
    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    total_bins = len(warehouse.bins)
    aisles, capacity = _warehouse_layout(warehouse)

    random.seed(seed + 100)
    mgr = Inventory_Manager(warehouse, affinity=None)
    tr = Tracer()
    _install_hooks(mgr, tr)

    def _arm():
        if strat.needs_affinity:
            mgr._affinity = affinity
            mgr.init_lift_state(affinity)
        if strat.needs_demand:
            mgr.init_demand_state(inventory, wp)

    batches: list[dict] = []

    # ── batch 0: initial stocking (the fill discrepancy lives here) ─────────────
    tr.reset()
    q0 = mgr.queue_depth
    if strat.stock_mode == 'policy':
        if strat.needs_affinity:
            mgr._affinity = affinity
        if strat.needs_demand:
            mgr.init_demand_state(inventory, wp)
        if strat.uses_aisle_index:
            mgr.init_travel_costs(wp)
        strat.build(mgr, ctx)
        mgr.enqueue_all(inventory.orders)
        _arm()
    else:
        mgr.enqueue_all(inventory.orders)
        _arm()
        if strat.uses_aisle_index:
            mgr.init_travel_costs(wp)
        strat.build(mgr, ctx)
    stuck = mgr.queue_depth
    snap = _fill_snapshot(mgr, warehouse, capacity)
    batches.append({
        'batch': 0,
        **snap,
        'queue_depth': stuck,
        'stage_counts': {'intake': tr.placed + stuck, 'placed': tr.placed,
                         'stuck': stuck, 'picked': 0, 'emptied': 0, 'reclaimed': 0},
        'fn_trace': _fn_trace(tr),
    })
    print(f'  batch 0 stock: placed={tr.placed:,}  stuck={stuck:,}  '
          f'fill={snap["fill_overall"]:.1%}')

    # ── simulation loop ─────────────────────────────────────────────────────────
    random.seed(seed + 200)
    for i in range(1, n_batches + 1):
        tr.reset()
        q_start = mgr.queue_depth
        mgr.check_reorders()                      # reclaim prev empties + place reorders
        placed = tr.placed
        q_end  = mgr.queue_depth
        intake = max(0, q_end - q_start + placed)

        batch = Batch(batch_cfg, inventory, affinity=affinity)
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()

        snap = _fill_snapshot(mgr, warehouse, capacity)
        batches.append({
            'batch': i,
            **snap,
            'queue_depth': q_end,
            'stage_counts': {'intake': intake, 'placed': placed, 'stuck': q_end,
                             'picked': tr.picked, 'emptied': tr.emptied,
                             'reclaimed': tr.reclaimed},
            'fn_trace': _fn_trace(tr),
        })

    final = batches[-1]['fill_overall']
    print(f'  final fill={final:.1%}  (after {n_batches} batches)')

    return {
        'meta': {'source': 'trace', 'strategy': strategy_key, 'label': strat.label,
                 'stock_mode': strat.stock_mode, 'n_skus': len(plan.sampled),
                 'total_bins': total_bins, 'target_fill': fill, 'n_batches': n_batches},
        'warehouse': {'aisles': aisles, 'grid_cols': _GRID_COLS},
        'batches': batches,
    }


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--strategies', default='uni_fifo_norsl,opt_map_norsl',
                    help='comma-separated strategy keys (default contrasts uniform vs policy)')
    ap.add_argument('--skus', type=int, default=2000)
    ap.add_argument('--bins-per-aisle', type=int, default=100)
    ap.add_argument('--batches', type=int, default=30)
    ap.add_argument('--pickers', type=int, default=10)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--fill', type=float, default=0.85)
    ap.add_argument('--list', action='store_true', help='print available strategy keys and exit')
    args = ap.parse_args()

    if args.list:
        print('Available strategies:')
        for s in STRATEGIES:
            print(f'  {s.key:24s} {s.label:18s} stock={s.stock_mode}')
        return

    keys = [k.strip() for k in args.strategies.split(',') if k.strip()]
    unknown = [k for k in keys if k not in STRATEGY_BY_KEY]
    if unknown:
        ap.error(f'unknown strategy key(s): {unknown}.  Use --list to see valid keys.')

    os.makedirs(_OUT_DIR, exist_ok=True)
    manifest = []
    for key in keys:
        result = trace_strategy(
            key, n_skus=args.skus, bins_per_aisle=args.bins_per_aisle,
            n_batches=args.batches, n_pickers=args.pickers, seed=args.seed, fill=args.fill)
        fname = f'trace_{key}.json'
        with open(os.path.join(_OUT_DIR, fname), 'w') as f:
            json.dump(result, f)
        manifest.append({'file': fname, 'strategy': key,
                         'label': result['meta']['label'],
                         'stock_mode': result['meta']['stock_mode'],
                         'final_fill': result['batches'][-1]['fill_overall']})

    with open(os.path.join(_OUT_DIR, 'manifest.json'), 'w') as f:
        json.dump({'runs': manifest}, f, indent=2)
    print(f'\nWrote {len(manifest)} trace(s) + manifest.json to {_OUT_DIR}')
    print('View:  cd Diagnostics && python -m http.server 8009  '
          '-> http://localhost:8009/static/')


if __name__ == '__main__':
    main()
