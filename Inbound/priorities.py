"""priorities — the dock's policy registries, on `put_policy`'s proven contract.

# ── the registries, and the split the standing yard made real ─────────────────────

GLOBAL ranks TRAILERS for the v1 drain-everything path, where one ranking serves both dock
moments.  The STANDING YARD (`YardTransit`) splits that decision in two, each with its own
registry and knob: YARD (`INBOUND_YARD_POLICY`) answers "a freed door goes to which
standing trailer"; DOCK (`INBOUND_DOCK_POLICY`) answers "the crew works which staged
trailer" — under door-team allocation that is a worker-ALLOCATION preference, decisive when
workers < staged trailers and graded otherwise.  Both additive, both seeded `'fifo'`; the
GLOBAL registry and its knob stay untouched and are simply unread in standing mode.  LOCAL
ranks a trailer's LOAD PALLETS — what a crew actually pulls; on a single-SKU pallet that
degenerates to the SKU lot — and is shared by both paths.

The contract is `put_policy`'s exactly: a policy is a PURE key function, HIGHER served
first, over (candidate, ctx) where `ctx` is FROZEN for the drain — computed once, reused
across every decision in it.  `ctx` is also where the warehouse-space signal arrives — the
reserved named view is now REAL: `ctx.space` carries the drain's frozen
`Inbound.space.SpaceView` whenever the standing yard runs (always on with the flag), None
otherwise; no signature change, and every seeded 'fifo' key ignores it.  A LOCAL policy may
reorder WORK but never pack composition — the pack plan is fixed per trailer.

There is deliberately NO `inherit` entry: at the dock there is no placement pool whose
precedence could stand in, so the default is plain `'fifo'`.

# ── the ordering bound ────────────────────────────────────────────────────────────

`bounded_order` is the dock's `k_cap` analog, shipped now by decision: the policy PROPOSES
an order, the bound limits how far it may depart from arrival order — "of the `bound`
longest-waiting trailers, take the best".  Denominated in TRAILERS; None = unbounded.  With
`'fifo'` (every seeded policy) any bound is inert, which is what keeps v1 byte-identical
while the interface is real.  The ONE bound covers the global ranking and both standing
rankings alike — a per-registry bound waits for an arm that needs them separate.
"""
from __future__ import annotations


class DockContext:
    """Everything a priority key may see, frozen once per drain.

    Keys must be pure functions of (candidate, ctx); anything that changes as work happens
    would be read stale by design — the same rule, for the same one-key-per-drain cost
    reason, as `put_policy`.
    """

    __slots__ = ('doors', 'free_doors', 'yard_depth', 'space')

    def __init__(self, doors: int, free_doors: int, yard_depth: int):
        self.doors = doors
        self.free_doors = free_doors
        self.yard_depth = yard_depth
        # The space arrival point (module docstring): the drain's frozen
        # `Inbound.space.SpaceView`, assigned at ctx-freeze by `_receive_standing` when
        # the standing yard runs; None everywhere else — the v1 path and every fifo key
        # never read it.
        self.space = None


def _fifo_trailer(trailer, ctx) -> float:
    """Strict arrival order: the longest-waiting trailer first."""
    return -float(trailer.seq)


def _fifo_standing(trailer, ctx) -> float:
    """Longest-STANDING first: earliest yard arrival wins.

    The charter's order — arrival stamp, `seq` as tiebreak — needs only the stamp here:
    both standing collections (the yard, the staged set) are maintained in (stamp, seq)
    order, and `bounded_order`'s sort is stable, so equal stamps fall back to `seq` for
    free.  A trailer with no stamp (no clock reached the drain — bare test managers)
    ranks oldest rather than crashing or silently sorting last: it has been standing
    since before anything the clock can see.
    """
    s = trailer.arrived_s
    return float('inf') if s is None else -float(s)


def _fifo_pallet(indexed_pallet, ctx) -> float:
    """Load order: the pallet loaded first is pulled first.  The candidate is
    (position_index, LoadPallet) so a key needs no back-pointer to its trailer."""
    return -float(indexed_pallet[0])


GLOBAL_POLICIES: dict = {'fifo': _fifo_trailer}
LOCAL_POLICIES: dict = {'fifo': _fifo_pallet}
#: The standing yard's split of the global decision (see the module docstring).  ADDITIVE:
#: nothing here changes what GLOBAL_POLICIES means to the v1 path.  The space-aware arms
#: (myopic, standing-demand forecasting) land here as entries, not as rewiring.
YARD_POLICIES: dict = {'fifo': _fifo_standing}
DOCK_POLICIES: dict = {'fifo': _fifo_standing}


def global_key(policy: str):
    if policy not in GLOBAL_POLICIES:
        raise KeyError(f'unknown global priority {policy!r}; known: {sorted(GLOBAL_POLICIES)}')
    return GLOBAL_POLICIES[policy]


def local_key(policy: str):
    if policy not in LOCAL_POLICIES:
        raise KeyError(f'unknown local priority {policy!r}; known: {sorted(LOCAL_POLICIES)}')
    return LOCAL_POLICIES[policy]


def yard_key(policy: str):
    if policy not in YARD_POLICIES:
        raise KeyError(f'unknown yard priority {policy!r}; known: {sorted(YARD_POLICIES)}')
    return YARD_POLICIES[policy]


def dock_key(policy: str):
    if policy not in DOCK_POLICIES:
        raise KeyError(f'unknown dock priority {policy!r}; known: {sorted(DOCK_POLICIES)}')
    return DOCK_POLICIES[policy]


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
