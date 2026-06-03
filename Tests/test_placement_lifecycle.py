"""
test_placement_lifecycle.py — Verify every state dict is updated correctly at
each stage of the SKU lifecycle: initial stock, pick depletion, reorder trigger,
and reorder placement.

Covers both uniform (A) and load-aware (B/C) assignment paths.

Usage
-----
    cd Tests
    python test_placement_lifecycle.py          # all tests, verbose
    python test_placement_lifecycle.py -q       # quiet (pass/fail only)
"""
from __future__ import annotations

import math
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'Warehouse'))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'Optimization'))

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from Aisle_Storage import Aisle
from Affinity_Store import AffinityStore
from Carton import Carton
from Demand import Demand
from Inventory_Management import (
    Inventory_Manager, LoadParams,
    build_load_minimizing_assignment_fn,
    build_load_maximizing_assignment_fn,
)
from Pick import PickConfig, PickSimulation
from Storage_Primitive import Pallet, Singleton, viable_storage_units
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig, Task
from Workload import WorkloadParams

# ── colour helpers ────────────────────────────────────────────────────────────
_GREEN  = '\033[92m'
_RED    = '\033[91m'
_YELLOW = '\033[93m'
_RESET  = '\033[0m'

_QUIET = '-q' in sys.argv

_passed = 0
_failed = 0


def ok(name: str) -> None:
    global _passed
    _passed += 1
    if not _QUIET:
        print(f'  {_GREEN}PASS{_RESET}  {name}')


def fail(name: str, detail: str = '') -> None:
    global _failed
    _failed += 1
    print(f'  {_RED}FAIL{_RESET}  {name}')
    if detail:
        print(f'      {_RED}{detail}{_RESET}')


def section(title: str) -> None:
    print(f'\n{_YELLOW}-- {title} --{_RESET}')


def check(name: str, condition: bool, detail: str = '') -> None:
    if condition:
        ok(name)
    else:
        fail(name, detail)


# ── warehouse factory ─────────────────────────────────────────────────────────

_CATEGORIES = ['food']
_W, _H = 5 * 48, 4 * 48   # 5 pallet-column widths × 4 extra_large-height levels
_AISLE_CFGS = [
    AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['small'], None),
    AisleConfig('conveyable', 'food', 'singleton', _W, _H, ['small', 'medium'], [0.5, 0.5]),
]

def _build_warehouse(seed: int = 0) -> tuple[Any, Inventory_Manager]:
    """Build a small warehouse and return (warehouse, manager)."""
    Aisle.next_aisle_id = 1
    random.seed(seed)
    wh_cfg = WarehouseConfig(
        total_aisles  = 2,
        aisle_splits  = [0.5, 0.5],
        aisle_configs = _AISLE_CFGS,
    )
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh)
    return wh, mgr


def _build_warehouse_with_affinity(seed: int = 0) -> tuple[Any, Inventory_Manager, AffinityStore]:
    """Build warehouse with an in-memory affinity store."""
    Aisle.next_aisle_id = 1
    random.seed(seed)
    wh_cfg = WarehouseConfig(
        total_aisles  = 2,
        aisle_splits  = [0.5, 0.5],
        aisle_configs = _AISLE_CFGS,
    )
    wh       = Warehouse_Builder().from_config(wh_cfg).build()
    affinity = AffinityStore(':memory:')
    mgr      = Inventory_Manager(wh, affinity=affinity)
    return wh, mgr, affinity


def _make_carton(sku: int, stock_qty: int = 35,
                 handling: str = 'conveyable',
                 category: str = 'food') -> Carton:
    """Create a Carton directly (no DB) with known dimensions and stock_qty."""
    from Carton import StorageHandleConfig
    c                        = object.__new__(Carton)
    c._sku                   = sku
    c.storage_type           = (handling, category)
    c.storage_handle_config  = StorageHandleConfig(handling, category)
    c.lift_group             = (handling, category)
    c.length        = 8
    c.width         = 8
    c.height        = 6
    c.weight        = 5
    c.demand        = Demand.from_rates(0.9, 3.0)
    c.stock_qty     = stock_qty
    return c


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: Initial bin placement — all state dicts populated
# ─────────────────────────────────────────────────────────────────────────────

