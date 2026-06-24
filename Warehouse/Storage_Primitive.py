from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import TYPE_CHECKING, TypeVar

from Order import Order

if TYPE_CHECKING:
    from Aisle_Storage import Aisle

T = TypeVar('T', bound='StorageUnit')


class Storage_Size:
    available_sizes_heights: dict[str, int] = {
        'small': 12,
        'medium': 24,
        'large': 36,
        'extra_large': 48
    }

    def __init__(self) -> None:
        self.max_length: int = 48
        self.max_width: int = 48
        self.max_height: int = self.available_sizes_heights["extra_large"]


class Storage_Type:
    def __init__(self) -> None:
        self.handling_storage_types: list[str] = ['conveyable', 'non-conveyable']
        self.category_storage_types: list[str] = ['food', 'clothing', 'electronic',
                                                   'furniture', 'seasonal', 'chemical']
        self.unit_storage_types: list[str] = ['pallet', 'singleton']
        self.available_storage_types: list[tuple[str, str, str]] = list(
            itertools.product(self.handling_storage_types, self.category_storage_types, self.unit_storage_types)
        )


# ── compacting cache ─────────────────────────────────────────────────────────────
# A unit's fit (orientation, size tier, stacked dims) is a pure function of its order
# DIMENSIONS, the quantity stacked, and the footprint (max_width/length) — never of the
# SKU id.  The search is an itertools.permutations × stack-axis scan run on every pallet/
# singleton construction, so memoising it by geometry collapses the per-reorder cost: a SKU
# reordered every batch (or any two SKUs sharing a geometry) pays the scan once per process.
# Only ints/strings cross process boundaries (the cache lives per-process), so this stays
# pickling-safe.  Callers regress to a fresh computation transparently on a cache miss.
#
# BOUNDED: maxsize caps per-process memory.  At full scale a worker sees hundreds of
# thousands of distinct (geometry, qty) keys over a 100-batch run (reorder quantities vary
# with supply_cv + capacity-reloader churn), and an unbounded cache grew to hundreds of MB
# per worker — ×20 workers that OOM'd the pool (BrokenProcessPool).  The hot set within a
# batch is far smaller than this bound, so the LRU keeps hit rate high while capping memory.
_FIT_CACHE_MAX = 100_000


@lru_cache(maxsize=_FIT_CACHE_MAX)
def _pallet_fit_dims(h: int, w: int, l: int, quantity: int,
                     max_width: int, max_length: int):
    """Smallest-tier pallet fit for a geometry, or None if no orientation fits.
    Returns (storage_size, height, width, length, stack_axis)."""
    dims = [h, w, l]
    sorted_sizes = sorted(Storage_Size.available_sizes_heights.items(), key=lambda x: x[1])
    best = None
    for hh, ww, ll in itertools.permutations(dims):
        for stack_h, stack_w, stack_l in [(quantity, 1, 1), (1, quantity, 1), (1, 1, quantity)]:
            if ww * stack_w <= max_width and ll * stack_l <= max_length:
                stacked_height = hh * stack_h
                for size_name, size_height in sorted_sizes:
                    if stacked_height <= size_height:
                        if best is None or size_height < best[0]:
                            axis = ('height', 'width', 'length')[[stack_h, stack_w, stack_l].index(quantity)]
                            best = (size_height, size_name, hh, ww, ll, axis)
                        break
    return best[1:] if best is not None else None


@lru_cache(maxsize=_FIT_CACHE_MAX)
def _singleton_fit_dims(h: int, w: int, l: int, quantity: int,
                        max_width: int, max_length: int, bin_h: int):
    """Min-stacked-height singleton fit for a geometry, or None if no orientation fits.
    Returns (height, width, length, stack_axis)."""
    dims = [h, w, l]
    best = None   # (stacked_h, h, w, l, axis)
    for hh, ww, ll in itertools.permutations(dims):
        for stack_h, stack_w, stack_l in [(quantity, 1, 1), (1, quantity, 1), (1, 1, quantity)]:
            if ww * stack_w <= max_width and ll * stack_l <= max_length:
                stacked_height = hh * stack_h
                if stacked_height <= bin_h:
                    if best is None or stacked_height < best[0]:
                        axis = ('height', 'width', 'length')[[stack_h, stack_w, stack_l].index(quantity)]
                        best = (stacked_height, hh, ww, ll, axis)
    return best[1:] if best is not None else None


