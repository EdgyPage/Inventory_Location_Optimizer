import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from Carton import Carton
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, Storage_Size, viable_storage_units, _max_qty_fits as _sq_max
from Affinity_Store import AffinityStore

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]

@dataclass
class LoadParams:
    lambda_: float = 1.0   # startup-cost multiplier
    k: float       = 1.0   # pickers per task (normally 1 for single-aisle tasks)
    gamma: float   = 1.5   # congestion exponent

_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(
        sorted(Storage_Size.available_sizes_heights, key=Storage_Size.available_sizes_heights.__getitem__)
    )
}

# Sizes ordered from largest to smallest — used by _candidates for O(1) tier lookup.
_SIZES_DESCENDING: tuple[str, ...] = tuple(
    sorted(_SIZE_RANKS, key=_SIZE_RANKS.__getitem__, reverse=True)
)

BinKey = tuple[str, str, str, str]

# Maximum consecutive failed drain attempts before a stuck unit is abandoned
# and _queued_sku_counts is decremented so a fresh reorder can fire next batch.
_MAX_DRAIN_RETRIES: int = 5


def _equilibrium_qty(carton: Carton) -> int:
    """Return the Order-Up-To target for *carton*.

    Reads equilibrium_qty if present (new schema); falls back to the legacy
    stock_qty attribute so old in-memory inventories still work correctly.
    """
    return getattr(carton, 'equilibrium_qty',
                   getattr(carton, 'stock_qty', 1))


def _max_qty_fitting_pallet_size(carton: Carton, target_size: str) -> int:
    """Return the maximum number of *carton* items that stack onto one pallet
    whose storage_size is at most *target_size*.

    Pallet stacking height increases monotonically with quantity, so the
    required storage_size also increases.  We scan from 1 upward until the
    pallet outgrows the target tier and return the last fitting quantity.
    Used by _drain to repack a stranded unit into smaller bins.
    """
    target_rank = _SIZE_RANKS.get(target_size, 0)
    result = 0
    for q in range(1, 10_000):
        try:
            p = Pallet(carton, q)
            if _SIZE_RANKS.get(p.storage_size, 99) <= target_rank:
                result = q
            else:
                break   # size is monotone-increasing — stop early
        except ValueError:
            break
    return result


def _uniform_assignment(unit: StorageUnit, candidates: list[Aisle.Bin]) -> Aisle.Bin | None:
    """Pick uniformly at random from the candidate bin list.

    candidates is pre-filtered by _candidates() to the correct handling type,
    storage category, unit type, and largest available size tier.  Picking
    randomly within that filtered set uniformly distributes placements across
    the matching bin locations.
    """
    return random.choice(candidates) if candidates else None