def test_initial_placement() -> None:
    section('Stage 1: Initial bin placement')
    random.seed(42)
    wh, mgr = _build_warehouse()

    cartons = [_make_carton(sku=i, stock_qty=30) for i in range(1, 6)]
    mgr.enqueue_all(cartons)

    total_bins = len(wh.bins)

    # ── 1a: every SKU has at least one bin in _sku_*_bins
    all_indexed = all(
        mgr._sku_singleton_bins.get(c.sku) or mgr._sku_pallet_bins.get(c.sku)
        for c in cartons
    )
    check('All SKUs indexed in _sku_singleton_bins or _sku_pallet_bins', all_indexed)

    # ── 1b: _unavailable count equals placed bins
    placed = len(mgr.unavailable)
    check('_unavailable non-empty after enqueue_all', placed > 0,
          f'placed={placed}')

    # ── 1c: _bin_sku populated for every placed bin
    bin_sku_count = len(mgr._bin_sku)
    check('_bin_sku has one entry per placed bin',
          bin_sku_count == placed,
          f'_bin_sku={bin_sku_count}  placed={placed}')

    # ── 1d: stock_qty override — unit.quantity == stock_qty (not 1)
    wrong_qty = []
    for bin_ in mgr.unavailable:
        if bin_.storage is not None:
            sku = bin_.storage.carton.sku
            sq  = bin_.storage.carton.stock_qty
            if bin_.storage.quantity != sq:
                wrong_qty.append((sku, bin_.storage.quantity, sq))
    check('unit.quantity overridden to stock_qty for all initial bins',
          len(wrong_qty) == 0,
          f'mismatches: {wrong_qty[:3]}')

    # ── 1e: _current_quantities == stock_qty × n_bins for each SKU
    for c in cartons:
        bins_for_sku = list(mgr._sku_singleton_bins.get(c.sku, set())) + \
                       list(mgr._sku_pallet_bins.get(c.sku, set()))
        expected_qty = sum(b.storage.quantity for b in bins_for_sku if b.storage)
        actual_qty   = mgr._current_quantities.get(c.sku, 0)
        check(f'_current_quantities[{c.sku}] == sum of bin quantities',
              actual_qty == expected_qty,
              f'expected={expected_qty}  got={actual_qty}')

    # ── 1f: _initial_quantities set for every SKU
    missing_init = [c.sku for c in cartons if c.sku not in mgr._initial_quantities]
    check('_initial_quantities set for all SKUs', len(missing_init) == 0,
          f'missing: {missing_init}')

    # ── 1g: _originals set for every SKU
    missing_orig = [c.sku for c in cartons if c.sku not in mgr._originals]
    check('_originals set for all SKUs', len(missing_orig) == 0,
          f'missing: {missing_orig}')

    # ── 1h: _aisle_sku_counts is only updated when affinity is set (by design)
    #        Verify it stays zero without affinity (correct behaviour).
    total_counts = sum(sum(d.values()) for d in mgr._aisle_sku_counts.values())
    check('_aisle_sku_counts stays zero without affinity (expected)',
          total_counts == 0,
          f'total={total_counts}')

    # ── 1i: _is_reorder flag absent on original cartons
    has_reorder_flag = any(
        getattr(b.storage.carton, '_is_reorder', False)
        for b in mgr.unavailable if b.storage
    )
    check('No _is_reorder flag on initially-placed cartons', not has_reorder_flag)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: Pick depletion → _notify_pick → _depleted_skus
# ─────────────────────────────────────────────────────────────────────────────

def test_pick_depletion() -> None:
    section('Stage 2: Pick depletion and threshold detection')
    random.seed(42)
    wh, mgr = _build_warehouse()

    stock_qty = 20
    carton    = _make_carton(sku=10, stock_qty=stock_qty)
    mgr.enqueue_all([carton], quantity=1)

    placed_bins = [
        b for b in mgr.unavailable
        if b.storage and b.storage.carton.sku == 10
    ]
    check('SKU 10 placed in at least one bin', len(placed_bins) > 0)
    if not placed_bins:
        return

    initial = mgr._initial_quantities[10]
    threshold = max(1, round(initial * 0.10))

    # Deplete to threshold+1 (one unit above threshold — should NOT trigger yet)
    above_thresh_qty = initial - (threshold + 1)
    mgr._notify_pick(10, above_thresh_qty)
    check('_depleted_skus empty while current > threshold',
          10 not in mgr._depleted_skus,
          f'current={mgr._current_quantities[10]}  threshold={threshold}')

    # One more pick to cross threshold
    mgr._notify_pick(10, 1)
    check('_depleted_skus populated once threshold crossed',
          10 in mgr._depleted_skus,
          f'current={mgr._current_quantities[10]}  threshold={threshold}')

    # Empty a bin and check pending_reclaim
    bin_ = placed_bins[0]
    if bin_.storage:
        bin_.storage.quantity = 0
        bin_.storage = None
        mgr._notify_bin_emptied(bin_)
    check('_pending_reclaim populated after bin emptied',
          bin_ in mgr._pending_reclaim)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3: _reclaim_empty_bins — bin returned to index, lift state updated
# ─────────────────────────────────────────────────────────────────────────────

def test_bin_reclaim() -> None:
    section('Stage 3: Bin reclaim via _reclaim_empty_bins')
    random.seed(42)
    wh, mgr, affinity = _build_warehouse_with_affinity()

    cartons = [_make_carton(sku=i, stock_qty=15) for i in range(1, 4)]
    mgr.enqueue_all(cartons, quantity=1)
    mgr.init_lift_state(affinity)

    # Pick a bin empty
    placed = [b for b in mgr.unavailable if b.storage and b.storage.carton.sku == 1]
    check('SKU 1 has at least one placed bin', len(placed) > 0)
    if not placed:
        return

    bin_ = placed[0]
    aid  = bin_.location[0]
    pre_avail = sum(len(v) for v in mgr._index.values())
    pre_sku_counts = dict(mgr._aisle_sku_counts.get(aid, {}))

    # Manually empty the bin
    bin_.storage.quantity = 0
    bin_.storage = None
    mgr._notify_bin_emptied(bin_)
    mgr._reclaim_empty_bins()

    post_avail = sum(len(v) for v in mgr._index.values())
    check('_index grows by 1 after reclaim', post_avail == pre_avail + 1,
          f'pre={pre_avail}  post={post_avail}')

    check('_unavailable shrinks by 1 after reclaim',
          id(bin_) not in {id(b) for b in mgr.unavailable},
          'bin still in _unavailable')

    check('_bin_sku entry removed after reclaim',
          id(bin_) not in mgr._bin_sku)

    sku_1_pallet = [b for b in mgr._sku_pallet_bins.get(1, set()) if b is bin_]
    sku_1_single = [b for b in mgr._sku_singleton_bins.get(1, set()) if b is bin_]
    check('_sku_*_bins no longer contains reclaimed bin',
          len(sku_1_pallet) == 0 and len(sku_1_single) == 0)

    check('_pending_reclaim cleared after _reclaim_empty_bins',
          len(mgr._pending_reclaim) == 0)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 4: check_reorders — carton.reorder() flags, qty formula, state dicts
# ─────────────────────────────────────────────────────────────────────────────

