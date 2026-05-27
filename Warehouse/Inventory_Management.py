import random
from collections import defaultdict, deque
from typing import Callable

from Carton import Carton
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, Storage_Size, viable_storage_units

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]

# Maps size name → rank so we can query "this size or larger" in O(sizes) time.
_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(
        sorted(Storage_Size.available_sizes_heights, key=Storage_Size.available_sizes_heights.__getitem__)
    )
}

# (handling_type, storage_type, storage_size, unit_type) → available bins
BinKey = tuple[str, str, str, str]


def _uniform_assignment(_: StorageUnit, candidates: list[Aisle.Bin]) -> Aisle.Bin | None:
    return random.choice(candidates) if candidates else None


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
