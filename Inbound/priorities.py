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

The contract is `put_policy`'s, generalized ONE step ("Define the inbound objective",
decision 4): an entry is EITHER a pure key function — HIGHER served first, over
(candidate, ctx), the degenerate case every seeded `'fifo'` entry rides — OR an ORDERING
function `(candidates, ctx) -> ordered list`, tagged `@ordering`, for policies whose
whole proposal is the value (a gain plan that virtually consumes space as it picks has no
per-candidate key).  `bounded_order` resolves either kind, so the registries, the
accessors, `transit.py` and the manager are all kind-blind.  Both kinds are PURE over a
`ctx` FROZEN for the drain — computed once, reused across every decision in it; an entry
mutates no manager state (an ordering entry is handed a COPY of its candidates) and
consumes no RNG.  `ctx` is also where the warehouse-space signal arrives — the
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

With an ORDERING entry the bound composes BOUND-FIRST — bound the candidate set, then
order: the entry is called ONCE per drain, on the `bound` longest-waiting candidates
only, and the remainder follows its proposal in arrival order.  Never re-run per pick — a
plan is one call, however deep the drain consumes it.  Inert exactly when the proposal is
arrival order, the same degenerate case that keeps `'fifo'` keys inert.
"""
from __future__ import annotations


class DockContext:
    """Everything a priority key may see, frozen once per drain.

    Keys must be pure functions of (candidate, ctx); anything that changes as work happens
    would be read stale by design — the same rule, for the same one-key-per-drain cost
    reason, as `put_policy`.
    """

    __slots__ = ('doors', 'free_doors', 'yard_depth', 'space', 'gain')

    def __init__(self, doors: int, free_doors: int, yard_depth: int):
        self.doors = doors
        self.free_doors = free_doors
        self.yard_depth = yard_depth
        # The space arrival point (module docstring): the drain's frozen
        # `Inbound.space.SpaceView`, assigned at ctx-freeze by `_receive_standing` when
        # the standing yard runs; None everywhere else — the v1 path and every fifo key
        # never read it.
        self.space = None
        # The gain arms' machinery: the driver-injected `Inbound.gain.GainBundle`,
        # assigned at ctx-freeze by `YardTransit.freeze_ctx` when a gain policy was
        # named; None everywhere else — the seeded keys never read it, and a gain
        # entry finding None raises rather than quietly ranking as fifo.
        self.gain = None


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


def _lifo_standing(trailer, ctx) -> float:
    """Newest-STANDING first — the adversarial control ("Name the policy arms", 05):
    anchors the fee axis's bad end and null-checks whether trailer ordering moves
    labor at all.  The exact sign-flip of `_fifo_standing` on stamped trailers; a
    stampless trailer (no clock reached the drain) is the OLDEST standing, so here it
    ranks last — symmetric with fifo ranking it first.  Equal stamps keep arrival
    order (the stable sort), which only ever matters to zero-lead test rigs."""
    s = trailer.arrived_s
    return float('-inf') if s is None else float(s)


def _fifo_pallet(indexed_pallet, ctx) -> float:
    """Load order: the pallet loaded first is pulled first.  The candidate is
    (position_index, LoadPallet) so a key needs no back-pointer to its trailer."""
    return -float(indexed_pallet[0])


def ordering(fn):
    """Tag `fn` as an ORDERING entry: `(candidates, ctx) -> ordered list`.

    The registries hold both kinds; this attribute — probed via getattr, the `STANDING`
    idiom — is how `bounded_order` tells a whole-order proposal from a per-candidate
    key.  The return must be a PERMUTATION of the candidates handed in (the same
    objects): `bounded_order` raises on anything else, because a silently dropped
    trailer would stand in the yard forever and a duplicated one would stage twice —
    neither with an error.
    """
    fn.ORDERING = True
    return fn


GLOBAL_POLICIES: dict = {'fifo': _fifo_trailer}
LOCAL_POLICIES: dict = {'fifo': _fifo_pallet}
#: The standing yard's split of the global decision (see the module docstring).  ADDITIVE:
#: nothing here changes what GLOBAL_POLICIES means to the v1 path.  The space-aware arms
#: land here as entries — pure keys or `@ordering` functions alike — not as rewiring:
#: `lifo` is seeded below; the gain family (`gain_myopic` / `gain_forecast` /
#: `gain_gated`) registers itself from `Inbound/gain.py` at package import.
YARD_POLICIES: dict = {'fifo': _fifo_standing, 'lifo': _lifo_standing}
DOCK_POLICIES: dict = {'fifo': _fifo_standing, 'lifo': _lifo_standing}


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


def bounded_order(candidates: list, entry, ctx, bound: int | None) -> list:
    """The entry's order, bounded.  A KEY entry: repeatedly take the best-keyed of the
    `bound` longest-waiting remaining candidates (arrival order = input order) — stable
    on key ties, like the put drain.  An `@ordering` entry: bound FIRST, then order —
    one call, on a copy of the `bound` longest-waiting candidates, the remainder
    following in arrival order.  None = the entry's order stands unbounded, either
    kind."""
    if not candidates:
        return []
    if getattr(entry, 'ORDERING', False):
        cut = len(candidates) if bound is None else max(1, bound)
        window, rest = candidates[:cut], candidates[cut:]
        out = list(entry(list(window), ctx))
        if sorted(map(id, out)) != sorted(map(id, window)):
            name = getattr(entry, '__name__', repr(entry))
            detail = (f'returned {len(out)} of {len(window)}'
                      if len(out) != len(window) else
                      f'{len(out)} returned, but duplicated or foreign objects stand in')
            raise ValueError(
                f'ordering entry {name!r} must return a permutation of its candidates '
                f'({detail}) — a dropped trailer stands in the yard forever and a '
                f'duplicated one stages twice, neither with an error')
        return out + rest
    if bound is None or bound >= len(candidates):
        return sorted(candidates, key=lambda c: -entry(c, ctx))
    remaining = list(candidates)
    out = []
    while remaining:
        window = remaining[:max(1, bound)]
        best = max(range(len(window)), key=lambda i: (entry(window[i], ctx), -i))
        out.append(remaining.pop(best))
    return out
