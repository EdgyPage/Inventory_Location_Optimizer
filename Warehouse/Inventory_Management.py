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

        # Persistent lift state shared with load-aware assignment functions.
        self._aisle_sku_sets: dict[int, set[int]]         = defaultdict(set)
        self._aisle_lift_sum: dict[int, float]             = defaultdict(float)
        self._aisle_sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        # id(bin) → sku; needed for lift removal after storage is cleared.
        self._bin_sku: dict[int, int] = {}

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
        self._bin_sku.clear()
        self._current_quantities.clear()

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

        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_lift_sum[aid] = affinity.sum_lift(list(sku_set))

    # ── pick notifications (called by PickSimulation, O(1) each) ────────────

    def _notify_pick(self, sku: int, qty: int) -> None:
        """Decrement the incremental quantity counter by the picked amount.

        Called by PickSimulation after each pick event — must be O(1).
        Does NOT reclaim the bin or check reorder thresholds; those happen
        in bulk at check_reorders() between batches.
        """
        cur = self._current_quantities.get(sku, 0)
        if cur > 0:
            self._current_quantities[sku] = max(0, cur - qty)

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
            if sku is not None and self._affinity is not None:
                aid    = bin_.location[0]
                counts = self._aisle_sku_counts[aid]
                n      = counts.get(sku, 0)
                if n > 1:
                    counts[sku] = n - 1
                else:
                    counts.pop(sku, None)
                    self._aisle_sku_sets[aid].discard(sku)
                    delta = 2.0 * self._affinity.delta_lift(
                        sku, list(self._aisle_sku_sets[aid])
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

        queued_skus: set[int] = {carton.sku for carton, _ in self._queue}

        triggered: list[int] = []
        for sku, initial_qty in self._initial_quantities.items():
            threshold: int = max(1, round(initial_qty * self.REORDER_THRESHOLD))
            if (self._current_quantities.get(sku, 0) <= threshold
                    and sku not in queued_skus):
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
                for unit, bin_ in assigned:
                    bin_.storage = unit
                    self._index_remove(bin_)
                    self._unavailable[id(bin_)] = bin_
                    self._bin_sku[id(bin_)] = unit.carton.sku
                    # Maintain incremental quantity counter
                    sku = unit.carton.sku
                    self._current_quantities[sku] = (
                        self._current_quantities.get(sku, 0) + unit.quantity
                    )
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

def build_load_minimizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
) -> AssignmentFn:
    """Build an AssignmentFn that greedily minimises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    aisle_sku_sets and aisle_lift_sum are the Inventory_Manager's persistent
    dicts (_aisle_sku_sets, _aisle_lift_sum).  The function reads and updates
    them on every placement so that subsequent placements in the same drain
    cycle score against the current aisle composition.
    """
    lam = params.lambda_
    k   = params.k
    gam = params.gamma

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        aisle_ids: set[int] = {b.location[0] for b in candidates}
        delta_lift_by_aisle: dict[int, float] = {
            aid: 2.0 * affinity.delta_lift(sku, list(aisle_sku_sets[aid]))
            for aid in aisle_ids
        }

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('inf'), float('inf'))
        best_delta_lift : float               = 0.0

        for b in candidates:
            aid        = b.location[0]
            W          = wp.x_move_time * b.bayX + wp.y_move_time * b.bayY
            ls         = aisle_lift_sum[aid]
            delta_lift = delta_lift_by_aisle[aid]
            old_L      = _L(W, ls)
            new_L      = _L(W, ls + delta_lift)
            delta_l2   = new_L * new_L - old_L * old_L
            score      = (delta_l2, old_L)
            if score < best_score:
                best_score      = score
                best_bin        = b
                best_aid        = aid
                best_delta_lift = delta_lift

        if best_bin is None:
            return None

        aisle_lift_sum[best_aid] += best_delta_lift
        aisle_sku_sets[best_aid].add(sku)
        return best_bin

    return assign


def build_load_maximizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
) -> AssignmentFn:
    """Build an AssignmentFn that greedily maximises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Structural mirror of build_load_minimizing_assignment_fn.
    """
    lam = params.lambda_
    k   = params.k
    gam = params.gamma

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku

        aisle_ids: set[int] = {b.location[0] for b in candidates}
        delta_lift_by_aisle: dict[int, float] = {
            aid: 2.0 * affinity.delta_lift(sku, list(aisle_sku_sets[aid]))
            for aid in aisle_ids
        }

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('-inf'), float('-inf'))
        best_delta_lift : float               = 0.0

        for b in candidates:
            aid        = b.location[0]
            W          = wp.x_move_time * b.bayX + wp.y_move_time * b.bayY
            ls         = aisle_lift_sum[aid]
            delta_lift = delta_lift_by_aisle[aid]
            old_L      = _L(W, ls)
            new_L      = _L(W, ls + delta_lift)
            delta_l2   = new_L * new_L - old_L * old_L
            score      = (delta_l2, old_L)
            if score > best_score:
                best_score      = score
                best_bin        = b
                best_aid        = aid
                best_delta_lift = delta_lift

        if best_bin is None:
            return None

        aisle_lift_sum[best_aid] += best_delta_lift
        aisle_sku_sets[best_aid].add(sku)
        return best_bin

    return assign
