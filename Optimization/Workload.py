from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkloadParams:
    """Coefficients that define how physical effort is estimated for one aisle.

    Mirrors the relevant fields of PickConfig (Warehouse/Pick.py) so the
    Optimization layer can compute W_a without importing simulation internals.

    Fields
    ------
    x_move_time      : time units per bayX step
    y_move_time      : time units per bayY step
    pick_intercept   : fixed overhead per pick stop
    pick_weight_coef : time added per (weight × quantity) unit
    pick_volume_coef : time added per (volume × quantity) unit
    cart_swap_coef   : penalty per additional cart needed beyond the first
    """
    x_move_time: float      = 1.0
    y_move_time: float      = 0.5
    pick_intercept: float   = 1.0
    pick_weight_coef: float = 0.02
    pick_volume_coef: float = 1e-4
    cart_swap_coef: float   = 5.0

    @classmethod
    def from_pick_config(cls, cfg: object) -> WorkloadParams:
        """Build WorkloadParams from a PickConfig instance (duck-typed)."""
        return cls(
            x_move_time      = cfg.x_move_time,       # type: ignore[attr-defined]
            y_move_time      = cfg.y_move_time,       # type: ignore[attr-defined]
            pick_intercept   = cfg.pick_intercept,    # type: ignore[attr-defined]
            pick_weight_coef = cfg.pick_weight_coef,  # type: ignore[attr-defined]
            pick_volume_coef = cfg.pick_volume_coef,  # type: ignore[attr-defined]
            cart_swap_coef   = cfg.cart_swap_coef,    # type: ignore[attr-defined]
        )


def aisle_workload(
    x_traversed: int,
    y_traversed: int,
    carts_required: int,
    pick_lines: list[tuple[int, int, int]],
    params: WorkloadParams,
) -> float:
    """Estimate W_a: the total time to complete all picks in one aisle.

    Formula
    -------
    W_a = travel_time + pick_time + cart_penalty

    travel_time  = x_traversed * x_move_time + y_traversed * y_move_time
    pick_time    = Σ_stops (intercept + weight_coef*weight*qty
                                      + volume_coef*volume*qty)
    cart_penalty = cart_swap_coef * max(0, carts_required - 1)

    Parameters
    ----------
    x_traversed    : total bayX distance walked in the aisle (from Task)
    y_traversed    : total bayY distance walked in the aisle (from Task)
    carts_required : number of carts the aisle's picks fill (from Task)
    pick_lines     : one (weight, volume, qty) tuple per pick stop
    params         : WorkloadParams coefficients
    """
    travel: float = (
        x_traversed * params.x_move_time
        + y_traversed * params.y_move_time
    )
    pick: float = sum(
        params.pick_intercept
        + params.pick_weight_coef * weight * qty
        + params.pick_volume_coef * volume * qty
        for weight, volume, qty in pick_lines
    )
    cart_penalty: float = params.cart_swap_coef * max(0, carts_required - 1)
    return travel + pick + cart_penalty
