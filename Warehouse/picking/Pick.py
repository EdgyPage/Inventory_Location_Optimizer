from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from Warehouse.layout.Storage_Primitive import StorageCart, StoreCart
from Warehouse.picking.Workload_Builder import Task
# Cost-model primitives live in cost_model (single source of truth).  Re-exported here so
# `from Pick import DEFAULT_HEIGHT_BRACKETS, height_multiplier` keeps working.
from Warehouse.kernel.allocation import partition
from Warehouse.kernel.cost_model import aisle_exit_cost, DEFAULT_HEIGHT_BRACKETS, height_multiplier, handle_var, per_pick, sec_per_inch, cart_step, validate_speeds, SpeedProfile

if TYPE_CHECKING:
    from Warehouse.inventory.Inventory_Management import Inventory_Manager

_CART_CAPACITY: int = StoreCart.capacity()   # default (store) cart volume; see PickConfig.cart


# ── configuration ────────────────────────────────────────────────────────────

@dataclass
class PickConfig:
    num_pickers: int        = 1
    x_speed: float          = 4.0   # horizontal travel speed in ft/s (bin positions are inches)
    y_speed: float          = 2.0   # vertical travel speed in ft/s (bin positions are inches)
    # Log model: pick_time = intercept + weight_coef*ln(weight)*qty + volume_coef*ln(volume)*qty + cart_swap_coef*swapped
    pick_intercept: float   = 1.0
    pick_weight_coef: float = 0.02
    pick_volume_coef: float = 1e-4
    # Base function for each handling term (coef·fn(value)): 'log'(default)/'linear'/'sqrt'/'pow:p'/'log:b'.
    pick_weight_fn: str     = 'log'
    pick_volume_fn: str     = 'log'
    cart_swap_coef: float   = 5.0
    # Cart TYPE for this channel — its capacity() sets the cart-swap threshold. A smaller cart
    # (e.g. FulfillmentCart) swaps more often. Default StoreCart = today's 125,000, so store is
    # unchanged. Stored as the class (stateless config), read via cfg.cart.capacity().
    cart: type[StorageCart] = StoreCart
    # (upper_y_phys, handling_multiplier) brackets — scales the per-unit handling by height
    height_brackets: tuple  = field(default_factory=lambda: DEFAULT_HEIGHT_BRACKETS)
    # One-way lanes: the picker enters an aisle at the mouth and must traverse to the far end
    # to exit, so aisle DEPTH (not within-aisle span) drives x-travel.  False (default) = today's
    # two-way model (no explicit exit).  Consumed by the shared aisle_traverse_cost helper.
    one_way: bool           = False
    # Task→picker scheduler (see assign_tasks): 'round_robin' (default) = the legacy i%num_pickers,
    # byte-identical; 'lpt' = load-balance the fixed work to minimise makespan (higher throughput,
    # unchanged total labor) by minimising the EXACT per-picker load (travel+handling + real cart).
    scheduler: str          = 'round_robin'

    def __post_init__(self):
        # A non-positive speed is NaN-poison, not a slow picker — see validate_speeds().
        validate_speeds(self.x_speed, self.y_speed, source='PickConfig')

    @property
    def speed(self) -> SpeedProfile:
        """This picker's travel speeds as one value, ft/s converted to s/inch once.

        A PROPERTY, not a field, and deliberately: `x_speed`/`y_speed` are a public
        serialization contract.  `run_map_precompute` rebuilds a `PickConfig` from an
        ARCHIVED `config.json` by filtering `dataclasses.fields`, and `docs/macros.py` reads
        `c["x_speed"]` to render the published pick-time formula.  Both would fall back to
        defaults SILENTLY if the fields were replaced — and no canary would catch it,
        because none of them reads an archived config.

        So the pair stays exactly where it is and this is the derived view of it, for the
        callers that want to hoist one object instead of two floats.
        """
        return SpeedProfile(self.x_speed, self.y_speed)


# ── events ───────────────────────────────────────────────────────────────────

