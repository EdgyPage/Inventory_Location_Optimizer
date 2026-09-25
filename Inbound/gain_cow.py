"""gain_cow — how a VIRTUAL placement sees the aisle books without advancing them.

The inbound gain evaluator prices a placement by actually making it, in a pool opened over
the arm's own builder. A virtual placement must therefore never touch the LIVE bookkeeping,
and what "a copy" IS belongs to the DICT rather than to the arm reading it — two families
sharing a dict share its copier. So the shapes are declared here, once.

## Why this is its own module

It was ~200 lines inside a 1,410-line `gain.py` that is really four things: this, the owner
resolution, the evaluator, and the entries. The deletion test passes loudly — removing it
brings back a MEASURED 37.8x copy blow-up at every `_make_pool` — it simply is not the same
module as the evaluator.

## READ THE TABLES THROUGH THIS MODULE, never through a bare imported name

`Tests/unit/test_gain_cow_equivalence.py` REBINDS `AISLE_VIEWS` to sabotage the views, and
its own docstring calls that the saving throw: without it the file passes if the views are
quietly pointed back at the eager copiers. A reader that did `from Inbound.gain_cow import
AISLE_VIEWS` would bind the name at import and never see the rebinding — the sabotage would
stop sabotaging, silently, and the file would go on passing.

That is not hypothetical: `test_gain_bundle_labor_families.py` records it happening once
already, when `_make_pool` moved from `AISLE_COPIERS` to `AISLE_VIEWS`. So `_Evaluator` reads
`_cow.AISLE_VIEWS[n]`, and `gain.py` does not re-export either table.
"""
from __future__ import annotations

from collections import defaultdict

__all__ = ['AISLE_COPIERS', 'AISLE_VIEWS']


# ── the purity rule, stated once per aisle dict ───────────────────────────────────────
# A virtual placement may never advance the LIVE bookkeeping, so the pool adapter opens
# the arm's pool over COPIES.  What "a copy" IS belongs to the DICT, not to the arm that
# reads it -- two families sharing a dict share its copier -- so it is declared here,
# once, keyed by the manager attribute the driver hands over.
#
# The shapes are not interchangeable and the wrong one fails SILENTLY: `dict(d)` over
# `aisle_member_pos` hands the pool the live inner LISTS, and `_MinLaborPool.take`
# appends a column position to one of them -- no error, no symptom, and every later
# placement in the RUN priced against a warehouse that never happened.  So a name with no
# entry here is refused at bundle construction rather than copied by a guess.
#
# Every copy is a `defaultdict` with the factory `Inventory_Manager` declares, because the
# pools index and `+=` an aisle they have not seen (`_ads[aid] += fq`,
# `_amp[aid][idx].append(...)`) exactly as they do against the live dicts.


# ── copy-on-write, because the eager copy is ~40x larger than the pool needs ───────────
#
# MEASURED, on the real driver at 2,000 SKUs / 20 batches / one leaf, 5,534 pool opens:
#
#     live aisles in aisle_sku_sets  : 46
#     aisles a pool can TOUCH        : mean 1.22, max 3   (1:4726  2:412  3:396)
#     set entries copied per open    : mean 1,683
#     total set entries copied       : 9,313,598
#
# A pool's candidates are the free bins of ONE BinKey, which live in one or two aisles; the
# eager copy walks all forty-six.  The ratio is 37.8x and it is paid `entries x T x tiers`
# times -- 12.59 pool opens per placement, against a yard depth that production never takes
# above 6.  So this is the inbound evaluator's real cost, and it is NOT the O(T^2) candidate
# loop it was assumed to be.
#
# The classes below preserve the purity rule exactly: a virtual placement still never
# advances the live bookkeeping.  They only stop paying for aisles it never looks at.
#
# WHERE THE LAZINESS STOPS, deliberately.  `values()` and `items()` materialize EVERY aisle,
# because a lazy one would hand out the LIVE container and the caller could mutate it -- the
# precise failure the eager copy exists to prevent.  `_RankedAssignPool.__init__` builds
# `set().union(*aisle_idx_sets.values())`, so `rank_random` (its only phase-2 user) gets
# correctness and no win.  Every other ranked family reads by key and gets both.


