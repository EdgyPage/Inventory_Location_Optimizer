import random
from Demand import Demand

_MAX_DIM: int = 48  # mirrors Storage_Size.available_sizes_heights['extra_large']
_MIN_DIM: int = 3


def _sample_dim() -> int:
    return round(random.triangular(_MIN_DIM, _MAX_DIM, _MAX_DIM))


class Carton:
    next_sku: int = 1

    def __init__(self, storage_type: tuple[str, str], weight: int = 5) -> None:
        self.length: int = _sample_dim()
        self.width: int = _sample_dim()
        self.height: int = _sample_dim()
        self.weight: int = weight
        self.storage_type: tuple[str, str] = storage_type
        self._sku: int = Carton.next_sku
        Carton.next_sku += 1
        self.demand: Demand = Demand()

    def volume(self) -> int:
        return self.length * self.width * self.height

    @property
    def sku(self) -> int:
        return self._sku
