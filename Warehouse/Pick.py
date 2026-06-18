from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from Storage_Primitive import StorageCart
from Workload_Builder import Task

if TYPE_CHECKING:
    from Inventory_Management import Inventory_Manager

_CART_CAPACITY: int = StorageCart.max_length * StorageCart.max_width * StorageCart.max_height

# Height brackets: equipment is slower handling at height.  Each entry is
# (upper_y_phys_exclusive, handling_multiplier); a bin's bracket is the first whose
# threshold exceeds its y_phys (else the last).  The multiplier scales ONLY the
# per-unit weight/volume handling term (not intercept/cart).  Placeholder defaults —
# tune per config / in the calibration notebook.  Mirrored in Optimization/Workload.py.
DEFAULT_HEIGHT_BRACKETS: tuple = ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4))


def height_multiplier(brackets: tuple, y_phys: float) -> float:
    """Handling multiplier for a pick at physical height y_phys (step over brackets)."""
    for thr, mult in brackets:
        if y_phys < thr:
            return mult
    return brackets[-1][1]


# ── configuration ────────────────────────────────────────────────────────────

@dataclass
class PickConfig:
    num_pickers: int        = 1
    x_speed: float          = 1.0   # time units per physical X unit (bin.x_phys)
    y_speed: float          = 0.5   # time units per physical Y unit (bin.y_phys)
    # Log model: pick_time = intercept + weight_coef*ln(weight)*qty + volume_coef*ln(volume)*qty + cart_swap_coef*swapped
    pick_intercept: float   = 1.0
    pick_weight_coef: float = 0.02
    pick_volume_coef: float = 1e-4
    cart_swap_coef: float   = 5.0
    # (upper_y_phys, handling_multiplier) brackets — scales the per-unit handling by height
    height_brackets: tuple  = field(default_factory=lambda: DEFAULT_HEIGHT_BRACKETS)


# ── events ───────────────────────────────────────────────────────────────────

@dataclass
class PickEvent:
    time: float
    picker_id: int
    # 'task_start' | 'arrive' | 'cart_swap' | 'pick' | 'task_end' | 'done'
    event_type: str
    aisle_id: int | None                = None
    sku: int | None                     = None
    quantity: int | None                = None
    location: tuple[int, int, int] | None = None
    bins_completed: int                 = 0
    total_bins: int                     = 0
    items_picked: int                   = 0
    total_items: int                    = 0

    def __lt__(self, other: PickEvent) -> bool:
        return self.time < other.time


# ── per-time-step snapshot ───────────────────────────────────────────────────

@dataclass
class PickerProgress:
    time: float
    picker_id: int
    # 'traveling' | 'picking' | 'cart_swap' | 'idle'
    status: str
    task_aisle_id: int | None
    bins_completed: int
    total_bins: int
    items_picked: int
    total_items: int
    carts_used: int
    progress: float     # bins_completed / total_bins (1.0 when idle/done)

    def __repr__(self) -> str:
        return (
            f'Picker {self.picker_id} t={self.time:.2f} [{self.status}] '
            f'aisle={self.task_aisle_id} '
            f'bins={self.bins_completed}/{self.total_bins} '
            f'items={self.items_picked}/{self.total_items} '
            f'carts={self.carts_used} '
            f'{self.progress:.0%}'
        )


# ── helpers ──────────────────────────────────────────────────────────────────

def _pick_time(cfg: PickConfig, weight: int, volume: int, quantity: int,
               cart_swapped: bool, y_phys: float = 0.0) -> float:
    """Log-linear regression model for time to pick `quantity` units of a carton.

    weight and volume must be ≥ 1; values of 0 would cause math.log(0) which
    raises ValueError.  Clamp both to 1 as a safety floor — a zero-weight or
    zero-volume carton is physically impossible and indicates bad data.

    y_phys is the bin's physical height; the per-unit weight/volume handling is
    multiplied by the height bracket factor (equipment is slower up high).  The
    intercept and cart-swap penalty are not height-scaled.  y_phys defaults to 0
    (ground bracket → factor 1.0) so callers without a bin are unaffected.
    """
    hmult = height_multiplier(cfg.height_brackets, y_phys)
    return (
        cfg.pick_intercept
        + hmult * (cfg.pick_weight_coef * math.log(max(weight, 1))
                   + cfg.pick_volume_coef * math.log(max(volume, 1))) * quantity
        + cfg.cart_swap_coef * int(cart_swapped)
    )


# ── simulation ───────────────────────────────────────────────────────────────

