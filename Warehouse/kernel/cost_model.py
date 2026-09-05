"""cost_model.py — single source of truth for the pick-cost primitives.

These three helpers (height multiplier, per-unit handling, travel) were previously
copy-pasted across Pick, Workload, Assignment_Functions, Inventory_Management, Order
and Capacity_Reloader.  Centralising them keeps the simulated pick time, the analytical
workload W, the placement scorers, and the optimal-layout/map computations in lockstep.

Depends only on `math` + `functools`, so every layer (Warehouse + Optimization) can import it
without introducing a cycle or a layer violation.
"""
import math
from dataclasses import dataclass, field
from functools import lru_cache

# (upper_y_phys_exclusive, handling_multiplier) brackets — a bin's bracket is the first
# whose threshold exceeds its y_phys (else the last).  The multiplier scales the WHOLE
# at-location expression `per_pick` (intercept, per-item charge and handling alike);
# the only at-location term outside it is the cart-swap penalty, which the sim loops
# charge as their own timed step.  (This comment said "ONLY the per-unit handling term"
# for as long as it existed, while `per_pick` scaled the whole — `_pick_time` had it right.)
DEFAULT_HEIGHT_BRACKETS: tuple = ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4))

# ── the handling model's DEFAULT coefficients — ONE literal set ─────────────────────
# `PickConfig` (Warehouse/picking), `WorkloadParams` (Optimization/metrics) and
# `PutawayCost` (Warehouse/operations) all default to THESE, by reference.  They used to be
# three copied literal sets that "mirrored" each other — and this project has already paid
# for two default sets that drifted 55x apart.  A put crew may not import the pick
# simulation (architecture.yml: wh_operations -> wh_picking is forbidden), so the kernel
# is the only place all three can share a declaration.
#
# THE MODEL, stated once:  M(y) · (intercept + qty · per_item + qty · var)
#   intercept  — charged once per pick LINE (one bin visit for one SKU), never per item;
#   per_item   — the fixed labour of placing one unit into the cart and labelling it,
#                charged once per UNIT.  Non-zero by decision (ADR-0001, docs/adr/): the
#                intercept was believed to carry this and never did, and a 0.0 default
#                would have preserved a model the calibrated era deliberately ends;
#   var        — the per-unit weight/volume handling term (`handle_var`).
DEFAULT_PICK_INTERCEPT: float   = 1.0
DEFAULT_PICK_PER_ITEM: float    = 0.5
DEFAULT_PICK_WEIGHT_COEF: float = 0.02
DEFAULT_PICK_VOLUME_COEF: float = 1e-4
DEFAULT_PICK_WEIGHT_FN: str     = 'log'
DEFAULT_PICK_VOLUME_FN: str     = 'log'
# The other two crews keep picking's shape and coefficients and differ ONLY by these
# declared scalars (the run-level knobs are settings.PUT_INTERCEPT_SCALE and siblings):
#   put-away   intercept × PUT_INTERCEPT_SCALE ("putting is less work"),
#              per_item  × PUT_ITEM_RATIO;
#   receiving  put-away's intercept × RECV_INTERCEPT_SCALE, and put-away's per-item
#              charge ONCE PER PACK rather than per item (a pack is what a receiver lifts).
DEFAULT_PUT_INTERCEPT_SCALE: float  = 0.5
DEFAULT_PUT_ITEM_RATIO: float       = 0.2
DEFAULT_RECV_INTERCEPT_SCALE: float = 1.0


def height_multiplier(brackets: tuple, y_phys: float) -> float:
    """Handling multiplier for a pick at physical height y_phys (step over brackets)."""
    for thr, mult in brackets:
        if y_phys < thr:
            return mult
    return brackets[-1][1] if brackets else 1.0


# ── per-term base functions (the tunable shape of the weight/volume handling term) ──
# A handling term is  coef · fn(value).  `fn` is named by a spec string so it can ride in
# configs / the DB:  'log' (natural), 'log:b' (base b), 'linear', 'sqrt', 'pow:p'.
TRANSFORMS = {
    'log':    lambda v: math.log(max(v, 1.0)),   # natural log (base e)
    'linear': lambda v: float(v),
    'sqrt':   lambda v: math.sqrt(max(v, 0.0)),
}