class _CowView:
    """Copy-on-write over one of the manager's `{aisle: ...}` dicts.

    A virtual placement must never advance the live bookkeeping, and the original way to
    guarantee that was to hand the pool an EAGER copy of the whole dict.  The measurement in
    front of this block is why that stopped: a pool's candidates are the free bins of ONE
    BinKey, living in one or two aisles, while the copy walked all forty-six -- 9.3M set
    entries copied per run, at 12.59 pool opens per placement.

    A view pays for the aisles the pool actually touches instead.  Reads fall through to the
    live dict; the first access to an aisle whose value is MUTABLE materializes that one
    aisle into an overlay, and every write lands in the overlay.  The purity rule is
    unchanged -- it is the price that moved.

    # -- what a subclass owns, and what it does not --------------------------------------

    `__getitem__` ONLY.  How a miss materializes IS the difference between the shapes: a
    float needs no copy at all (it is immutable, so a read can fall straight through), a set
    needs its own set, and the minlabor shape needs a whole inner mapping.  The remaining
    eight methods never differed, and were three byte-identical copies until this base
    existed; `Tests/unit/test_gain_cow_protocol.py` asserts they stay one implementation and
    that `__getitem__` stays three.

    # -- where the laziness stops, deliberately -------------------------------------------

    `values()` and `items()` materialize EVERY aisle, because a lazy one would hand out the
    LIVE container and the caller could mutate it -- the precise failure the eager copy
    existed to prevent.  `_RankedAssignPool.__init__` used to build
    `set().union(*aisle_idx_sets.values())` through that path, so `rank_random` (its only
    phase-2 user) got correctness and no win; it now asks the view for `union()`, which
    `_CowSets` answers from the owner's counted inverse without touching a single aisle
    (ticket 02 of the phase-2 campaign).  Every other ranked family reads by key.

    Slotted, and the subclasses re-declare `__slots__ = ()` to stay that way: a view is
    opened per pool, and a stray attribute would be per-pool heap nobody frees.
    """

    __slots__ = ('_live', '_over', 'memo')

    def __init__(self, live):
        self._live = live
        self._over = {}
        #: A DRAIN-SCOPED memo for pure functions of the LIVE books, attached by the gain
        #: evaluator (`_Evaluator._make_pool`) and None everywhere else.  The live dicts do
        #: not move while a drain plans -- the purity rule, and the owner writes only at
        #: hand-off -- so a value computed from an aisle this pool has not written is the
        #: same value at every open of the drain.  A pool reads it only for such aisles.
        self.memo = None

    def __getitem__(self, k):
        raise NotImplementedError('a CoW view subclass owns how a miss materializes')

    def __setitem__(self, k, v):
        self._over[k] = v

    def get(self, k, default=None):
        """Read WITHOUT materializing -- which is the whole reason a pool ever calls it.

        The fall-through hands back the LIVE value for a key the overlay has not taken.
        That is read-only by contract: a caller that mutates what `get` returned writes
        into the manager's own dict.  Callers that intend to write use `__getitem__`.
        """
        o = self._over
        return o[k] if k in o else self._live.get(k, default)

    def seed_floats(self, keys) -> dict:
        """`{k: float(self.get(k, 0.0)) for k in keys}` in ONE Python frame.

        The pools seed their running per-aisle float books from a view at every open, over
        every live aisle -- ~1,400 keys at campaign scale, ~650,000 opens per arm.  Written
        as a comprehension over `self.get`, that is a Python call per key, and it was the
        single largest thing a view cost the pool prologue.  Reading the two backing dicts
        directly makes the whole seed one frame.

        `get`'s semantics, deliberately, not `__getitem__`'s: a float falls straight through
        without materializing, which is the whole point of `_CowFloats`.  The read-only
        contract on what `get` hands back is satisfied by construction here, because the
        seed COPIES each value into the pool's own dict.

        Overridden by no subclass -- the float shape is the only one a pool seeds this way,
        and a set or an inner mapping would need a copy per key rather than a `float()`.
        The caller (`Assignment_Functions._seed_floats`) duck-types it, because
        `Warehouse/placement/` may not import this package.
        """
        over, live = self._over, self._live
        if not over:
            return {k: float(live.get(k, 0.0)) for k in keys}
        return {k: float(over[k] if k in over else live.get(k, 0.0)) for k in keys}

    def __contains__(self, k):
        return k in self._over or k in self._live

    def __iter__(self):
        live = self._live
        for k in live:
            yield k
        for k in self._over:
            if k not in live:
                yield k

    def __len__(self):
        return len(self._live) + sum(1 for k in self._over if k not in self._live)

    def keys(self):
        return list(self)

    def items(self):
        return [(k, self[k]) for k in self]     # materializes -- see the class docstring

    def values(self):
        return [self[k] for k in self]          # materializes -- see the class docstring


