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

BinKey = tuple[str, str, str, str]


def _uniform_assignment(unit: StorageUnit, candidates: list[Aisle.Bin]) -> Aisle.Bin | None:
    handling, category = unit.carton.storage_type
    unit_type = 'pallet' if isinstance(unit, Pallet) else 'singleton'
    min_rank = _SIZE_RANKS[unit.storage_size] if isinstance(unit, Pallet) and unit.storage_size else 0
    compatible = [
        b for b in candidates
        if b.handling_type == handling
        and b.storage_type == category
        and b.unit_type == unit_type
        and _SIZE_RANKS[b.storage_size] >= min_rank
    ]
    return random.choice(compatible) if compatible else None


class Inventory_Manager:
    REORDER_THRESHOLD: float = 0.10

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

        self._queue: deque[tuple[Carton, int]] = deque()
        self._initial_quantities: dict[int, int] = {}
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

    def enqueue(self, carton: Carton, quantity: int = 1) -> 'Inventory_Manager':
        self._queue.append((carton, quantity))
        self._drain()
        return self

    def enqueue_all(self, cartons: list[Carton], quantity: int = 1) -> 'Inventory_Manager':
        for carton in cartons:
            self._queue.append((carton, quantity))
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

    # ── pick notifications (called by PickSimulation, O(1) each) ────────────

    def _notify_pick(self, sku: int, qty: int) -> None:
        """Decrement the incremental quantity counter and flag the SKU if it
        crosses the reorder threshold.

        Called by PickSimulation after each pick event — must be O(1).
        Adds the SKU to _depleted_skus so check_reorders only iterates SKUs
        that actually need attention rather than all N_skus.
        """
        cur = self._current_quantities.get(sku, 0)
        if cur <= 0:
            return
        new_qty = max(0, cur - qty)
        self._current_quantities[sku] = new_qty
        initial = self._initial_quantities.get(sku, 0)
        if initial > 0:
            threshold = max(1, round(initial * self.REORDER_THRESHOLD))
            if new_qty <= threshold:
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
            self._index_add(bin_)
            self._unavailable.pop(bin_id, None)

        self._pending_reclaim.clear()

    def check_reorders(self) -> list[int]:
        """Enqueue replenishment for any SKU at or below 10% of initial quantity.

        Uses _current_quantities (maintained incrementally by _notify_pick)
        instead of scanning all bins — O(N_skus) instead of O(N_bins).
        Called once per batch loop in the run script, not inside Pick.py.
        """
        self._reclaim_empty_bins()

        if not self._depleted_skus:
            return []

        queued_skus: set[int] = {carton.sku for carton, _ in self._queue}

        triggered: list[int] = []
        for sku in self._depleted_skus:
            if sku not in queued_skus:
                initial_qty = self._initial_quantities.get(sku, 0)
                if initial_qty > 0:
                    # Always reorder 1 unit — _drain overrides unit.quantity
                    # to carton.stock_qty, so the bin is restocked to the
                    # correct level without needing multiple bins.
                    self._queue.append((self._originals[sku].reorder(), 1))
                    triggered.append(sku)
        self._depleted_skus.clear()

        if triggered:
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

    def _candidates(self, unit: StorageUnit, excluded: set[int]) -> list[Aisle.Bin]:
        handling, category = unit.carton.storage_type
        unit_type = 'pallet' if isinstance(unit, Pallet) else 'singleton'
        result: list[Aisle.Bin] = []
        min_rank = _SIZE_RANKS[unit.storage_size] if isinstance(unit, Pallet) and unit.storage_size else 0
        for size, rank in _SIZE_RANKS.items():
            if rank >= min_rank:
                result.extend(
                    b for b in self._index.get((handling, category, size, unit_type), [])
                    if id(b) not in excluded
                )
        return result

    def _try_place(self, carton: Carton, qty: int) -> list[tuple[StorageUnit, Aisle.Bin]] | None:
        units = viable_storage_units(carton, qty)
        excluded: set[int] = set()
        assigned: list[tuple[StorageUnit, Aisle.Bin]] = []
        for unit in units:
            candidates = self._candidates(unit, excluded)
            bin_ = self.assignment_fn(unit, candidates)
            if bin_ is None:
                return None
            excluded.add(id(bin_))
            assigned.append((unit, bin_))
        return assigned

    def _drain(self) -> None:
        pending: deque[tuple[Carton, int]] = deque()
        while self._queue:
            carton, qty = self._queue.popleft()
            assigned = self._try_place(carton, qty)
            if assigned is not None:
                # If the carton carries a stock_qty, override each unit's quantity
                # so the bin starts with more units without needing additional bins.
                # One bin per SKU is preserved; reorders fire far less frequently.
                stock_qty = getattr(carton, 'stock_qty', None)
                for unit, bin_ in assigned:
                    if stock_qty is not None:
                        unit.quantity = stock_qty
                    bin_.storage = unit
                    self._index_remove(bin_)
                    self._unavailable[id(bin_)] = bin_
                    sku = unit.carton.sku
                    self._bin_sku[id(bin_)] = sku
                    # Incremental quantity counter (avoids O(N_bins) scan in check_reorders)
                    self._current_quantities[sku] = (
                        self._current_quantities.get(sku, 0) + unit.quantity
                    )
                    # SKU→bins index split by type (used by Task.from_batch)
                    if isinstance(unit, Singleton):
                        self._sku_singleton_bins[sku].append(bin_)
                    else:
                        self._sku_pallet_bins[sku].append(bin_)
                    if self._affinity is not None:
                        aid    = bin_.location[0]
                        counts = self._aisle_sku_counts[aid]
                        counts[sku] = counts.get(sku, 0) + 1
                if carton.sku not in self._initial_quantities:
                    self._initial_quantities[carton.sku] = sum(u.quantity for u, _ in assigned)
                    self._originals[carton.sku] = carton
            else:
                pending.append((carton, qty))
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

            ls         = aisle_lift_sum[aid]
            dl         = 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid])
            old_L      = _L(W, ls)
            new_L      = _L(W, ls + dl)
            delta_l2   = new_L * new_L - old_L * old_L
            score      = (delta_l2, old_L)

            if score < best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

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
            W          = best_W[aid]
            ls         = aisle_lift_sum[aid]
            dl         = 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid])
            old_L      = _L(W, ls)
            new_L      = _L(W, ls + dl)
            delta_l2   = new_L * new_L - old_L * old_L
            score      = (delta_l2, old_L)

            if score > best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        aisle_lift_sum[best_aid] += best_delta_lift
        aisle_sku_sets[best_aid].add(sku)
        idx = affinity._sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign
