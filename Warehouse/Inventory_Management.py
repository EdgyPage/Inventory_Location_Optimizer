import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from Carton import Carton
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, Storage_Size, viable_storage_units

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]

# (sku_i, sku_j) → lift value; symmetric, both directions stored
AffMatrix = dict[tuple[int, int], float]


@dataclass
class LoadParams:
    lambda_: float = 1.0   # startup-cost multiplier
    k: float       = 1.0   # pickers per task (normally 1 for single-aisle tasks)
    gamma: float   = 1.5   # congestion exponent

# Maps size name → rank so we can query "this size or larger" in O(sizes) time.
_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(
        sorted(Storage_Size.available_sizes_heights, key=Storage_Size.available_sizes_heights.__getitem__)
    )
}

# (handling_type, storage_type, storage_size, unit_type) → available bins
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
    ) -> None:
        self.warehouse: Warehouse = warehouse
        self.assignment_fn: AssignmentFn = assignment_fn
        self._index: dict[BinKey, list[Aisle.Bin]] = defaultdict(list)
        self._unavailable: list[Aisle.Bin] = []
        self._queue: deque[tuple[Carton, int]] = deque()
        self._initial_quantities: dict[int, int] = {}   # sku → qty on first placement
        self._originals: dict[int, Carton] = {}         # sku → original carton

        for b in warehouse.bins:
            if b.storage is None:
                self._index_add(b)
            else:
                self._unavailable.append(b)

    # ── public API ──────────────────────────────────────────────────────────

    def enqueue(self, carton: Carton, quantity: int = 1) -> 'Inventory_Manager':
        """Add one carton config to the queue and attempt to drain."""
        self._queue.append((carton, quantity))
        self._drain()
        return self

    def enqueue_all(self, cartons: list[Carton], quantity: int = 1) -> 'Inventory_Manager':
        """Add all carton configs to the queue in order, then attempt to drain."""
        for carton in cartons:
            self._queue.append((carton, quantity))
        self._drain()
        return self

    def _reclaim_empty_bins(self) -> None:
        """Return bins emptied by picking back to the available index.

        StorageCart.add_from_bin sets bin_.storage = None when a bin is fully
        picked but does not notify the manager.  This sweep keeps _unavailable
        and _index consistent before any quantity check or reorder decision.
        """
        still_filled: list[Aisle.Bin] = []
        for bin_ in self._unavailable:
            if bin_.storage is None:
                self._index_add(bin_)
            else:
                still_filled.append(bin_)
        self._unavailable = still_filled

    def check_reorders(self) -> list[int]:
        """Enqueue replenishment for any SKU whose total warehouse quantity is at
        or below 10% of its initial placement quantity.

        Empty bins are reclaimed first so that reorder stock has slots to fill
        and the quantity totals reflect only bins that are actually stocked.
        Returns the SKU IDs of triggered reorders.
        """
        self._reclaim_empty_bins()

        current: dict[int, int] = {}
        for bin_ in self._unavailable:
            sku = bin_.storage.carton.sku  # type: ignore[union-attr]
            current[sku] = current.get(sku, 0) + bin_.storage.quantity  # type: ignore[union-attr]

        queued_skus: set[int] = {carton.sku for carton, _ in self._queue}

        triggered: list[int] = []
        for sku, initial_qty in self._initial_quantities.items():
            threshold: int = max(1, round(initial_qty * self.REORDER_THRESHOLD))
            if current.get(sku, 0) <= threshold and sku not in queued_skus:
                self._queue.append((self._originals[sku].reorder(), initial_qty))
                triggered.append(sku)

        if triggered:
            self._drain()
        return triggered

    @property
    def available(self) -> list[Aisle.Bin]:
        return [b for bins in self._index.values() for b in bins]

    @property
    def unavailable(self) -> list[Aisle.Bin]:
        return list(self._unavailable)

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    @property
    def assigned_bins(self) -> list[Aisle.Bin]:
        return list(self._unavailable)

    @property
    def empty_bins(self) -> list[Aisle.Bin]:
        return self.available

    def summary(self) -> None:
        total: int = len(self.warehouse.bins)
        filled: int = len(self._unavailable)
        singles: int = sum(1 for b in self._unavailable if isinstance(b.storage, Singleton))
        pallets: int = sum(1 for b in self._unavailable if isinstance(b.storage, Pallet))
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
        """Look up compatible bins from the index, skipping speculatively-taken ones.

        Pallets query their required size and all larger sizes.
        Singletons have no height constraint and query all sizes.
        """
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
        """Speculatively assign all units without committing.
        Returns (unit, bin) pairs to commit, or None if any unit has no compatible bin."""
        units = viable_storage_units(carton, qty)
        excluded: set[int] = set()
        assigned: list[tuple[StorageUnit, Aisle.Bin]] = []
        for unit in units:
            candidates = self._candidates(unit, excluded)
            bin_ = self.assignment_fn(unit, candidates)
            if bin_ is None:
                return None
            # id(b) rather than b itself: Aisle.Bin defines no __hash__
            excluded.add(id(bin_))
            assigned.append((unit, bin_))
        return assigned

    def _drain(self) -> None:
        """One FIFO pass: place each entry if all its units fit, else keep it in position."""
        pending: deque[tuple[Carton, int]] = deque()
        while self._queue:
            carton, qty = self._queue.popleft()
            assigned = self._try_place(carton, qty)
            if assigned is not None:
                for unit, bin_ in assigned:
                    bin_.storage = unit
                    self._index_remove(bin_)
                    self._unavailable.append(bin_)
                if carton.sku not in self._initial_quantities:
                    self._initial_quantities[carton.sku] = sum(u.quantity for u, _ in assigned)
                    self._originals[carton.sku] = carton
            else:
                pending.append((carton, qty))
        self._queue = pending


# ── load-aware assignment functions ───────────────────────────────────────────

def build_load_minimizing_assignment_fn(
    params: LoadParams,
    affinity: AffMatrix,
    wp: Any,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily minimises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    For each new unit, every candidate aisle is scored by its marginal L2 cost:
        score = (L_a_after² − L_a_before², L_a_before)
    The aisle with the smallest score is chosen; ties (zero-affinity SKUs) fall
    back to current-load ordering, giving load balancing for free.

    wp must expose .x_move_time and .y_move_time (e.g. WorkloadParams).

    The closure owns mutable state updated after every successful placement.
    Pass a freshly built function to each Inventory_Manager; do not share across runs.
    """
    sku_partners: dict[int, dict[int, float]] = defaultdict(dict)
    for (i, j), v in affinity.items():
        sku_partners[i][j] = v

    aisle_sku_sets: dict[int, set[int]] = defaultdict(set)
    aisle_lift_sum: dict[int, float]    = defaultdict(float)
    aisle_W_cache:  dict[int, float]    = {}

    lam = params.lambda_
    k   = params.k
    gam = params.gamma

    def _w(aisle_obj: Any) -> float:
        aid = aisle_obj.aisle_id
        if aid not in aisle_W_cache:
            aisle_W_cache[aid] = (
                wp.x_move_time * aisle_obj.bayXPerAisle
                + wp.y_move_time * aisle_obj.bayYPerAisle
            )
        return aisle_W_cache[aid]

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku      = unit.carton.sku
        partners = sku_partners.get(sku, {})

        bins_by_aisle: dict[int, list[Any]] = defaultdict(list)
        aisle_obj_map: dict[int, Any]       = {}
        for b in candidates:
            aid = b.location[0]
            bins_by_aisle[aid].append(b)
            if aid not in aisle_obj_map:
                aisle_obj_map[aid] = b.aisle

        best_aid        = -1
        best_score: tuple[float, float] = (float('inf'), float('inf'))
        best_delta_lift = 0.0

        for aid in bins_by_aisle:
            W  = _w(aisle_obj_map[aid])
            ls = aisle_lift_sum[aid]
            # Counts ordered pairs: each undirected pair contributes 2×
            delta_lift = 2.0 * sum(
                v for si, v in partners.items() if si in aisle_sku_sets[aid]
            )
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + delta_lift)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)   # tiebreak: prefer lighter aisle
            if score < best_score:
                best_score      = score
                best_aid        = aid
                best_delta_lift = delta_lift

        if best_aid == -1:
            return None

        aisle_lift_sum[best_aid] += best_delta_lift
        aisle_sku_sets[best_aid].add(sku)
        return random.choice(bins_by_aisle[best_aid])

    return assign


def build_load_maximizing_assignment_fn(
    params: LoadParams,
    affinity: AffMatrix,
    wp: Any,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily maximises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Structural mirror of build_load_minimizing_assignment_fn — identical logic
    but selects the maximum delta_l2 aisle, concentrating high-affinity SKUs
    together. Useful as a worst-case congestion baseline.

    Tiebreak on equal delta_l2: prefers the currently heavier aisle.

    wp must expose .x_move_time and .y_move_time (e.g. WorkloadParams).
    """
    sku_partners: dict[int, dict[int, float]] = defaultdict(dict)
    for (i, j), v in affinity.items():
        sku_partners[i][j] = v

    aisle_sku_sets: dict[int, set[int]] = defaultdict(set)
    aisle_lift_sum: dict[int, float]    = defaultdict(float)
    aisle_W_cache:  dict[int, float]    = {}

    lam = params.lambda_
    k   = params.k
    gam = params.gamma

    def _w(aisle_obj: Any) -> float:
        aid = aisle_obj.aisle_id
        if aid not in aisle_W_cache:
            aisle_W_cache[aid] = (
                wp.x_move_time * aisle_obj.bayXPerAisle
                + wp.y_move_time * aisle_obj.bayYPerAisle
            )
        return aisle_W_cache[aid]

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku      = unit.carton.sku
        partners = sku_partners.get(sku, {})

        bins_by_aisle: dict[int, list[Any]] = defaultdict(list)
        aisle_obj_map: dict[int, Any]       = {}
        for b in candidates:
            aid = b.location[0]
            bins_by_aisle[aid].append(b)
            if aid not in aisle_obj_map:
                aisle_obj_map[aid] = b.aisle

        best_aid        = -1
        best_score: tuple[float, float] = (float('-inf'), float('-inf'))
        best_delta_lift = 0.0

        for aid in bins_by_aisle:
            W  = _w(aisle_obj_map[aid])
            ls = aisle_lift_sum[aid]
            delta_lift = 2.0 * sum(
                v for si, v in partners.items() if si in aisle_sku_sets[aid]
            )
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + delta_lift)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)   # tiebreak: prefer heavier aisle
            if score > best_score:
                best_score      = score
                best_aid        = aid
                best_delta_lift = delta_lift

        if best_aid == -1:
            return None

        aisle_lift_sum[best_aid] += best_delta_lift
        aisle_sku_sets[best_aid].add(sku)
        return random.choice(bins_by_aisle[best_aid])

    return assign