@lru_cache(maxsize=None)
def resolve_transform(spec: str):
    """Resolve a transform spec → callable.  Cached per process (the returned lambda is never
    pickled — only the spec string crosses process boundaries), so per-pick use is cheap.
      'log' | 'log:e' -> ln ;  'log:b' -> log base b ;  'linear' ;  'sqrt' ;  'pow:p' -> v**p."""
    if ':' in spec:
        name, p = spec.split(':', 1)
        if name == 'pow':
            power = float(p)
            return lambda v: max(v, 0.0) ** power
        if name == 'log':
            if p == 'e':
                return TRANSFORMS['log']
            lnb = math.log(float(p))                       # log_b(x) = ln(x) / ln(b)
            return lambda v: math.log(max(v, 1.0)) / lnb
        raise ValueError(f'unknown parametric transform {name!r}')
    return TRANSFORMS[spec]


def handle_var(weight: float, volume: float,
               weight_coef: float, volume_coef: float,
               weight_fn: str = 'log', volume_fn: str = 'log') -> float:
    """Per-unit weight/volume handling term v_s = pw·fn_w(w) + pv·fn_v(v) (no intercept, no
    quantity).  fn defaults to natural log (the original behaviour); other base functions
    (linear / sqrt / pow:p / log:b) are honored so a config's pick_weight_fn/pick_volume_fn
    actually changes labor.  The transforms clamp w/v internally (log→≥1, sqrt/pow→≥0)."""
    return (weight_coef * resolve_transform(weight_fn)(weight)
            + volume_coef * resolve_transform(volume_fn)(volume))


def per_pick(mult: float, intercept: float, var: float, qty: float = 1,
             per_item: float = 0.0) -> float:
    """The composite at-location expression  mult · (intercept + qty · per_item + qty · var).

    THE one formula behind every per-pick cost in the codebase — the sim's pick time
    (mult = height bracket M(y)), the put-away and unload costs, the analytical workload
    P-term, the per-SKU labor_cost (mult=1, qty=1), the optimal-map coefficients (mult =
    frequency), the gain evaluator's at-bin price and the marginal placement scores (… + D).
    Previously inlined at 7 sites with "mirrors _pick_time" comments; one helper makes the
    invariant structural.

    `per_item` is the per-UNIT charge (see DEFAULT_PICK_PER_ITEM above).  It is a parameter
    HERE, defaulting to 0.0, so the primitive does not fork: every caller passes its own
    crew's value (`PickConfig.pick_per_item`, `PutawayCost.per_item`, …), and the model-level
    defaults are non-zero where the decision says so.  With `per_item == 0.0` the expression
    is bit-identical to the pre-charge `mult · (intercept + qty · var)`: `intercept + 0.0`
    is exactly `intercept`, and the remaining associativity is unchanged."""
    return mult * (intercept + qty * per_item + qty * var)


# Positions (x_phys/y_phys) are in inches — a pallet column is 48 in = 4 ft (Aisle_Dimensions).
# Travel SPEEDS (x_speed/y_speed) are in ft/s, so travel time divides distance by speed:
#   time = (distance_in / 12) / speed_ft_per_sec = distance_in * sec_per_inch(speed).
INCHES_PER_FOOT: float = 12.0


def sec_per_inch(speed_ft_per_sec: float) -> float:
    """Per-inch pace (s/inch) for a travel SPEED in ft/s.  Positions are in inches, so this
    is the factor the hot loops multiply by:  travel = x_phys·sec_per_inch(x_speed) + …
    Guards speed ≤ 0 → inf (a zero/negative speed never moves)."""
    return float('inf') if speed_ft_per_sec <= 0 else 1.0 / (INCHES_PER_FOOT * speed_ft_per_sec)


