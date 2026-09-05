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
    DEFAULT_HEIGHT_BRACKETS, DEFAULT_PICK_INTERCEPT, DEFAULT_PICK_PER_ITEM,
    DEFAULT_PICK_VOLUME_COEF, DEFAULT_PICK_VOLUME_FN, DEFAULT_PICK_WEIGHT_COEF,
    DEFAULT_PICK_WEIGHT_FN, DEFAULT_PUT_INTERCEPT_SCALE, DEFAULT_PUT_ITEM_RATIO,
    SpeedProfile, handle_var, height_multiplier, per_pick,
)


@dataclass(frozen=True)
class PutawayCost:
    """The put-away time model's coefficients — the put-side twin of `PickConfig`'s.

    PICKING'S SHAPE, PICKING'S COEFFICIENTS, TWO DECLARED SCALARS.  Every value here is the
    pick model's BY REFERENCE — the kernel's one literal set (`cost_model.DEFAULT_PICK_*`),
    the same declaration `PickConfig` reads — and the only way a put crew's numbers may
    differ from a picker's is through the two scales the calibrated era declares:

        intercept = picking's × PUT_INTERCEPT_SCALE   ("putting is less work", default 0.5)
        per_item  = picking's × PUT_ITEM_RATIO        (default 0.2, i.e. 0.1 s per unit)

    The fields hold the EFFECTIVE (already-scaled) values, so `put_cost` stays one call to
    the shared primitive and the object is self-describing.  `from_pick` is how a run
    builds one from its own `PickConfig` (or `WorkloadParams`) and the run's scales; the
    dataclass defaults are what that yields for the kernel defaults.  There is deliberately
    no second literal set: this project has already paid for two default sets that drifted
    55x apart, and the by-reference link is what makes the drift structurally impossible.

    This module may not import the pick simulation (architecture.yml forbids
    wh_operations -> wh_picking), which is why the shared declaration lives in the kernel.
    """
    intercept: float = DEFAULT_PICK_INTERCEPT * DEFAULT_PUT_INTERCEPT_SCALE
    per_item: float = DEFAULT_PICK_PER_ITEM * DEFAULT_PUT_ITEM_RATIO
    weight_coef: float = DEFAULT_PICK_WEIGHT_COEF
    volume_coef: float = DEFAULT_PICK_VOLUME_COEF
    weight_fn: str = DEFAULT_PICK_WEIGHT_FN
    volume_fn: str = DEFAULT_PICK_VOLUME_FN
    height_brackets: tuple = field(default_factory=lambda: DEFAULT_HEIGHT_BRACKETS)

    @classmethod
    def from_pick(cls, cfg, *, intercept_scale: float = DEFAULT_PUT_INTERCEPT_SCALE,
                  item_ratio: float = DEFAULT_PUT_ITEM_RATIO) -> 'PutawayCost':
        """Build the put crew's cost from a pick config's coefficients (duck-typed: a
        `PickConfig` or a `WorkloadParams`, anything with the `pick_*` attributes).

        THE constructor the run harness uses, so a registered pick config's intercept
        (15 s on the store arms, 10 s on fulfillment) actually reaches the put crew — the
        class defaults alone would price every put at the kernel's 1.0 while the pickers
        beside it were charged 15.  The scales are the run's declared knobs
        (`settings.PUT_INTERCEPT_SCALE` / `PUT_ITEM_RATIO`), carried in the worker payload.
        """
        return cls(
            intercept=cfg.pick_intercept * intercept_scale,
            per_item=cfg.pick_per_item * item_ratio,
            weight_coef=cfg.pick_weight_coef,
            volume_coef=cfg.pick_volume_coef,
            weight_fn=cfg.pick_weight_fn,
            volume_fn=cfg.pick_volume_fn,
            height_brackets=tuple(cfg.height_brackets),
        )


def put_cost(x_phys: float, y_phys: float, weight: float, volume: float, quantity: int,
             speed: SpeedProfile, cost: PutawayCost) -> float:
    """Seconds to put `quantity` units away at physical location `(x_phys, y_phys)`.

    Travel from the aisle mouth plus height-bracketed handling — the same two terms, in the
    same order, through the same primitives as `Pick._pick_time` and `Workload`'s P-term:

        travel + M(y) · (intercept + quantity · per_item + quantity · var)

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
        cost.per_item,
    )
    return travel + handling