class _CowFloats(_CowView):
    """`{aisle: float}` -- and floats need NO copy at all.

    A float is immutable, so a read falls straight through to the live dict; only a write
    needs an overlay.  `_ads[aid] += fq` is `__getitem__` then `__setitem__`, and the write
    lands in the overlay rather than on the manager's dict.

    A miss CREATES the entry at 0.0, matching `defaultdict(float)` -- the shape this
    replaces -- so `len()` and iteration order are unchanged.
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        if k in o:
            return o[k]
        v = self._live.get(k)
        if v is None and k not in self._live:
            o[k] = 0.0                      # defaultdict(float) creates on access
            return 0.0
        return v


class _CowSets(_CowView):
    """`{aisle: set}`.

    Unlike floats the value is MUTABLE and the caller does `d[aid].add(sku)` -- and
    `__getitem__` cannot tell that from the `sku in d[aid]` two lines above it.  So an
    access materializes that ONE aisle's set.  Bounded by the aisles a pool touches
    (measured 1.22), not by the warehouse (46).
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        got = o.get(k)
        if got is None:
            src = self._live.get(k)
            got = o[k] = set(src) if src is not None else set()
        return got

    def holding(self, keys, item) -> list:
        """`[item in self[k] for k in keys]` -- WITHOUT materializing a single aisle.

        rank_cartlabor's run boundary asks, for every live aisle, whether the aisle already
        holds the SKU (`_TravelVec.boundary`, the cart hinge).  Through `__getitem__` that
        question COPIED every aisle's set into the overlay -- ~1,400 set copies per boundary
        at campaign scale, the same blow-up this module exists to prevent, re-entered
        through a membership test: 34% of a 400k `plan_order` (cProfile of
        `.scratch/inbound-fullscale-perf/` S10).

        The answer is the same boolean for every key: an overlay aisle answers from the
        overlay, any other from the live set (read-only here), an aisle in neither holds
        nothing -- exactly what `__getitem__`'s materialized copy would have answered.  The
        copy it skips is unobservable to the pool: the live dict is not written while a
        virtual placement is open, so an aisle the ledger later writes materializes then,
        with the same contents it would have been copied with now.  (The LIVE-dict path --
        the put drain -- does not come here; its `defaultdict` inserts keys that
        `aisle_metrics` writes, so it keeps its own read.)"""
        o, live = self._over, self._live
        empty = ()
        return [item in (o[k] if k in o else live.get(k, empty)) for k in keys]

    def union(self):
        """Every idx in any aisle of this view -- what `set().union(*self.values())` returns,
        answered from the owner's counted inverse when the live dict is the owner's
        `_IdxSets`, so no aisle is materialized.  A live dict with no inverse (a loose dict
        in a test) gets the materializing answer, so the two are interchangeable by value.

        The overlay is reconciled exactly rather than assumed empty: an idx an overlay aisle
        holds that no live aisle does is EXTRA; an idx a live aisle held that every one of
        its holders has been overridden without it is GONE.  Both are empty at every pool
        open the evaluator makes today (`_make_pool` builds fresh views per open), and both
        cost O(overlay), never O(warehouse)."""
        live = self._live
        inv = getattr(live, 'inverse', None)
        if inv is None or not hasattr(inv, 'n_placed'):
            return set().union(*self.values())
        over = self._over
        if not over:
            return _PlacedUnion(inv)
        over_union = set().union(*over.values())
        extra = frozenset(i for i in over_union if not inv.get(i))
        gone = set()
        for aid, s in over.items():
            src = live.get(aid)
            if not src:
                continue
            for i in src:
                if i in s or i in over_union or i in gone:
                    continue
                if all(a in over for a in inv.get(i, ())):
                    gone.add(i)
        return _PlacedUnion(inv, extra, frozenset(gone))


