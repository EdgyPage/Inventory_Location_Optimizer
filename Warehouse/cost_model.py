"""cost_model.py — single source of truth for the pick-cost primitives.

These three helpers (height multiplier, per-unit handling, travel) were previously
copy-pasted across Pick, Workload, Assignment_Functions, Inventory_Management, Carton
and Capacity_Reloader.  Centralising them keeps the simulated pick time, the analytical
workload W, the placement scorers, and the optimal-layout/map computations in lockstep.

Depends only on `math`, so every layer (Warehouse + Optimization) can import it without
introducing a cycle or a layer violation.
"""
import math

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


def handle_var(weight: float, volume: float,
               weight_coef: float, volume_coef: float) -> float:
    """Per-unit weight/volume handling term v_s = pw·ln(w) + pv·ln(v) (no intercept, no
    quantity).  weight/volume are clamped to ≥1 — a zero would make math.log raise and is
    physically impossible (bad data)."""
    return (weight_coef * math.log(max(weight, 1))
            + volume_coef * math.log(max(volume, 1)))


def travel_cost(x_phys: float, y_phys: float,
                x_speed: float, y_speed: float) -> float:
    """Demand-blind travel time to a bin: x_speed·x_phys + y_speed·y_phys.  (Hot inner
    loops may inline this expression to avoid call overhead; everywhere else, call this.)"""
    return x_speed * x_phys + y_speed * y_phys
