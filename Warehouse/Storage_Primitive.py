from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, TypeVar

from Carton import Carton

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


class StorageUnit(ABC):
    def __init__(self, carton: Carton, quantity: int) -> None:
        self._height: int | None = None
        self._width: int | None = None
        self._length: int | None = None
        self._stack_axis: str | None = None
        self.carton: Carton = carton
        self.quantity: int = quantity
        self._fit(carton)

    @abstractmethod
    def _fit(self, carton: Carton) -> None:
        pass

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


class Singleton(StorageUnit):
    max_height: int = 48
    max_width: int = 16
    max_length: int = 16

    def _fit(self, carton: Carton) -> None:
        dims: list[int] = [carton.height, carton.width, carton.length]
        for h, w, l in itertools.permutations(dims):
            for stack_h, stack_w, stack_l in [(self.quantity, 1, 1), (1, self.quantity, 1), (1, 1, self.quantity)]:
                if (h * stack_h <= self.max_height and
                        w * stack_w <= self.max_width and
                        l * stack_l <= self.max_length):
                    self._height = h
                    self._width = w
                    self._length = l
                    self._stack_axis = ('height', 'width', 'length')[[stack_h, stack_w, stack_l].index(self.quantity)]
                    return
        raise ValueError(
            f"No valid orientation for carton SKU {carton.sku} with dimensions "
            f"({carton.height}, {carton.width}, {carton.length}) x{self.quantity} within limits "
            f"({self.max_height}, {self.max_width}, {self.max_length})"
        )


class Pallet(StorageUnit):
    max_length: int = 48
    max_width: int = 48

    def __init__(self, carton: Carton, quantity: int) -> None:
        self.storage_size: str | None = None
        super().__init__(carton, quantity)

    def _fit(self, carton: Carton) -> None:
        dims: list[int] = [carton.height, carton.width, carton.length]
        sorted_sizes: list[tuple[str, int]] = sorted(
            Storage_Size.available_sizes_heights.items(), key=lambda x: x[1]
        )

        best: tuple[int, str, int, int, int, str] | None = None
        for h, w, l in itertools.permutations(dims):
            for stack_h, stack_w, stack_l in [(self.quantity, 1, 1), (1, self.quantity, 1), (1, 1, self.quantity)]:
                if w * stack_w <= self.max_width and l * stack_l <= self.max_length:
                    stacked_height: int = h * stack_h
                    for size_name, size_height in sorted_sizes:
                        if stacked_height <= size_height:
                            if best is None or size_height < best[0]:
                                axis: str = ('height', 'width', 'length')[[stack_h, stack_w, stack_l].index(self.quantity)]
                                best = (size_height, size_name, h, w, l, axis)
                            break

        if best is None:
            raise ValueError(
                f"No valid orientation for carton SKU {carton.sku} with dimensions "
                f"({carton.height}, {carton.width}, {carton.length}) x{self.quantity} within pallet limits"
            )

        _, self.storage_size, self._height, self._width, self._length, self._stack_axis = best


def _can_fit(carton: Carton, unit_class: type[T], qty: int) -> bool:
    try:
        unit_class(carton, qty)
        return True
    except ValueError:
        return False


def _max_qty_fits(carton: Carton, unit_class: type[T]) -> int:
    if not _can_fit(carton, unit_class, 1):
        return 0
    lo: int = 1
    # upper bound: tallest storage tier (48) divided by the minimum carton dimension (3)
    hi: int = max(Storage_Size.available_sizes_heights.values()) // 3
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _can_fit(carton, unit_class, mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _build_units(carton: Carton, unit_class: type[T], quantity: int) -> list[T]:
    max_qty: int = _max_qty_fits(carton, unit_class)
    if max_qty == 0:
        return []
    units: list[T] = []
    remaining: int = quantity
    while remaining > 0:
        n: int = min(remaining, max_qty)
        units.append(unit_class(carton, n))
        remaining -= n
    return units


def _total_volume(units: list[StorageUnit]) -> int:
    return sum(u.height * u.width * u.length for u in units)


def viable_storage_units(carton: Carton, quantity: int) -> list[StorageUnit]:
    """Pack *quantity* items using the minimum number of full pallets, with any
    remainder routed to a singleton unit.

    Each pallet is filled to its maximum physical capacity so the fewest pallet
    locations are occupied.  Items that don't fill a complete pallet — or whose
    total quantity is already smaller than one pallet's capacity — go into a
    singleton unit instead.  Singletons handle the "less than a pallet" case;
    they are not used for bulk storage unless the carton cannot be palletized at
    all.

    Examples  (max_per_pallet = 3)
    --------------------------------
      quantity = 9  →  3 pallets × 3 items,  no singleton
      quantity = 10 →  3 pallets × 3 items,  1 singleton × 1 item
      quantity = 2  →  0 pallets,             1 singleton × 2 items
    """
    max_pallet: int = _max_qty_fits(carton, Pallet)

    if max_pallet == 0:
        # Carton geometry doesn't allow any pallet orientation; use singletons.
        return _build_units(carton, Singleton, quantity)

    if quantity < max_pallet:
        # Quantity is smaller than one full pallet — singleton is the right fit.
        # Fall back to a partial pallet only if the carton can't be singletonised.
        singletons = _build_units(carton, Singleton, quantity)
        return singletons if singletons else [Pallet(carton, quantity)]

    # Pack as many complete pallets as possible.
    n_full    = quantity // max_pallet
    remainder = quantity % max_pallet
    units: list[StorageUnit] = [Pallet(carton, max_pallet) for _ in range(n_full)]

    if remainder > 0:
        singletons = _build_units(carton, Singleton, remainder)
        if singletons:
            units.extend(singletons)
        else:
            # Carton cannot be singletonised; carry the remainder on an extra
            # partial pallet rather than silently discarding those items.
            units.append(Pallet(carton, remainder))

    return units


class StorageCart:
    max_length: int = 50
    max_width: int = 50
    max_height: int = 50

    def __init__(self) -> None:
        self._remaining_volume: int = self.max_length * self.max_width * self.max_height
        self._contents: list[tuple[Carton, int]] = []

    @property
    def total_volume(self) -> int:
        return self.max_length * self.max_width * self.max_height

    @property
    def remaining_volume(self) -> int:
        return self._remaining_volume

    @property
    def contents(self) -> list[tuple[Carton, int]]:
        return list(self._contents)

    def add_from_bin(self, bin_: Aisle.Bin, quantity: int) -> int:
        """Take up to `quantity` units of the bin's carton into the cart.

        Assumes perfect packing — placement succeeds if carton volume fits in
        remaining cart volume, regardless of orientation.
        Returns the number of units actually taken.
        Clears bin_.storage when its quantity reaches zero.
        """
        if bin_.storage is None or quantity <= 0:
            return 0
        unit = bin_.storage
        carton_vol = unit.carton.volume()
        if carton_vol > self._remaining_volume:
            return 0
        max_fits = self._remaining_volume // carton_vol
        actual = min(quantity, unit.quantity, max_fits)
        if actual <= 0:
            return 0
        self._remaining_volume -= actual * carton_vol
        self._contents.append((unit.carton, actual))
        # mutates unit in-place; Inventory_Manager only learns the bin is empty
        # when _reclaim_empty_bins() is called, not at pick time
        unit.quantity -= actual
        if unit.quantity == 0:
            bin_.storage = None
        return actual
