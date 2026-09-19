"""frozen_tier.py — one tier's candidates sorted ONCE per drain, and the per-open overlay
that lets a placement pool open over them without rebuilding anything.

THE COST THIS REMOVES (measured 2026-09-19 on the campaign catalogue, `rank_random` under
`gain_myopic`, 200k SKUs, after the placed-union fix): a priced drain opened 7,384 pools
over a tier of ~4,000 empty bins and seated ~12 units from each, and every open rebuilt the
tier's whole ranked structure — the travel-cost map, the per-aisle (per-bracket) D-sorted
buckets, the aisle heads — from the flat candidate list: 54 s of a 72 s drain, 300 units of
construction per unit placed.  The inputs to that structure do not change inside a drain:
the gain evaluator freezes its space view at the drain epoch, so a tier's empties are the
same list at every one of the T(T+1) x K opens, and a bin's travel cost is geometry.  The
only thing that differs between two opens of one tier is WHICH bins the current virtual
placement has already taken.

So: `FrozenTier` sorts a tier once (per drain, per key — the evaluator owns it, which is
the scope object memory `a-cache-needs-a-scope-object` asks for), and `TierSlice` is what a
pool opens over: the frozen order plus one set of excluded bin ids.  A pool's per-aisle
bucket becomes a `_Cursor` — a position into the frozen order that skips excluded bins
lazily — so an open costs O(aisles) plus the skips, never O(bins).

BYTE-IDENTICAL BY CONSTRUCTION, and each clause below is a tie-break the pools depend on:

  * Within a bucket the order is by D with ties in first-appearance order.  The eager
    pools get that from a STABLE sort of the appearance-ordered list; the frozen bucket is
    the same stable sort of the same list, and filtering a stable-sorted list is the same
    as stable-sorting the filtered list.  A cursor that skips excluded entries therefore
    yields exactly the sequence the eager bucket would have popped.
  * Across aisles (and across brackets inside an aisle) the pools' dicts are in FIRST-
    APPEARANCE order of the FILTERED candidates, and that order is a tie-break in every
    one of them (`_rank`, `next(iter(head_bin))`, the strict `<` over brackets).  It is not
    the first-appearance order of the unfiltered list: excluding an aisle's first bin can
    move that aisle behind another.  `TierSlice` recomputes it per open from each
    aisle's/bucket's first non-excluded appearance index — O(aisles + skips) — and drops an
    aisle or bracket whose every bin is excluded, exactly as the eager build never creates
    a key for it.
  * D is the expression `_D_map` evaluates, `x_pace * b.x_phys + y_pace * b.y_phys`, on the
    same operands; the height multiplier is the same step function.  Same bits.
  * `reverse` sorts (tmax) are stable in Python, so a descending stable sort is NOT the
    reverse of the ascending one; the tier keeps both, computed on demand.

The pools accept a `TierSlice` where they accept a candidate list; handed a list they build
exactly as before, so the wave path (one open per wave per key, and the seam every
placement oracle pins) is untouched byte for byte.  Only the gain evaluator opens over
slices (`Inbound/gain.py`, through the bundle's `freeze_tier`).
"""
from __future__ import annotations

import heapq

from Warehouse.kernel.cost_model import height_multiplier

__all__ = ['FrozenTier', 'TierSlice', 'HeapBucket']


