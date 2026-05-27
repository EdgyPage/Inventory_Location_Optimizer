import random
from Demand import Demand

_MAX_DIM: int = 48  # mirrors Storage_Size.available_sizes_heights['extra_large']
_MIN_DIM: int = 3
_NUM_LIFT_GROUPS: int = 5


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
        self.lift_group: int = random.randint(1, _NUM_LIFT_GROUPS)

    def volume(self) -> int:
        return self.length * self.width * self.height

    @property
    def sku(self) -> int:
        return self._sku

    def reorder(self) -> 'Carton':
        """Return a new shipment of this carton: same SKU, dimensions, weight, type, and demand rates."""
        c = object.__new__(Carton)
        c.length = self.length
        c.width = self.width
        c.height = self.height
        c.weight = self.weight
        c.storage_type = self.storage_type
        c._sku = self._sku
        c.demand = Demand.from_rates(self.demand.frequency, self.demand.quantity_rate)
        c.lift_group = self.lift_group
        return c

    @property
    def popularity(self) -> float:
        """Pick weight for weighted random selection; backed by demand frequency."""
        return self.demand.frequency
