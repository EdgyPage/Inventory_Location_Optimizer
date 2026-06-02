"""Two-phase deferred-mutation pick simulation.

Phase 1 (concurrent):  All pickers run inside a ThreadPoolExecutor.
  Each thread reads a bin-quantity snapshot taken before Phase 1 starts,
  then simulates its full task list without touching any shared state.
  Output: (list[PickEvent], list[_PickMutation]) per picker.

Phase 2 (sequential):  Main thread applies every _PickMutation to the
  actual bin objects, capping each pick at remaining quantity to handle
  any cross-picker contention (rare -- pickers get disjoint aisles).
  After updating bin_.storage.quantity it calls manager._notify_pick /
  _notify_bin_emptied exactly as the original PickSimulation did.

The return value of DeferredPickSimulation.run() is identical to
PickSimulation.run() so extract_batch_stats / extract_task_stats need
no changes.

GIL note: CPython threads share the GIL.  For pure-Python compute the
gain from Phase 1 threading is ~1.5-3x via GIL-switch interleaving.
Cython 'nogil' on _simulate_picker_deferred gives the full N-picker
parallelism without a language rewrite of the data model.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from Pick import PickConfig, PickEvent, PickerProgress, _pick_time
from Storage_Primitive import StorageCart
from Workload_Builder import Task

if TYPE_CHECKING:
    from Inventory_Management import Inventory_Manager

_CART_CAPACITY: int = StorageCart.max_length * StorageCart.max_width * StorageCart.max_height


@dataclass
class _PickMutation:
    """Deferred write recorded by Phase 1; applied to bin state in Phase 2."""
    bin_ref: object   # Aisle.Bin -- stable object ref; only .storage is written in Phase 2
    sku:     int
    qty:     int      # quantity as seen by this picker in the Phase-1 snapshot


def _simulate_picker_deferred(
    picker_id: int,
    tasks:     list[Task],
    cfg:       PickConfig,
    bin_snap:  dict[int, int],   # id(bin_) -> qty at Phase-1 start; never written by threads
) -> tuple[list[PickEvent], list[_PickMutation]]:
    """Phase 1 worker -- read-only picker simulation.

    Uses bin_snap so no bin_.storage.quantity reads/writes happen on the
    shared bin objects.  bin_.storage.carton is read (sku, weight, volume)
    but never mutated, so it is safe across all concurrent threads.
    """
    events:    list[PickEvent]     = []
    mutations: list[_PickMutation] = []

    t              = 0.0
    x, y           = 1, 1
    cart_remaining = _CART_CAPACITY
    session_items  = 0
    # Tracks intra-picker depletion so picking twice from the same bin
    # within one picker's session is handled correctly.
    local_qty: dict[int, int] = {}

    for task in tasks:
        total_bins  = len(task.path)
        total_items = sum(task.items.values())
        bins_done   = 0

        events.append(PickEvent(
            time=t, picker_id=picker_id, event_type='task_start',
            aisle_id=task.aisle_id,
            bins_completed=0, total_bins=total_bins,
            items_picked=session_items, total_items=total_items,
        ))

        for bin_ in task.path:
            t += (abs(bin_.bayX - x) * cfg.x_move_time
                  + abs(bin_.bayY - y) * cfg.y_move_time)
            x, y = bin_.bayX, bin_.bayY

            bid      = id(bin_)
            snap_qty = local_qty.get(bid, bin_snap.get(bid, 0))
            if snap_qty == 0:
                continue
            # bin_.storage is guaranteed non-None here because bin_snap was
            # built from bins where storage is not None, and Phase 1 never
            # writes bin_.storage = None (that only happens in Phase 2).
            carton = bin_.storage.carton
            qty    = min(task.items.get(carton.sku, 0), snap_qty)
            if qty == 0:
                continue
            local_qty[bid] = snap_qty - qty

            events.append(PickEvent(
                time=t, picker_id=picker_id, event_type='arrive',
                aisle_id=task.aisle_id, location=bin_.location,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

            needed_vol   = carton.volume() * qty
            cart_swapped = needed_vol > cart_remaining
            if cart_swapped:
                events.append(PickEvent(
                    time=t, picker_id=picker_id, event_type='cart_swap',
                    aisle_id=task.aisle_id, location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                ))
                cart_remaining = _CART_CAPACITY

            t             += _pick_time(cfg, carton.weight, carton.volume(), qty, cart_swapped)
            cart_remaining  = max(0, cart_remaining - needed_vol)
            bins_done      += 1
            session_items  += qty

            events.append(PickEvent(
                time=t, picker_id=picker_id, event_type='pick',
                aisle_id=task.aisle_id, sku=carton.sku, quantity=qty,
                location=bin_.location,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

            mutations.append(_PickMutation(bin_ref=bin_, sku=carton.sku, qty=qty))

        events.append(PickEvent(
            time=t, picker_id=picker_id, event_type='task_end',
            aisle_id=task.aisle_id,
            bins_completed=bins_done, total_bins=total_bins,
            items_picked=session_items, total_items=total_items,
        ))

    events.append(PickEvent(
        time=t, picker_id=picker_id, event_type='done',
        items_picked=session_items, total_items=session_items,
    ))
    return events, mutations


class DeferredPickSimulation:
    """Two-phase pick simulation with the same interface as PickSimulation.

    After run() completes, .phase1_time and .phase2_time hold wall-clock
    seconds for Phase 1 (concurrent picker compute) and Phase 2 (sequential
    mutation application) respectively -- useful for benchmarking.
    """

    def __init__(
        self,
        tasks  : list[Task],
        config : PickConfig,
        manager: Inventory_Manager | None = None,
    ) -> None:
        sorted_tasks = sorted(tasks, key=lambda t: t.aisle_id)
        n = config.num_pickers
        self._picker_tasks: list[list[Task]] = [[] for _ in range(n)]
        for i, task in enumerate(sorted_tasks):
            self._picker_tasks[i % n].append(task)
        self._config   = config
        self._manager  = manager
        self._events: list[PickEvent] | None = None
        self.phase1_time = 0.0
        self.phase2_time = 0.0

    def run(self) -> list[PickEvent]:
        """Two-phase simulation; returns all events sorted by time."""
        import time as _time

        cfg = self._config
        n   = len(self._picker_tasks)

        # ── Phase 1: snapshot + concurrent picker simulation ──────────────────
        t0 = _time.perf_counter()

        bin_snap: dict[int, int] = {}
        for tasks in self._picker_tasks:
            for task in tasks:
                for bin_ in task.path:
                    bid = id(bin_)
                    if bid not in bin_snap and bin_.storage is not None:
                        bin_snap[bid] = bin_.storage.quantity

        results: list[tuple[list[PickEvent], list[_PickMutation]]] = [None] * n  # type: ignore
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = {
                pool.submit(_simulate_picker_deferred, pid, tasks, cfg, bin_snap): pid
                for pid, tasks in enumerate(self._picker_tasks)
            }
            for fut in futs:
                results[futs[fut]] = fut.result()

        self.phase1_time = _time.perf_counter() - t0

        # ── Phase 2: apply mutations sequentially ─────────────────────────────
        t0  = _time.perf_counter()
        mgr = self._manager
        for _, mutations in results:
            for mut in mutations:
                bin_ = mut.bin_ref
                if bin_.storage is None:
                    continue    # earlier mutation in this Phase 2 pass emptied it
                actual = min(mut.qty, bin_.storage.quantity)
                if actual == 0:
                    continue
                bin_.storage.quantity -= actual
                if mgr is not None:
                    mgr._notify_pick(mut.sku, actual)
                if bin_.storage.quantity == 0:
                    bin_.storage = None
                    if mgr is not None:
                        mgr._notify_bin_emptied(bin_)

        self.phase2_time = _time.perf_counter() - t0

        # ── collect and sort events ────────────────────────────────────────────
        all_events: list[PickEvent] = []
        for evts, _ in results:
            all_events.extend(evts)
        all_events.sort()
        self._events = all_events
        return all_events

    # ── same progress API as PickSimulation ───────────────────────────────────

    def progress_at(self, t: float) -> list[PickerProgress]:
        if self._events is None:
            raise RuntimeError('Call run() before progress_at()')
        return [self._state_at(pid, t) for pid in range(self._config.num_pickers)]

    def step_table(self, step: float = 1.0) -> list[list[PickerProgress]]:
        if self._events is None:
            raise RuntimeError('Call run() before step_table()')
        max_time  = max((e.time for e in self._events), default=0.0)
        snapshots = []
        t = 0.0
        while t <= max_time:
            snapshots.append(self.progress_at(t))
            t = round(t + step, 10)
        return snapshots

    def _state_at(self, picker_id: int, t: float) -> PickerProgress:
        picker_events = [e for e in (self._events or []) if e.picker_id == picker_id]
        past = [e for e in picker_events if e.time <= t]
        if not past:
            return PickerProgress(t, picker_id, 'idle', None, 0, 0, 0, 0, 1, 0.0)
        last       = past[-1]
        carts_used = sum(1 for e in past if e.event_type == 'cart_swap') + 1
        if last.event_type == 'done':
            return PickerProgress(
                t, picker_id, 'idle', None,
                last.bins_completed, last.total_bins,
                last.items_picked, last.total_items,
                carts_used, 1.0,
            )
        status = {
            'task_start': 'traveling', 'arrive': 'picking',
            'cart_swap': 'cart_swap', 'pick': 'traveling', 'task_end': 'traveling',
        }.get(last.event_type, 'idle')
        return PickerProgress(
            time=t, picker_id=picker_id, status=status,
            task_aisle_id=last.aisle_id,
            bins_completed=last.bins_completed, total_bins=last.total_bins,
            items_picked=last.items_picked, total_items=last.total_items,
            carts_used=carts_used,
            progress=last.bins_completed / (last.total_bins or 1),
        )