class PickSimulation:
    """Simulate multiple pickers processing a set of Tasks in aisle order.

    Tasks are sorted by aisle_id and distributed to pickers round-robin so that
    each picker works through their assigned aisles in order.
    """

    def __init__(
        self,
        tasks  : list[Task],
        config : PickConfig,
        manager: Inventory_Manager | None = None,
    ) -> None:
        sorted_tasks = sorted(tasks, key=lambda t: t.aisle_id)
        self._picker_tasks: list[list[Task]] = [[] for _ in range(config.num_pickers)]
        for i, task in enumerate(sorted_tasks):
            self._picker_tasks[i % config.num_pickers].append(task)
        self._config  = config
        self._manager = manager
        self._events: list[PickEvent] | None = None

    def run(self) -> list[PickEvent]:
        """Simulate all pickers and return all events sorted by time."""
        all_events: list[PickEvent] = []
        all_picks: list[tuple[int, int]] = []
        all_empties: list = []
        for picker_id, tasks in enumerate(self._picker_tasks):
            all_events.extend(
                self._simulate_picker(picker_id, tasks, all_picks, all_empties)
            )
        all_events.sort()
        self._events = all_events
        if self._manager is not None:
            self._manager._apply_picks_batch(all_picks, all_empties)
        return all_events

    def progress_at(self, t: float) -> list[PickerProgress]:
        """State of every picker at time t. run() must be called first."""
        if self._events is None:
            raise RuntimeError('Call run() before progress_at()')
        return [self._state_at(pid, t) for pid in range(self._config.num_pickers)]

    def step_table(self, step: float = 1.0) -> list[list[PickerProgress]]:
        """Progress snapshots at regular time steps until all pickers are done."""
        if self._events is None:
            raise RuntimeError('Call run() before step_table()')
        max_time = max((e.time for e in self._events), default=0.0)
        snapshots: list[list[PickerProgress]] = []
        t = 0.0
        while t <= max_time:
            snapshots.append(self.progress_at(t))
            t = round(t + step, 10)
        return snapshots

    # ── picker simulation ────────────────────────────────────────────────────

    def _simulate_picker(
        self, picker_id: int, tasks: list[Task],
        picks: list[tuple[int, int]], empties: list['Aisle.Bin'],
    ) -> list[PickEvent]:
        cfg = self._config
        events: list[PickEvent] = []
        time: float = 0.0
        x: float = 0.0   # physical X position (starts at aisle entrance)
        y: float = 0.0   # physical Y position
        cart_remaining: int = _CART_CAPACITY
        carts_used: int = 1
        session_items: int = 0   # cumulative items picked across all tasks
        has_manager: bool = self._manager is not None

        for task in tasks:
            total_bins  = len(task.path)
            total_items = sum(task.items.values())
            bins_done   = 0

            events.append(PickEvent(
                time=time, picker_id=picker_id, event_type='task_start',
                aisle_id=task.aisle_id,
                bins_completed=0, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

            for bin_ in task.path:
                # ── travel (physical distances) ──────────────────────────────
                travel = (abs(bin_.x_phys - x) * cfg.x_speed
                          + abs(bin_.y_phys - y) * cfg.y_speed)
                time += travel
                x, y = bin_.x_phys, bin_.y_phys

                if bin_.storage is None:
                    continue
                carton  = bin_.storage.carton
                qty     = task.items.get(carton.sku, 0)
                if qty == 0:
                    continue

                events.append(PickEvent(
                    time=time, picker_id=picker_id, event_type='arrive',
                    aisle_id=task.aisle_id, location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                ))

                # ── cart swap ────────────────────────────────────────────────
                needed_vol   = carton.volume() * qty
                cart_swapped = needed_vol > cart_remaining
                if cart_swapped:
                    events.append(PickEvent(
                        time=time, picker_id=picker_id, event_type='cart_swap',
                        aisle_id=task.aisle_id, location=bin_.location,
                        bins_completed=bins_done, total_bins=total_bins,
                        items_picked=session_items, total_items=total_items,
                    ))
                    carts_used   += 1
                    cart_remaining = _CART_CAPACITY

                # ── pick ─────────────────────────────────────────────────────
                pt = _pick_time(cfg, carton.weight, carton.volume(), qty, cart_swapped, bin_.y_phys)
                time          += pt
                cart_remaining = max(0, cart_remaining - needed_vol)
                bins_done      += 1
                session_items  += qty

                events.append(PickEvent(
                    time=time, picker_id=picker_id, event_type='pick',
                    aisle_id=task.aisle_id, sku=carton.sku, quantity=qty,
                    location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                ))

                # Deplete the bin; accumulate notifications for batch
                # application after the simulation ends (before check_reorders).
                bin_.storage.quantity = max(0, bin_.storage.quantity - qty)
                if has_manager:
                    picks.append((carton.sku, qty))
                if bin_.storage.quantity == 0:
                    bin_.storage = None
                    if has_manager:
                        empties.append(bin_)

            events.append(PickEvent(
                time=time, picker_id=picker_id, event_type='task_end',
                aisle_id=task.aisle_id,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

        events.append(PickEvent(
            time=time, picker_id=picker_id, event_type='done',
            items_picked=session_items, total_items=session_items,
        ))
        return events

    # ── progress derivation ──────────────────────────────────────────────────


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

        # Derive status from last recorded event type
        status_map = {
            'task_start': 'traveling',
            'arrive':     'picking',
            'cart_swap':  'cart_swap',
            'pick':       'traveling',  # pick is recorded at completion; picker is already moving
            'task_end':   'traveling',
        }
        status   = status_map.get(last.event_type, 'idle')
        total_b  = last.total_bins or 1
        progress = last.bins_completed / total_b

        return PickerProgress(
            time=t,
            picker_id=picker_id,
            status=status,
            task_aisle_id=last.aisle_id,
            bins_completed=last.bins_completed,
            total_bins=last.total_bins,
            items_picked=last.items_picked,
            total_items=last.total_items,
            carts_used=carts_used,
            progress=progress,
        )