class FrozenTier:
    """One tier's candidates in appearance order, with every per-bucket order sorted once.

    `cands` is the tier's candidate list exactly as the evaluator would have handed the
    pool BEFORE excluding anything (the frozen empties, plus the predicted tier when the
    deferred side is being priced), in that order — the order is part of the result.
    """

    __slots__ = ('bins', 'ids', 'D', 'aisle', 'D_by_id', 'x_pace', 'y_pace', 'brackets',
                 '_aisle_appear', '_bucket_appear', '_aisle_asc', '_aisle_desc', '_bucket_asc')

    def __init__(self, cands, x_pace: float, y_pace: float, brackets=()):
        bins = list(cands)
        self.bins = bins
        self.ids = [id(b) for b in bins]
        # THE `_D_map` EXPRESSION, verbatim: same operand order, same float.
        self.D = [x_pace * b.x_phys + y_pace * b.y_phys for b in bins]
        self.aisle = [b.location[0] for b in bins]
        self.D_by_id = dict(zip(self.ids, self.D))
        self.x_pace, self.y_pace, self.brackets = x_pace, y_pace, tuple(brackets)
        aisle_appear: dict = {}
        bucket_appear: dict = {}
        for i, b in enumerate(bins):
            aid = self.aisle[i]
            aisle_appear.setdefault(aid, []).append(i)
            bucket_appear.setdefault((aid, height_multiplier(self.brackets, b.y_phys)),
                                     []).append(i)
        self._aisle_appear = aisle_appear
        self._bucket_appear = bucket_appear
        D = self.D
        # Stable ascending sorts of the appearance-ordered index lists: the eager pools'
        # `lst.sort(key=D_of)` over the same bins in the same order.
        self._aisle_asc = {aid: sorted(lst, key=D.__getitem__) for aid, lst in aisle_appear.items()}
        self._bucket_asc = {k: sorted(lst, key=D.__getitem__) for k, lst in bucket_appear.items()}
        self._aisle_desc = None            # tmax only; a stable DESCENDING sort, on demand

    def __len__(self) -> int:
        return len(self.bins)

    def aisle_order(self, aid: int, reverse: bool = False) -> list:
        """The aisle's bin indices in the pool's bucket order (ascending D, or the stable
        descending sort when `reverse`)."""
        if not reverse:
            return self._aisle_asc[aid]
        if self._aisle_desc is None:
            D = self.D
            self._aisle_desc = {a: sorted(lst, key=D.__getitem__, reverse=True)
                                for a, lst in self._aisle_appear.items()}
        return self._aisle_desc[aid]

    def slice(self, excluded) -> 'TierSlice':
        """The tier minus `excluded` (a set of bin ids), for one pool open."""
        return TierSlice(self, excluded)


class TierSlice:
    """A `FrozenTier` minus one set of excluded bin ids — what a pool opens over.

    Cheap to make (two references) and cheap to read: the first-appearance orders are
    computed when a pool asks for its buckets, from each list's first non-excluded index.
    """

    __slots__ = ('tier', 'excluded')

    def __init__(self, tier: FrozenTier, excluded):
        self.tier = tier
        self.excluded = excluded

    # -- the first-appearance order under exclusion ---------------------------------------

    def _first_live(self, appear: list):
        ids, excl = self.tier.ids, self.excluded
        for i in appear:
            if ids[i] not in excl:
                return i
        return None

    def __bool__(self) -> bool:
        """Does ANY candidate survive the exclusion?  The eager path's `if not live`."""
        ids, excl = self.tier.ids, self.excluded
        for i in ids:
            if i not in excl:
                return True
        return False

    def aisles(self, reverse: bool = False) -> dict:
        """`{aisle: _Cursor}` in first-appearance order of the surviving candidates, one
        cursor per aisle over its D-sorted bins — `_RankedAssignPool`'s `by_aisle`."""
        tier = self.tier
        order = []
        for aid, appear in tier._aisle_appear.items():
            first = self._first_live(appear)
            if first is not None:
                order.append((first, aid))
        order.sort()
        excl = self.excluded
        return {aid: _Cursor(tier, tier.aisle_order(aid, reverse), excl) for _f, aid in order}

    def aisle_buckets(self) -> dict:
        """`{aisle: {height_mult: _Cursor}}`, aisles and brackets each in first-appearance
        order of the surviving candidates — the travel-balanced and min-labor pools'
        `by_aisle`."""
        tier = self.tier
        per_aisle: dict = {}
        for (aid, m), appear in tier._bucket_appear.items():
            first = self._first_live(appear)
            if first is not None:
                per_aisle.setdefault(aid, []).append((first, m))
        excl = self.excluded
        out: dict = {}
        for aid, lst in sorted(per_aisle.items(), key=lambda kv: min(f for f, _m in kv[1])):
            lst.sort()
            out[aid] = {m: _Cursor(tier, tier._bucket_asc[(aid, m)], excl) for _f, m in lst}
        return out


