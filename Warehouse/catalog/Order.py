import math
import random
from collections import namedtuple
from Warehouse.catalog.Demand import Demand, LineDistribution, poisson_sample
from Warehouse.kernel.cost_model import handle_var as _handle_var, per_pick as _per_pick
from Warehouse.physical import PALLET_FOOTPRINT

# Named tuple combining a order's handling type and storage category.
# Replaces the pattern `handling, category = order.storage_type` throughout
# the codebase with the more self-documenting `order.storage_handle_config`.
StorageHandleConfig = namedtuple('StorageHandleConfig', ['handling', 'category'])

_MAX_DIM: int = PALLET_FOOTPRINT   # = Storage_Size.available_sizes_heights['extra_large']
_MIN_DIM: int = 3


def _sample_dim(max_dim: int = _MAX_DIM) -> int:
    # mode=max_dim left-skews the distribution; most orders cluster near the maximum dimension
    return round(random.triangular(_MIN_DIM, max_dim, max_dim))


def _sample_weight(length: int, width: int, height: int) -> int:
    # λ = cube root of volume so weight correlates with linear size, not volume;
    # large pallets average ~48 weight units, small singletons average ~3–16.
    # (The WEIGHT law -- a physical attribute drawn once per order.  The LINE law, the
    # quantity a pick line demands, is stamped on the SKU as `Demand.line`; this is not it.)
    lam = (length * width * height) ** (1 / 3)
    return max(1, poisson_sample(lam))


