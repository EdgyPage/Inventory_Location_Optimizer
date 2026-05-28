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
    events: list,
    tasks: list,
    batch_id: int,
    affinity: dict,
    wp: WorkloadParams,
    run_id: int = 0,
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
        ls       = sum_lift(list(task.items.keys()), affinity)
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