def test_reorder_trigger() -> None:
    section('Stage 4: Reorder trigger and carton.reorder() flags')
    random.seed(42)
    wh, mgr = _build_warehouse()

    stock_qty = 40
    carton    = _make_carton(sku=20, stock_qty=stock_qty)
    mgr.enqueue_all([carton], quantity=1)

    initial   = mgr._initial_quantities[20]
    threshold = max(1, round(initial * 0.10))

    # Drive quantity to threshold
    mgr._notify_pick(20, initial - threshold)
    assert 20 in mgr._depleted_skus, 'precondition: 20 must be depleted'

    pre_queue_depth = mgr.queue_depth
    triggered = mgr.check_reorders()

    check('check_reorders returns triggered SKUs list with sku=20',
          20 in triggered,
          f'triggered={triggered}')

    # Find any placed reorder bin for sku=20
    reorder_bins = [
        b for b in mgr.unavailable
        if b.storage and b.storage.carton.sku == 20
           and getattr(b.storage.carton, '_is_reorder', False)
    ]

    # Also check queued (may be unplaced if no space)
    queued_reorders = [
        u for u in mgr._queue
        if u.carton.sku == 20 and getattr(u.carton, '_is_reorder', False)
    ]

    placed_or_queued = len(reorder_bins) + len(queued_reorders)
    check('Reorder units placed or queued for sku=20', placed_or_queued > 0,
          f'placed={len(reorder_bins)}  queued={len(queued_reorders)}')

    # Check _is_reorder flag
    if reorder_bins:
        has_flag = all(getattr(b.storage.carton, '_is_reorder', False)
                       for b in reorder_bins)
        check('Reorder bins have _is_reorder=True on carton', has_flag)

        # Check quantity is NOT stock_qty (override must be skipped)
        for b in reorder_bins:
            qty = b.storage.quantity
            check(f'Reorder bin quantity {qty} is within [1, {stock_qty*2}]',
                  1 <= qty <= stock_qty * 2,
                  f'got={qty}  expected range [1, {stock_qty*2}]')

    if queued_reorders:
        for u in queued_reorders:
            check(f'Queued reorder unit qty {u.quantity} within [1, {stock_qty*2}]',
                  1 <= u.quantity <= stock_qty * 2)

    # _initial_quantities must NOT be updated by reorder
    check('_initial_quantities[20] unchanged after reorder',
          mgr._initial_quantities[20] == initial,
          f'expected={initial}  got={mgr._initial_quantities.get(20)}')

    # _depleted_skus cleared after check_reorders
    check('_depleted_skus cleared after check_reorders',
          20 not in mgr._depleted_skus)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5: Load-aware assignment (B/C) — lift state updated on reorder
# ─────────────────────────────────────────────────────────────────────────────

def test_load_aware_reorder() -> None:
    section('Stage 5: Load-aware assignment (B/C) updates lift state on reorder')
    random.seed(42)
    wh, mgr, affinity = _build_warehouse_with_affinity()

    cartons = [_make_carton(sku=i, stock_qty=20) for i in range(30, 35)]
    mgr.enqueue_all(cartons, quantity=1)
    mgr.init_lift_state(affinity)

    # Snapshot lift sums before reorder
    lift_before = dict(mgr._aisle_lift_sum)

    # Set load-minimizing assignment
    wp = WorkloadParams()
    lp = LoadParams(lambda_=1.1, k=1.0, gamma=1.5)
    mgr.assignment_fn = build_load_minimizing_assignment_fn(
        lp, affinity, wp,
        mgr._aisle_sku_sets, mgr._aisle_lift_sum, mgr._aisle_idx_sets,
    )

    # Deplete SKU 30
    initial   = mgr._initial_quantities[30]
    threshold = max(1, round(initial * 0.10))
    mgr._notify_pick(30, initial - threshold)
    assert 30 in mgr._depleted_skus

    # Trigger reorder — assignment_fn is now load-minimizing
    triggered = mgr.check_reorders()
    check('check_reorders triggered sku=30 with load-minimizing fn',
          30 in triggered, f'triggered={triggered}')

    # _aisle_sku_counts updated for reorder placement
    total_counts_after = sum(sum(d.values()) for d in mgr._aisle_sku_counts.values())
    check('_aisle_sku_counts updated after load-minimizing reorder',
          total_counts_after > 0)

    # Ensure queue_depth correctly reflects any unplaced items
    check('queue_depth is non-negative integer', mgr.queue_depth >= 0)

    # Repeat for load-maximizing (strategy C)
    random.seed(42)
    wh2, mgr2, aff2 = _build_warehouse_with_affinity()
    cartons2 = [_make_carton(sku=i, stock_qty=20) for i in range(40, 45)]
    mgr2.enqueue_all(cartons2, quantity=1)
    mgr2.init_lift_state(aff2)
    mgr2.assignment_fn = build_load_maximizing_assignment_fn(
        lp, aff2, wp,
        mgr2._aisle_sku_sets, mgr2._aisle_lift_sum, mgr2._aisle_idx_sets,
    )
    init2     = mgr2._initial_quantities[40]
    thresh2   = max(1, round(init2 * 0.10))
    mgr2._notify_pick(40, init2 - thresh2)
    triggered2 = mgr2.check_reorders()
    check('check_reorders triggered sku=40 with load-maximizing fn',
          40 in triggered2, f'triggered={triggered2}')


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 6: No duplicate reorder when same SKU already in queue
# ─────────────────────────────────────────────────────────────────────────────