class StorageUnit(ABC):
    def __init__(self, order: Order, quantity: int) -> None:
        self._height: int | None = None
        self._width: int | None = None
        self._length: int | None = None
        self._stack_axis: str | None = None
        self.order: Order = order
        self.quantity: int = quantity
        self._fit(order)

    @abstractmethod
    def _fit(self, order: Order) -> None:
        pass

    @property
    def total_labor_cost(self) -> float:
        """Labor to pick this whole unit = quantity x the order's per-unit labor_cost.
        Derived from the order's precomputed labor_cost (set once per worker)."""
        return self.quantity * self.order.labor_cost

    @property
    def height(self) -> int:
        return self._height  # type: ignore[return-value]

    @property
    def width(self) -> int:
        return self._width  # type: ignore[return-value]

    @property
    def length(self) -> int:
        return self._length  # type: ignore[return-value]

    @property
    def stack_axis(self) -> str:
        return self._stack_axis  # type: ignore[return-value]


class Pallet(StorageUnit):
    """Standard pallet storage — full 48×48 footprint.

    Subclasses (e.g. Singleton) may override max_width / max_length to model
    narrower footprints while inheriting the same size-tier logic and gaining
    a valid storage_size attribute from _fit().
    """
    max_length:    int = 48
    max_width:     int = 48
    unit_category: str = 'pallet'

    def __init__(self, order: Order, quantity: int) -> None:
        self.storage_size: str | None = None
        super().__init__(order, quantity)

    def _fit(self, order: Order) -> None:
        res = _pallet_fit_dims(order.height, order.width, order.length,
                               self.quantity, self.max_width, self.max_length)
        if res is None:
            raise ValueError(
                f"No valid orientation for order SKU {order.sku} with dimensions "
                f"({order.height}, {order.width}, {order.length}) x{self.quantity} within pallet limits"
            )
        self.storage_size, self._height, self._width, self._length, self._stack_axis = res


class Singleton(Pallet):
    """Forward-pick / small-footprint storage.

    Singleton bins are a single fixed size (no small/medium/large/extra_large
    tiers).  Any item whose two smallest dimensions both fit within 16×16 can
    be placed; items are rotated to minimise the stacking height.  storage_size
    is always 'singleton' — one bucket per (handling, category), distinct from
    all pallet size tiers and never None so DB NOT NULL constraints are satisfied.
    """
    max_width:     int = 16
    max_length:    int = 16
    unit_category: str = 'singleton'

    def _fit(self, order: Order) -> None:
        """Validate fit in 16×16 footprint; set dimensions without size-tier logic."""
        _BIN_H = 48  # SINGLETON_BIN_HEIGHT — avoids circular import from Aisle_Dimensions
        res = _singleton_fit_dims(order.height, order.width, order.length,
                                  self.quantity, self.max_width, self.max_length, _BIN_H)
        if res is None:
            raise ValueError(
                f"No valid orientation for order SKU {order.sku} with dimensions "
                f"({order.height}, {order.width}, {order.length}) x{self.quantity} "
                f"within singleton limits {self.max_width}×{self.max_length}×{_BIN_H}"
            )
        self._height, self._width, self._length, self._stack_axis = res
        self.storage_size = 'singleton'  # fixed label — no size tier, never None


def _can_fit(order: Order, unit_class: type[T], qty: int) -> bool:
    try:
        unit_class(order, qty)
        return True
    except ValueError:
        return False