class _PlacedUnion:
    """The set of matrix indices placed ANYWHERE, as a pool under the gain evaluator sees it.

    What `set().union(*aisle_idx_sets.values())` was, without the walk: the owner's inverse
    (`_PartnerAisles`, every idx held by at least one live aisle) plus what this view's
    overlay ADDS beyond the live warehouse, minus what it REMOVES from the last aisle
    holding it.  Answers everything `_delta_lift_from_row` asks of `_all_idx` -- truth,
    `len`, `in`, and iteration -- and nothing else.

    FROZEN BY THE PURITY RULE, not by copying.  A pool's `_all_idx` is frozen for the group
    by decision (later units rank against the pre-group union), and the old set was a copy.
    This object reads the LIVE inverse, so it is frozen only where the live books are never
    written during the pool's life -- which is exactly the copy-on-write view's contract: a
    virtual placement writes its overlay and never the owner.  The size is taken once, at
    construction, and it must be EXACT: `_delta_lift_from_row` folds the shorter side, and
    a wrong `len` would change which side is iterated and therefore the float.

    Iteration order is dict order plus the overlay's extras, not a set's hash order.  That
    can only reach the arithmetic when the placed set is SMALLER than a SKU's partner row
    (the side iterated is the shorter one) -- a warehouse holding fewer than a row's worth
    of indices, which neither a stocked toy run nor the campaign ever presents to a pool.
    The digest gate (`Tests/bench/run_digest.py`) is what says so, not this sentence.
    """

    __slots__ = ('_inv', '_extra', '_gone', '_n')

    def __init__(self, inv, extra=frozenset(), gone=frozenset()):
        self._inv, self._extra, self._gone = inv, extra, gone
        self._n = inv.n_placed + len(extra) - len(gone)

    def __len__(self):
        return self._n

    def __bool__(self):
        return self._n > 0

    def __contains__(self, idx):
        if idx in self._extra:
            return True
        if idx in self._gone:
            return False
        return bool(self._inv.get(idx))

    def __iter__(self):
        gone = self._gone
        for idx, held in self._inv.items():
            if held and idx not in gone:
                yield idx
        yield from self._extra


class _CowInner:
    """One aisle's `{sku_idx: [x_phys, ...]}` over the LIVE inner mapping: a read falls
    through to the live list (read-only by contract, like `_CowView.get`), and a write --
    `inner[idx].append(...)` -- copies THAT one list, never the aisle.

    WHY.  `_CowListsByKey` used to materialise an aisle's whole inner mapping (every SKU's
    list) the first time a pool touched it.  The min-labor pool touches every aisle it
    wins, every pool open: 768k materialisations for 71 s on a six-day coupled unit at
    campaign scale (ticket 03), copying hundreds of lists to append to one.

    ORDER IS THE CONTRACT.  `_demand_weighted_partner_centroid` folds `member_pos.items()`
    in iteration order, so this must iterate exactly as the materialised copy did: the
    live keys in live order (with the overlay's list substituted where one exists), then
    the keys the overlay created, in creation order -- which is what a copy that was
    filled from the live dict and then appended to would have held.  The live dict does
    not change during a pool's life (the purity rule), so the live half is stable.

    A missing key creates an empty list, as the `defaultdict(list)` it replaces did; there
    is no delete, because the drop path runs against the live book only.

    READS ARE C-SPEED OR THEY ARE A REGRESSION.  The first draft yielded `items()` from a
    Python generator, one `k in o` per member, and the centroid -- which folds every member
    of the winning aisle on every unit -- went from 85 s to 292 s on the same unit, three
    times what the whole-aisle copy had cost.  So: an aisle whose lists this pool never wrote
    hands back the LIVE dict's own `items()` (the overwhelming case: a winner is read before
    it is written), and an aisle it did write merges once into a plain dict -- live keys in
    live order, the overlay's list in place of the live one, created keys appended -- cached
    until the overlay gains a KEY (an append to an existing copied list is visible through
    the merge, since it holds the same list object).  A written aisle therefore pays one
    O(members) merge per new key, on its next read; the old view paid one per first touch.
    """

    __slots__ = ('_live', '_over', '_merged')

    def __init__(self, live):
        self._live = live if live is not None else {}
        self._over: dict = {}
        self._merged = None

    def __getitem__(self, k):
        o = self._over
        got = o.get(k)
        if got is None:
            src = self._live.get(k)
            got = o[k] = list(src) if src is not None else []
            self._merged = None            # a new key: the merged view is stale
        return got

    def _view(self) -> dict:
        """The merged mapping, in the materialised copy's order."""
        m = self._merged
        if m is None:
            m = self._merged = {**self._live, **self._over}
        return m

    def get(self, k, default=None):
        o = self._over
        return o[k] if k in o else self._live.get(k, default)

    def __contains__(self, k):
        return k in self._over or k in self._live

    def __iter__(self):
        return iter(self._live if not self._over else self._view())

    def __len__(self):
        return len(self._live) if not self._over else len(self._view())

    def keys(self):
        return (self._live if not self._over else self._view()).keys()

    def items(self):
        return (self._live if not self._over else self._view()).items()

    def values(self):
        return (self._live if not self._over else self._view()).values()

    def __delitem__(self, k):
        raise TypeError('a copy-on-write inner view has no delete: the drop path runs against '
                        'the live book, never a virtual placement')


