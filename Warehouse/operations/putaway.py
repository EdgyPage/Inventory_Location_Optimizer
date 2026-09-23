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

from Warehouse.kernel import closed_form as _cf

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

    @property
    def closed_form(self) -> '_cf.Law':
        """This crew's put law as ONE expression tree -- `travel + M(y)·(I + q·p + q·v)` at
        these coefficients (`Warehouse.kernel.closed_form.put_event_model`).  Evaluates what
        `put_cost` bills for an event (`x, y, vx, vy, w, vol, q`) and renders the same tree as
        LaTeX; `Tests/unit/test_cost_laws.py` holds the two equal."""
        return _cf.Law(_cf.put_event_model(tuple(self.height_brackets), self.weight_fn,
                                           self.volume_fn),
                       output='put',
                       fixed={'I': self.intercept, 'p': self.per_item,
                              'cw': self.weight_coef, 'cv': self.volume_coef})


def put_seconds_at(x_phys: float, y_phys: float, *, speed: SpeedProfile,
                   cost: 'PutawayCost | None' = None,
                   weight: float = 0.0, volume: float = 0.0, quantity: int = 0) -> float:
    """Seconds to put `quantity` units away at `(x_phys, y_phys)` — the ONE expression.

    Travel from the aisle mouth plus height-bracketed handling — the same two terms, in the
    same order, through the same primitives as `Pick._pick_time` and `Workload`'s P-term:

        travel + M(y) · (intercept + quantity · per_item + quantity · var)

    Returns SECONDS, the unit the whole simulator counts in
    (`Warehouse.kernel.timeline.TIME_UNIT`).

    ## Two callers price the same physical act, and one of them drops a term

    `put_cost` below BILLS a put; `Inbound.gain._Evaluator._cost_at` OPTIMISES where to put
    it.  Until 2026-09-17 the objective restated this formula and kept only `travel` — no
    intercept, no per-item charge, and no HEIGHT MULTIPLIER, which is bin-dependent and
    therefore does not cancel out of a difference between two candidate bins.  The
    simplification was recorded in prose (`gain.py`'s header: "put travel is paid once at
    the put crew's speeds") and nowhere in the code, so **whether it was a decision or drift
    was undecidable from the tests** — and the put formula moved three times in two months
    (ADR-0001's per-item charge, the one-clock refactor, ADR-0003).

    `cost=None` is that drop, made VISIBLE: the handling term is not merely unwritten, it is
    refused at the call site, by name, where a reader can see it and a test can pin it.
    `Tests/unit/test_put_seconds_at.py` asserts the exact relationship between the two
    readings, so a change to either half now has somewhere to fail.

    `Inbound/unload.py`'s `UnloadCost.from_putaway` makes the same argument for the
    receiving twin: a reference makes the drift "structurally impossible rather than merely
    a diff someone might notice".
    """
    travel = x_phys * speed.x_pace + y_phys * speed.y_pace
    if cost is None:                       # the objective's documented simplification
        return travel
    handling = per_pick(
        height_multiplier(cost.height_brackets, y_phys),
        cost.intercept,
        handle_var(weight, volume, cost.weight_coef, cost.volume_coef,
                   cost.weight_fn, cost.volume_fn),
        quantity,
        cost.per_item,
    )
    return travel + handling


def put_cost(x_phys: float, y_phys: float, weight: float, volume: float, quantity: int,
             speed: SpeedProfile, cost: PutawayCost) -> float:
    """What the simulation BILLS for one put — `put_seconds_at` with the handling term in.

    Kept as its own name because it is the billing call and reads as one at every call site
    (`Inventory_Manager._cost_putaway`), and because `cost` is not optional here: a put that
    is charged always pays for the handling.
    """
    return put_seconds_at(x_phys, y_phys, speed=speed, cost=cost,
                          weight=weight, volume=volume, quantity=quantity)