class Inventory_Manager:

    def __init__(
        self,
        warehouse: Warehouse,
        assignment_fn: AssignmentFn = _uniform_assignment,
        affinity: AffinityStore | None = None,
    ) -> None:
        self.warehouse: Warehouse = warehouse
        self.assignment_fn: AssignmentFn = assignment_fn
        self._affinity: AffinityStore | None = affinity
        self._index: dict[BinKey, list[Aisle.Bin]] = defaultdict(list)
        # id(bin) → position in its _index tier list — O(1) swap-remove support.
        self._bin_index_pos: dict[int, int] = {}

        # Keyed by id(bin) for O(1) removal when bins are reclaimed.
        self._unavailable: dict[int, Aisle.Bin] = {}

        # Queue holds pre-palletized StorageUnit objects ready for bin assignment.
        self._queue: deque[StorageUnit] = deque()
        # Count of queued units per SKU — O(1) alternative to rebuilding a set
        # from the full queue on every check_reorders call.
        self._queued_sku_counts: dict[int, int] = {}
        self._originals: dict[int, Carton] = {}
        # equilibrium_qty at initial intake per SKU (not updated on reorders).
        self._initial_quantities: dict[int, int] = {}

        # Incremental inventory count — avoids O(N_bins) scan in check_reorders.
        self._current_quantities: dict[int, int] = {}

        # Deferred reorder support (Order-Up-To with lead times).
        # _batch_num increments on each check_reorders call; deferred reorders
        # are keyed by the batch number when they are due to arrive.
        self._batch_num: int = 0
        self._deferred_reorders: dict[int, list[tuple[int, list[StorageUnit]]]] = defaultdict(list)
        self._deferred_sku_counts: dict[int, int] = {}   # in-flight deferred units per SKU

        # Bins emptied by picks, pending return to _index at next check_reorders.
        self._pending_reclaim: list[Aisle.Bin] = []

        # Tracks consecutive failed drain attempts per unit (keyed by id(unit)).
        # When a unit exceeds _MAX_DRAIN_RETRIES it is abandoned so the SKU's
        # queued count drops to zero and a fresh reorder can fire next batch.
        self._unit_drain_retries: dict[int, int] = {}

        # SKUs whose current quantity has dropped to or below the reorder threshold
        # since the last check_reorders call.  Maintained by _notify_pick so
        # check_reorders scans only depleted SKUs instead of all N_skus.
        self._depleted_skus: set[int] = set()

        # Persistent lift state shared with load-aware assignment functions.
        self._aisle_sku_sets: dict[int, set[int]]         = defaultdict(set)
        self._aisle_lift_sum: dict[int, float]             = defaultdict(float)
        self._aisle_sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        # Pre-translated matrix indices mirror of _aisle_sku_sets — eliminates
        # the O(N_aisle_members) dict lookup set-comprehension in delta_lift_idxs.
        self._aisle_idx_sets: dict[int, set[int]]         = defaultdict(set)
        # id(bin) → sku; needed for lift removal after storage is cleared.
        self._bin_sku: dict[int, int] = {}

        # Demand-based state for trip-cost assignment functions.
        # Populated by init_demand_state(); unused for strategy A.
        self._aisle_demand_sum: dict[int, float]   = defaultdict(float)
        self._sku_demand_product: dict[int, float] = {}   # sku -> f * q

        # SKU → bins split by unit type for Task.from_batch lookups.
        # Sets give O(1) add/discard; Task.from_batch sorts the bins by
        # (bayX, bayY) anyway so insertion order doesn't matter.
        self._sku_singleton_bins: dict[int, set[Aisle.Bin]] = defaultdict(set)
        self._sku_pallet_bins: dict[int, set[Aisle.Bin]]    = defaultdict(set)

        for b in warehouse.bins:
            if b.storage is None:
                self._index_add(b)
            else:
                self._unavailable[id(b)] = b

    # ── public API ──────────────────────────────────────────────────────────

    def enqueue(self, carton: Carton, quantity: int | None = None) -> 'Inventory_Manager':
        """Queue one carton for bin placement.

        quantity=None (default) reads equilibrium_qty from the carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override the carton's own stock level (e.g. overstock sampling).
        """
        qty = quantity if quantity is not None else _equilibrium_qty(carton)
        for unit in viable_storage_units(carton, qty):
            self._queue.append(unit)
        if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
            self._originals[carton.sku] = carton
            self._initial_quantities[carton.sku] = qty
        self._drain()
        return self

    def enqueue_all(self, cartons: list[Carton], quantity: int | None = None) -> 'Inventory_Manager':
        """Queue a list of cartons for bin placement.

        quantity=None (default) reads equilibrium_qty from each carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override every carton's stock level (e.g. overstock sampling).
        """
        for carton in cartons:
            qty = quantity if quantity is not None else _equilibrium_qty(carton)
            for unit in viable_storage_units(carton, qty):
                self._queue.append(unit)
            if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
                self._originals[carton.sku] = carton
                self._initial_quantities[carton.sku] = qty
        self._drain()
        return self

    def init_lift_state(self, affinity: AffinityStore) -> None:
        """Populate aisle lift state from current warehouse contents.

        Call after uniform stocking, before swapping to a load-aware
        assignment_fn.  Ensures reorder decisions see the actual aisle
        composition rather than starting from zero.  Also rebuilds
        _current_quantities so the incremental counter is consistent with
        the actual bin contents after any bulk stocking operation.
        """
        self._aisle_sku_sets.clear()
        self._aisle_lift_sum.clear()
        self._aisle_sku_counts.clear()
        self._aisle_idx_sets.clear()
        self._bin_sku.clear()
        self._current_quantities.clear()
        self._sku_singleton_bins.clear()
        self._sku_pallet_bins.clear()

        sku_to_idx = affinity._sku_to_idx

        for bin_ in self._unavailable.values():
            if bin_.storage is not None:
                sku = bin_.storage.carton.sku
                aid = bin_.location[0]
                qty = bin_.storage.quantity
                self._aisle_sku_sets[aid].add(sku)
                counts = self._aisle_sku_counts[aid]
                counts[sku] = counts.get(sku, 0) + 1
                self._bin_sku[id(bin_)] = sku
                self._current_quantities[sku] = (
                    self._current_quantities.get(sku, 0) + qty
                )
                idx = sku_to_idx.get(sku)
                if idx is not None:
                    self._aisle_idx_sets[aid].add(idx)
                if bin_.unit_type == 'singleton':
                    self._sku_singleton_bins[sku].add(bin_)
                else:
                    self._sku_pallet_bins[sku].add(bin_)

        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_lift_sum[aid] = affinity.sum_lift(list(sku_set))

    def init_demand_state(self, inventory: Any) -> None:
        """Populate demand-product lookup and per-aisle demand sums.

        Must be called after init_lift_state() so _aisle_sku_sets already
        reflects the actual placement.  Call once per strategy worker before
        swapping to a trip-cost assignment function.
        """
        self._sku_demand_product = {
            c.sku: c.demand.frequency * c.demand.quantity_rate
            for c in inventory.cartons
        }
        self._aisle_demand_sum.clear()
        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_demand_sum[aid] = sum(
                self._sku_demand_product.get(s, 0.0) for s in sku_set
            )

    # ── pick notifications (called by PickSimulation, O(1) each) ────────────

    def _notify_pick(self, sku: int, qty: int) -> None:
        """Decrement the incremental quantity counter and flag the SKU if it
        crosses its reorder_point.

        Called by PickSimulation after each pick event — must be O(1).
        Adds the SKU to _depleted_skus so check_reorders only iterates SKUs
        that actually need attention rather than all N_skus.

        reorder_point is a pre-computed attribute stored on the Carton at
        inventory generation time (frequency × quantity_rate × coverage_batches).
        No calculation is performed here — the value is simply read.
        """
        cur = self._current_quantities.get(sku, 0)
        if cur <= 0:
            return
        new_qty = max(0, cur - qty)
        self._current_quantities[sku] = new_qty
        orig = self._originals.get(sku)
        rp = getattr(orig, 'reorder_point', None) if orig is not None else None
        if rp is not None and new_qty <= rp:
            self._depleted_skus.add(sku)

    def _notify_bin_emptied(self, bin_: Aisle.Bin) -> None:
        """Queue an emptied bin for reclaim at the next check_reorders call.

        Called by PickSimulation immediately after bin_.storage is set to
        None — must be O(1).  The bin stays in _unavailable until
        _reclaim_empty_bins processes _pending_reclaim.
        """
        self._pending_reclaim.append(bin_)

    # ── reorder logic ────────────────────────────────────────────────────────

    def _reclaim_empty_bins(self) -> None:
        """Return bins in _pending_reclaim to the available index.

        With _unavailable as a dict and _pending_reclaim as a targeted list,
        this is O(pending_bins) — typically a handful per batch — instead of
        the previous O(total_bins) full scan.
        """
        if not self._pending_reclaim:
            return

        for bin_ in self._pending_reclaim:
            bin_id = id(bin_)
            sku    = self._bin_sku.pop(bin_id, None)
            if sku is not None:
                # Remove from SKU→bins index
                if bin_.unit_type == 'singleton':
                    lst = self._sku_singleton_bins.get(sku)
                else:
                    lst = self._sku_pallet_bins.get(sku)
                if lst:
                    lst.discard(bin_)
                # Update lift state
                if self._affinity is not None:
                    aid    = bin_.location[0]
                    counts = self._aisle_sku_counts[aid]
                    n      = counts.get(sku, 0)
                    if n > 1:
                        counts[sku] = n - 1
                    else:
                        counts.pop(sku, None)
                        self._aisle_sku_sets[aid].discard(sku)
                        idx = self._affinity._sku_to_idx.get(sku)
                        if idx is not None:
                            self._aisle_idx_sets[aid].discard(idx)
                        delta = 2.0 * self._affinity.delta_lift_idxs(
                            sku, self._aisle_idx_sets[aid]
                        )
                        self._aisle_lift_sum[aid] = max(
                            0.0, self._aisle_lift_sum[aid] - delta
                        )
                        self._aisle_demand_sum[aid] = max(
                            0.0,
                            self._aisle_demand_sum[aid]
                            - self._sku_demand_product.get(sku, 0.0),
                        )
            self._index_add(bin_)
            self._unavailable.pop(bin_id, None)

        self._pending_reclaim.clear()

    def check_reorders(self) -> list[int]:
        """Order-Up-To replenishment with optional per-SKU lead-time deferral.

        Each call:
          1. Increments the internal batch counter.
          2. Releases any deferred reorders whose lead time has elapsed.
          3. For each SKU flagged by _notify_pick as below its reorder_point,
             computes qty = equilibrium_qty − current_qty (OUP fill-back) and
             either schedules immediately or defers by lead_time_mean batches.

        Guard: a SKU with units already queued OR in-flight deferred is skipped
        so only one replenishment wave is ever in-flight per SKU at a time.

        Lead-time sampling: if carton.lead_time_mean > 0, samples
        max(1, round(N(mean, mean))) batches.  Zero mean = immediate placement.
        """
        self._batch_num += 1
        self._reclaim_empty_bins()

        # ── 1. Release deferred reorders that have arrived ───────────────────
        due = self._deferred_reorders.pop(self._batch_num, None)
        if due:
            for sku, units in due:
                self._deferred_sku_counts[sku] = max(
                    0, self._deferred_sku_counts.get(sku, 0) - len(units)
                )
                for unit in units:
                    self._queue.append(unit)
                self._queued_sku_counts[sku] = (
                    self._queued_sku_counts.get(sku, 0) + len(units)
                )

        # Fast exit when there is nothing to do.
        if not self._depleted_skus and not self._queue:
            return []

        # ── 2. Fire OUP reorders for depleted SKUs ───────────────────────────
        triggered: list[int] = []
        for sku in self._depleted_skus:
            in_queue    = self._queued_sku_counts.get(sku, 0)
            in_deferred = self._deferred_sku_counts.get(sku, 0)
            if in_queue == 0 and in_deferred == 0 and sku in self._originals:
                rc      = self._originals[sku].reorder()
                eq_qty  = _equilibrium_qty(rc)
                cur_qty = self._current_quantities.get(sku, 0)
                qty     = max(1, eq_qty - cur_qty)   # OUP: fill back to target
                units   = viable_storage_units(rc, qty)
                if not units:
                    continue

                lt_mean = getattr(rc, 'lead_time_mean', 0.0)
                if lt_mean > 0.0:
                    # Sample lead time; floor at 1 so deferred ≠ immediate
                    lead = max(1, round(random.gauss(lt_mean, lt_mean)))
                    self._deferred_reorders[self._batch_num + lead].append((sku, units))
                    self._deferred_sku_counts[sku] = (
                        self._deferred_sku_counts.get(sku, 0) + len(units)
                    )
                else:
                    for unit in units:
                        self._queue.append(unit)
                    self._queued_sku_counts[sku] = (
                        self._queued_sku_counts.get(sku, 0) + len(units)
                    )
                triggered.append(sku)
        self._depleted_skus.clear()

        # Always drain — retries prior-batch stragglers too.
        if self._queue:
            self._drain()
        return triggered

    @property
    def available(self) -> list[Aisle.Bin]:
        return [b for bins in self._index.values() for b in bins]

    @property
    def unavailable(self) -> list[Aisle.Bin]:
        return list(self._unavailable.values())

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    @property
    def assigned_bins(self) -> list[Aisle.Bin]:
        return list(self._unavailable.values())

    @property
    def empty_bins(self) -> list[Aisle.Bin]:
        return self.available

    def summary(self) -> None:
        total: int     = len(self.warehouse.bins)
        filled: int    = len(self._unavailable)
        singles: int   = sum(1 for b in self._unavailable.values() if b.storage is not None and b.storage.unit_category == 'singleton')
        pallets: int   = sum(1 for b in self._unavailable.values() if b.storage is not None and b.storage.unit_category == 'pallet')
        available: int = sum(len(v) for v in self._index.values())
        print(f'Total bins  : {total}')
        print(f'Filled      : {filled}  ({singles} singletons, {pallets} pallets)')
        print(f'Empty       : {available}')
        print(f'Queued      : {self.queue_depth} items pending')

    # ── index maintenance ────────────────────────────────────────────────────

    def _key(self, bin_: Aisle.Bin) -> BinKey:
        return (bin_.handling_type, bin_.storage_type, bin_.storage_size, bin_.unit_type)

    def _index_add(self, bin_: Aisle.Bin) -> None:
        lst = self._index[self._key(bin_)]
        self._bin_index_pos[id(bin_)] = len(lst)
        lst.append(bin_)

    def _index_remove(self, bin_: Aisle.Bin) -> None:
        """O(1) removal via swap-remove: move last element into the vacated slot."""
        lst = self._index[self._key(bin_)]
        pos  = self._bin_index_pos.pop(id(bin_))
        last = lst[-1]
        lst[pos] = last
        lst.pop()
        if last is not bin_:
            self._bin_index_pos[id(last)] = pos

    # ── placement ───────────────────────────────────────────────────────────

    def _candidates(self, unit: StorageUnit) -> list[Aisle.Bin]:
        """Return available bins for *unit*, scoped to the largest non-empty size tier.

        Iterates _SIZES_DESCENDING (largest → smallest) and returns the first
        tier that has at least one empty bin matching the unit's handling type,
        storage category, and unit type.  Falls back to smaller tiers automatically,
        so a unit is never stranded while compatible bins exist.

        Returning a single tier instead of all compatible bins keeps the list
        small (one index bucket, typically a few thousand bins) regardless of
        total warehouse size, avoiding the O(N_all_bins) bottleneck that caused
        multi-minute hangs during initial stocking of large warehouses.
        """
        shc       = unit.carton.storage_handle_config
        unit_type = unit.unit_category                    # 'pallet' or 'singleton'
        if unit_type == 'singleton':
            # Singleton bins are a single size bucket keyed with storage_size=None.
            bins = self._index.get((shc.handling, shc.category, None, 'singleton'))
            return list(bins) if bins else []
        min_rank  = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
        for size in _SIZES_DESCENDING:
            if _SIZE_RANKS[size] >= min_rank:
                bins = self._index.get((shc.handling, shc.category, size, unit_type))
                if bins:
                    return list(bins)   # shallow copy — _index_remove mutates the original
        return []

    def _drain(self) -> None:
        """Place queued StorageUnit objects into warehouse bins.

        Units are pre-palletized (created by viable_storage_units) before being
        enqueued, and already carry the correct quantity for their bin slot.
        Each unit is placed independently via assignment_fn — no all-or-nothing
        grouping.

        When a Pallet unit finds no compatible bin (its required size tier is
        fully occupied), _drain attempts to repack the quantity into one or more
        smaller pallets that fit in the largest available smaller size tier.
        The replacement units are prepended to the queue and retried immediately
        in the same _drain call.  If no smaller tier is available either, the
        original unit waits in the pending queue for the next check_reorders.
        """
        pending: deque[StorageUnit] = deque()
        while self._queue:
            unit   = self._queue.popleft()
            carton = unit.carton
            sku    = carton.sku

            candidates = self._candidates(unit)
            bin_       = self.assignment_fn(unit, candidates)

            if bin_ is not None:
                # Unit placed — clean up retry counter and decrement queued count.
                self._unit_drain_retries.pop(id(unit), None)
                n = self._queued_sku_counts.get(sku, 0)
                if n <= 1:
                    self._queued_sku_counts.pop(sku, None)
                else:
                    self._queued_sku_counts[sku] = n - 1

                bin_.storage = unit
                self._index_remove(bin_)
                self._unavailable[id(bin_)] = bin_
                self._bin_sku[id(bin_)] = sku
                self._current_quantities[sku] = (
                    self._current_quantities.get(sku, 0) + unit.quantity
                )
                if isinstance(unit, Singleton):
                    self._sku_singleton_bins[sku].add(bin_)
                else:
                    self._sku_pallet_bins[sku].add(bin_)
                if self._affinity is not None:
                    aid    = bin_.location[0]
                    counts = self._aisle_sku_counts[aid]
                    counts[sku] = counts.get(sku, 0) + 1
            else:
                # No bin fits this unit.  Attempt rescues in priority order:
                #   1. Repack into smaller pallet size tier (existing logic).
                #   2. Fall back to singleton bins of the same carton type.
                #   3. If all else fails, track consecutive failures; after
                #      _MAX_DRAIN_RETRIES the unit is abandoned and the queued-
                #      count is decremented so a fresh reorder can fire next batch.
                repacked = False
                shc = carton.storage_handle_config

                # ── rescue 1: smaller pallet tier ────────────────────────────
                if unit.unit_category == 'pallet' and unit.storage_size is not None:
                    current_rank = _SIZE_RANKS.get(unit.storage_size, 99)
                    for size in _SIZES_DESCENDING:
                        if _SIZE_RANKS[size] >= current_rank:
                            continue   # same or larger tier — already failed
                        avail = self._index.get(
                            (shc.handling, shc.category, size, 'pallet'))
                        if not avail:
                            continue
                        max_q = _max_qty_fitting_pallet_size(carton, size)
                        if max_q <= 0:
                            continue
                        remaining  = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_q)
                            new_units.append(Pallet(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True
                        break

                # ── rescue 2: singleton bins of same carton type ──────────────
                if not repacked:
                    max_sing = _sq_max(carton, Singleton)
                    avail = self._index.get((shc.handling, shc.category, None, 'singleton'))
                    if max_sing > 0 and avail:
                        remaining = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_sing)
                            new_units.append(Singleton(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True

                # ── rescue 3: retry limit — abandon permanently stuck units ──
                if not repacked:
                    retries = self._unit_drain_retries.get(id(unit), 0) + 1
                    if retries >= _MAX_DRAIN_RETRIES:
                        # Give up on this unit; let check_reorders fire afresh.
                        self._unit_drain_retries.pop(id(unit), None)
                        n = self._queued_sku_counts.get(sku, 0)
                        if n <= 1:
                            self._queued_sku_counts.pop(sku, None)
                        else:
                            self._queued_sku_counts[sku] = n - 1
                    else:
                        self._unit_drain_retries[id(unit)] = retries
                        pending.append(unit)
        self._queue = pending


# ── load-aware assignment functions ───────────────────────────────────────────

def _aisle_extremal_bins(
    candidates: list[Any],
    x_speed   : float,
    y_speed   : float,
    minimize  : bool,
) -> tuple[dict[int, float], dict[int, Any]]:
    """Reduce candidates to one bin per aisle — the extremal-W representative.

    Proof of correctness
    --------------------
    For a fixed aisle (fixed ls, dl), the score tuple (delta_l2, old_L) is
    strictly monotone increasing in W = x_speed*x_phys + y_speed*y_phys:
      old_L  = W + λ(W/k)^γ ls           — increasing in W
      new_L  = W + λ(W/k)^γ (ls+dl)      — increasing in W
      delta_l2 = new_L² − old_L²          — product of two positive increasing
                                             functions, so also increasing in W

    Consequence: within a fixed aisle, the minimum-W bin always yields the
    minimum score (best for minimising) and the maximum-W bin always yields the
    maximum score (best for maximising).  Reducing O(N_bins) candidates to one
    representative per aisle is exact — no approximation.
    """
    best_W  : dict[int, float] = {}
    best_bin: dict[int, Any]   = {}
    for b in candidates:
        aid = b.location[0]
        W   = x_speed * b.x_phys + y_speed * b.y_phys
        if aid not in best_W or (W < best_W[aid] if minimize else W > best_W[aid]):
            best_W[aid]   = W
            best_bin[aid] = b
    return best_W, best_bin


def build_load_minimizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
) -> AssignmentFn:
    """Build an AssignmentFn that greedily minimises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Dual-optimisation algorithm
    ---------------------------
    1. Reduce candidates to one bin per aisle (minimum-W bin) — exact by
       monotonicity of delta_l2 in W within a fixed aisle.
    2. Sort the O(N_aisles) representatives by W ascending.
    3. Evaluate aisles in W order with LAZY CSR queries (delta_lift computed
       only when the aisle is actually reached, not upfront for all aisles).
    4. Early termination: once the best score has delta_l2 = 0 (no affinity
       partners in the winning aisle), any remaining aisle with W ≥ best_old_L
       cannot improve — old_L ≥ W ≥ best_old_L and delta_l2 ≥ 0 = best_delta_l2.
       With sparse top-20 affinity most aisles have delta_lift = 0, so the
       termination typically fires after the first few aisles.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        # Step 1: one representative bin per aisle (min-W) — O(N_candidates)
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)

        # Step 2: sort aisles by ascending min-W — O(N_aisles log N_aisles)
        sorted_aids = sorted(best_W, key=best_W.__getitem__)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('inf'), float('inf'))
        best_delta_lift : float               = 0.0

        # Step 3+4: lazy CSR queries + early termination
        for aid in sorted_aids:
            W = best_W[aid]

            # Early termination: best has delta_l2=0; remaining W ≥ best old_L
            # means score ≥ (0, W) ≥ (0, best_old_L) = best — prune the rest.
            if best_score[0] == 0.0 and W >= best_score[1]:
                break

            ls = aisle_lift_sum[aid]
            # Marginal lift is zero when the SKU already lives in this aisle —
            # it's already counted in aisle_lift_sum and adding a duplicate bin
            # does not create a new unique SKU pair.
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if score < best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        # Only update lift state when this is a genuinely new SKU for the aisle.
        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign


def build_load_maximizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
) -> AssignmentFn:
    """Build an AssignmentFn that greedily maximises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Same dual-optimisation structure as the minimising variant:
    one bin per aisle (min-W) + aisles sorted by W descending (largest
    travel cost first — highest potential delta_l2) + lazy CSR queries.
    No early termination for maximising: a low-W aisle can still win if it
    has very high affinity lift, so the sorted order does not guarantee
    pruning.  The one-bin-per-aisle reduction still eliminates O(N_bins)
    evaluations, leaving O(N_aisles) CSR queries.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        # One representative bin per aisle (max-W) — exact by monotonicity.
        # For maximising, larger W → larger delta_l2, so max-W bin is optimal.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=False)

        # Sort descending: high-W aisles have the largest potential delta_l2
        sorted_aids = sorted(best_W, key=best_W.__getitem__, reverse=True)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('-inf'), float('-inf'))
        best_delta_lift : float               = 0.0

        for aid in sorted_aids:
            W  = best_W[aid]
            ls = aisle_lift_sum[aid]
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if score > best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign


# ── trip-cost assignment functions ────────────────────────────────────────────


def _demand_weighted_delta_lift(
    affinity       : AffinityStore,
    sku            : int,
    member_idx_set : set[int],
    freq_by_idx    : dict[int, float],
) -> float:
    """Sum of affinity(s, i) * f_i for all affinity partners i in the aisle.

    Uses the same CSR row-slice pattern as delta_lift_idxs but multiplies
    each affinity score by the partner's demand frequency.  This weights the
    co-location benefit by how often the partner actually appears in a batch,
    so rare-but-high-affinity pairs do not dominate over common low-affinity ones.
    """
    if not member_idx_set or affinity._matrix is None or sku not in affinity._sku_to_idx:
        return 0.0
    i     = affinity._sku_to_idx[sku]
    start = int(affinity._matrix.indptr[i])
    end   = int(affinity._matrix.indptr[i + 1])
    if start == end:
        return 0.0
    col_indices = affinity._matrix.indices[start:end]
    data        = affinity._matrix.data[start:end]
    return float(sum(
        d * freq_by_idx.get(int(ci), 0.0)
        for ci, d in zip(col_indices, data)
        if ci in member_idx_set
    ))


def build_trip_minimizing_assignment_fn(
    affinity         : AffinityStore,
    wp               : Any,
    aisle_sku_sets   : dict[int, set[int]],
    aisle_idx_sets   : dict[int, set[int]],
    aisle_demand_sum : dict[int, float],
    freq_by_idx      : dict[int, float],
    freq_by_sku      : dict[int, float],
    qty_by_sku       : dict[int, float],
    beta             : float = 1.0,
) -> AssignmentFn:
    """Build an AssignmentFn that minimises expected marginal aisle-trip cost.

    Objective
    ---------
    score(s, a) = f_s * W  -  beta * co_occur_a   (minimise)

      f_s        = demand frequency of the SKU being placed
      W          = x_speed*x_phys + y_speed*y_phys for the representative bin
      co_occur_a = sum_{i in aisle_a} affinity(s,i) * f_i  (demand-weighted lift)
      beta       = co-location subsidy weight (default 1.0)

    Tie-broken by (aisle_demand_sum + f_s*q_s): prefer less-loaded aisles to
    reduce makespan variance across pickers.

    Placement behaviour
    -------------------
    High-frequency SKUs are pulled toward low-W aisles.  SKUs whose
    high-frequency affinity partners already live in an aisle receive a
    co-location subsidy (beta * co_occur_a) that offsets the travel cost,
    collapsing correlated demand into fewer aisles per batch and reducing
    the dominant makespan driver: n_tasks (aisles visited per batch).
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Minimum-W representative per aisle: monotonicity holds for f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)

        best_score : tuple[float, float] = (float('inf'), float('inf'))
        best_bin   : Any | None = None
        best_aid   : int        = -1

        for aid, W in best_W.items():
            co_occur  = (0.0 if sku in aisle_sku_sets[aid]
                         else _demand_weighted_delta_lift(
                             affinity, sku, aisle_idx_sets[aid], freq_by_idx))
            primary   = f_s * W - beta * co_occur
            secondary = aisle_demand_sum[aid] + f_s * q_s
            score     = (primary, secondary)
            if score < best_score:
                best_score = score
                best_bin   = best_bin_map[aid]
                best_aid   = aid

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        return best_bin

    return assign


def build_trip_maximizing_assignment_fn(
    affinity         : AffinityStore,
    wp               : Any,
    aisle_sku_sets   : dict[int, set[int]],
    aisle_idx_sets   : dict[int, set[int]],
    aisle_demand_sum : dict[int, float],
    freq_by_idx      : dict[int, float],
    freq_by_sku      : dict[int, float],
    qty_by_sku       : dict[int, float],
    beta             : float = 1.0,
) -> AssignmentFn:
    """Build an AssignmentFn that maximises expected marginal aisle-trip cost.

    Mirror of build_trip_minimizing_assignment_fn.  Places high-frequency SKUs
    in distant aisles isolated from their affinity partners, maximising the
    expected number of distinct aisle visits per batch.  Intended as the
    strategy-C upper bound to contrast against B (minimising) and A (uniform).

    Tie-broken by -(aisle_demand_sum + f_s*q_s): prefer already-loaded aisles
    to concentrate demand and maximise picker load imbalance.
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Maximum-W representative per aisle: farther bins yield higher f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=False)

        best_score : tuple[float, float] = (float('-inf'), float('-inf'))
        best_bin   : Any | None = None
        best_aid   : int        = -1

        for aid, W in best_W.items():
            co_occur  = (0.0 if sku in aisle_sku_sets[aid]
                         else _demand_weighted_delta_lift(
                             affinity, sku, aisle_idx_sets[aid], freq_by_idx))
            primary   = f_s * W - beta * co_occur
            secondary = -(aisle_demand_sum[aid] + f_s * q_s)
            score     = (primary, secondary)
            if score > best_score:
                best_score = score
                best_bin   = best_bin_map[aid]
                best_aid   = aid

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        return best_bin

    return assign
