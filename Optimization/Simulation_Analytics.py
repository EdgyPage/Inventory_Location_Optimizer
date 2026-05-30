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

task_stats_to_aisle_loads(task_stats, run_id)
    Convert TaskStats records into AisleLoadRecords for parameter recovery.

recover_params_to_db(db_path, run_id, records, k_per_task, ...)
    Full IQR-clean recovery pipeline — fits LoadParams and persists to DB.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))

from Picking_Data import BatchStats, TaskStats
from Picking_Analytics import sum_lift
from Workload import WorkloadParams, aisle_workload


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
    for e in events:
        if e.event_type == 'arrive':
            changes.append((e.time, +1))
        elif e.event_type == 'pick':
            changes.append((e.time, -1))

    if not changes:
        return 0.0

    changes.sort()

    done_times = [e.time for e in events if e.event_type == 'done']
    t_end = max(done_times) if done_times else changes[-1][0]
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

def picker_time_breakdown(events: list, k_pickers: int) -> dict[str, float]:
    """Aggregate picking vs traveling fractions across all k_pickers.

    Returns {'picking_pct': ..., 'traveling_pct': ...} where both sum to 1.0.
    Pickers with no assigned tasks contribute 0 to both numerator and denominator
    so they don't distort the average.

    Picking time per picker = sum of (pick.time - arrive.time) for each bin.
    Traveling time = total picker duration - picking time.
    """
    total_time   = 0.0
    picking_time = 0.0

    for pid in range(k_pickers):
        picker_evs = sorted(
            [e for e in events if e.picker_id == pid],
            key=lambda e: e.time,
        )
        done_evs = [e for e in picker_evs if e.event_type == 'done']
        if not done_evs:
            continue
        picker_total = done_evs[-1].time
        if picker_total <= 0.0:
            continue
        total_time += picker_total

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

