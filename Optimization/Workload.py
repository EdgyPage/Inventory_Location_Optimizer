from __future__ import annotations

import math
from dataclasses import dataclass, field

# Mirror of Pick.DEFAULT_HEIGHT_BRACKETS / height_multiplier (kept here to preserve
# the "Optimization layer computes W without importing simulation internals" rule —
# same pattern as aisle_workload duplicating the _pick_time formula).  At runtime the
# brackets come from the PickConfig via from_pick_config, so they stay in sync.
_DEFAULT_HEIGHT_BRACKETS: tuple = ((96.0, 1.0), (240.0, 1.4), (float('inf'), 1.9))


def _height_mult(brackets: tuple, y_phys: float) -> float:
    for thr, mult in brackets:
        if y_phys < thr:
            return mult
    return brackets[-1][1]


@dataclass
class WorkloadParams:
    """Coefficients that define how physical effort is estimated for one aisle.

    Mirrors the relevant fields of PickConfig (Warehouse/Pick.py) so the
    Optimization layer can compute W without importing simulation internals.

    Fields
    ------
    x_speed      : time units per bayX step
    y_speed      : time units per bayY step
    pick_intercept   : fixed overhead per P stop
    pick_weight_coef : time added per (weight × quantity) unit
    pick_volume_coef : time added per (volume × quantity) unit
    cart_swap_coef   : penalty per additional cart needed beyond the first
    """
    x_speed: float          = 1.0   # time per physical X unit (bin.x_phys)
    y_speed: float          = 0.5   # time per physical Y unit (bin.y_phys)
    pick_intercept: float   = 1.0
    pick_weight_coef: float = 0.02
    pick_volume_coef: float = 1e-4
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
            cart_swap_coef   = cfg.cart_swap_coef,    # type: ignore[attr-defined]
            height_brackets  = getattr(cfg, 'height_brackets', _DEFAULT_HEIGHT_BRACKETS),
        )


def aisle_workload(
    x_traversed: int,
    y_traversed: int,
    carts_required: int,
    pick_lines: list[tuple],
    params: WorkloadParams,
) -> float:
    """Estimate W: the total time (workload) to complete all picks in one aisle.

    W = D + P + C
      D (travel) = x_traversed * x_speed + y_traversed * y_speed
      P (pick)   = Σ_stops (intercept + height_mult(y) * qty
                                      * (weight_coef*ln(weight) + volume_coef*ln(volume)))
      C (cart)   = cart_swap_coef * max(0, carts_required - 1)

    Mirrors Warehouse/Pick._pick_time, including the height-bracket multiplier on the
    per-unit weight/volume handling.

    Parameters
    ----------
    x_traversed    : total bayX distance walked in the aisle (from Task)
    y_traversed    : total bayY distance walked in the aisle (from Task)
    carts_required : number of carts the aisle's picks fill (from Task)
    pick_lines     : one (weight, volume, qty[, y_phys]) tuple per pick stop
    params         : WorkloadParams coefficients
    """
    D: float = (
        x_traversed * params.x_speed
        + y_traversed * params.y_speed
    )
    P = 0.0
    for line in pick_lines:
        weight, volume, qty = line[0], line[1], line[2]
        y_phys = line[3] if len(line) > 3 else 0.0
        hmult = _height_mult(params.height_brackets, y_phys)
        P += (params.pick_intercept
              + hmult * qty * (params.pick_weight_coef * math.log(weight)
                               + params.pick_volume_coef * math.log(volume)))
    C: float = params.cart_swap_coef * max(0, carts_required - 1)
    return D + P + C
