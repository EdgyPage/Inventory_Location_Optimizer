"""unload — what it costs to take merchandise off a trailer.

# ── the put-away cost, minus the two terms a dock does not have ───────────────────

`putaway.put_cost` is `travel + per_pick(height_multiplier(...), intercept, handle_var(...),
quantity)`.  An unload keeps the handling term verbatim and drops both of the others:

**No travel.**  `put_cost`'s travel is `x_phys * speed.x_pace + y_phys * speed.y_pace`
against a BIN.  A receive has no bin, and there is no dock coordinate anywhere in the model
-- grepping `dock|trailer|unload` across `Warehouse/` finds this file and prose.  The
tempting shortcut is to call `put_cost` with the dock at `(0, 0)`: travel comes out exactly
0.0 and `height_multiplier(brackets, 0.0)` comes out exactly 1.0, so it returns a plausible
number while silently asserting that unloading involves no travel at all.  Same for a
`dock_travel = 0.0` field: it is a slot for a number no layout can validate, and setting it
to 200 would assert 200 inches of x-travel corresponding to nothing.  So the term is ABSENT,
and this paragraph is the record that its absence is a decision.

**No height multiplier.**  A trailer floor is one height.  `UnloadCost` therefore has no
`height_brackets` field at all, rather than one defaulted to a flat bracket -- the field's
absence is what makes the claim checkable.

**No speed, and therefore no mode.**  Without a travel term there is nothing for a speed to
scale, so `unload_cost` takes no `SpeedProfile`.  The consequence is worth stating plainly
because someone will want a knob for it: *this model cannot say whether a forklift crew
unloads faster than a hand crew.*  Only crew SIZE changes a receiving makespan, because size
is the number of clocks.  A mode knob would label rows without moving a second, and a sweep
over it would publish "mode makes no difference to receiving".  When an unload gains a
travel term, mode arrives with it.

# ── the granularity, which moves the answer by an order of magnitude ──────────────

`per_pick(mult, intercept, var, qty, per_item)` is `mult * (intercept + qty * per_item +
qty * var)`, so the intercept is charged ONCE PER CALL.  A 60-unit arrival therefore costs
one intercept if it is costed as one call and sixty if it is costed per pack -- and at the
put-away default that is a 59 x 0.5 s difference on a single arrival, scaling with the run.

The charge is **per `StorageUnit`**: one call per pack, because a pack is what a person
lifts, and the fixed grab-orient-set-down charge belongs to the lift.  **So is the per-item
charge**, by decision (`.scratch/department-calibration`, "Define the calibrated era"): a
receiver handles and labels the PACK, not the items inside it, so the charge lands once per
pack rather than once per item -- an order split into five pallets and a bag of singletons
is six intercepts and six per-item charges, never six intercepts and sixty charges.  It is
therefore added OUTSIDE `per_pick`'s quantity term below rather than passed as `per_item`.
There is deliberately no per-DELIVERY setup term (backing the trailer in, opening the
doors): its default would have to be invented, and a coefficient defaulted to 0.0 is dead
code that rots.
"""
from __future__ import annotations

from dataclasses import dataclass

from Warehouse.kernel.cost_model import DEFAULT_RECV_INTERCEPT_SCALE, handle_var, per_pick
from Warehouse.operations.putaway import PutawayCost


@dataclass(frozen=True)
class UnloadCost:
    """The unload time model's coefficients — the receiving twin of `PutawayCost`.

    Every default is the PUT-AWAY default **by reference**, not by copied literal.  That is
    the whole point: `PutawayCost`'s own docstring warns against "a second set of magic
    numbers to reconcile later (this project has already paid for two default sets that
    drifted 55x apart)", and a reference makes the drift structurally impossible rather than
    merely a diff someone might notice.  Change a put-away coefficient and this moves with
    it, which is right until someone measures a dock and has a reason to separate them.

    The one declared divergence is `RECV_INTERCEPT_SCALE` (default 1.0): the receive
    intercept is put-away's × that scale, and the per-item charge is put-away's, charged
    once per PACK (module docstring).  `from_putaway` is how a run builds one from the put
    crew's own cost and the run's scale, so the chain picking -> put-away -> receiving is
    the same chain at runtime as it is here at the class defaults.

    No `height_brackets`, and no speed — see the module docstring for why each is absent
    rather than defaulted.
    """

    intercept: float = PutawayCost.intercept * DEFAULT_RECV_INTERCEPT_SCALE
    per_item: float = PutawayCost.per_item
    weight_coef: float = PutawayCost.weight_coef
    volume_coef: float = PutawayCost.volume_coef
    weight_fn: str = PutawayCost.weight_fn
    volume_fn: str = PutawayCost.volume_fn

    @classmethod
    def from_putaway(cls, put: PutawayCost, *,
                     intercept_scale: float = DEFAULT_RECV_INTERCEPT_SCALE) -> 'UnloadCost':
        """The receiving cost derived from the put crew's — the runtime form of the
        by-reference defaults above, so a run whose pickers charge 15 s a line receives
        at 15 × PUT_INTERCEPT_SCALE × RECV_INTERCEPT_SCALE rather than the kernel's 0.5."""
        return cls(
            intercept=put.intercept * intercept_scale,
            per_item=put.per_item,
            weight_coef=put.weight_coef,
            volume_coef=put.volume_coef,
            weight_fn=put.weight_fn,
            volume_fn=put.volume_fn,
        )


def unload_cost(weight: float, volume: float, quantity: int, cost: UnloadCost) -> float:
    """Seconds to take ONE storage unit off a trailer:

        intercept + per_item + quantity · var

    `weight` and `volume` are PER ITEM — `order.weight` and `order.volume()`, exactly as
    `put_cost` takes them.  Passing a pack's or a trailer's volume instead inflates the log
    term by `ln(quantity)` per unit, and `handle_var` clamps its log input to >= 1 so it
    never raises: a 34-pallet arrival would simply cost as if every item were 34x bigger.

    `quantity` is the merchandise in THIS pack (`unit.quantity`), so the intercept AND the
    per-item charge are charged once for the pack and only the variable term once per item
    in it.  The per-item charge is added outside `per_pick`'s quantity term on purpose —
    passing it as `per_item` would charge it `quantity` times, which is the pick model, not
    the receiving one (module docstring, "the granularity").

    Returns SECONDS, the unit the whole simulator counts in
    (`Warehouse.kernel.timeline.TIME_UNIT`).
    """
    return cost.per_item + per_pick(
        1.0,                       # no height multiplier: a trailer floor is one height
        cost.intercept,
        handle_var(weight, volume, cost.weight_coef, cost.volume_coef,
                   cost.weight_fn, cost.volume_fn),
        quantity,
    )