class Order:
    # Every attribute ever set on an instance, across all four production construction
    # paths (__init__, build, reorder, generate_inventory's inline __new__) plus the two
    # duck-typed contracts: 'stock_qty' (the documented legacy fallback —
    # inventory_common's hasattr probe must stay False until something assigns it) and
    # '_is_reorder' (set only by reorder(); every reader is getattr-with-default).
    # 'subtype' is set on generated orders and read back via getattr(c, 'subtype', None).
    #
    # THE STOCK DECLARATION -- 'equilibrium_qty', 'reorder_point', 'stock_plan' and
    # 'pipeline_qty' -- is a RUN's fact, never the SKU's (ADR-0002, department-calibration
    # "Field the floor", decision 5).  No construction path sets it: a freshly built or
    # generated order carries NO level (the slots are UNSET, `stock_declared()` is False), and
    # exactly one method, `declare_stock`, writes all four -- called by the coverage
    # derivation at setup (`simconfig/coverage.rescale_section`), by the warehouse planner
    # when it packs the level (`inventory_planning.sample_to_capacity`) and by the loader for
    # a run's own planned inventory, whose `stock_levels` table carries the declaration.
    # 'pipeline_qty' is the stamped lead pipeline (department-calibration, "Build the line
    # floor"): None = not stamped, and `pipeline_allowance()` falls back to the manager's
    # rp x lead / (lead + 1) heuristic -- every reader goes through that method.
    # ~263k live orders at production scale — slots drop the per-instance __dict__.
    __slots__ = ('_sku', 'storage_type', 'storage_handle_config', 'lift_group',
                 'length', 'width', 'height', 'weight', 'demand',
                 'expected_batch_demand', 'equilibrium_qty', 'reorder_point',
                 'lead_time_mean', 'supply_cv', 'stock_plan', 'pipeline_qty',
                 'labor_cost', 'handle_var', 'subtype', 'stock_qty', '_is_reorder')

    next_sku: int = 1
    # labor_cost / handle_var: per-unit pick-effort regression cost (intercept +
    # weight/volume log terms) and its no-intercept handling term.  Config-dependent
    # (PickConfig coefficients), computed once per worker via compute_labor_cost().
    # They USED to be class-level 0.0 defaults; a class attribute cannot share a name
    # with a slot, so every construction path now assigns the 0.0 explicitly (direct
    # non-getattr reads exist in Assignment_Functions and Storage_Primitive).

    # Physical bounds — every constructed order is clamped to these so the DB only ever
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
              relative_frequency: float, qty_rate: float, *,
              lead_time_mean: float = 0.0, supply_cv: float = 0.0,
              line: LineDistribution | None = None) -> 'Order':
        """Construct a order from supplied physical + demand values (not random sampling),
        applying all physical guardrails.  The single construction path for generated and
        DB-loaded orders — accepts demand/quantity as params so the default random Demand()
        is never invoked.  `line` is the SKU's stamped line law (`Demand.line`); absent, it
        is reconstructed as Poisson(qty_rate) with provenance `assumed`.  The scalar is the
        law's rate parameter and every reader must see ONE value, so a Poisson law authored at
        the unclamped rate follows the clamp (what every pre-stamp load sampled), and a law
        that agrees with neither RAISES.

        Takes NO stock level: the catalogue carries demand and geometry only, and a level is
        the run's declaration (`declare_stock`, ADR-0002).  Does NOT touch Order.next_sku
        (sku is explicit)."""
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
        fr = min(1.0, max(1e-6, float(relative_frequency)))       # fractional pick rate (0, 1]
        if line is not None and line.family == 'poisson_max1' \
                and abs(line.params['lam'] - qr) > 1e-9:
            if abs(line.params['lam'] - float(qty_rate)) <= 1e-9:
                line = LineDistribution.poisson(qr, line.provenance)   # the law follows the clamp
            else:
                raise ValueError(
                    f'SKU {sku}: the stamped line law is Poisson(lam={line.params["lam"]}) but '
                    f'the quantity rate is {qty_rate} (clamped {qr}); the scalar is the rate '
                    f'parameter of the law and every reader must see one value')
        c.demand = Demand.from_rates(fr, qr, line)
        c.expected_batch_demand = fr * qr
        c.lead_time_mean  = float(lead_time_mean)
        c.supply_cv       = float(supply_cv)
        c.labor_cost      = 0.0     # until compute_labor_cost() — was the class default
        c.handle_var      = 0.0
        return c

    def declare_stock(self, equilibrium_qty: int, reorder_point: int, *,
                      stock_plan=None, pipeline_qty: int | None = None) -> 'Order':
        """Write the run's stock declaration onto this SKU -- THE one mutation site for the
        four level slots (ADR-0002: a level is a run's fact, never the SKU's).

        The order-up-to is at least one unit; the reorder point at least one and at most one
        below it, so the trigger always fires before the target (a `Q = 1` SKU takes
        `rp = 1`).  `stock_plan` is the packing the warehouse planner wrote for exactly this
        quantity (None = the default pallet/singleton rule); `pipeline_qty` the stamped lead
        pipeline (None = not stamped, see `pipeline_allowance`).  Returns self.
        """
        q = max(1, int(equilibrium_qty))
        self.equilibrium_qty = q
        self.reorder_point   = max(1, min(q - 1, int(reorder_point))) if q > 1 else 1
        self.stock_plan      = stock_plan
        self.pipeline_qty    = None if pipeline_qty is None else max(0, int(pipeline_qty))
        return self

    def stock_declared(self) -> bool:
        """Whether a run has declared this SKU's stock level (`declare_stock` ran).  False on
        every freshly generated or catalogue-loaded order: the catalogue carries none."""
        return hasattr(self, 'equilibrium_qty')

    def __init__(self, storage_type: tuple[str, str], max_dim: int = _MAX_DIM) -> None:
        self.length: int = _sample_dim(max_dim)
        self.width: int = _sample_dim(max_dim)
        self.height: int = _sample_dim(max_dim)
        self.weight: int = _sample_weight(self.length, self.width, self.height)
        self.storage_type: tuple[str, str] = storage_type
        self.storage_handle_config: StorageHandleConfig = StorageHandleConfig(*storage_type)
        self._sku: int = Order.next_sku
        Order.next_sku += 1
        self.demand: Demand = Demand()
        self.lift_group: tuple[str, str] = storage_type
        self.pipeline_qty = None
        self.labor_cost: float = 0.0    # until compute_labor_cost() — was the class default
        self.handle_var: float = 0.0

    def volume(self) -> int:
        return self.length * self.width * self.height

    @property
    def sku(self) -> int:
        return self._sku

    def pipeline_allowance(self) -> int:
        """The units expected in transit over the lead -- what the order-up-to is raised by
        so on-hand returns to the equilibrium when the lot lands (`inventory_reorder
        ._fire_reorders`), and what the staffing derivation's expected lot reads.

        The ONE definition.  Stamped (`pipeline_qty`, the era's `round(d_s x lead)` from
        `simconfig/coverage.py`), the stamp is the answer.  Unstamped -- every flag-off run,
        every pre-stamp file -- the manager's heuristic stands, byte for byte:
        `round(rp x lead / (lead + 1))`, which assumed the reorder point encodes ~(lead + 1)
        batches of demand.  Under the line floor `rp` encodes a LINE, which is why the era
        stamps the pipeline instead of inferring it ("Choose the coverage floor", decision 6).
        Zero at lead 0 either way.
        """
        pq = getattr(self, 'pipeline_qty', None)
        if pq is not None:
            return int(pq)
        lead = max(0.0, float(getattr(self, 'lead_time_mean', 0.0)))
        if lead <= 0.0:
            return 0
        rp = getattr(self, 'reorder_point', 0)
        return round(rp * lead / (lead + 1.0))

    def reorder(self) -> 'Order':
        """Return a new shipment of this order: same SKU, dimensions, weight, type, and demand rates."""
        # object.__new__ bypasses __init__ so next_sku is not incremented for a restock
        c = object.__new__(Order)
        c.length = self.length
        c.width = self.width
        c.height = self.height
        c.weight = self.weight
        c.storage_type = self.storage_type
        c.storage_handle_config = self.storage_handle_config
        c._sku = self._sku
        c.demand = Demand.from_rates(self.demand.relative_frequency, self.demand.quantity_rate,
                                     self.demand.line)          # the stamped law rides along
        c.lift_group = self.lift_group
        c.expected_batch_demand = getattr(self, 'expected_batch_demand', 0.0)
        c.lead_time_mean        = getattr(self, 'lead_time_mean',        0.0)
        c.supply_cv             = getattr(self, 'supply_cv',             0.0)
        # The DECLARATION rides along when there is one, and does NOT get invented when there
        # is not.  These four used to default to 1 / 1 / None / None, which made a shipment of
        # an undeclared SKU answer "hold one unit, reorder at one" -- a fabricated policy, and
        # the one shape that would slip past the guards `inventory_common._equilibrium_qty`
        # and `inventory_reorder._undeclared_stock` now raise from, because the copy would
        # answer where the template refuses (ADR-0002).  A shipment carries what its SKU was
        # declared to hold, or carries no declaration at all.
        if self.stock_declared():
            # Preserve the multi-tier stock plan so reorders rebuild the same tier mix.
            c.declare_stock(self.equilibrium_qty, self.reorder_point,
                            stock_plan=getattr(self, 'stock_plan', None),
                            pipeline_qty=getattr(self, 'pipeline_qty', None))
        # Carry the precomputed per-unit labor cost forward (same weight/coeffs);
        # expected_popularity/expected_labor are properties so they follow demand.
        c.labor_cost            = getattr(self, 'labor_cost',            0.0)
        c.handle_var            = getattr(self, 'handle_var',            0.0)
        c._is_reorder = True
        return c

    @property
    def popularity(self) -> float:
        """Pick weight for weighted random selection; backed by demand frequency."""
        return self.demand.relative_frequency

    @property
    def expected_popularity(self) -> float:
        """Expected demand mass per period = frequency x quantity_rate (freq*qty).

        Static (depends only on demand), so derived on access rather than stored.
        Used as the per-aisle 'popularity' balance metric (Rank_popularity)."""
        return self.demand.relative_frequency * self.demand.quantity_rate

    @property
    def expected_labor(self) -> float:
        """Expected picking labor per period = expected_popularity x labor_cost
        (= freq*qty*cost1).  Drives the Rank_labor enqueue order + aisle balance.
        Zero until compute_labor_cost() has set labor_cost for this worker."""
        return self.expected_popularity * self.labor_cost

    def compute_labor_cost(self, pick_intercept: float,
                           pick_weight_coef: float, pick_volume_coef: float,
                           pick_weight_fn: str = 'log', pick_volume_fn: str = 'log',
                           *, pick_per_item: float) -> float:
        """Set (and return) labor_cost = per-unit pick regression cost for this order
        under the given PickConfig coefficients.  Mirrors Pick._pick_time at qty=1,
        no cart swap, ground level.  Call once per worker after inventory load.

        Also stores handle_var = the per-unit weight/volume term ALONE (without the
        intercept or the per-item charge).  labor_cost (= intercept + per_item + handle_var)
        is the qty=1 ground per-pick cost used to RANK items; the height multiplier scales
        the whole at-location pick at placement time:
        per-pick at height = mult*(pick_intercept + qty*pick_per_item + qty*handle_var).

        `pick_per_item` is keyword-only and REQUIRED: a caller that forgot it would price
        the pre-charge model and rank against a labor the sim no longer bills — silently,
        since nothing compares the two until a lockstep test does.
        """
        self.handle_var = _handle_var(self.weight, self.volume(),
                                      pick_weight_coef, pick_volume_coef,
                                      pick_weight_fn, pick_volume_fn)
        self.labor_cost = _per_pick(1.0, pick_intercept, self.handle_var, 1, pick_per_item)
        return self.labor_cost
