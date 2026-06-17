import math
import random
from collections import namedtuple
from Demand import Demand, poisson_sample

# Named tuple combining a carton's handling type and storage category.
# Replaces the pattern `handling, category = carton.storage_type` throughout
# the codebase with the more self-documenting `carton.storage_handle_config`.
StorageHandleConfig = namedtuple('StorageHandleConfig', ['handling', 'category'])

_MAX_DIM: int = 48  # mirrors Storage_Size.available_sizes_heights['extra_large']
_MIN_DIM: int = 3


def _sample_dim(max_dim: int = _MAX_DIM) -> int:
    # mode=max_dim left-skews the distribution; most cartons cluster near the maximum dimension
    return round(random.triangular(_MIN_DIM, max_dim, max_dim))


def _sample_weight(length: int, width: int, height: int) -> int:
    # λ = cube root of volume so weight correlates with linear size, not volume;
    # large pallets average ~48 weight units, small singletons average ~3–16.
    lam = (length * width * height) ** (1 / 3)
    return max(1, poisson_sample(lam))


class Carton:
    next_sku: int = 1
    # Per-unit pick-effort regression cost (intercept + weight/volume log terms).
    # Config-dependent (depends on PickConfig coefficients), so it is computed once
    # per worker via compute_labor_cost(); 0.0 until set.  Reorders copy it forward.
    labor_cost: float = 0.0
    handle_var: float = 0.0   # per-unit weight/volume handling term (no intercept) for height scaling

    def __init__(self, storage_type: tuple[str, str], max_dim: int = _MAX_DIM) -> None:
        self.length: int = _sample_dim(max_dim)
        self.width: int = _sample_dim(max_dim)
        self.height: int = _sample_dim(max_dim)
        self.weight: int = _sample_weight(self.length, self.width, self.height)
        self.storage_type: tuple[str, str] = storage_type
        self.storage_handle_config: StorageHandleConfig = StorageHandleConfig(*storage_type)
        self._sku: int = Carton.next_sku
        Carton.next_sku += 1
        self.demand: Demand = Demand()
        self.lift_group: tuple[str, str] = storage_type

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
        c.storage_handle_config = self.storage_handle_config
        c._sku = self._sku
        c.demand = Demand.from_rates(self.demand.frequency, self.demand.quantity_rate)
        c.lift_group = self.lift_group
        c.expected_batch_demand = getattr(self, 'expected_batch_demand', 0.0)
        c.equilibrium_qty       = getattr(self, 'equilibrium_qty',       1)
        c.reorder_point         = getattr(self, 'reorder_point',         1)
        c.lead_time_mean        = getattr(self, 'lead_time_mean',        0.0)
        c.supply_cv             = getattr(self, 'supply_cv',             0.0)
        # Preserve the multi-tier stock plan so reorders rebuild the same tier mix.
        c.stock_plan            = getattr(self, 'stock_plan',            None)
        # Carry the precomputed per-unit labor cost forward (same weight/coeffs);
        # expected_popularity/expected_labor are properties so they follow demand.
        c.labor_cost            = getattr(self, 'labor_cost',            0.0)
        c.handle_var            = getattr(self, 'handle_var',            0.0)
        c._is_reorder = True
        return c

    @property
    def popularity(self) -> float:
        """Pick weight for weighted random selection; backed by demand frequency."""
        return self.demand.frequency

    @property
    def expected_popularity(self) -> float:
        """Expected demand mass per period = frequency x quantity_rate (freq*qty).

        Static (depends only on demand), so derived on access rather than stored.
        Used as the per-aisle 'popularity' balance metric (Rank_popularity)."""
        return self.demand.frequency * self.demand.quantity_rate

    @property
    def expected_labor(self) -> float:
        """Expected picking labor per period = expected_popularity x labor_cost
        (= freq*qty*cost1).  Drives the Rank_labor enqueue order + aisle balance.
        Zero until compute_labor_cost() has set labor_cost for this worker."""
        return self.expected_popularity * self.labor_cost

    def compute_labor_cost(self, pick_intercept: float,
                           pick_weight_coef: float, pick_volume_coef: float) -> float:
        """Set (and return) labor_cost = per-unit pick regression cost for this carton
        under the given PickConfig coefficients.  Mirrors Pick._pick_time at qty=1,
        no cart swap, ground level.  Call once per worker after inventory load.

        Also stores handle_var = the per-unit weight/volume term ALONE (without the
        intercept) so a height-aware placement can scale just that part by the bin's
        height multiplier: per-unit handling at height = pick_intercept + mult*handle_var.
        """
        self.handle_var = (pick_weight_coef * math.log(max(self.weight, 1))
                           + pick_volume_coef * math.log(max(self.volume(), 1)))
        self.labor_cost = pick_intercept + self.handle_var
        return self.labor_cost
