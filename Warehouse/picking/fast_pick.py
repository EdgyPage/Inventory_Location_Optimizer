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

from Warehouse.picking.Pick import PickConfig, PickEvent, PickerProgress, _pick_time, _ProgressAPIMixin, assign_tasks
from Warehouse.layout.Storage_Primitive import StoreCart
from Warehouse.picking.Workload_Builder import Task
from Warehouse.kernel.cost_model import aisle_exit_cost, sec_per_inch, cart_step

if TYPE_CHECKING:
    from Warehouse.inventory.Inventory_Management import Inventory_Manager

_CART_CAPACITY: int = StoreCart.capacity()   # default (store) cart volume; see PickConfig.cart


@dataclass
class _PickMutation:
    """Deferred write recorded by Phase 1; applied to bin state in Phase 2."""
    bin_ref: object   # Aisle.Bin -- stable object ref; only .storage is written in Phase 2
    sku:     int
    qty:     int      # quantity as seen by this picker in the Phase-1 snapshot
    time:    float = 0.0
    """Picker-local seconds at which this pick completed.

    Phase 2 applies mutations in PICKER order, not time order, so without this the instant
    a bin ran dry is unrecoverable from the mutation alone.  Carried so
    `_notify_bin_emptied` can say WHEN, which is what a forecast of upcoming bin slots is
    built from.  Recorded and not yet read by anything that changes an outcome."""