def validate_speeds(x_speed: float, y_speed: float, *, source: str) -> None:
    """Reject a travel speed that would poison every score with NaN.  Raises ValueError.

    THE TRAP this exists to close.  `sec_per_inch` maps a non-positive speed to `inf`, which
    is the honest answer on its own (never moves → infinite time).  But the hot loops spend it
    as `pace · phys`, and a bin ON the axis origin has `phys == 0` — so `inf · 0` yields
    **NaN**, not 0.  NaN then propagates through every travel term, and because every
    comparison against NaN is False, `min()`/`max()`/`sorted()` silently degrade to "keep
    whichever candidate came first".  Placement quietly becomes insertion order.  No warning,
    no exception, no visibly wrong number — just a run whose assignment function did nothing.

    Nothing else catches it: `x_speed`/`y_speed` are read straight out of the config JSON
    (`Optimization/config/sim_config.py`), so one `"y_speed": 0` in a config file would do this
    to a whole sweep.  Validating at the two dataclass boundaries (PickConfig, WorkloadParams)
    costs one call per config rather than per bin, and cannot perturb a valid run — it only
    raises on input that was already producing NaN.
    """
    for name, speed in (('x_speed', x_speed), ('y_speed', y_speed)):
        # NaN fails `> 0` too, so this one comparison covers 0, negatives and NaN alike.
        if not (float(speed) > 0.0) or float(speed) == float('inf'):
            raise ValueError(
                f'{source}: {name}={speed!r} is not a positive finite travel speed (ft/s). '
                f'A non-positive speed makes sec_per_inch() return inf, and inf·0 = NaN for '
                f'any bin on the axis origin — every travel score becomes NaN and placement '
                f'silently degrades to insertion order. Use a small positive speed to model '
                f'"slow"; there is no valid way to spell "this axis is free".')


def travel_cost(x_phys: float, y_phys: float,
                x_speed: float, y_speed: float) -> float:
    """Demand-blind travel time (s) to a bin.  x_speed/y_speed are SPEEDS in ft/s; positions
    are in inches.  Hot inner loops inline this with a precomputed sec_per_inch() pace to
    avoid the per-bin division; everywhere else, call this."""
    return x_phys * sec_per_inch(x_speed) + y_phys * sec_per_inch(y_speed)


def aisle_exit_cost(last_x: float, last_y: float, aisle_length: float,
                    x_pace: float, y_pace: float) -> tuple[float, float]:
    """One-way lane EXIT (s): traverse from the last pick to the aisle far end, then
    descend to the ground.  Returns `(exit_x, exit_y)`.

    THE definition both pick simulations use, and the reason it exists separately from
    `aisle_traverse_cost`: the sims never compute an aisle ENTRY as such — their first
    bin-to-bin segment already covers it, and they do not track a "first pick" position to
    pass.  What they genuinely share is this half, so this half is what is shared.

    Charged to NON-PICK travel: the picker is walking to leave, not to pick.  A two-way
    lane has no exit segment at all, which is the caller's `if cfg.one_way` — kept there
    because the caller also has to decide whether to accumulate the result.
    """
    return abs(aisle_length - last_x) * x_pace, last_y * y_pace


def aisle_traverse_cost(first_x: float, first_y: float,
                        last_x: float, last_y: float,
                        aisle_length: float,
                        x_pace: float, y_pace: float,
                        one_way: bool,
                        entrance_x: float = 0.0) -> tuple[float, float, float, float]:
    """Non-pick aisle-traversal time (s) for ONE aisle visit, split into ENTRY and EXIT
    per axis.

    NOT called by the pick simulations, and the docstring here claimed otherwise for as
    long as it existed: it said it was "the single definition shared by the sim (Pick /
    fast_pick), the analytical workload (Workload), and the travel decomposition so they
    can never drift" while having zero callers, and both sims inlined the arithmetic.  A
    seam that lies about being one is worse than an absent seam, because it stops anyone
    looking for the real duplication.

    The half the sims DO share is `aisle_exit_cost`, which this now delegates to.  The
    entry half is folded into their generic bin-to-bin travel loop, so there is nothing
    here for them to call.

    ENTRY = aisle entrance → first pick:
        entry_x = |first_x - entrance_x| * x_pace ;  entry_y = first_y * y_pace  (raise from ground)
    EXIT  = last pick → aisle far end (one-way lane only), descend to ground:
        exit_x  = |aisle_length - last_x| * x_pace ;  exit_y = last_y * y_pace
    TWO-WAY (default): no exit — the picker leaves the way it came, reproducing today's model.

    `x_pace`/`y_pace` are per-inch paces (sec_per_inch of the regime's speed).  Returns
    (entry_x, entry_y, exit_x, exit_y) in seconds; sum them for the total non-pick travel."""
    entry_x = abs(first_x - entrance_x) * x_pace
    entry_y = first_y * y_pace
    exit_x, exit_y = (aisle_exit_cost(last_x, last_y, aisle_length, x_pace, y_pace)
                      if one_way else (0.0, 0.0))
    return entry_x, entry_y, exit_x, exit_y


