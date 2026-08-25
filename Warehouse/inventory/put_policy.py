"""put_policy.py — how a put-away queue decides which waiting item to work next.

# ── two different questions ───────────────────────────────────────────────────────

A placement pool answers "which BIN for this unit". Until the pool inversion it also answered
"which unit first", by returning its assignments pre-sorted — and that conflation is what
this branch exists to undo, because put-away order is an operational decision and a scoring
function is not the thing that should be making it.

`PutQueueSpec.k_cap` narrows how far the pool may reach past the head of the queue. A put
POLICY is the other half: it replaces the pool's opinion with the floor's own. "Put the
heaviest away first" and "finish one SKU before starting the next" are real rules that no
placement objective expresses, and they belong to the queue, not to the assignment function.

The two compose. A policy proposes an order; `k_cap` then bounds how far that order may
depart from arrival order. `fifo` with any `k_cap` is still FIFO; `largest_first` with
`k_cap=3` is "of the three oldest, take the biggest".

# ── the contract ──────────────────────────────────────────────────────────────────

A policy is `(PutawayItem) -> comparable`, HIGHER served first, or None for `inherit` —
which means "no opinion of my own, use the placement pool's precedence", the pre-registry
behaviour and still the default. A tuple is fine and `sku_batched` uses one; the drain ranks
by a stable sort rather than by arithmetic on the key, so nothing requires a number.

Keys must be a pure function of the item and of state frozen for the batch. The drain
computes each key ONCE and reuses it across every window the item appears in, so a key that
changed as placements happened would be read stale — and it would also make the window's
cost quadratic, which is the reason the pool states a `sort_key` rather than sorting.

# ── what is NOT here yet ──────────────────────────────────────────────────────────

Anything that reads a clock. A "finish what fits before the whistle" rule needs `WorkDay`,
which does not exist yet; when it does it lands here as one more entry rather than as a
change to any of these.
"""
from __future__ import annotations

#: The default: no opinion of its own. The placement pool's precedence stands, bounded by
#: `k_cap`. Named rather than left as None so a spec can state it deliberately.
INHERIT = 'inherit'


def _fifo(item) -> float:
    """Strict arrival order, whatever the placement policy would prefer.

    Distinct from `k_cap=1`, which also produces arrival order: that constrains the POOL,
    this replaces it. With `fifo` the pool has no say even at a wide `k_cap`, which is what
    you want when the constraint is physical (no floor to re-sort on) rather than a
    tolerance you chose.
    """
    return -float(item.age)


def _lifo(item) -> float:
    """Newest first. Not an aspiration — it is what an unmanaged stack of pallets against a
    wall actually produces, and having it named makes the cost of that measurable."""
    return float(item.age)


def _largest_first(item) -> float:
    """Biggest quantity first: clear the bulk while the crew is fresh and the aisles are
    empty, on the theory that a big unit placed late has to thread a fuller warehouse."""
    return float(getattr(item.unit, 'quantity', 0) or 0)


def _sku_batched(item) -> float:
    """Finish one SKU before starting the next, oldest SKU first.

    A real floor practice — one trip, one item, many bins — and it happens to restore the
    SKU-run caches that a FIFO window otherwise destroys, because those caches key on
    `sku != last_sku` and only ever hit on adjacent same-SKU units. That is a side effect
    worth knowing about and NOT the reason to choose this policy: the caches stay correct
    either way, they just stop paying.

    The key is two-level: the negated age of the SKU's OLDEST waiting item, then the
    negated age of this item.  The second level is load-bearing -- with only the first, every
    unit of a SKU ties and their order falls back to whatever order the caller listed them
    in, which is arrival order in the drain and arbitrary anywhere else.  Requires the
    batch-scoped table below; the drain builds it once per queue per batch.
    """
    raise NotImplementedError('_sku_batched needs the per-batch SKU age table; '
                              'use key_for(policy, items) rather than calling it directly')


#: name -> per-item key function, HIGHER first. `INHERIT` maps to None: no opinion.
PUT_POLICIES: dict = {
    INHERIT: None,
    'fifo': _fifo,
    'lifo': _lifo,
    'largest_first': _largest_first,
    'sku_batched': _sku_batched,
}

#: Policies whose key depends on the whole waiting set rather than on one item, so they are
#: built by `key_for` instead of being read straight out of PUT_POLICIES.
_SET_SCOPED = frozenset({'sku_batched'})


def key_for(policy: str | None, items):
    """Resolve a policy name to a `(item) -> float` key over THIS batch's waiting items.

    Returns None for `inherit` (or None), meaning the caller should use the placement pool's
    own precedence. Raises on an unknown name rather than silently inheriting: a typo in a
    swept configuration would otherwise produce a run that looks like a legitimate arm.
    """
    if policy is None or policy == INHERIT:
        return None
    if policy not in PUT_POLICIES:
        raise KeyError(f'unknown put policy {policy!r}; known: {sorted(PUT_POLICIES)}')
    if policy not in _SET_SCOPED:
        return PUT_POLICIES[policy]

    # sku_batched: every unit of a SKU sorts at its oldest member's position.
    oldest: dict = {}
    for it in items:
        sku = it.unit.order.sku
        a = it.age
        if sku not in oldest or a < oldest[sku]:
            oldest[sku] = a
    return lambda it: (-float(oldest[it.unit.order.sku]), -float(it.age))