class _Cursor:
    """One bucket of a slice: a window into a frozen D-sorted index list that skips
    excluded bins lazily.  Speaks the deque protocol the pools read (`dq[0]`, `dq[-1]`,
    `popleft()`, `pop()`, truth, `len`) and the bucket protocol (`top()`, `pop_top()`), so
    a pool's `take` is the same code over a slice as over its own deque or heap.
    """

    __slots__ = ('_tier', '_order', '_excl', '_lo', '_hi', 'head')

    def __init__(self, tier: FrozenTier, order: list, excluded):
        self._tier, self._order, self._excl = tier, order, excluded
        self._lo, self._hi = 0, len(order)
        #: `(D, bin)` of the head, or None when empty -- a PLAIN ATTRIBUTE, settled at
        #: construction and after every pop, because the travel-balanced pool's run-boundary
        #: rebuild reads every bucket's head of every aisle for every new SKU (29 M reads on
        #: a six-day coupled unit at campaign scale), and a method call there cost as much
        #: as the tier rebuild this overlay removed.  The exclusion set is fixed for the
        #: pool's life (the evaluator's taken set moves only between placements), so a
        #: settled head stays settled until this cursor's own pop.
        self.head = None
        self._settle_lo()

    def _settle_lo(self) -> None:
        ids, order, excl = self._tier.ids, self._order, self._excl
        lo, hi = self._lo, self._hi
        while lo < hi and ids[order[lo]] in excl:
            lo += 1
        self._lo = lo
        if lo < hi:
            i = order[lo]
            self.head = (self._tier.D[i], self._tier.bins[i])
        else:
            self.head = None

    def _settle_hi(self) -> None:
        ids, order, excl = self._tier.ids, self._order, self._excl
        lo, hi = self._lo, self._hi
        while hi > lo and ids[order[hi - 1]] in excl:
            hi -= 1
        self._hi = hi
        if hi <= lo:
            self.head = None

    def __bool__(self) -> bool:
        return self.head is not None

    def __len__(self) -> int:
        ids, order, excl = self._tier.ids, self._order, self._excl
        return sum(1 for i in order[self._lo:self._hi] if ids[i] not in excl)

    def __getitem__(self, i):
        if i == 0:
            if self.head is None:
                raise IndexError('cursor is empty')
            return self.head[1]
        if i == -1:
            self._settle_hi()
            if self._lo >= self._hi:
                raise IndexError('cursor is empty')
            return self._tier.bins[self._order[self._hi - 1]]
        raise IndexError('a cursor exposes only its two ends')

    def popleft(self) -> None:
        if self.head is None:
            raise IndexError('pop from an empty cursor')
        self._lo += 1
        self._settle_lo()

    def pop(self) -> None:
        self._settle_hi()
        if self._lo >= self._hi:
            raise IndexError('pop from an empty cursor')
        self._hi -= 1
        if self._hi <= self._lo:
            self.head = None

    # -- the bucket protocol (travel-balanced pool): `head` is the attribute above ---------

    def pop_top(self) -> None:
        self.popleft()


class HeapBucket:
    """The eager travel-balanced bucket: a heap of `(D, seq, bin)`, with the same `head`
    attribute and `pop_top` a `_Cursor` has, so `_aisle_best` and `take` are one code path.
    `head` is `(D, bin)` -- the heap entry without its seq -- or None when empty."""

    __slots__ = ('_h', 'head')

    def __init__(self, entries: list):
        self._h = entries                  # already heapified by the pool
        self.head = (entries[0][0], entries[0][2]) if entries else None

    def __bool__(self) -> bool:
        return self.head is not None

    def __len__(self) -> int:
        return len(self._h)

    def pop_top(self) -> None:
        h = self._h
        heapq.heappop(h)
        self.head = (h[0][0], h[0][2]) if h else None
