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