def _simulate_picker_deferred(
    picker_id: int,
    tasks:     list[Task],
    cfg:       PickConfig,
    bin_snap:  dict[int, int],   # id(bin_) -> qty at Phase-1 start; never written by threads
) -> tuple[list[PickEvent], list[_PickMutation]]:
    """Phase 1 worker -- read-only picker simulation.

    Uses bin_snap so no bin_.storage.quantity reads/writes happen on the
    shared bin objects.  bin_.storage.order is read (sku, weight, volume)
    but never mutated, so it is safe across all concurrent threads.
    """
    events:    list[PickEvent]     = []
    mutations: list[_PickMutation] = []

    t              = 0.0
    x, y           = 0.0, 0.0   # physical position (starts at aisle entrance)
    cart_cap       = cfg.cart.capacity()   # this channel's cart volume (swap threshold)
    cart_remaining = cart_cap
    session_items  = 0
    # x_speed/y_speed are ft/s; positions are inches → convert to per-inch pace once.
    x_pace         = sec_per_inch(cfg.x_speed)
    y_pace         = sec_per_inch(cfg.y_speed)
    # Tracks intra-picker depletion so picking twice from the same bin
    # within one picker's session is handled correctly.
    local_qty: dict[int, int] = {}

    for task in tasks:
        total_bins  = len(task.path)
        total_items = sum(task.items.values())
        bins_done   = 0
        # Per-task position reset to the aisle entrance (lockstep with Pick.py).
        x = 0.0
        y = 0.0
        # Travel decomposition — kept byte-for-byte in lockstep with Pick.py._simulate_picker.
        # The guards are Tests/unit/test_travel_decomposition.py and test_scheduler.py; this
        # comment named test_placement_fastpath_equivalence for years, which is about
        # _PrefPool map placement and touches neither sim's travel math.  Phase flips at the first picked
        # stop: before = aisle ENTRY (non_pick), after = INTER-PICK (pick).
        first_pick_seen = False
        acc_px = acc_py = acc_npx = acc_npy = 0.0

        events.append(PickEvent(
            time=t, picker_id=picker_id, event_type='task_start',
            aisle_id=task.aisle_id,
            bins_completed=0, total_bins=total_bins,
            items_picked=session_items, total_items=total_items,
        ))

        for bin_ in task.path:
            seg_x = abs(bin_.x_phys - x) * x_pace
            seg_y = abs(bin_.y_phys - y) * y_pace
            t += seg_x + seg_y
            x, y = bin_.x_phys, bin_.y_phys
            if first_pick_seen:
                acc_px += seg_x; acc_py += seg_y
            else:
                acc_npx += seg_x; acc_npy += seg_y

            bid      = id(bin_)
            snap_qty = local_qty.get(bid, bin_snap.get(bid, 0))
            if snap_qty == 0:
                continue
            # bin_.storage is guaranteed non-None here because bin_snap was
            # built from bins where storage is not None, and Phase 1 never
            # writes bin_.storage = None (that only happens in Phase 2).
            order = bin_.storage.order
            qty    = min(task.items.get(order.sku, 0), snap_qty)
            if qty == 0:
                continue
            local_qty[bid] = snap_qty - qty

            events.append(PickEvent(
                time=t, picker_id=picker_id, event_type='arrive',
                aisle_id=task.aisle_id, location=bin_.location,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
                pick_travel_x=acc_px, pick_travel_y=acc_py,
                non_pick_travel_x=acc_npx, non_pick_travel_y=acc_npy,
            ))
            acc_px = acc_py = acc_npx = acc_npy = 0.0
            first_pick_seen = True

            # Swap consumes its own time; advancing `t` before emitting the event makes the gap
            # ending at cart_swap carry the swap seconds → attributed to travel (see Pick.py).
            needed_vol   = order.volume() * qty
            # Shared next-fit primitive — lockstep with Pick.py and the LPT scheduler's predictor.
            cart_swapped, cart_remaining = cart_step(needed_vol, cart_remaining, cart_cap)
            if cart_swapped:
                t += cfg.cart_swap_coef
                events.append(PickEvent(
                    time=t, picker_id=picker_id, event_type='cart_swap',
                    aisle_id=task.aisle_id, location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                    cart_move=cfg.cart_swap_coef,
                ))

            t             += _pick_time(cfg, order.weight, order.volume(), qty, bin_.y_phys)
            bins_done      += 1
            session_items  += qty

            events.append(PickEvent(
                time=t, picker_id=picker_id, event_type='pick',
                aisle_id=task.aisle_id, sku=order.sku, quantity=qty,
                location=bin_.location,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

            mutations.append(_PickMutation(bin_ref=bin_, sku=order.sku, qty=qty,
                                           time=t))

        # One-way lane EXIT (lockstep with Pick.py): traverse to the aisle far end + descend.
        if cfg.one_way and task.path:
            L = getattr(getattr(task.path[0], 'aisle', None), 'aisle_width', None)
            if L is None:
                L = max((b.x_phys for b in task.path), default=0.0)
            exit_x, exit_y = aisle_exit_cost(x, y, L, x_pace, y_pace)
            t      += exit_x + exit_y
            acc_npx += exit_x; acc_npy += exit_y

        events.append(PickEvent(
            time=t, picker_id=picker_id, event_type='task_end',
            aisle_id=task.aisle_id,
            bins_completed=bins_done, total_bins=total_bins,
            items_picked=session_items, total_items=total_items,
            pick_travel_x=acc_px, pick_travel_y=acc_py,
            non_pick_travel_x=acc_npx, non_pick_travel_y=acc_npy,
        ))

    events.append(PickEvent(
        time=t, picker_id=picker_id, event_type='done',
        items_picked=session_items, total_items=session_items,
    ))
    return events, mutations


class DeferredPickSimulation(_ProgressAPIMixin):
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
        self._picker_tasks: list[list[Task]] = assign_tasks(sorted_tasks, config)   # shared with Pick
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
                        mgr._notify_bin_emptied(bin_, at=mut.time)

        self.phase2_time = _time.perf_counter() - t0

        # ── collect and sort events ────────────────────────────────────────────
        all_events: list[PickEvent] = []
        for evts, _ in results:
            all_events.extend(evts)
        all_events.sort()
        self._events = all_events
        return all_events

    # ── same progress API as PickSimulation ───────────────────────────────────