def _pick_lines(task) -> list[tuple[int, int, int]]:
    """(weight, volume, qty) per bin stop that has inventory for this task."""
    return [
        (b.storage.carton.weight, b.storage.carton.volume(),
         task.items[b.storage.carton.sku])
        for b in task.path
        if b.storage is not None and b.storage.carton.sku in task.items
    ]


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
    done_times = [e.time for e in events if e.event_type == 'done']
    duration   = max(done_times, default=0.0)

    num_tasks = len({
        e.aisle_id for e in events
        if e.event_type == 'task_start' and e.aisle_id is not None
    })

    total_items = sum(
        max(
            (e.items_picked for e in events
             if e.picker_id == pid and e.event_type == 'done'),
            default=0,
        )
        for pid in range(k_pickers)
    )

    conc      = avg_concurrent_pickers(events)
    breakdown = picker_time_breakdown(events, k_pickers)

    return BatchStats(
        run_id                 = run_id,
        batch_id               = batch_id,
        duration               = duration,
        num_tasks              = num_tasks,
        total_items            = total_items,
        avg_concurrent_pickers = conc,
        picking_pct            = breakdown['picking_pct'],
        traveling_pct          = breakdown['traveling_pct'],
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
    W_a              : analytical workload via aisle_workload()
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
        W_a = aisle_workload(
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
        num_bins = sum(
            1 for b in task.path
            if b.storage is not None and b.storage.carton.sku in task.items
        )
        result.append(TaskStats(
            run_id           = run_id,
            batch_id         = batch_id,
            aisle_id         = aisle_id,
            picker_id        = aisle_picker.get(aisle_id, -1),
            duration         = end_time - aisle_start[aisle_id],
            W_a              = W_a,
            lift_sum         = ls,
            num_bins_visited = num_bins,
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
            picker_id=s.picker_id, duration=s.duration, W_a=s.W_a,
            lift_sum=s.lift_sum, num_bins_visited=s.num_bins_visited,
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


# ── recovery pipeline ─────────────────────────────────────────────────────────

def task_stats_to_aisle_loads(
    task_stats: list[TaskStats],
    run_id: int = 0,
) -> list:
    """Convert TaskStats records into AisleLoadRecords for parameter recovery.

    Maps task simulation duration → observed_L_a.  Records with duration <= 0
    or W_a <= 0 are excluded; they cannot contribute to OLS regression.
    """
    from Picking_Data import AisleLoadRecord

    return [
        AisleLoadRecord(
            run_id       = run_id,
            batch_id     = s.batch_id,
            aisle_id     = s.aisle_id,
            W_a          = s.W_a,
            lift_sum     = s.lift_sum,
            observed_L_a = s.duration,
        )
        for s in task_stats
        if s.duration > 0.0 and s.W_a > 0.0
    ]


def recover_params_to_db(
    db_path: str,
    run_id: int,
    records: list,
    k_per_task: int = 1,
    json_path: str | None = None,
    do_plot: bool = False,
) -> object:
    """Full IQR-clean recovery pipeline: fit LoadParams and persist to DB.

    Steps
    -----
    1. OLS fit on all records → raw_params.
    2. Flag outliers via IQR on (observed_L_a - W_a) residual.
    3. OLS fit on clean subset → clean_params.
    4. RMSE for raw and clean fits.
    5. Save flagged AisleLoadRecords to aisle_loads table.
    6. Save RecoveredParams to recovered_params table.
    7. Optionally export JSON and plot.

    Returns the RecoveredParams instance stored in the DB, or None if there
    are no usable records (lift_sum = 0 for all, or fewer than 2 valid points).

    Parameters
    ----------
    k_per_task : pickers assigned to a single aisle task — almost always 1
        in a simulation where each task goes to one picker.  This is NOT the
        total picker count; passing the fleet size will shrink W_a/k by that
        factor and produce wrong λ/γ estimates.

    Notes
    -----
    Records with lift_sum = 0 are automatically skipped by
    recover_params_from_records — they contribute to the outlier / RMSE
    calculation but not to the OLS fit.  Use build_placed_affinity() before
    the simulation loop to ensure non-zero lift_sums.
    """
    from math import sqrt
    from datetime import datetime, timezone

    from Picking_Analytics import (
        aisle_load_from_sum,
        flag_outliers,
        plot_loads,
        recover_params_from_records,
    )
    from Picking_Data import (
        RecoveredParams,
        export_params_json,
        save_aisle_loads,
        save_recovered_params,
    )

    if not records:
        print('  [recovery] No records supplied — skipping.')
        return None

    k = float(k_per_task)

    # ── raw fit ────────────────────────────────────────────────────────────────
    raw_params = recover_params_from_records(records, k)
    raw_rmse   = sqrt(
        sum(
            (aisle_load_from_sum(r.W_a, r.lift_sum, raw_params) - r.observed_L_a) ** 2
            for r in records
        ) / max(len(records), 1)
    )

    # ── IQR outlier flagging + clean fit ───────────────────────────────────────
    flagged = flag_outliers(records, iqr_factor=1.5)
    clean   = [r for r in flagged if not r.is_outlier]

    if len(clean) >= 3:
        clean_params = recover_params_from_records(clean, k)
        clean_rmse   = sqrt(
            sum(
                (aisle_load_from_sum(r.W_a, r.lift_sum, clean_params) - r.observed_L_a) ** 2
                for r in clean
            ) / len(clean)
        )
    else:
        clean_params = raw_params
        clean_rmse   = raw_rmse

    # ── persist ────────────────────────────────────────────────────────────────
    for r in flagged:
        r.run_id = run_id
    save_aisle_loads(db_path, run_id, flagged)

    rp = RecoveredParams(
        run_id     = run_id,
        lambda_    = clean_params.lambda_,
        k          = clean_params.k,
        gamma      = clean_params.gamma,
        n_samples  = len(records),
        n_clean    = len(clean),
        rmse_raw   = raw_rmse,
        rmse_clean = clean_rmse,
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
    save_recovered_params(db_path, rp)

    if json_path:
        export_params_json(rp, json_path)

    n_valid = sum(1 for r in records if r.lift_sum > 0)
    print(
        f'  Records : {len(records):,}  |  lift_sum > 0 : {n_valid:,}  |  '
        f'outliers : {len(records) - len(clean):,}  |  clean : {len(clean):,}\n'
        f'  Raw    — RMSE={raw_rmse:.4f}  '
        f'lambda={raw_params.lambda_:.4f}  gamma={raw_params.gamma:.4f}\n'
        f'  Clean  — RMSE={clean_rmse:.4f}  '
        f'lambda={clean_params.lambda_:.4f}  gamma={clean_params.gamma:.4f}  '
        f'k={clean_params.k:.1f}'
    )

    if do_plot:
        plot_loads(
            flagged,
            raw_params   = raw_params,
            clean_params = clean_params,
            title        = f'Run {run_id} — LoadParams Recovery (simulation data)',
        )

    return rp