@dataclass(slots=True)
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
    # ── travel decomposition (seconds accrued since the previous event, stamped in place) ──
    # Every second the sim advances `time` lands in exactly one field so the split reconciles:
    #   pick_travel_{x,y}     = INTER-PICK travel (bin→bin between picks), by axis
    #   non_pick_travel_{x,y} = aisle ENTRY (+ one-way EXIT) travel, by axis
    #   cart_move             = cart-swap seconds (non-pick, non-axis)
    # Derived views: pick_travel = px+py;  non_pick_travel = npx+npy+cart_move;
    #                travel_x = px+npx;  travel_y = py+npy;  task_travel = all five.
    pick_travel_x: float                = 0.0
    pick_travel_y: float                = 0.0
    non_pick_travel_x: float            = 0.0
    non_pick_travel_y: float            = 0.0
    cart_move: float                    = 0.0

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
               y_phys: float = 0.0) -> float:
    """Log-linear model for the at-location HANDLING time to pick `quantity` units of an order.

    weight and volume must be ≥ 1; values of 0 would cause math.log(0) which
    raises ValueError.  Clamp both to 1 as a safety floor — a zero-weight or
    zero-volume order is physically impossible and indicates bad data.

    y_phys is the bin's physical height; the height bracket factor scales the ENTIRE
    at-location pick operation — the fixed setup/intercept AND the per-unit weight/volume
    handling (equipment is slower at everything up high), i.e. M(y)·(intercept + qty·var).
    y_phys defaults to 0 (ground bracket → factor 1.0) so callers without a bin are unaffected.

    The cart-swap penalty (cfg.cart_swap_coef) is NOT part of this handling term — the sim loops
    charge it as its own timed step at the swap and the decomposition attributes it to travel.
    """
    hmult = height_multiplier(cfg.height_brackets, y_phys)
    var   = handle_var(weight, volume, cfg.pick_weight_coef, cfg.pick_volume_coef,
                       cfg.pick_weight_fn, cfg.pick_volume_fn)
    return per_pick(hmult, cfg.pick_intercept, var, quantity)


# ── simulation ───────────────────────────────────────────────────────────────

class _ProgressAPIMixin:
    """Post-run progress inspection shared by BOTH pick simulations.

    progress_at/step_table/_state_at were verbatim duplicates in PickSimulation
    and DeferredPickSimulation (cosmetic drift only).  They read nothing but
    self._events (set by run()) and self._config.num_pickers, so one mixin
    serves both.  NOT part of the numeric sim path - the picker loops stay
    deliberately separate (deferred-mutation contract).
    """
    def _start_at(self, picker_id: int) -> float:
        """Picker `picker_id`'s clock at the start of this batch.

        `0.0` unless the caller supplied `start_times`, so every construction that does not
        track clocks -- tests, Diagnostics, the calltree harness -- keeps the old model where
        every picker is reborn at zero each batch.  The runner threads the previous batch's
        finish times through instead, which is what puts a whole run's events on one axis.

        A short list is tolerated and reads 0.0 past its end: a crew can grow between
        batches, and a new picker starting at the origin is the honest answer.
        """
        st = getattr(self, '_start_times', None)
        if not st or picker_id >= len(st):
            return 0.0
        return float(st[picker_id])

    def progress_at(self, t: float) -> list[PickerProgress]:
        """State of every picker at time t. run() must be called first.

        Enumerates `range(num_pickers)`, so it reports the PICK crew and only the pick
        crew.  Any other actor sharing the event stream — a putter, an unloader — is
        invisible here no matter what id it carries, which is the same per-crew, dense
        id space `Simulation_Analytics._group_events_by_picker` enforces, and the reason
        a second stream records to its own table rather than widening `picker_events`.
        """
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


# ── task → picker scheduling ─────────────────────────────────────────────────────

def count_cart_swaps(pick_volumes, cart_remaining: float, cart_cap: float) -> tuple[int, float]:
    """Next-fit cart swaps for an ORDERED sequence of pick volumes, starting from ``cart_remaining``.
    Returns ``(swaps, new_remaining)``.  Uses the same ``cart_step`` primitive the sim runs per pick,
    so this reproduces exactly what the sim charges for those picks.

    This is the **exact-makespan predictor** — a partition's realized cart cost, computable up front.
    The LPT scheduler does NOT balance on this; it balances a cheaper continuous proxy (see
    ``assign_tasks``).  This is the oracle the scheduler↔sim self-consistency test checks the sim
    against, plus a reusable helper for scoring a partition's exact makespan."""
    swaps = 0
    for v in pick_volumes:
        swapped, cart_remaining = cart_step(v, cart_remaining, cart_cap)
        swaps += swapped
    return swaps, cart_remaining


