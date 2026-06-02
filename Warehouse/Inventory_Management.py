import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from Carton import Carton
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, Storage_Size, viable_storage_units
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

        # Keyed by id(bin) for O(1) removal when bins are reclaimed.
        self._unavailable: dict[int, Aisle.Bin] = {}

        # Queue holds pre-palletized StorageUnit objects ready for bin assignment.
        self._queue: deque[StorageUnit] = deque()
        self._originals: dict[int, Carton] = {}

        # Incremental inventory count — avoids O(N_bins) scan in check_reorders.
        self._current_quantities: dict[int, int] = {}

        # Bins emptied by picks, pending return to _index at next check_reorders.
        self._pending_reclaim: list[Aisle.Bin] = []

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

        # SKU → bins split by unit type for O(1) Task.from_batch lookups.
        # Singletons are kept separate so forward-pick locations are always
        # iterated first without sorting on every batch.
        self._sku_singleton_bins: dict[int, list[Aisle.Bin]] = defaultdict(list)
        self._sku_pallet_bins: dict[int, list[Aisle.Bin]]    = defaultdict(list)

        for b in warehouse.bins:
            if b.storage is None:
                self._index_add(b)
            else:
                self._unavailable[id(b)] = b

    # ── public API ──────────────────────────────────────────────────────────

    def enqueue(self, carton: Carton, quantity: int | None = None) -> 'Inventory_Manager':
        """Queue one carton for bin placement.

        quantity=None (default) reads stock_qty from the carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override the carton's own stock level (e.g. overstock sampling).
        """
        qty = quantity if quantity is not None else getattr(carton, 'stock_qty', 1)
        for unit in viable_storage_units(carton, qty):
            self._queue.append(unit)
        if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
            self._originals[carton.sku] = carton
        self._drain()
        return self

    def enqueue_all(self, cartons: list[Carton], quantity: int | None = None) -> 'Inventory_Manager':
        """Queue a list of cartons for bin placement.

        quantity=None (default) reads stock_qty from each carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override every carton's stock level (e.g. overstock sampling).
        """
        for carton in cartons:
            qty = quantity if quantity is not None else getattr(carton, 'stock_qty', 1)
            for unit in viable_storage_units(carton, qty):
                self._queue.append(unit)
            if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
                self._originals[carton.sku] = carton
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
                    self._sku_singleton_bins[sku].append(bin_)
                else:
                    self._sku_pallet_bins[sku].append(bin_)

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
        if orig is not None and new_qty <= orig.reorder_point:
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
                    try:
                        lst.remove(bin_)
                    except ValueError:
                        pass
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
        """Enqueue replenishment for any SKU whose quantity <= its reorder_point.

        reorder_point is a per-SKU threshold stored on the Carton at inventory
        generation time (expected_batch_demand × coverage_batches).  No threshold
        computation happens here — _notify_pick already flagged the SKU.

        Queue semantics (FIFO, persistent across batches):
          - Items that cannot be placed remain in the queue in arrival order.
          - A SKU already in the queue is never re-enqueued — the existing
            entry will be placed as soon as a compatible bin becomes free.
          - _drain() is called whenever the queue is non-empty (not only when
            new reorders are triggered) so previously-unplaceable items are
            retried every batch as reclaimed bins return to the index.
        """
        self._reclaim_empty_bins()

        # Fast exit only when there is genuinely nothing to do.
        if not self._depleted_skus and not self._queue:
            return []

        queued_skus: set[int] = {unit.carton.sku for unit in self._queue}

        triggered: list[int] = []
        for sku in self._depleted_skus:
            if sku not in queued_skus and sku in self._originals:
                rc       = self._originals[sku].reorder()
                qty      = getattr(rc, 'stock_qty', 1)
                for unit in viable_storage_units(rc, qty):
                    self._queue.append(unit)
                triggered.append(sku)
        self._depleted_skus.clear()

        # Always drain — retries pending items from prior batches as well as
        # newly triggered reorders.  Items that still can't be placed stay in
        # the queue for the next call.
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
        singles: int   = sum(1 for b in self._unavailable.values() if isinstance(b.storage, Singleton))
        pallets: int   = sum(1 for b in self._unavailable.values() if isinstance(b.storage, Pallet))
        available: int = sum(len(v) for v in self._index.values())
        print(f'Total bins  : {total}')
        print(f'Filled      : {filled}  ({singles} singletons, {pallets} pallets)')
        print(f'Empty       : {available}')
        print(f'Queued      : {self.queue_depth} items pending')

    # ── index maintenance ────────────────────────────────────────────────────

    def _key(self, bin_: Aisle.Bin) -> BinKey:
        return (bin_.handling_type, bin_.storage_type, bin_.storage_size, bin_.unit_type)

    def _index_add(self, bin_: Aisle.Bin) -> None:
        self._index[self._key(bin_)].append(bin_)

    def _index_remove(self, bin_: Aisle.Bin) -> None:
        self._index[self._key(bin_)].remove(bin_)

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
        handling, category = unit.carton.storage_type
        unit_type = 'pallet' if isinstance(unit, Pallet) else 'singleton'
        min_rank  = _SIZE_RANKS[unit.storage_size] if isinstance(unit, Pallet) and unit.storage_size else 0
        for size in _SIZES_DESCENDING:
            if _SIZE_RANKS[size] >= min_rank:
                bins = self._index.get((handling, category, size, unit_type))
                if bins:
                    return bins
        return []

    def _drain(self) -> None:
        """Place queued StorageUnit objects into warehouse bins.

        Units are pre-palletized (created by viable_storage_units) before being
        enqueued, and already carry the correct quantity for their bin slot.
        Each unit is placed independently via assignment_fn — no all-or-nothing
        grouping.  Units that find no compatible bin remain in the queue and are
        retried on the next check_reorders call.
        """
        pending: deque[StorageUnit] = deque()
        while self._queue:
            unit   = self._queue.popleft()
            carton = unit.carton
            sku    = carton.sku

            candidates = self._candidates(unit)
            bin_       = self.assignment_fn(unit, candidates)

            if bin_ is not None:

                bin_.storage = unit
                self._index_remove(bin_)
                self._unavailable[id(bin_)] = bin_
                self._bin_sku[id(bin_)] = sku
                self._current_quantities[sku] = (
                    self._current_quantities.get(sku, 0) + unit.quantity
                )
                if isinstance(unit, Singleton):
                    self._sku_singleton_bins[sku].append(bin_)
                else:
                    self._sku_pallet_bins[sku].append(bin_)
                if self._affinity is not None:
                    aid    = bin_.location[0]
                    counts = self._aisle_sku_counts[aid]
                    counts[sku] = counts.get(sku, 0) + 1
            else:
                pending.append(unit)
        self._queue = pending


