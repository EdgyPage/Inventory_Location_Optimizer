"""put_rungs — the put-away fallback chain, as a sequence instead of a diff.

ADR-0003 fixes what happens when a queued unit finds no empty bin, and in what order:

    0. an EMPTY bin                    (`placement.place_one` — where placement is chosen)
    1. the SKU's OWN bins, fullest first, filled to capacity
    2. repack into a smaller size tier (pallet or fulfillment)
    3. singleton bins of the same order type (store only)
    4. pending — back on the queue, retried next batch, no expiry

All five were written inline in one 180-line `while waiting:` loop, each hand-maintaining
`_queued_sku_counts`, the budget charge and `waiting.appendleft(...)` with subtly different
bookkeeping.  There was no seam: **a new rung was an edit INSIDE the hottest and most
invariant-dense loop in the manager.**  The ADR itself says the rule "may become a knob
later, which is why the rework is recorded rather than hidden" — and that knob would have
cost exactly that edit.

The contrast was one file away.  `put_policy.py` answers the ADJACENT question — which
waiting item to work next — with a one-line interface, five adapters, a `key_for` resolver
that raises on an unknown name and a documented "what is NOT here yet".  Two sibling policy
questions; one had a real seam and the other had none.

## What is here and what is not

Here: the result type, the registry of rung names, and the resolver.  **Not** here: the rung
BODIES, which stay on `Inventory_Manager`.  They reach into `_index`, `_execute_placement`,
`_top_up_own_bins`, `_charge_repack` and `_queued_sku_counts` — that depth is why
`put_policy`'s adapters could leave the manager and these cannot, and pretending otherwise
would buy a thinner file at the cost of a wider interface.

## The bookkeeping the driver now expresses once

Every rung is handed the popped item and answers with what it did.  The driver reads the
answer and does the same three things for all of them:

    placed += result.bins                         # the budget counts PLACEMENTS
    waiting.extendleft(reversed(result.units))     # push back at the head, in order
    _queued_sku_counts[sku] += len(result.units) - 1

That last line is the one worth staring at.  The item was POPPED, so it stops counting as
one queued unit; `len(units)` go back in its place.  Every rung's hand-written delta was a
special case of it:

| rung | bins | units back | delta | was written as |
|---|---|---|---|---|
| empty bin | 1 | 0 | −1 | inside `_execute_placement` |
| own bins, absorbed | n | 0 | −1 | a hand-rolled pop-or-decrement |
| own bins, partial | n | 1 | 0 | "needs no delta (unlike the rescues, which split)" |
| repack | 0 | k | k−1 | `+ delta` where `delta = len(new_units) - 1` |
| singleton | 0 | k | k−1 | the same expression again |
| pending | 0 | 0 | — | the item is held, not consumed |

`counts_booked` is the one asymmetry, and it is declared rather than buried: the empty-bin
rung goes through `_execute_placement`, which drops the count itself because it is also
reached from places that never touched a queue.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ['RungResult', 'DECLINED', 'PUT_RUNGS', 'DEFAULT_PUT_CHAIN', 'chain_for']


@dataclass(frozen=True)
class RungResult:
    """What one rung did with the unit it was handed.  Falsy means it declined.

    `bins` are PLACEMENTS — trips a putter made, `bin_placement` rows written, and what the
    budget caps.  A unit absorbed into three of its own bins is three of them, which is why
    the budget cannot simply count popped items: a cap read that way is silently generous
    exactly when the warehouse is most fragmented.

    `units` go back on the head of the queue IN ORDER and take the chain from the top.  A
    repack's splits and a top-up's remainder are the same shape to the driver even though
    one is rework and the other is a part-filled trip.

    `counts_booked` says this rung already applied the `_queued_sku_counts` change itself.
    Only the empty-bin rung sets it, because its placement call is shared with callers that
    never had a queue item to drop.
    """

    bins: int = 0
    units: tuple = ()
    counts_booked: bool = False

    def __bool__(self) -> bool:
        """A rung that neither placed nor respawned anything DECLINED, and the chain walks on.

        Deliberately derived rather than a fourth field: a rung that reports work and also
        reports declining is not a state the chain has, and a flag would let one exist.
        """
        return bool(self.bins or self.units)


#: The one declined answer.  Shared because it is immutable and every rung returns it on
#: most units — the rescues are "expected to happen never" (ADR-0003).
DECLINED = RungResult()


#: rung name -> the `Inventory_Manager` method that implements it.  Names, not bound
#: methods, because the chain is declared at class scope and resolved per manager.
PUT_RUNGS: dict[str, str] = {
    'empty_bin': '_rung_empty_bin',
    'own_bins':  '_rung_own_bins',
    'repack':    '_rung_repack',
    'singleton': '_rung_singleton',
}

#: ADR-0003's order, and the default chain.  **This tuple IS the ADR's ruling**: empty bin
#: first, consolidate only when none fits, ahead of the repack and singleton rescues and
#: ahead of pending.
#:
#: Reordering it is not free, and the ADR says why: "the new-bin decision is where placement
#: optimisation happens: a restock that returned to its own bin would hand the arms nothing
#: to rank until a shelf was taken to zero." A chain that demotes `empty_bin` forfeits that,
#: silently — every arm would still run, and every comparison between them would be
#: measuring less.
DEFAULT_PUT_CHAIN: tuple[str, ...] = ('empty_bin', 'own_bins', 'repack', 'singleton')


def chain_for(names) -> tuple[str, ...]:
    """Resolve rung names to a chain, refusing an unknown one.

    Mirrors `put_policy.key_for`: a typo must be a refusal at build time, not a rung that
    silently never fires.  `None` is the default chain.
    """
    if names is None:
        return DEFAULT_PUT_CHAIN
    out = tuple(names)
    unknown = [n for n in out if n not in PUT_RUNGS]
    if unknown:
        raise ValueError(
            f'unknown put-away rung(s) {unknown}; known rungs are '
            f'{sorted(PUT_RUNGS)}. A chain is ADR-0003\'s order expressed as data — add '
            f'the rung to PUT_RUNGS with its method before naming it here.')
    if not out:
        raise ValueError(
            'an empty put-away chain would send every unit straight to pending, which is '
            'not "no policy" but "place nothing"; pass None for the default chain')
    return out
