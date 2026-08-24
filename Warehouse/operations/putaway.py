"""putaway — what a put-away COSTS, in the same seconds the pick clock counts.

Put-away has been a zero-duration phase for the life of the project: `check_reorders` →
`_stock` → `_execute_placement` move inventory and produce no time value anywhere. So a
strategy that defers placement looked free, and a second crew had nothing to be measured
in.

This gives it a cost, deliberately mirroring the PICK model rather than inventing a second
one — travel to the location plus the same height-bracketed handling expression, through
the same `cost_model` primitives. That is the honest starting point: put-away and picking
are the same physical act with the inventory moving the other way, and a model that shares
its shape is one that can be re-tuned against the pick model rather than drifting from it.

## What this deliberately does NOT model

The user's own framing for this phase: *"do what you can to align puts stream but I didn't
really model out that intended behavior yet… it will be changing."* So:

- **No contention.** A putter and a picker never wait for each other, an aisle holds any
  number of actors, and put-away time does not move the pick clock by one second. The two
  streams are simulated independently and merged, which is the same assumption
  `fast_pick` already makes across its own pickers.
- **No travel between placements.** Each put is costed from the aisle mouth, exactly as a
  pick's aisle-entry travel is. A real putter carries a pallet down a lane and drops
  several; that is a routing problem, and routing put-away is the feature, not the seam.
- **No dock, no trailer, no unload.** `_stock(budget=)` bounds how much a crew places in a
  tick and is where those will attach.

Because it adds a measurement without perturbing anything, turning it on changes no pick
result: the same items are picked, in the same order, at the same instants. It adds rows
and a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from Warehouse.kernel.cost_model import (
    DEFAULT_HEIGHT_BRACKETS, SpeedProfile, handle_var, height_multiplier, per_pick,
)


@dataclass(frozen=True)
class PutawayCost:
    """The put-away time model's coefficients — the put-side twin of `PickConfig`'s.

    Defaults mirror `PickConfig`'s own dataclass defaults, so a caller that supplies
    nothing gets the pick model's shape at the pick model's scale rather than a second set
    of magic numbers to reconcile later (this project has already paid for two default sets
    that drifted 55x apart).
    """
    intercept: float = 1.0
    weight_coef: float = 0.02
    volume_coef: float = 1e-4
    weight_fn: str = 'log'
    volume_fn: str = 'log'
    height_brackets: tuple = field(default_factory=lambda: DEFAULT_HEIGHT_BRACKETS)


def put_cost(x_phys: float, y_phys: float, weight: float, volume: float, quantity: int,
             speed: SpeedProfile, cost: PutawayCost) -> float:
    """Seconds to put `quantity` units away at physical location `(x_phys, y_phys)`.

    Travel from the aisle mouth plus height-bracketed handling — the same two terms, in the
    same order, through the same primitives as `Pick._pick_time` and `Workload`'s P-term.

    Returns SECONDS, the unit the whole simulator counts in
    (`Warehouse.kernel.timeline.TIME_UNIT`).
    """
    travel = x_phys * speed.x_pace + y_phys * speed.y_pace
    handling = per_pick(
        height_multiplier(cost.height_brackets, y_phys),
        cost.intercept,
        handle_var(weight, volume, cost.weight_coef, cost.volume_coef,
                   cost.weight_fn, cost.volume_fn),
        quantity,
    )
    return travel + handling
