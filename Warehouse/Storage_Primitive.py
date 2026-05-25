import itertools
from Carton import Carton

class Storage_Size:
    available_sizes_heights = {
        'small': 12,
        'medium': 24,
        'large': 36,
        'extra_large': 48
    }

    def __init__(self):
        self.max_length = 48
        self.max_width = 48
        self.max_height = self.available_sizes_heights["extra_large"]

class Storage_Type:
    def __init__(self):
        self.handling_storage_types = ['conveyable', 'non-conveyable']
        self.category_storage_types = ['food', 'clothing', 'electronic',
                                            'furniture', 'seasonal', 'chemical']
        self.available_storage_types = list(itertools.product(self.handling_storage_types, self.category_storage_types))

class Singleton:
    max_height = 48
    max_width = 16
    max_length = 16

    def __init__(self, carton: Carton, quantity):
        self._height = None
        self._width = None
        self._length = None
        self._stack_axis = None
        self.carton = carton
        self.quantity = quantity
        self._fit(carton)

    def _fit(self, carton):
        dims = [carton.height, carton.width, carton.length]
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

    @property
    def height(self):
        return self._height

    @property
    def width(self):
        return self._width

    @property
    def length(self):
        return self._length

    @property
    def stack_axis(self):
        return self._stack_axis

class Pallet:
    max_length = 48
    max_width = 48

    def __init__(self, carton: Carton, quantity):
        self._height = None
        self._width = None
        self._length = None
        self._stack_axis = None
        self.storage_size = None
        self.carton = carton
        self.quantity = quantity
        self._fit(carton)

    def _fit(self, carton):
        dims = [carton.height, carton.width, carton.length]
        sorted_sizes = sorted(Storage_Size.available_sizes_heights.items(), key=lambda x: x[1])

        best = None
        for h, w, l in itertools.permutations(dims):
            for stack_h, stack_w, stack_l in [(self.quantity, 1, 1), (1, self.quantity, 1), (1, 1, self.quantity)]:
                if w * stack_w <= self.max_width and l * stack_l <= self.max_length:
                    stacked_height = h * stack_h
                    for size_name, size_height in sorted_sizes:
                        if stacked_height <= size_height:
                            if best is None or size_height < best[0]:
                                axis = ('height', 'width', 'length')[[stack_h, stack_w, stack_l].index(self.quantity)]
                                best = (size_height, size_name, h, w, l, axis)
                            break

        if best is None:
            raise ValueError(
                f"No valid orientation for carton SKU {carton.sku} with dimensions "
                f"({carton.height}, {carton.width}, {carton.length}) x{self.quantity} within pallet limits"
            )

        _, self.storage_size, self._height, self._width, self._length, self._stack_axis = best

    @property
    def height(self):
        return self._height

    @property
    def width(self):
        return self._width

    @property
    def length(self):
        return self._length

    @property
    def stack_axis(self):
        return self._stack_axis
