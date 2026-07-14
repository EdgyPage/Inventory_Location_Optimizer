"""cost_model.py — single source of truth for the pick-cost primitives.

These three helpers (height multiplier, per-unit handling, travel) were previously
copy-pasted across Pick, Workload, Assignment_Functions, Inventory_Management, Order
and Capacity_Reloader.  Centralising them keeps the simulated pick time, the analytical
workload W, the placement scorers, and the optimal-layout/map computations in lockstep.

Depends only on `math` + `functools`, so every layer (Warehouse + Optimization) can import it
without introducing a cycle or a layer violation.
"""
import math
from functools import lru_cache

# (upper_y_phys_exclusive, handling_multiplier) brackets — a bin's bracket is the first
# whose threshold exceeds its y_phys (else the last).  The multiplier scales ONLY the
# per-unit weight/volume handling term (not the intercept or cart-swap penalty).
DEFAULT_HEIGHT_BRACKETS: tuple = ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4))


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


def per_pick(mult: float, intercept: float, var: float, qty: float = 1) -> float:
    """The composite at-location pick expression  mult · (intercept + qty · var).

    THE one formula behind every per-pick cost in the codebase — the sim's pick time
    (mult = height bracket M(y)), the analytical workload P-term, the per-SKU labor_cost
    (mult=1, qty=1), the optimal-map coefficients (mult = frequency), and the marginal
    placement scores (… + D).  Previously inlined at 7 sites with "mirrors _pick_time"
    comments; one helper makes the invariant structural.  Expression shape/associativity
    preserved exactly — float-identical with the inlined originals (a·b is commutative
    bit-for-bit in IEEE 754, so qty·var == var·qty)."""
    return mult * (intercept + qty * var)


# Positions (x_phys/y_phys) are in inches — a pallet column is 48 in = 4 ft (Aisle_Dimensions).
# Travel SPEEDS (x_speed/y_speed) are in ft/s, so travel time divides distance by speed:
#   time = (distance_in / 12) / speed_ft_per_sec = distance_in * sec_per_inch(speed).
INCHES_PER_FOOT: float = 12.0


def sec_per_inch(speed_ft_per_sec: float) -> float:
    """Per-inch pace (s/inch) for a travel SPEED in ft/s.  Positions are in inches, so this
    is the factor the hot loops multiply by:  travel = x_phys·sec_per_inch(x_speed) + …
    Guards speed ≤ 0 → inf (a zero/negative speed never moves)."""
    return float('inf') if speed_ft_per_sec <= 0 else 1.0 / (INCHES_PER_FOOT * speed_ft_per_sec)


def travel_cost(x_phys: float, y_phys: float,
                x_speed: float, y_speed: float) -> float:
    """Demand-blind travel time (s) to a bin.  x_speed/y_speed are SPEEDS in ft/s; positions
    are in inches.  Hot inner loops inline this with a precomputed sec_per_inch() pace to
    avoid the per-bin division; everywhere else, call this."""
    return x_phys * sec_per_inch(x_speed) + y_phys * sec_per_inch(y_speed)


def aisle_traverse_cost(first_x: float, first_y: float,
                        last_x: float, last_y: float,
                        aisle_length: float,
                        x_pace: float, y_pace: float,
                        one_way: bool,
                        entrance_x: float = 0.0) -> tuple[float, float, float, float]:
    """Non-pick aisle-traversal time (s) for ONE aisle visit, split into ENTRY and EXIT
    per axis — the single definition shared by the sim (Pick / fast_pick), the analytical
    workload (Workload), and the travel decomposition so they can never drift.

    ENTRY = aisle entrance → first pick:
        entry_x = |first_x - entrance_x| * x_pace ;  entry_y = first_y * y_pace  (raise from ground)
    EXIT  = last pick → aisle far end (one-way lane only), descend to ground:
        exit_x  = |aisle_length - last_x| * x_pace ;  exit_y = last_y * y_pace
    TWO-WAY (default): no exit — the picker leaves the way it came, reproducing today's model.

    `x_pace`/`y_pace` are per-inch paces (sec_per_inch of the regime's speed).  Returns
    (entry_x, entry_y, exit_x, exit_y) in seconds; sum them for the total non-pick travel."""
    entry_x = abs(first_x - entrance_x) * x_pace
    entry_y = first_y * y_pace
    if one_way:
        exit_x = abs(aisle_length - last_x) * x_pace
        exit_y = last_y * y_pace
    else:
        exit_x = 0.0
        exit_y = 0.0
    return entry_x, entry_y, exit_x, exit_y


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