# ── a travel speed as one named value ────────────────────────────────────────────

@dataclass(frozen=True)
class SpeedProfile:
    """One actor's travel speeds: **ft/s in, s/inch out**, converted once.

    A speed is configured in ft/s and spent in s/inch, because bin positions are inches.
    Nothing named that boundary, so every consumer crossed it by hand — 47 separate
    `sec_per_inch` calls across the two pick simulations, the analytical workload, the
    assignment functions, the optimal-placement search and the strategy runner.

    The discipline at those sites is already right: every one hoists the conversion to the
    top of its function and passes `x_pace`/`y_pace` down, so the arithmetic is never
    per-bin.  What was missing is a NAME for the hoisted pair.  Two bare floats travelling
    together through a dozen signatures is the shape that lets an x pace reach a y slot, and
    it is also the shape that cannot be extended: a putter and an unloader need their own
    speeds, and `(x_pace, y_pace)` has nowhere to record whose they are.

    Lives HERE, in `cost_model`, rather than in a module of its own or in the operations
    package that will own roles and modes.  `architecture.yml` forbids `wh_kernel -> *` and
    the wildcard matches the kernel itself, so a separate `kernel/speed.py` importing
    `sec_per_inch` is a boundary violation — the kernel's modules stay independently
    importable.  Beside the two functions it uses is also simply where it belongs.

    THE AXES ARE NOT INTERCHANGEABLE.  `x` is horizontal travel ALONG an aisle
    (`aisle_width_for(50)` = 200 ft of run); `y` is VERTICAL lift (`aisle_height_for(10)` =
    40 ft of rack).  Separate DB columns record them (`pick_travel_x/y`,
    `non_pick_travel_x/y`) and `height_multiplier` brackets on the same `y_phys` this paces
    — so a machine that drives fast and lifts slowly is a DIFFERENT profile from a walker,
    not a scaled one.

    Frozen and picklable, so it crosses the spawn boundary in a worker payload and can be
    shared rather than copied.
    """
    x_ft_s: float
    y_ft_s: float

    # Derived, s/inch.  init=False keeps them out of the constructor (not something a caller
    # supplies) and out of __eq__ (they add nothing the ft/s pair does not already decide).
    x_pace: float = field(init=False, compare=False, repr=False)
    y_pace: float = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        # A non-positive speed is NaN-poison, not a slow actor — see validate_speeds.
        validate_speeds(self.x_ft_s, self.y_ft_s, source='SpeedProfile')
        object.__setattr__(self, 'x_pace', sec_per_inch(self.x_ft_s))
        object.__setattr__(self, 'y_pace', sec_per_inch(self.y_ft_s))

    @property
    def paces(self) -> tuple[float, float]:
        """`(x_pace, y_pace)` — for the call sites that still want the bare pair."""
        return self.x_pace, self.y_pace


def cart_step(needed_vol: float, cart_remaining: float, cart_cap: float) -> tuple[bool, float]:
    """One next-fit cart step for a single pick of volume ``needed_vol`` against a cart with
    ``cart_remaining`` free volume (capacity ``cart_cap``).  Returns ``(swapped, new_remaining)``.

    A **swap** fires when the pick doesn't fit the current cart; the cart is then refilled to full
    before the pick is loaded.  This is the SINGLE source of the cart next-fit, shared by the sim's
    per-pick loop (``Pick`` / ``fast_pick``) and the scheduler's makespan predictor
    (``count_cart_swaps``), so the two can never drift — which is what makes the LPT scheduler's
    predicted makespan equal the sim's realized makespan."""
    swapped = needed_vol > cart_remaining
    if swapped:
        cart_remaining = cart_cap
    return swapped, max(0, cart_remaining - needed_vol)
