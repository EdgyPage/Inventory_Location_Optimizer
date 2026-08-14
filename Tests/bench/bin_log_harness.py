"""bin_log_harness.py — a sandbox for proving a bin-mutation log can replay the simulation.

NOT COLLECTED BY PYTEST (no `test_` prefix). This is the fixture builder behind
`Tests/integration/test_bin_log_replay.py`.

Why it exists
-------------
The persisted record is lossy: `bin_inventory` logs picks and **never** restocks, so the viewer can
only reconstruct state exactly at keyframe batches. The proposed fix is to record the placement
event. Before changing a line of `Warehouse/` or `Optimization/`, this harness proves the proposed
log is *sufficient* — that folding it reproduces the simulation's own bin state exactly, at every
batch and at every sim-time inside a batch.

Zero production edits
---------------------
The recorder attaches by rebinding methods on the manager **instance**, so `Warehouse/` is untouched
and a strategy swapping `mgr.placement` cannot detach it. This is the same technique
`Diagnostics/trace_lifecycle.py:94-135` already uses on `_execute_placement`.

Every bin mutation in the codebase happens at one of five sites, and only two of them are unrecorded
today:

    Inventory_Management._execute_placement   `bin_.storage = unit`     -> PLACE   (wrapped here)
    inventory_reorder.requeue_bin             `bin_.storage = None`     -> EVICT   (wrapped here)
    fast_pick / Pick                          quantity -= / = None      -> PICK    (already in `picks`)
    Storage_Primitive.StorageCart             dead code, zero callers   -> n/a

So a log of PLACE + EVICT + PICK is complete by construction. This harness is the experiment that
checks the construction argument against a running simulation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from Warehouse.catalog.Order import Order
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.picking.Pick import PickConfig
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
from Warehouse.picking.fast_pick import DeferredPickSimulation
from Warehouse.placement.Capacity_Reloader import Capacity_Reloader, demote_unpopular

import perf_simulation                                   # sibling in Tests/bench (see conftest)


# ── the event log ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Place:
    """A bin was filled. `seq` orders fills within a batch; restocks have no finer clock."""
    batch: int
    seq: int
    loc: tuple            # (aisle_id, bayX, bayY)
    sku: int
    qty: int
    cause: str            # 'initial' | 'reorder' | 'reslot'


@dataclass(frozen=True)
class Evict:
    """A bin was emptied by the reloader; the SAME unit object goes back on the stock queue."""
    batch: int
    seq: int
    loc: tuple
    sku: int
    qty: int
    unit_tag: int


@dataclass(frozen=True)
class Pick:
    """Units removed by a picker. Mirrors a row of the existing `picks` table."""
    batch: int
    t: float
    loc: tuple
    sku: int
    qty: int


@dataclass
class BinLog:
    places: list = field(default_factory=list)
    evicts: list = field(default_factory=list)
    picks: list = field(default_factory=list)

    def events_upto(self, batch: int, t: float | None = None):
        """Every event at or before (batch, t), in application order.

        Order within a batch is EVICT -> PLACE -> PICK, mirroring the runner: the reloader runs
        first (`strategy_runner.py:428`), then `check_reorders` (`:429`), then the pick simulation
        (`:484`).
        """
        for b in range(batch + 1):
            last = b == batch
            for e in (x for x in self.evicts if x.batch == b):
                yield e
            for p in (x for x in self.places if x.batch == b):
                yield p
            for k in (x for x in self.picks if x.batch == b):
                if last and t is not None and k.t > t:
                    continue
                yield k


def fold(log: BinLog, batch: int, t: float | None = None) -> dict:
    """Replay the log to `(batch, t)` and return `{loc: (sku, qty)}` for occupied bins.

    This is the whole claim under test: no snapshot, no keyframe — just the events.
    """
    state: dict = {}
    for ev in log.events_upto(batch, t):
        if isinstance(ev, Evict):
            state.pop(ev.loc, None)
        elif isinstance(ev, Place):
            state[ev.loc] = (ev.sku, ev.qty)
        else:                                             # Pick
            cur = state.get(ev.loc)
            if cur is None:
                continue
            remaining = cur[1] - ev.qty
            if remaining > 0:
                state[ev.loc] = (cur[0], remaining)
            else:
                state.pop(ev.loc, None)
    return state


# ── the recorder: instance wrappers, no domain edits ─────────────────────────────

def attach_recorder(mgr) -> BinLog:
    """Rebind `_execute_placement` and `requeue_bin` on this manager to also record.

    `_execute_placement` is the single chokepoint for every fill — `_stock_per_unit`,
    `_stock_ranked` and `inventory_optimal._optimal_assign` all reach `bin_.storage = unit`
    through `self.`, so an instance attribute shadows the class method for all three.

    Placement quantities and the bin are read BEFORE delegating, because the manager mutates
    `_queued_qty` and the bin during the call.
    """
    log = BinLog()
    state = {'batch': 0, 'place_seq': 0, 'evict_seq': 0, 'evicted_units': set()}

    orig_place = mgr._execute_placement
    orig_evict = mgr.requeue_bin

    def _execute_placement(unit, bin_):
        # A unit that was evicted earlier is being re-placed; that is a reslot move, not a
        # fresh arrival. requeue_bin re-queues the identical object, so identity separates them.
        tag = id(unit)
        cause = ('reslot' if tag in state['evicted_units']
                 else ('initial' if state['batch'] == 0 and not log.picks else 'reorder'))
        state['evicted_units'].discard(tag)
        loc, sku, qty = bin_.location, unit.order.sku, unit.quantity
        orig_place(unit, bin_)
        log.places.append(Place(state['batch'], state['place_seq'], loc, sku, qty, cause))
        state['place_seq'] += 1

    def requeue_bin(bin_, *a, **kw):
        unit = bin_.storage
        rec = None
        if unit is not None:
            rec = Evict(state['batch'], state['evict_seq'], bin_.location,
                        unit.order.sku, unit.quantity, id(unit))
        out = orig_evict(bin_, *a, **kw)
        if rec is not None:
            log.evicts.append(rec)
            state['evict_seq'] += 1
            state['evicted_units'].add(rec.unit_tag)
        return out

    mgr._execute_placement = _execute_placement
    mgr.requeue_bin = requeue_bin
    log._state = state                                    # the driver advances `batch`
    return log


def begin_batch(log: BinLog, batch: int) -> None:
    """Roll the recorder onto a new batch and reset the per-batch sequence counters."""
    log._state.update(batch=batch, place_seq=0, evict_seq=0)


# ── ground truth ─────────────────────────────────────────────────────────────────

def observe(warehouse) -> dict:
    """The simulation's ACTUAL bin state, read straight off the objects.

    Deliberately independent of the manager's bookkeeping (`_unavailable` lags a pick by one
    batch), so it cannot agree with the log for the wrong reason.
    """
    return {b.location: (b.storage.order.sku, b.storage.quantity)
            for b in warehouse.bins
            if b.storage is not None and b.storage.quantity > 0}


# ── scenario construction ────────────────────────────────────────────────────────

def build_scenario(n_skus: int = 300, bins_per_aisle: int = 40, seed: int = 42,
                   reorder_point: int = 4, equilibrium_qty: int = 8):
    """A small but structurally complete warehouse whose SKUs actually reorder.

    `perf_simulation._build_inventory` builds `Order((handling, category))` with **no
    reorder_point**, so `check_reorders()` never fires and a placement recorder would be tested
    against zero placements — the whole exercise would pass vacuously. Setting it here is the
    point of this function.
    """
    inv = perf_simulation._build_inventory(n_skus, seed=seed)
    for order in inv.orders:
        order.equilibrium_qty = equilibrium_qty
        order.reorder_point = reorder_point
        order.lead_time_mean = 0.0
        order.supply_cv = 0.0

    cfg = perf_simulation._build_warehouse_cfg(n_skus, bins_per_aisle)
    random.seed(seed)
    warehouse = Warehouse_Builder().from_config(cfg).build()
    mgr = Inventory_Manager(warehouse)
    return inv, warehouse, mgr


def make_reloader(move_limit_pct: float = 0.5) -> Capacity_Reloader:
    """A reloader tuned to actually evict on a SMALL warehouse.

    `per_aisle_cap` is `int(move_limit_pct * bins in the largest XL-pallet aisle)`, so the
    production default of 0.005 floors to **zero** at this scale and `requeue_bin` would never
    fire. Eviction is the one mutation path with no production data behind it (every shipped arm
    is `norsl`), so it has to be forced here or it goes untested entirely.
    """
    return Capacity_Reloader('demote_unpopular', demote_unpopular, move_limit_pct=move_limit_pct)


def _freq_of(inv) -> dict:
    """sku -> relative frequency, the ranking input the reloader selects victims by."""
    return {o.sku: getattr(o.demand, 'relative_frequency', 0.0) if hasattr(o, 'demand')
            else 0.0 for o in inv.orders}


def run_sim(inv, warehouse, mgr, log: BinLog, n_batches: int = 8, seed: int = 42,
            n_pickers: int = 4, reloader: Capacity_Reloader | None = None):
    """Drive the batch loop the way `strategy_runner.py:424-515` does.

    Returns `[(batch, truth_after_reorders, truth_after_picks, max_t), ...]` so a test can check
    the fold at both edges of every batch.
    """
    batch_cfg = BatchConfig(inventory_size=len(inv.orders), mean_fraction=0.25, std_fraction=0.05)
    pick_cfg = PickConfig(num_pickers=n_pickers)
    freq = _freq_of(inv)
    frames = []

    begin_batch(log, 0)
    mgr.enqueue_all(inv.orders)                           # initial stock -> PLACE events

    for i in range(n_batches):
        begin_batch(log, i)
        if reloader is not None and i > 0:
            # Same order as the runner: reload (:428) then check_reorders (:429).
            reloader.reload(mgr, freq, pick_cfg.x_speed, pick_cfg.y_speed)   # -> EVICT
        mgr.check_reorders()                              # -> PLACE
        after_reorders = observe(warehouse)

        batch = Batch(batch_cfg, inv, rng=random.Random(seed + i))
        tasks = Task.from_batch(batch, warehouse, manager=mgr, cart=pick_cfg.cart)
        max_t = 0.0
        if tasks:
            events = DeferredPickSimulation(tasks, pick_cfg, manager=mgr).run()
            for e in events:                              # -> PICK
                if e.event_type == 'pick' and e.location is not None:
                    log.picks.append(Pick(i, e.time, tuple(e.location), e.sku, e.quantity))
                    max_t = max(max_t, e.time)
        frames.append((i, after_reorders, observe(warehouse), max_t))

    return frames
