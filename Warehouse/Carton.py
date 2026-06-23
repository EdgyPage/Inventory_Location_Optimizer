import math
import random
from collections import namedtuple
from Demand import Demand, poisson_sample
from cost_model import handle_var as _handle_var

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

    # Physical bounds — every constructed carton is clamped to these so the DB only ever
    # holds grounded integers (no float dims, no fractional/zero weights).  Tunable.
    MIN_DIM:    int = 1
    MAX_DIM:    int = _MAX_DIM   # 48 (pallet max)
    MIN_WEIGHT: int = 1
    MAX_WEIGHT: int = 200
    MIN_QTY:    int = 1          # demand quantity_rate is integer units/pick
    MAX_QTY:    int = 20

    @staticmethod
    def _clamp_int(x: float, lo: int, hi: int) -> int:
        """Round to int and clamp to [lo, hi] — the single guardrail for physical fields."""
        return min(hi, max(lo, int(round(x))))

    @classmethod
    def build(cls, sku: int, handling: str, category: str,
              length: float, width: float, height: float, weight: float,
              frequency: float, qty_rate: float, *,
              equilibrium_qty: int, reorder_point: int,
              lead_time_mean: float = 0.0, supply_cv: float = 0.0,
              stock_plan=None) -> 'Carton':
        """Construct a carton from supplied physical + demand values (not random sampling),
        applying all physical guardrails.  The single construction path for generated and
        DB-loaded cartons — accepts demand/quantity as params so the default random Demand()
        is never invoked.  Does NOT touch Carton.next_sku (sku is explicit)."""
        c = object.__new__(cls)
        c._sku = sku
        c.storage_type          = (handling, category)
        c.storage_handle_config = StorageHandleConfig(handling, category)
        c.lift_group            = (handling, category)
        c.length = cls._clamp_int(length, cls.MIN_DIM,    cls.MAX_DIM)
        c.width  = cls._clamp_int(width,  cls.MIN_DIM,    cls.MAX_DIM)
        c.height = cls._clamp_int(height, cls.MIN_DIM,    cls.MAX_DIM)
        c.weight = cls._clamp_int(weight, cls.MIN_WEIGHT, cls.MAX_WEIGHT)
        qr = cls._clamp_int(qty_rate, cls.MIN_QTY, cls.MAX_QTY)   # integer units/pick
        fr = min(1.0, max(1e-6, float(frequency)))                # fractional pick rate (0, 1]
        c.demand = Demand.from_rates(fr, qr)
        c.expected_batch_demand = fr * qr
        c.equilibrium_qty = max(1, int(equilibrium_qty))
        c.reorder_point   = max(1, min(c.equilibrium_qty - 1, int(reorder_point))) \
            if c.equilibrium_qty > 1 else 1
        c.lead_time_mean  = float(lead_time_mean)
        c.supply_cv       = float(supply_cv)
        c.stock_plan      = stock_plan
        return c

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
                           pick_weight_coef: float, pick_volume_coef: float,
                           pick_weight_fn: str = 'log', pick_volume_fn: str = 'log') -> float:
        """Set (and return) labor_cost = per-unit pick regression cost for this carton
        under the given PickConfig coefficients.  Mirrors Pick._pick_time at qty=1,
        no cart swap, ground level.  Call once per worker after inventory load.

        Also stores handle_var = the per-unit weight/volume term ALONE (without the
        intercept).  labor_cost (= intercept + handle_var) is the qty=1 ground per-pick
        cost used to RANK items; the height multiplier scales the whole at-location pick
        at placement time: per-pick at height = mult*(pick_intercept + qty*handle_var).
        """
        self.handle_var = _handle_var(self.weight, self.volume(),
                                      pick_weight_coef, pick_volume_coef,
                                      pick_weight_fn, pick_volume_fn)
        self.labor_cost = pick_intercept + self.handle_var
        return self.labor_cost
