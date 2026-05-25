import itertools
from abc import ABC, abstractmethod
from Carton import Carton


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
        self.available_storage_types: list[tuple[str, str]] = list(
            itertools.product(self.handling_storage_types, self.category_storage_types)
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
    def height(self) -> int | None:
        return self._height

    @property
    def width(self) -> int | None:
        return self._width

    @property
    def length(self) -> int | None:
        return self._length

    @property
    def stack_axis(self) -> str | None:
        return self._stack_axis


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