class _CowListsByKey(_CowView):
    """`{aisle: {sku_idx: [x_phys, ...]}}` -- the minlabor shape.

    Two levels down, and the inner lists are appended to (`_amp[aid][idx].append(...)`), so
    a shallow copy would hand the pool the live inner list.  An access hands out a
    `_CowInner` over that one aisle, which copies only the list that is written to.
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        got = o.get(k)
        if got is None:
            got = o[k] = _CowInner(self._live.get(k))
        return got

    def untouched(self, k) -> bool:
        """Has this view NOT written aisle `k`'s lists?  Then every read of it is the live
        book's, and a value derived from it may come from the drain memo (`memo`)."""
        got = self._over.get(k)
        return got is None or not got._over


def _copy_of_sets(d):
    out = defaultdict(set)
    for a, v in d.items():
        out[a] = set(v)
    return out


def _copy_of_floats(d):
    return defaultdict(float, d)


def _copy_of_lists_by_key(d):
    """Two levels down: {aisle: {sku_idx: [x_phys, ...]}}, and the lists are appended to."""
    out = defaultdict(lambda: defaultdict(list))
    for a, inner in d.items():
        o = out[a]
        for k, xs in inner.items():
            o[k] = list(xs)
    return out


#: manager attribute -> the EAGER copy of it.  Kept as the frozen reference the copy-on-write
#: views are tested against (`Tests/unit/test_gain_cow_equivalence.py`), and as the fallback a
#: caller can ask for explicitly.  Production no longer opens pools over these -- see
#: `AISLE_VIEWS` below and the measurement in front of `_CowView`.
AISLE_COPIERS = {
    'aisle_sku_sets':      _copy_of_sets,
    'aisle_idx_sets':      _copy_of_sets,
    'aisle_demand_sum':    _copy_of_floats,
    'aisle_pick_load_sum': _copy_of_floats,
    'aisle_vol_sum':       _copy_of_floats,
    'aisle_member_pos':    _copy_of_lists_by_key,
}

#: manager attribute -> how a virtual placement's VIEW of it is made.  Same six names, same
#: shapes, same purity rule; the difference is that a view pays for the aisles the pool touches
#: instead of every aisle in the warehouse.
#:
#: The two tables must cover each other exactly.  A name that has a copier and no view would
#: silently fall back to the eager path and quietly cost 40x; a name with a view and no copier
#: would have no oracle to be tested against.  Asserted immediately below, at import.
#:
#: That check says the tables agree WITH EACH OTHER; it cannot say they cover every book a
#: placement policy writes, because `Inbound/` may not import the placement engine.  The
#: DRIVER makes that comparison instead -- `strategy_runner` checks this table against
#: `AisleLedger.POLICY_BOOKS` at import, and refuses to load if a writable book has no view.
AISLE_VIEWS = {
    'aisle_sku_sets':      _CowSets,
    'aisle_idx_sets':      _CowSets,
    'aisle_demand_sum':    _CowFloats,
    'aisle_pick_load_sum': _CowFloats,
    'aisle_vol_sum':       _CowFloats,
    'aisle_member_pos':    _CowListsByKey,
}

if set(AISLE_VIEWS) != set(AISLE_COPIERS):          # at import, not at the first drain
    raise RuntimeError(
        f'AISLE_VIEWS and AISLE_COPIERS name different dicts: '
        f'{sorted(set(AISLE_COPIERS) ^ set(AISLE_VIEWS))}. Every aisle dict needs both -- the '
        f'view is what production opens pools over, the copier is the oracle it is proven '
        f'against.')