def _task_static(task: Task, cfg: PickConfig, x_pace: float, y_pace: float) -> tuple[float, list]:
    """A task's assignment-INDEPENDENT time (travel + handling, NO cart) plus its ordered pick-volume
    sequence — the inputs the LPT scheduler balances.  Replicates PickSimulation._simulate_picker's
    per-task arithmetic exactly (position resets to (0,0) each aisle; one-way exit to the far end);
    the cart term is session-persistent so it is added separately via count_cart_swaps.  Kept in
    lockstep with the sim by the scheduler↔sim self-consistency test.

    NOTE: it reads the UNCAPPED ``task.items[sku]``, matching PickSimulation; fast_pick's deferred
    loop can cap a pick at the snapshot stock under cross-picker contention, so predicted==realized is
    exact for PickSimulation and for the normal disjoint well-stocked aisles, but can differ for a bin
    under-stocked relative to demand under DeferredPickSimulation."""
    x = y = 0.0
    t = 0.0
    volumes: list = []
    for _bi, bin_ in enumerate(task.path):
        t += abs(bin_.x_phys - x) * x_pace + abs(bin_.y_phys - y) * y_pace
        x, y = bin_.x_phys, bin_.y_phys
        if bin_.storage is None:
            continue
        order = bin_.storage.order
        # The same per-bin plan the sims spend, so predicted == realized.  Reading the
        # per-aisle `task.items[sku]` here made the LPT scheduler balance a load no sim
        # would ever produce, on exactly the aisles where a SKU sits in several bins.
        qty = task.planned[_bi]
        if qty == 0:
            continue
        t += _pick_time(cfg, order.weight, order.volume(), qty, bin_.y_phys)
        volumes.append(order.volume() * qty)
    if cfg.one_way and task.path:
        L = getattr(getattr(task.path[0], 'aisle', None), 'aisle_width', None)
        if L is None:
            L = max((b.x_phys for b in task.path), default=0.0)
        t += abs(L - x) * x_pace + y * y_pace
    return t, volumes


def assign_tasks(sorted_tasks: list, cfg: PickConfig) -> list:
    """Partition aisle-Tasks across ``cfg.num_pickers`` pickers.  ``sorted_tasks`` MUST be aisle_id
    sorted (the sim processes each picker's tasks in that order).  The SINGLE task→picker assignment
    used by both PickSimulation and fast_pick, so they cannot drift.

    - ``'round_robin'`` (default): ``i % num_pickers`` — byte-identical to the legacy scheduler.
    - ``'lpt'``: LPT — visit tasks **heaviest-first** and append each to the **least-loaded** picker.
      Both use a per-task cost ``est(t) = (travel+handling) + swap_coef · task_volume / cart_cap`` —
      a *continuous* cart proxy that is monotone in volume, so it can't be fooled by the cart
      step-function the way an exact-but-myopic greedy is (a light task looking free on a
      cart-favourable picker).  Heaviest-first + least-loaded is a standard makespan heuristic — but
      note it is NOT a guaranteed bound on the *realized* step-function makespan and can, on adverse
      inputs, do no better than round-robin; it never yields an invalid partition and total work is
      unchanged, so results stay correct regardless.  Each picker's tasks are then kept in aisle_id
      order — exactly the order the sim picks them — so the **realized** makespan (and the cart swaps
      that dominate it) is computed exactly by the shared next-fit; only the *balancing decisions* use
      the smooth proxy.  Runs once per batch in ``__init__`` at O(total_picks + tasks·pickers)."""
    policy = getattr(cfg, 'scheduler', 'round_robin')
    if policy != 'lpt':
        # Anything that is not 'lpt' has always meant round-robin here, and `partition`
        # rejects an unknown policy rather than falling through — so the coercion stays in
        # THIS function, where the legacy contract lives, and does not leak into the
        # generic splitter.
        return partition(sorted_tasks, cfg.num_pickers, policy='round_robin')

    cap = cfg.cart.capacity()
    coef = cfg.cart_swap_coef
    x_pace, y_pace = cfg.speed.paces

    def _est(t):
        # Per-task balancing cost: static (travel+handling) + a continuous cart proxy
        # (volume/cap).  The proxy is the point — see allocation.partition's docstring.
        st, vols = _task_static(t, cfg, x_pace, y_pace)
        return st + coef * (sum(vols) / cap if cap else 0.0)

    return partition(sorted_tasks, cfg.num_pickers, policy='lpt',
                     cost_of=_est, order_key=lambda t: t.aisle_id)