def _max_qty_fits(order: Order, unit_class: type[T]) -> int:
    if not _can_fit(order, unit_class, 1):
        return 0
    lo: int = 1
    # upper bound: tallest storage tier (48) divided by the minimum order dimension (3)
    hi: int = max(Storage_Size.available_sizes_heights.values()) // 3
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _can_fit(order, unit_class, mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _build_units(order: Order, unit_class: type[T], quantity: int) -> list[T]:
    max_qty: int = _max_qty_fits(order, unit_class)
    if max_qty == 0:
        return []
    units: list[T] = []
    remaining: int = quantity
    while remaining > 0:
        n: int = min(remaining, max_qty)
        units.append(unit_class(order, n))
        remaining -= n
    return units


def _total_volume(units: list[StorageUnit]) -> int:
    return sum(u.height * u.width * u.length for u in units)


def viable_storage_units(order: Order, quantity: int) -> list[StorageUnit]:
    """Pack *quantity* items using the minimum number of full pallets, with any
    remainder routed to a singleton unit.

    Prefers Singleton over Pallet when the total item volume fits within a
    'small' singleton slot (12 × 16 × 16 physical units), since singletons use
    a narrower footprint that leaves full-size pallet locations free.

    Otherwise, packs complete pallets at maximum capacity then routes the
    remainder to a singleton.  Falls back to a partial pallet for the remainder
    if the order cannot be singletonised.

    Examples  (max_per_pallet = 3, small_slot_vol = 3072)
    -------------------------------------------------------
      volume*qty ≤ 3072  →  1 singleton × qty items  (forward-pick preferred)
      quantity = 9       →  3 pallets × 3 items,  no singleton
      quantity = 10      →  3 pallets × 3 items,  1 singleton × 1 item
      quantity = 2       →  0 pallets,             1 singleton × 2 items
    """
    # If the order carries an explicit stock_plan (assigned at warehouse-planning
    # time to fill diverse bin tiers), reproduce it so every reorder rebuilds the
    # same tier mix.  The plan is a list of run-length slots
    # (is_singleton, qty_per_unit, count) summing to equilibrium_qty.  For a
    # partial reorder (qty < plan total) the leading slots/runs are filled until
    # qty is exhausted; any excess beyond the plan falls through to default packing.
    plan = getattr(order, 'stock_plan', None)
    if plan:
        units: list[StorageUnit] = []
        remaining = quantity
        for is_single, per, count in plan:
            for _ in range(count):
                if remaining <= 0:
                    break
                take = min(per, remaining)
                units.append(Singleton(order, take) if is_single else Pallet(order, take))
                remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            # qty exceeds the plan's total — pack the surplus with default logic.
            units.extend(_build_units(order, Pallet, remaining)
                         or _build_units(order, Singleton, remaining))
        return units

    # Prefer singleton for small total volumes (fits in a 'small' singleton slot).
    small_slot_vol = (Storage_Size.available_sizes_heights['small']
                      * Singleton.max_width * Singleton.max_length)  # 12*16*16 = 3072
    if order.volume() * quantity <= small_slot_vol:
        singletons = _build_units(order, Singleton, quantity)
        if singletons:
            return singletons

    max_pallet: int = _max_qty_fits(order, Pallet)

    if max_pallet == 0:
        # Order geometry doesn't allow any pallet orientation; use singletons.
        return _build_units(order, Singleton, quantity)

    if quantity < max_pallet:
        # Quantity is smaller than one full pallet — singleton is the right fit.
        # Fall back to a partial pallet only if the order can't be singletonised.
        singletons = _build_units(order, Singleton, quantity)
        return singletons if singletons else [Pallet(order, quantity)]

    # Pack as many complete pallets as possible.
    n_full    = quantity // max_pallet
    remainder = quantity % max_pallet
    units: list[StorageUnit] = [Pallet(order, max_pallet) for _ in range(n_full)]

    if remainder > 0:
        singletons = _build_units(order, Singleton, remainder)
        if singletons:
            units.extend(singletons)
        else:
            # Order cannot be singletonised; carry the remainder on an extra
            # partial pallet rather than silently discarding those items.
            units.append(Pallet(order, remainder))

    return units


class StorageCart:
    max_length: int = 50
    max_width: int = 50
    max_height: int = 50

    def __init__(self) -> None:
        self._remaining_volume: int = self.max_length * self.max_width * self.max_height
        self._contents: list[tuple[Order, int]] = []

    @property
    def total_volume(self) -> int:
        return self.max_length * self.max_width * self.max_height

    @property
    def remaining_volume(self) -> int:
        return self._remaining_volume

    @property
    def contents(self) -> list[tuple[Order, int]]:
        return list(self._contents)

    def add_from_bin(self, bin_: Aisle.Bin, quantity: int) -> int:
        """Take up to `quantity` units of the bin's order into the cart.

        Assumes perfect packing — placement succeeds if order volume fits in
        remaining cart volume, regardless of orientation.
        Returns the number of units actually taken.
        Clears bin_.storage when its quantity reaches zero.
        """
        if bin_.storage is None or quantity <= 0:
            return 0
        unit = bin_.storage
        carton_vol = unit.order.volume()
        if carton_vol > self._remaining_volume:
            return 0
        max_fits = self._remaining_volume // carton_vol
        actual = min(quantity, unit.quantity, max_fits)
        if actual <= 0:
            return 0
        self._remaining_volume -= actual * carton_vol
        self._contents.append((unit.order, actual))
        # mutates unit in-place; Inventory_Manager only learns the bin is empty
        # when _reclaim_empty_bins() is called, not at pick time
        unit.quantity -= actual
        if unit.quantity == 0:
            bin_.storage = None
        return actual
