"""priorities — the dock's two policy registries, on `put_policy`'s proven contract.

# ── two levels, two registries ────────────────────────────────────────────────────

GLOBAL ranks TRAILERS, and one ranking serves BOTH dock moments: a freed door goes to the
top-ranked unstaged trailer, and the crew unloads the top-ranked staged one.  LOCAL ranks a
trailer's LOAD PALLETS — what a crew actually pulls; on a single-SKU pallet that degenerates
to the SKU lot.  A separate staging-vs-unload split is a later registry addition if a real
policy ever needs it (one adapter is a hypothetical seam).

The contract is `put_policy`'s exactly: a policy is a PURE key function, HIGHER served
first, over (candidate, ctx) where `ctx` is FROZEN for the drain — computed once, reused
across every decision in it.  `ctx` is also where the warehouse-space signal arrives when
the future feature builds it ("unloading that takes advantage of space in warehouse"): a
named view on the context, no signature change.  A LOCAL policy may reorder WORK but never
pack composition — the pack plan is fixed per trailer.

There is deliberately NO `inherit` entry: at the dock there is no placement pool whose
precedence could stand in, so the default is plain `'fifo'`.

# ── the ordering bound ────────────────────────────────────────────────────────────

`bounded_order` is the dock's `k_cap` analog, shipped now by decision: the policy PROPOSES
an order, the bound limits how far it may depart from arrival order — "of the `bound`
longest-waiting trailers, take the best".  Denominated in TRAILERS; None = unbounded.  With
`'fifo'` (v1's only policy) any bound is inert, which is what keeps v1 byte-identical while
the interface is real.
"""
from __future__ import annotations


class DockContext:
    """Everything a priority key may see, frozen once per drain.

    Keys must be pure functions of (candidate, ctx); anything that changes as work happens
    would be read stale by design — the same rule, for the same one-key-per-drain cost
    reason, as `put_policy`.
    """

    __slots__ = ('doors', 'free_doors', 'lot_depth')

    def __init__(self, doors: int, free_doors: int, lot_depth: int):
        self.doors = doors
        self.free_doors = free_doors
        self.lot_depth = lot_depth


def _fifo_trailer(trailer, ctx) -> float:
    """Strict arrival order: the longest-waiting trailer first."""
    return -float(trailer.seq)


def _fifo_pallet(indexed_pallet, ctx) -> float:
    """Load order: the pallet loaded first is pulled first.  The candidate is
    (position_index, LoadPallet) so a key needs no back-pointer to its trailer."""
    return -float(indexed_pallet[0])


GLOBAL_POLICIES: dict = {'fifo': _fifo_trailer}
LOCAL_POLICIES: dict = {'fifo': _fifo_pallet}


def global_key(policy: str):
    if policy not in GLOBAL_POLICIES:
        raise KeyError(f'unknown global priority {policy!r}; known: {sorted(GLOBAL_POLICIES)}')
    return GLOBAL_POLICIES[policy]


def local_key(policy: str):
    if policy not in LOCAL_POLICIES:
        raise KeyError(f'unknown local priority {policy!r}; known: {sorted(LOCAL_POLICIES)}')
    return LOCAL_POLICIES[policy]


def bounded_order(candidates: list, key, ctx, bound: int | None) -> list:
    """The policy's order, bounded: repeatedly take the best-keyed of the `bound`
    longest-waiting remaining candidates (arrival order = input order).  None = the
    policy's order stands unbounded.  Stable on key ties, like the put drain."""
    if not candidates:
        return []
    if bound is None or bound >= len(candidates):
        return sorted(candidates, key=lambda c: -key(c, ctx))
    remaining = list(candidates)
    out = []
    while remaining:
        window = remaining[:max(1, bound)]
        best = max(range(len(window)), key=lambda i: (key(window[i], ctx), -i))
        out.append(remaining.pop(best))
    return out