# ── load-aware assignment functions ───────────────────────────────────────────

def _aisle_extremal_bins(
    candidates: list[Any],
    x_time    : float,
    y_time    : float,
    minimize  : bool,
) -> tuple[dict[int, float], dict[int, Any]]:
    """Reduce candidates to one bin per aisle — the extremal-W representative.

    Proof of correctness
    --------------------
    For a fixed aisle (fixed ls, dl), the score tuple (delta_l2, old_L) is
    strictly monotone increasing in W = x_time*bayX + y_time*bayY:
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
        W   = x_time * b.bayX + y_time * b.bayY
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
    x_time = wp.x_move_time
    y_time = wp.y_move_time

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        # Step 1: one representative bin per aisle (min-W) — O(N_candidates)
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_time, y_time, minimize=True)

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
    x_time = wp.x_move_time
    y_time = wp.y_move_time

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        # One representative bin per aisle (max-W) — exact by monotonicity.
        # For maximising, larger W → larger delta_l2, so max-W bin is optimal.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_time, y_time, minimize=False)

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
      W          = x_time*bayX + y_time*bayY for the representative bin
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
    x_time = wp.x_move_time
    y_time = wp.y_move_time

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Minimum-W representative per aisle: monotonicity holds for f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_time, y_time, minimize=True)

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
    x_time = wp.x_move_time
    y_time = wp.y_move_time

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Maximum-W representative per aisle: farther bins yield higher f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_time, y_time, minimize=False)

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
