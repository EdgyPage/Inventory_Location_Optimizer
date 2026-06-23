from __future__ import annotations

from dataclasses import dataclass, field

# Single source of truth for the cost primitives (Warehouse/cost_model.py — on sys.path
# alongside Optimization at runtime).  No more local mirror of the bracket/handling math.
from cost_model import DEFAULT_HEIGHT_BRACKETS as _DEFAULT_HEIGHT_BRACKETS
from cost_model import height_multiplier as _height_mult, handle_var, sec_per_inch


@dataclass
class WorkloadParams:
    """Coefficients that define how physical effort is estimated for one aisle.

    Mirrors the relevant fields of PickConfig (Warehouse/Pick.py) so the
    Optimization layer can compute W without importing simulation internals.

    Fields
    ------
    x_speed      : horizontal travel speed in ft/s (positions are inches)
    y_speed      : vertical travel speed in ft/s (positions are inches)
    pick_intercept   : fixed overhead per P stop
    pick_weight_coef : time added per (weight × quantity) unit
    pick_volume_coef : time added per (volume × quantity) unit
    cart_swap_coef   : penalty per additional cart needed beyond the first
    """
    x_speed: float          = 4.0   # horizontal travel speed (ft/s); positions in inches
    y_speed: float          = 2.0   # vertical travel speed (ft/s); positions in inches
    pick_intercept: float   = 1.0
    pick_weight_coef: float = 0.02
    pick_volume_coef: float = 1e-4
    pick_weight_fn: str     = 'log'   # base function per handling term ('log'/'linear'/'sqrt'/'pow:p'/'log:b')
    pick_volume_fn: str     = 'log'
    cart_swap_coef: float   = 5.0
    height_brackets: tuple  = field(default_factory=lambda: _DEFAULT_HEIGHT_BRACKETS)

    @classmethod
    def from_pick_config(cls, cfg: object) -> WorkloadParams:
        """Build WorkloadParams from a PickConfig instance (duck-typed)."""
        return cls(
            x_speed          = cfg.x_speed,            # type: ignore[attr-defined]
            y_speed          = cfg.y_speed,            # type: ignore[attr-defined]
            pick_intercept   = cfg.pick_intercept,    # type: ignore[attr-defined]
            pick_weight_coef = cfg.pick_weight_coef,  # type: ignore[attr-defined]
            pick_volume_coef = cfg.pick_volume_coef,  # type: ignore[attr-defined]
            pick_weight_fn   = getattr(cfg, 'pick_weight_fn', 'log'),
            pick_volume_fn   = getattr(cfg, 'pick_volume_fn', 'log'),
            cart_swap_coef   = cfg.cart_swap_coef,    # type: ignore[attr-defined]
            height_brackets  = getattr(cfg, 'height_brackets', _DEFAULT_HEIGHT_BRACKETS),
        )


def aisle_workload_components(
    x_traversed: int,
    y_traversed: int,
    carts_required: int,
    pick_lines: list[tuple],
    params: WorkloadParams,
) -> tuple[float, float, float]:
    """The (D, P, C) decomposition of one aisle's workload W = D + P + C.

      D (travel)   = x_traversed * sec_per_inch(x_speed) + y_traversed * sec_per_inch(y_speed)
                     (x_speed/y_speed are ft/s; x_traversed/y_traversed are inches walked)
      P (handling) = Σ_stops  height_mult(y) * (intercept + qty
                                        * (weight_coef*ln(weight) + volume_coef*ln(volume)))
      C (cart)     = cart_swap_coef * max(0, carts_required - 1)

    Mirrors Warehouse/Pick._pick_time, including the height-bracket multiplier on the
    per-unit weight/volume handling.  Split out so callers can report the
    handling-vs-travel breakdown of the analytical objective (see expected_task_labor).

    Parameters
    ----------
    x_traversed    : total bayX distance walked in the aisle (from Task)
    y_traversed    : total bayY distance walked in the aisle (from Task)
    carts_required : number of carts the aisle's picks fill (from Task)
    pick_lines     : one (weight, volume, qty[, y_phys]) tuple per pick stop
    params         : WorkloadParams coefficients
    """
    D: float = (x_traversed * sec_per_inch(params.x_speed)
                + y_traversed * sec_per_inch(params.y_speed))
    P = 0.0
    for line in pick_lines:
        weight, volume, qty = line[0], line[1], line[2]
        y_phys = line[3] if len(line) > 3 else 0.0
        hmult = _height_mult(params.height_brackets, y_phys)
        # height scales the ENTIRE at-location pick: M·(intercept + qty·var) (mirrors _pick_time)
        P += hmult * (params.pick_intercept
                      + qty * handle_var(weight, volume,
                                         params.pick_weight_coef, params.pick_volume_coef,
                                         params.pick_weight_fn, params.pick_volume_fn))
    C: float = params.cart_swap_coef * max(0, carts_required - 1)
    return D, P, C


def aisle_workload(
    x_traversed: int,
    y_traversed: int,
    carts_required: int,
    pick_lines: list[tuple],
    params: WorkloadParams,
) -> float:
    """Estimate W = D + P + C, the total time (workload) for one aisle's picks.
    See aisle_workload_components for the term definitions."""
    D, P, C = aisle_workload_components(
        x_traversed, y_traversed, carts_required, pick_lines, params)
    return D + P + C