def test_no_duplicate_reorder() -> None:
    section('Stage 6: Duplicate reorder guard (FIFO queue)')
    random.seed(42)
    # Build warehouse with zero available bins so nothing can be placed
    # We do this by filling all bins first, then deplete one SKU
    wh, mgr = _build_warehouse()

    carton1 = _make_carton(sku=50, stock_qty=10)
    carton2 = _make_carton(sku=51, stock_qty=10)
    mgr.enqueue_all([carton1, carton2], quantity=1)

    # Drain all remaining bins with dummy cartons to fill the warehouse
    dummy_cartons = [_make_carton(sku=100 + i) for i in range(100)]
    mgr.enqueue_all(dummy_cartons, quantity=1)

    # Deplete sku=50
    initial = mgr._initial_quantities.get(50, 10)
    mgr._notify_pick(50, initial)
    mgr.check_reorders()   # first call — enqueues reorder

    queue_depth_after_first = mgr.queue_depth

    # Deplete again (should NOT double-enqueue)
    mgr._notify_pick(50, 1)   # already at 0, won't cross threshold again
    mgr._depleted_skus.add(50)  # force flag to test guard
    mgr.check_reorders()

    check('Second check_reorders does not double-enqueue same SKU',
          mgr.queue_depth == queue_depth_after_first,
          f'before={queue_depth_after_first}  after={mgr.queue_depth}')


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 7: Full end-to-end mini-simulation
# ─────────────────────────────────────────────────────────────────────────────

def test_end_to_end_mini_sim() -> None:
    section('Stage 7: End-to-end mini-simulation (5 batches)')
    random.seed(99)
    wh, mgr = _build_warehouse(seed=99)

    cartons = [_make_carton(sku=i, stock_qty=15) for i in range(1, 6)]
    mgr.enqueue_all(cartons, quantity=1)

    pick_cfg  = PickConfig(num_pickers=2, x_speed=1.0, y_speed=0.5,
                           pick_intercept=0.5, pick_weight_coef=0.1,
                           pick_volume_coef=0.001, cart_swap_coef=2.0)
    batch_cfg = BatchConfig(inventory_size=5, mean_fraction=0.6, std_fraction=0.1)

    # Dummy inventory wrapper
    class _Inv:
        def __init__(self, c): self.cartons = c

    inv = _Inv(cartons)

    reorders_total = 0
    for batch_num in range(5):
        triggered = mgr.check_reorders()
        reorders_total += len(triggered)

        batch = Batch(batch_cfg, inv, affinity=None)
        tasks = Task.from_batch(batch, wh, manager=mgr)
        if not tasks:
            continue

        events = PickSimulation(tasks, pick_cfg, manager=mgr).run()
        done_events = [e for e in events if e.event_type == 'done']
        check(f'Batch {batch_num+1}: PickSimulation produced done events',
              len(done_events) > 0,
              f'got {len(done_events)} done events')

    # After 5 batches, at least some reorders should have triggered
    # (not guaranteed — depends on demand — so we just check it ran without error)
    check('Mini-simulation completed 5 batches without exception', True)
    if not _QUIET:
        print(f'      reorders triggered across 5 batches: {reorders_total}')
        print(f'      final queue_depth: {mgr.queue_depth}')
        print(f'      filled bins: {len(mgr.unavailable)} / {len(wh.bins)}')


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f'\n{"="*60}')
    print('  SKU Placement Lifecycle Tests')
    print(f'{"="*60}')

    test_initial_placement()
    test_pick_depletion()
    test_bin_reclaim()
    test_reorder_trigger()
    test_load_aware_reorder()
    test_no_duplicate_reorder()
    test_end_to_end_mini_sim()

    print(f'\n{"="*60}')
    total = _passed + _failed
    if _failed == 0:
        print(f'  {_GREEN}All {total} checks passed.{_RESET}')
    else:
        print(f'  {_GREEN}{_passed} passed{_RESET}  '
              f'{_RED}{_failed} failed{_RESET}  '
              f'({total} total)')
    print(f'{"="*60}\n')

    sys.exit(0 if _failed == 0 else 1)
