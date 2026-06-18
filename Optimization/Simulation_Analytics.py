"""Simulation_Analytics.py — per-batch and per-task statistics from PickSimulation events.

Public API
----------
avg_concurrent_pickers(events)
    Time-weighted average number of pickers simultaneously in "picking" state.

picker_time_breakdown(events, k_pickers)
    Aggregate picking vs traveling fractions across all pickers.

extract_batch_stats(events, batch_id, k_pickers, run_id)
    Summarise one PickSimulation run into a BatchStats record.

extract_task_stats(events, tasks, batch_id, affinity, wp, run_id)
    Extract per-aisle TaskStats from one PickSimulation run.

flag_batch_outliers(stats, iqr_factor)
    IQR outlier detection on batch duration.

flag_task_outliers(stats, iqr_factor)
    IQR outlier detection on task duration.

build_placed_affinity(warehouse, inventory, max_per_group)
    Sparse lift matrix for only the SKUs that have a physical bin.

sum_lift(skus, affinity)
    Demand-weighted co-occurrence helper for a dict-based lift matrix (AffinityStore
    objects carry their own faster .sum_lift; this is the dict fallback).
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))

from Picking_Data import BatchStats, TaskStats, PickRecord
from Workload import WorkloadParams, aisle_workload, aisle_workload_components


# (sku_i, sku_j) -> lift; symmetric dict.  Only the fallback for non-AffinityStore
# affinity (the live pipeline passes an AffinityStore with its own .sum_lift method).
def sum_lift(skus: list[int], affinity: dict) -> float:
    """Sum of pairwise lift over ordered pairs (i, j), i != j, in `skus`."""
    return sum(affinity.get((i, j), 0.0) for i in skus for j in skus if i != j)


# ── concurrency ───────────────────────────────────────────────────────────────

def avg_concurrent_pickers(events: list) -> float:
    """Time-weighted average number of pickers simultaneously in 'picking' state.

    A picker is "picking" from the moment they arrive at a bin (arrive event)
    until they finish handling it (pick event).  Cart-swap overhead is folded
    into that interval because both arrive and pick share the same pick_time span.

    Algorithm
    ---------
    Build a (time, +1/-1) change-list, sort it, then sweep to integrate the
    concurrent-count curve.  Divide by total simulation span.
    """
    changes: list[tuple[float, int]] = []
    t_end = 0.0
    for e in events:
        if e.event_type == 'arrive':
            changes.append((e.time, +1))
        elif e.event_type == 'pick':
            changes.append((e.time, -1))
        elif e.event_type == 'done':
            if e.time > t_end:
                t_end = e.time

    if not changes:
        return 0.0

    changes.sort()
    if t_end <= 0.0:
        t_end = changes[-1][0]
    if t_end <= 0.0:
        return 0.0

    weighted = 0.0
    count    = 0
    prev_t   = 0.0

    for t, delta in changes:
        if t > prev_t:
            weighted += count * (t - prev_t)
        count  += delta
        prev_t  = t

    # Flush any residual (count should be 0 after a well-formed simulation)
    if count > 0 and prev_t < t_end:
        weighted += count * (t_end - prev_t)

    return weighted / t_end


# ── picker utilisation ────────────────────────────────────────────────────────

def _group_events_by_picker(events: list, k_pickers: int) -> list[list]:
    """Partition events into per-picker lists in a single O(N) pass.

    Pick.py generates events in time order within each picker's timeline so
    no sort is needed — the per-picker lists are already time-ordered.
    """
    grouped: list[list] = [[] for _ in range(k_pickers)]
    for e in events:
        pid = e.picker_id
        if pid is not None and 0 <= pid < k_pickers:
            grouped[pid].append(e)
    return grouped


def picker_time_breakdown(events: list, k_pickers: int) -> dict[str, float]:
    """Aggregate picking vs traveling fractions across all k_pickers.

    Returns {'picking_pct': ..., 'traveling_pct': ...} where both sum to 1.0.
    Pickers with no assigned tasks contribute 0 to both numerator and denominator
    so they don't distort the average.

    Picking time per picker = sum of (pick.time - arrive.time) for each bin.
    Traveling time = total picker duration - picking time.
    """
    return _picker_time_breakdown_grouped(_group_events_by_picker(events, k_pickers))


def _picker_time_breakdown_grouped(grouped: list[list]) -> dict[str, float]:
    """Compute picking/traveling breakdown from pre-grouped picker event lists."""
    total_time   = 0.0
    picking_time = 0.0

    for picker_evs in grouped:
        done_t = picker_evs[-1].time if picker_evs and picker_evs[-1].event_type == 'done' else None
        if done_t is None or done_t <= 0.0:
            continue
        total_time += done_t

        last_arrive: float | None = None
        for e in picker_evs:
            if e.event_type == 'arrive':
                last_arrive = e.time
            elif e.event_type == 'pick' and last_arrive is not None:
                picking_time += e.time - last_arrive
                last_arrive   = None

    if total_time <= 0.0:
        return {'picking_pct': 0.0, 'traveling_pct': 1.0}

    picking_pct = min(1.0, picking_time / total_time)
    return {'picking_pct': picking_pct, 'traveling_pct': 1.0 - picking_pct}


# ── internal helpers ──────────────────────────────────────────────────────────

def _pick_lines(task) -> list[tuple[int, int, int, float]]:
    """(weight, volume, qty, y_phys) per bin stop that has inventory for this task.
    y_phys lets aisle_workload apply the height-bracket handling multiplier."""
    return [
        (b.storage.carton.weight, b.storage.carton.volume(),
         task.items[b.storage.carton.sku], b.y_phys)
        for b in task.path
        if b.storage is not None and b.storage.carton.sku in task.items
    ]


# ── analytical objective evaluator ──────────────────────────────────────────────

def expected_task_labor(tasks: list, wp: WorkloadParams) -> dict:
    """E[task labor] for a placement, scored ANALYTICALLY from the realised tasks.

    The optimization objective is the expected labor of a randomly-assigned task =
    mean over tasks of W = D + P + C (see Workload.aisle_workload).  Computed straight
    from each Task's path/items via the same bracket-aware formula the sim bills, so it
    is independent of sim wall-timing (queue waits, parallelism, reorder starvation).

    Returns {'objective': mean W, 'handling': mean P, 'travel': mean (D+C), 'n_tasks': n}.
    'travel' folds the cart-swap term C in with travel D since both are placement/route
    effects rather than per-unit handling; 'handling' is the placement-modulated P term.
    """
    n = 0
    sum_W = sum_P = sum_DC = 0.0
    for t in tasks:
        lines = _pick_lines(t)
        if not lines:
            continue
        D, P, C = aisle_workload_components(
            t.x_traversed, t.y_traversed, t.carts_required, lines, wp)
        sum_W  += D + P + C
        sum_P  += P
        sum_DC += D + C
        n += 1
    if n == 0:
        return {'objective': 0.0, 'handling': 0.0, 'travel': 0.0, 'n_tasks': 0}
    return {'objective': sum_W / n, 'handling': sum_P / n,
            'travel': sum_DC / n, 'n_tasks': n}


# ── extraction ────────────────────────────────────────────────────────────────

def extract_batch_stats(
    events: list,
    batch_id: int,
    k_pickers: int,
    run_id: int = 0,
) -> BatchStats:
    """Summarise one PickSimulation.run() result into a BatchStats record.

    duration      : max done-event time across all pickers
    num_tasks     : unique aisles that received a task_start
    total_items   : sum of items_picked from each picker's done event
    avg_concurrent_pickers : time-weighted mean (see avg_concurrent_pickers)
    picking/traveling pct  : aggregate fractions (see picker_time_breakdown)
    """
    # Group once; reuse for total_items and picker_time_breakdown.
    grouped = _group_events_by_picker(events, k_pickers)

    duration = 0.0
    num_tasks_set: set = set()
    total_items = 0
    t_min = float('inf')
    t_max = 0.0

    for e in events:
        if e.time < t_min:
            t_min = e.time
        if e.time > t_max:
            t_max = e.time
        if e.event_type == 'done':
            if e.time > duration:
                duration = e.time
        elif e.event_type == 'task_start' and e.aisle_id is not None:
            num_tasks_set.add(e.aisle_id)
    num_tasks = len(num_tasks_set)
    batch_start_time = 0.0 if t_min == float('inf') else t_min
    batch_end_time   = t_max

    for picker_evs in grouped:
        if picker_evs and picker_evs[-1].event_type == 'done':
            total_items += picker_evs[-1].items_picked

    conc      = avg_concurrent_pickers(events)
    breakdown = _picker_time_breakdown_grouped(grouped)

    return BatchStats(
        run_id                 = run_id,
        batch_id               = batch_id,
        duration               = duration,
        num_tasks              = num_tasks,
        total_items            = total_items,
        avg_concurrent_pickers = conc,
        picking_pct            = breakdown['picking_pct'],
        traveling_pct          = breakdown['traveling_pct'],
        batch_start_time       = batch_start_time,
        batch_end_time         = batch_end_time,
        is_outlier             = False,
    )


def extract_task_stats(
    events     : list,
    tasks      : list,
    batch_id   : int,
    affinity,
    wp         : WorkloadParams,
    run_id     : int = 0,
    lift_cache : dict | None = None,
) -> list[TaskStats]:
    """Extract per-aisle TaskStats from one PickSimulation.run() result.

    duration         : task_end.time - task_start.time for this aisle
    W              : analytical workload via aisle_workload()
    lift_sum         : sum_lift for SKUs present in this task
    num_bins_visited : bins with at least one pick (task.items membership)
    total_items      : sum of quantities across this aisle's SKUs
    """
    aisle_start:  dict[int, float] = {}
    aisle_end:    dict[int, float] = {}
    aisle_picker: dict[int, int]   = {}

    for e in events:
        if e.event_type == 'task_start' and e.aisle_id is not None:
            aisle_start[e.aisle_id]  = e.time
            aisle_picker[e.aisle_id] = e.picker_id
        elif (e.event_type == 'task_end'
              and e.aisle_id is not None
              and e.aisle_id in aisle_start):
            aisle_end[e.aisle_id] = e.time

    task_by_aisle = {t.aisle_id: t for t in tasks}
    result: list[TaskStats] = []

    for aisle_id, end_time in aisle_end.items():
        task = task_by_aisle.get(aisle_id)
        if task is None:
            continue
        lines = _pick_lines(task)
        W = aisle_workload(
            task.x_traversed, task.y_traversed,
            task.carts_required, lines, wp,
        ) if lines else 0.0
        task_skus = list(task.items.keys())
        cache_key = frozenset(task_skus)
        if lift_cache is not None and cache_key in lift_cache:
            ls = lift_cache[cache_key]
        else:
            ls = (affinity.sum_lift(task_skus)
                  if hasattr(affinity, 'sum_lift')
                  else sum_lift(task_skus, affinity))
            if lift_cache is not None:
                lift_cache[cache_key] = ls
        result.append(TaskStats(
            run_id           = run_id,
            batch_id         = batch_id,
            aisle_id         = aisle_id,
            picker_id        = aisle_picker.get(aisle_id, -1),
            task_start_time  = aisle_start[aisle_id],
            task_end_time    = end_time,
            duration         = end_time - aisle_start[aisle_id],
            W              = W,
            lift_sum         = ls,
            # task.path is built at task-creation time from non-empty bins;
            # its length is the planned visit count, unaffected by post-pick state.
            num_bins_visited = len(task.path),
            total_items      = sum(task.items.values()),
            is_outlier       = False,
        ))

    return result


# ── outlier flagging ──────────────────────────────────────────────────────────

def flag_batch_outliers(
    stats: list[BatchStats],
    iqr_factor: float = 1.5,
) -> list[BatchStats]:
    """Return new list with is_outlier set via Tukey IQR fences on duration."""
    if not stats:
        return []
    durations = np.array([s.duration for s in stats])
    q1, q3 = float(np.percentile(durations, 25)), float(np.percentile(durations, 75))
    iqr     = q3 - q1
    lo, hi  = q1 - iqr_factor * iqr, q3 + iqr_factor * iqr
    return [
        BatchStats(
            run_id=s.run_id, batch_id=s.batch_id, duration=s.duration,
            num_tasks=s.num_tasks, total_items=s.total_items,
            avg_concurrent_pickers=s.avg_concurrent_pickers,
            picking_pct=s.picking_pct, traveling_pct=s.traveling_pct,
            batch_start_time=s.batch_start_time, batch_end_time=s.batch_end_time,
            sigma_fd=s.sigma_fd, reload_moves=s.reload_moves,
            reorder_placements=s.reorder_placements,
            is_outlier=bool(d < lo or d > hi),
        )
        for s, d in zip(stats, durations)
    ]


def flag_task_outliers(
    stats: list[TaskStats],
    iqr_factor: float = 1.5,
) -> list[TaskStats]:
    """Return new list with is_outlier set via Tukey IQR fences on duration."""
    if not stats:
        return []
    durations = np.array([s.duration for s in stats])
    q1, q3 = float(np.percentile(durations, 25)), float(np.percentile(durations, 75))
    iqr     = q3 - q1
    lo, hi  = q1 - iqr_factor * iqr, q3 + iqr_factor * iqr
    return [
        TaskStats(
            run_id=s.run_id, batch_id=s.batch_id, aisle_id=s.aisle_id,
            picker_id=s.picker_id, duration=s.duration,
            task_start_time=s.task_start_time,
            task_end_time=s.task_end_time,
            W=s.W, lift_sum=s.lift_sum, num_bins_visited=s.num_bins_visited,
            total_items=s.total_items,
            is_outlier=bool(d < lo or d > hi),
        )
        for s, d in zip(stats, durations)
    ]


# ── affinity helpers ──────────────────────────────────────────────────────────

def build_placed_affinity(
    warehouse,
    inventory,
    max_per_group: int = 300,
) -> dict:
    """Build a sparse lift matrix covering only placed SKUs.

    Calling inventory.affinity_matrix() on 50 000+ SKUs with 5 lift groups
    of ~10 000 each generates ~250 M pairs — impractical.  This function caps
    each lift group at max_per_group placed SKUs, yielding a bounded dict
    (5 * C(300,2) * 2 ≈ 450 k entries at the default) while still producing
    non-zero lift_sums for tasks that contain two or more eligible SKUs.

    Parameters
    ----------
    warehouse      : built Warehouse object (bins already stocked)
    inventory      : Inventory object whose cartons have .sku and .lift_group
    max_per_group  : SKUs with affinity per lift group; higher → denser lift
                     coverage but more memory (~150 MB at 1 000 per group)
    """
    import random as _rng
    from collections import defaultdict

    placed_skus: set[int] = {
        b.storage.carton.sku
        for b in warehouse.bins
        if b.storage is not None
    }

    by_group: defaultdict[int, list[int]] = defaultdict(list)
    for c in inventory.cartons:
        if c.sku in placed_skus:
            by_group[c.lift_group].append(c.sku)

    affinity: dict = {}
    for skus in by_group.values():
        eligible = skus[:max_per_group]
        for i, sku_i in enumerate(eligible):
            for sku_j in eligible[i + 1:]:
                lv = _rng.uniform(1.5, 5.0)
                affinity[(sku_i, sku_j)] = lv
                affinity[(sku_j, sku_i)] = lv

    return affinity


# ── aisle metric snapshot ─────────────────────────────────────────────────────

def snapshot_aisle_metrics(
    manager  : object,
    batch_id : int,
    run_id   : int = 0,
) -> list:
    """Capture the trip-cost equation state for every aisle in the manager.

    Call once per batch after check_reorders() and before picks — the same
    moment as build_pre_snapshot() — so the snapshot reflects the current
    warehouse layout after any reorder placements.

    Fields captured
    ---------------
    n_skus     : len(_aisle_sku_sets[aid])            — unique SKUs in aisle
    n_bins     : sum(_aisle_sku_counts[aid].values())  — occupied bin count
    demand_sum : _aisle_demand_sum[aid]               — Σ f_i * q_i secondary score
    lift_sum   : _aisle_lift_sum[aid]                 — affinity co-location quality

    Strategy A note
    ---------------
    For uniform placement (no affinity), _aisle_sku_sets / _aisle_lift_sum /
    _aisle_demand_sum are never populated.  Only aisles touched by B/C reorders
    will have non-zero values.  Strategy A produces no rows here by design —
    it has no structured placement state to track.
    """
    from Picking_Data import AisleMetricRecord

    aisle_sku_sets    = manager._aisle_sku_sets
    aisle_sku_counts  = manager._aisle_sku_counts
    aisle_demand_sum  = manager._aisle_demand_sum
    aisle_lift_sum    = manager._aisle_lift_sum

    # Union of all aisle IDs present in any state dict
    all_aids = (set(aisle_sku_sets) | set(aisle_demand_sum) | set(aisle_lift_sum))
    if not all_aids:
        return []

    records = []
    for aid in all_aids:
        sku_set  = aisle_sku_sets.get(aid, set())
        sku_cnts = aisle_sku_counts.get(aid, {})
        records.append(AisleMetricRecord(
            run_id     = run_id,
            batch_id   = batch_id,
            aisle_id   = aid,
            n_skus     = len(sku_set),
            n_bins     = sum(sku_cnts.values()),
            demand_sum = float(aisle_demand_sum.get(aid, 0.0)),
            lift_sum   = float(aisle_lift_sum.get(aid, 0.0)),
        ))
    return records


# ── picker event extraction ───────────────────────────────────────────────────

def build_pre_snapshot(manager) -> dict:
    """Capture bin quantities before a batch simulation runs.

    Call immediately after check_reorders() and before DeferredPickSimulation.
    Returns a dict keyed by id(bin) holding all data needed to build a
    BinInventoryRecord once the post-simulation quantities are known.

    Only non-empty bins are captured; empty bins are implicitly quantity=0
    and are not written to the DB.
    """
    from Storage_Primitive import Singleton
    snap = {}
    for bin_ in manager._unavailable.values():
        if bin_.storage is None:
            continue
        snap[id(bin_)] = {
            'bin_ref'     : bin_,
            'aisle_id'    : bin_.location[0],
            'bayX'        : bin_.bayX,
            'bayY'        : bin_.bayY,
            'sku'         : bin_.storage.carton.sku,
            'unit_type'   : 'singleton' if isinstance(bin_.storage, Singleton) else 'pallet',
            'storage_size': bin_.storage_size,
            'pre_qty'     : bin_.storage.quantity,
        }
    return snap


def snapshot_bin_inventory(
    manager       : object,
    pre_snap      : dict,
    batch_id      : int,
    run_id        : int  = 0,
    full_snapshot : bool = False,
) -> list:
    """Merge pre-snapshot with post-simulation bin state into BinInventoryRecords.

    Call immediately after DeferredPickSimulation.run() (Phase 2 complete).

    Covers three cases:
      - Picked bins     : were in pre_snap; post_qty < pre_qty — always recorded.
      - Untouched bins  : were in pre_snap; post_qty == pre_qty — only recorded
                          when full_snapshot=True (first batch only).
      - Reorder bins    : NOT in pre_snap; newly placed by check_reorders() —
                          always recorded (they represent a state change).

    Writing untouched bins on every batch (~2.4M rows for a large warehouse)
    dominated checkpoint time (~185–308s per 10-batch checkpoint).  Recording
    only changed bins drops this to ~100K rows/batch while preserving full
    reconstruction: set full_snapshot=True for the first batch of each run to
    establish a complete baseline, then apply diffs batch-by-batch thereafter.

    Visualization query — inventory of one aisle at sim-time T (mid-batch):
        WITH pre AS (
            SELECT aisle_id, bayX, bayY, sku, pre_qty
            FROM   bin_inventory WHERE run_id=? AND batch_id=?
        ),
        picks AS (
            SELECT aisle_id, bayX, bayY, SUM(quantity) AS picked
            FROM   picker_events
            WHERE  run_id=? AND batch_id=? AND aisle_id=? AND event_type='pick'
              AND  time <= ?
            GROUP  BY aisle_id, bayX, bayY
        )
        SELECT p.*, MAX(0, p.pre_qty - COALESCE(pk.picked,0)) AS qty_at_t
        FROM   pre p LEFT JOIN picks pk USING (aisle_id, bayX, bayY)
    """
    from Picking_Data import BinInventoryRecord
    from Storage_Primitive import Singleton

    records = []

    # ── bins present at pre-snapshot time ─────────────────────────────────────
    for bin_id, info in pre_snap.items():
        bin_ = info['bin_ref']
        post_qty = bin_.storage.quantity if bin_.storage is not None else 0
        if not full_snapshot and post_qty == info['pre_qty']:
            continue   # unchanged bin — skip to minimise write volume
        records.append(BinInventoryRecord(
            run_id       = run_id,
            batch_id     = batch_id,
            aisle_id     = info['aisle_id'],
            bayX         = info['bayX'],
            bayY         = info['bayY'],
            sku          = info['sku'],
            unit_type    = info['unit_type'],
            storage_size = info['storage_size'],
            pre_qty      = info['pre_qty'],
            post_qty     = post_qty,
        ))

    # ── bins that were empty before this batch and received a reorder ──────────
    # These appear in manager._unavailable but not in pre_snap.  Always record
    # regardless of full_snapshot since a new bin represents a state change.
    for bin_ in manager._unavailable.values():
        if id(bin_) in pre_snap or bin_.storage is None:
            continue
        records.append(BinInventoryRecord(
            run_id       = run_id,
            batch_id     = batch_id,
            aisle_id     = bin_.location[0],
            bayX         = bin_.bayX,
            bayY         = bin_.bayY,
            sku          = bin_.storage.carton.sku,
            unit_type    = 'singleton' if isinstance(bin_.storage, Singleton) else 'pallet',
            storage_size = bin_.storage_size,
            pre_qty      = bin_.storage.quantity,
            post_qty     = bin_.storage.quantity,
        ))

    return records


def extract_picker_events(
    events   : list,
    batch_id : int,
    run_id   : int = 0,
) -> list:
    """Convert a PickSimulation event list into PickerEventRecord rows for DB storage.

    Every event is stored verbatim so the full picker timeline can be replayed
    from SQL.  Location (bayX, bayY) is populated for arrive / cart_swap / pick
    events; it is NULL for task_start, task_end, and done events.

    Visualization query — state of all pickers at sim-time t in batch b:

        SELECT picker_id, aisle_id, bayX, bayY, event_type, items_picked
        FROM   picker_events
        WHERE  run_id = ? AND batch_id = ? AND time <= ?
        GROUP  BY picker_id
        HAVING time = MAX(time)
        ORDER  BY picker_id;
    """
    from Picking_Data import PickerEventRecord

    records = []
    for e in events:
        loc = e.location  # (aisle_id, bayX, bayY) or None
        records.append(PickerEventRecord(
            run_id         = run_id,
            batch_id       = batch_id,
            picker_id      = e.picker_id,
            time           = e.time,
            event_type     = e.event_type,
            aisle_id       = e.aisle_id,
            bayX           = loc[1] if loc is not None else None,
            bayY           = loc[2] if loc is not None else None,
            sku            = e.sku,
            quantity       = e.quantity,
            bins_completed = e.bins_completed,
            total_bins     = e.total_bins,
            items_picked   = e.items_picked,
            total_items    = e.total_items,
        ))
    return records


def task_time_breakdown(events: list) -> tuple[float, float, float]:
    """Decompose picker time into (travel, handling, other) from a picker-event stream.

    Each picker's timeline is walked in time order; the gap *ending* at an event is
    charged by that event's type: a gap ending at 'arrive' is travel (move to a bin),
    a gap ending at 'pick' is handling (= _pick_time, incl. any cart-swap), everything
    else (task_start/end, done, cart_swap markers) is 'other' (≈0 / inter-task).  Works
    on PickEvent objects or PickerEventRecord rows (both have .picker_id/.time/.event_type).
    travel + handling ≈ Σ task durations (productivity hours).
    """
    from collections import defaultdict
    by_picker: dict = defaultdict(list)
    for e in events:
        by_picker[e.picker_id].append(e)
    travel = handling = other = 0.0
    for evs in by_picker.values():
        evs.sort(key=lambda e: e.time)
        prev = None
        for e in evs:
            if prev is not None:
                gap = e.time - prev.time
                if gap > 0:
                    if e.event_type == 'arrive':
                        travel += gap
                    elif e.event_type == 'pick':
                        handling += gap
                    else:
                        other += gap
            prev = e
    return travel, handling, other


def extract_picks(events: list, batch_id: int, run_id: int = 0) -> list[PickRecord]:
    """Extract one PickRecord per 'pick' event from the picker event stream.

    Provides a compact, join-friendly picks table (run_id, batch_id, sku, qty,
    location, sim_time) without the full event stream overhead of picker_events.
    """
    records: list[PickRecord] = []
    for e in events:
        if e.event_type == 'pick' and e.location is not None and e.sku is not None:
            records.append(PickRecord(
                run_id   = run_id,
                batch_id = batch_id,
                picker_id= e.picker_id,
                sim_time = e.time,
                aisle_id = e.location[0],
                bayX     = e.location[1],
                bayY     = e.location[2],
                sku      = e.sku,
                quantity = e.quantity or 0,
            ))
    return records
