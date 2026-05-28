import math
import random
from Demand import Demand

_MAX_DIM: int = 48  # mirrors Storage_Size.available_sizes_heights['extra_large']
_MIN_DIM: int = 3
_NUM_LIFT_GROUPS: int = 5


def _sample_dim(max_dim: int = _MAX_DIM) -> int:
    # mode=max_dim left-skews the distribution; most cartons cluster near the maximum dimension
    return round(random.triangular(_MIN_DIM, max_dim, max_dim))


def _poisson(lam: float) -> int:
    """Knuth's algorithm — same approach used in Demand.sample()."""
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while p > threshold:
        k += 1
        p *= random.random()
    return k - 1


def _sample_weight(length: int, width: int, height: int) -> int:
    # λ = cube root of volume so weight correlates with linear size, not volume;
    # large pallets average ~48 weight units, small singletons average ~3–16.
    lam = max(1.0, (length * width * height) ** (1 / 3))
    return max(1, _poisson(lam))


class Carton:
    next_sku: int = 1

    def __init__(self, storage_type: tuple[str, str], max_dim: int = _MAX_DIM) -> None:
        self.length: int = _sample_dim(max_dim)
        self.width: int = _sample_dim(max_dim)
        self.height: int = _sample_dim(max_dim)
        self.weight: int = _sample_weight(self.length, self.width, self.height)
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
        # object.__new__ bypasses __init__ so next_sku is not incremented for a restock
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