class PickSimulation(_ProgressAPIMixin):
    """Simulate multiple pickers processing a set of Tasks in aisle order.

    Tasks are sorted by aisle_id and distributed to pickers round-robin so that
    each picker works through their assigned aisles in order.
    """

    def __init__(
        self,
        tasks  : list[Task],
        config : PickConfig,
        manager: Inventory_Manager | None = None,
        start_times: list[float] | None = None,
    ) -> None:
        # start_times[p] = picker p's clock when this batch begins (see _start_at).
        # None => every picker starts at 0.0, which is the pre-clock model exactly.
        self._start_times = start_times
        sorted_tasks = sorted(tasks, key=lambda t: t.aisle_id)
        self._picker_tasks: list[list[Task]] = assign_tasks(sorted_tasks, config)
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
                self._simulate_picker(picker_id, tasks, all_picks, all_empties,
                                      self._start_at(picker_id))
            )
        all_events.sort()
        self._events = all_events
        if self._manager is not None:
            self._manager._apply_picks_batch(all_picks, all_empties)
        return all_events

    def _simulate_picker(
        self, picker_id: int, tasks: list[Task],
        picks: list[tuple[int, int]], empties: list['Aisle.Bin'],
        t0: float = 0.0,
    ) -> list[PickEvent]:
        cfg = self._config
        events: list[PickEvent] = []
        # See fast_pick._simulate_picker_deferred: the clock carries across batches, and
        # t0=0.0 reproduces the pre-clock model exactly.  Kept in lockstep with that loop.
        time: float = t0
        x: float = 0.0   # physical X position (starts at aisle entrance)
        y: float = 0.0   # physical Y position
        cart_cap: int = cfg.cart.capacity()   # this channel's cart volume (swap threshold)
        cart_remaining: int = cart_cap
        carts_used: int = 1
        session_items: int = 0   # cumulative items picked across all tasks
        has_manager: bool = self._manager is not None
        # x_speed/y_speed are ft/s; positions are inches → convert to per-inch pace once.
        x_pace, y_pace = cfg.speed.paces

        for task in tasks:
            total_bins  = len(task.path)
            total_items = sum(task.items.values())
            bins_done   = 0
            # Per-task position reset to the aisle entrance (0,0): each aisle visit starts at the
            # mouth, so the picker never carries a physically-meaningless cross-aisle offset in
            # local coordinates.  Travel to the first pick is now the true aisle ENTRY.
            x = 0.0
            y = 0.0
            # Travel decomposition (reset per task).  The phase flips at the first picked stop:
            # travel BEFORE it is aisle ENTRY (non_pick); travel AFTER is INTER-PICK (pick).
            # Segments to skipped (empty / not-needed) bins accumulate in the pending buffer and
            # are flushed onto the next emitted event, so no second is lost.
            first_pick_seen = False
            acc_px = acc_py = acc_npx = acc_npy = 0.0

            events.append(PickEvent(
                time=time, picker_id=picker_id, event_type='task_start',
                aisle_id=task.aisle_id,
                bins_completed=0, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
            ))

            for _bi, bin_ in enumerate(task.path):
                # ── travel (physical distances in inches; pace = s/inch from ft/s) ───
                # Split per axis for the decomposition; `time` still advances by the identical
                # sum (seg_x + seg_y) so total task duration is byte-for-byte unchanged.
                seg_x = abs(bin_.x_phys - x) * x_pace
                seg_y = abs(bin_.y_phys - y) * y_pace
                time += seg_x + seg_y
                x, y = bin_.x_phys, bin_.y_phys
                if first_pick_seen:
                    acc_px += seg_x; acc_py += seg_y      # inter-pick sweep
                else:
                    acc_npx += seg_x; acc_npy += seg_y    # aisle entry

                if bin_.storage is None:
                    continue
                order  = bin_.storage.order
                # The PLAN for this bin, capped at what the bin actually holds.  This read
                # `task.items[sku]` -- the aisle total -- with NO cap, so it reported
                # picking more units than existed while `max(0, ...)` silently clamped the
                # depletion: the event stream contradicted its own bin state.
                qty     = min(task.planned[_bi], bin_.storage.quantity)
                if qty == 0:
                    continue

                events.append(PickEvent(
                    time=time, picker_id=picker_id, event_type='arrive',
                    aisle_id=task.aisle_id, location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                    pick_travel_x=acc_px, pick_travel_y=acc_py,
                    non_pick_travel_x=acc_npx, non_pick_travel_y=acc_npy,
                ))
                acc_px = acc_py = acc_npx = acc_npy = 0.0
                first_pick_seen = True

                # ── cart swap ────────────────────────────────────────────────
                # The swap consumes its own time (return the full cart, fetch an empty one).
                # Advancing `time` first, then emitting the event, makes the gap ending at the
                # cart_swap event carry the swap seconds — which the decomposition charges to
                # travel (a route/depot cost), not handling.
                needed_vol   = order.volume() * qty
                # Shared next-fit primitive (cost_model.cart_step) — the same step the LPT scheduler
                # replays to predict makespan.  cart_step returns the post-swap, post-decrement
                # remaining, so the trailing decrement is folded in here (byte-identical).
                cart_swapped, cart_remaining = cart_step(needed_vol, cart_remaining, cart_cap)
                if cart_swapped:
                    time += cfg.cart_swap_coef
                    events.append(PickEvent(
                        time=time, picker_id=picker_id, event_type='cart_swap',
                        aisle_id=task.aisle_id, location=bin_.location,
                        bins_completed=bins_done, total_bins=total_bins,
                        items_picked=session_items, total_items=total_items,
                        cart_move=cfg.cart_swap_coef,
                    ))
                    carts_used   += 1

                # ── pick (handling only; cart swap charged above) ────────────
                pt = _pick_time(cfg, order.weight, order.volume(), qty, bin_.y_phys)
                time          += pt
                bins_done      += 1
                session_items  += qty

                events.append(PickEvent(
                    time=time, picker_id=picker_id, event_type='pick',
                    aisle_id=task.aisle_id, sku=order.sku, quantity=qty,
                    location=bin_.location,
                    bins_completed=bins_done, total_bins=total_bins,
                    items_picked=session_items, total_items=total_items,
                ))

                # Deplete the bin; accumulate notifications for batch
                # application after the simulation ends (before check_reorders).
                bin_.storage.quantity = max(0, bin_.storage.quantity - qty)
                if has_manager:
                    picks.append((order.sku, qty))
                if bin_.storage.quantity == 0:
                    bin_.storage = None
                    if has_manager:
                        # (bin, when) — the same stamp fast_pick carries on its deferred
                        # mutation, so the two sims report bin-empty times identically.
                        empties.append((bin_, time))

            # One-way lane EXIT: the picker must traverse to the aisle far end (aisle_width) to
            # leave, then descend to the ground, so aisle DEPTH (not within-aisle span) drives
            # x-travel.  Charged to non_pick.  Two-way (default) has no exit segment.
            if cfg.one_way and task.path:
                L = getattr(getattr(task.path[0], 'aisle', None), 'aisle_width', None)
                if L is None:
                    L = max((b.x_phys for b in task.path), default=0.0)
                exit_x, exit_y = aisle_exit_cost(x, y, L, x_pace, y_pace)
                time   += exit_x + exit_y
                acc_npx += exit_x; acc_npy += exit_y

            # Flush any trailing travel (skipped bins after the last pick, plus the one-way exit)
            # so the split reconciles exactly with task duration.
            events.append(PickEvent(
                time=time, picker_id=picker_id, event_type='task_end',
                aisle_id=task.aisle_id,
                bins_completed=bins_done, total_bins=total_bins,
                items_picked=session_items, total_items=total_items,
                pick_travel_x=acc_px, pick_travel_y=acc_py,
                non_pick_travel_x=acc_npx, non_pick_travel_y=acc_npy,
            ))

        events.append(PickEvent(
            time=time, picker_id=picker_id, event_type='done',
            items_picked=session_items, total_items=session_items,
        ))
        return events

    # ── progress derivation ──────────────────────────────────────────────────

