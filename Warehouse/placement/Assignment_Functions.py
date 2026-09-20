"""Assignment_Functions.py -- placement (assignment) functions.

Separated from Inventory_Management so the placement POLICIES the simulation
compares live in one module.  A strategy bundles these builders into one
``Placement`` (per-unit ``place_one`` + optional ranked ``place_wave``) and hands it
to the manager.  The shared constants/types they need (BinKey, Placement, _SIZE_RANKS,
...) stay in Inventory_Management and are imported here (one-way -- no import cycle).
"""
from __future__ import annotations

import bisect
import math
import random
import heapq
from collections import deque
from typing import Any

from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.kernel.cost_model import (
    SpeedProfile, height_multiplier, per_pick, sec_per_inch)
from Warehouse.inventory.Inventory_Management import (
    _SIZE_RANKS, _SIZES_DESCENDING, BinKey, tier_ranks_for,
    AssignmentFn, RankedAssignmentFn, Placement, _wp_for,
)
from Warehouse.inventory.aisle_ledger import AisleLedger
from Warehouse.placement.frozen_tier import FrozenTier, HeapBucket, TierSlice


# ── sorted-by-pref placement helpers (map / cluster_map fast path) ─────────────
# `mgr._bin_pref[id(b)]` (per-bin preferred score) and `mgr._map_target[sku]` are
# CONSTANT during a reorder wave, so the map family's "free bin whose pref is closest
# to target" query becomes an O(log B) bisect on a per-wave sorted snapshot instead of
# an O(B) scan per unit.

def _closest_abs(prefs: list[float], target: float) -> float:
    """min |p - target| over the ascending `prefs` via bisect neighbours (O(log n)).
    Returns the identical value as `min(abs(p - target) for p in prefs)`; +inf if empty."""
    if not prefs:
        return math.inf
    i = bisect.bisect_left(prefs, target)
    best = math.inf
    if i < len(prefs):
        best = prefs[i] - target
    if i > 0:
        best = min(best, target - prefs[i - 1])
    return best



class _AislePrefIndex:
    """Every live aisle's bin prefs, MERGED once and maintained as bins are consumed.

    Answers the cold-start tie-break -- "the live aisle whose pref is closest to `target`" --
    in O(log N + k) against O(A) for the per-aisle `min(tied, key=_closest_abs(...))` it
    replaces, where k is the number of entries at the exact minimal gap (1 or 2 in practice).

    THE TIE ORDER IS THE WHOLE DIFFICULTY, and it is not the obvious one. The scan it replaces
    is `min(tied, key=...)`, and `min` returns the FIRST element achieving the minimum -- so
    among aisles whose closest pref is equally distant, the winner is the EARLIEST IN
    `by_aisle` ITERATION ORDER, not the lowest pref and not the lowest aisle id. `rank` carries
    that order (fixed when the group is built) and the walk below breaks ties on it.

    Consumption uses the same alive-chain trick as `_PrefPool`: a taken bin is spliced out of
    both directions so no tombstone is ever re-scanned. It is kept in multiset-sync with
    `prefs_by_aisle` by the one caller that removes, which is `_place`.
    """

    __slots__ = ('_pref', '_rank', '_aid', '_alive', '_nxt', '_prv', '_n')

    def __init__(self, by_aisle, prefs_by_aisle):
        rank = {aid: i for i, aid in enumerate(by_aisle)}     # by_aisle ITERATION order
        keyed = sorted(((p, rank[aid], aid)
                        for aid, lst in by_aisle.items() if lst
                        for p in prefs_by_aisle[aid]),
                       key=lambda k: (k[0], k[1]))
        self._pref = [k[0] for k in keyed]
        self._rank = [k[1] for k in keyed]
        self._aid = [k[2] for k in keyed]
        n = self._n = len(keyed)
        self._alive = [True] * n
        self._nxt = list(range(n + 1))
        self._prv = list(range(-1, n))

    def _find_nxt(self, i: int) -> int:
        nxt, n = self._nxt, self._n
        root = i
        while root < n and not self._alive[root]:
            root = nxt[root]
        while i != root:
            i, nxt[i] = nxt[i], root
        return root

    def _find_prv(self, i: int) -> int:
        prv = self._prv
        root = i
        while root >= 0 and not self._alive[root]:
            root = prv[root + 1]
        while i != root and i >= 0:
            i, prv[i + 1] = prv[i + 1], root
        return root

    def remove(self, aid: int, p: float) -> None:
        """Drop ONE entry for `(aid, p)` -- the bin `_place` just took."""
        i = bisect.bisect_left(self._pref, p)
        while i < self._n and self._pref[i] == p:
            if self._alive[i] and self._aid[i] == aid:
                self._alive[i] = False
                self._nxt[i] = i + 1
                self._prv[i + 1] = i - 1
                return
            i += 1

    def closest(self, target):
        """The aisle with the smallest `|pref - target|`; ties to the earliest `by_aisle` rank.

        `target is None` means the scan it replaces is `min(tied, key=prefs[a][0])` -- the aisle
        holding the globally smallest pref -- which is the first alive entry.
        """
        if target is None:
            i = self._find_nxt(0)
            return self._aid[i] if i < self._n else None

        p = bisect.bisect_left(self._pref, target)
        r = self._find_nxt(p)
        l = self._find_prv(p - 1)
        best_gap = None
        best_rank = best_aid = None
        # Walk outward. Each side is visited while it can still TIE the best gap, because a tie
        # can be won on rank by an entry further out in pref order.
        while True:
            cand = None
            if r < self._n and l >= 0:
                gr, gl = self._pref[r] - target, target - self._pref[l]
                cand = r if gr < gl else l
            elif r < self._n:
                cand = r
            elif l >= 0:
                cand = l
            if cand is None:
                break
            gap = abs(self._pref[cand] - target)
            if best_gap is not None and gap > best_gap:
                break
            if best_gap is None or gap < best_gap:
                best_gap, best_rank, best_aid = gap, self._rank[cand], self._aid[cand]
            elif self._rank[cand] < best_rank:
                best_rank, best_aid = self._rank[cand], self._aid[cand]
            if cand == r:
                r = self._find_nxt(r + 1)
            else:
                l = self._find_prv(l - 1)
        return best_aid


class _PrefPool:
    """A wave-local pool of candidate bins sorted by `pref`, with O(log) closest-to-target
    queries and O(α) consumption (a bin taken by one unit is invisible to later queries).

    Sort key is geometry-derived and process-reproducible: (pref, aisle_id, x_phys, y_phys)
    — never id(bin), whose value is not reproducible across processes.  Consumption uses a
    union-find "next/prev alive" so no tombstones are ever re-scanned.
    """

    __slots__ = ('prefs', 'bins', '_nxt', '_prv', '_alive')

    def __init__(self, bins, pref):
        keyed = sorted(
            ((pref.get(id(b), 0.0), b.location[0], b.x_phys, b.y_phys, b) for b in bins),
            key=lambda k: k[:4])
        self.prefs = [k[0] for k in keyed]
        self.bins  = [k[4] for k in keyed]
        n = len(keyed)
        self._nxt   = list(range(n + 1))   # _nxt[i] = first alive index >= i (sentinel n)
        self._prv   = list(range(-1, n))   # _prv[i+1] = last alive index <= i (sentinel -1)
        self._alive = [True] * n

    def __len__(self):
        return sum(self._alive)

    def _find_nxt(self, i: int) -> int:
        nxt = self._nxt
        root = i
        while root < len(self._alive) and not self._alive[root]:
            root = nxt[root]
        while i != root:                    # path-compress
            nxt_i = nxt[i]
            nxt[i] = root
            i = nxt_i
        return root

    def _find_prv(self, i: int) -> int:
        prv = self._prv
        root = i
        while root >= 0 and not self._alive[root]:
            root = prv[root + 1]
        while i != root and i >= 0:          # path-compress (prv indexed by i+1)
            nxt_i = prv[i + 1]
            prv[i + 1] = root
            i = nxt_i
        return root

    def _take(self, i: int):
        """Mark index i dead, splice it out of both alive-chains, return its bin."""
        self._alive[i] = False
        self._nxt[i] = i + 1                 # subsequent _find_nxt(i) skips forward
        self._prv[i + 1] = i - 1             # subsequent _find_prv(i) skips backward
        return self.bins[i]

    def take_closest(self, target: float):
        """Take & return the alive bin with min |pref - target| (tie -> lower pref); None if empty."""
        p = bisect.bisect_left(self.prefs, target)
        r = self._find_nxt(p)
        l = self._find_prv(p - 1)
        cand = None
        if l >= 0:
            cand = l
        if r < len(self._alive):
            if cand is None or (target - self.prefs[l]) > (self.prefs[r] - target):
                cand = r                     # strictly-nearer right; tie keeps left (lower pref)
        return self._take(cand) if cand is not None else None

    def take_ge(self, target: float):
        """Take the alive bin with the smallest pref >= target (map_rank cap); if none qualify,
        fall back to the alive bin with the largest pref (least-prime).  None if empty."""
        p = bisect.bisect_left(self.prefs, target)
        r = self._find_nxt(p)
        if r < len(self._alive):
            return self._take(r)
        l = self._find_prv(len(self._alive) - 1)
        return self._take(l) if l >= 0 else None

    def take_min(self):
        r = self._find_nxt(0)
        return self._take(r) if r < len(self._alive) else None

    def take_max(self):
        l = self._find_prv(len(self._alive) - 1)
        return self._take(l) if l >= 0 else None


# ── affinity CSR-row hoist (cohesion fast path) ───────────────────────────────
# `_demand_weighted_delta_lift` re-slices the SKU's CSR row on every aisle.  Build the
# row ONCE per unit as a plain dict and intersect with each aisle's idx-set in pure
# Python — same value, no per-aisle numpy element iteration.

def _affinity_row(affinity, sku):
    """{partner_idx: lift} for `sku`, or None when the SKU has no stored partners.
    One numpy CSR slice per call (hoist to once per unit, then reuse across aisles)."""
    m = affinity._matrix
    if m is None or sku not in affinity._sku_to_idx:
        return None
    i = affinity._sku_to_idx[sku]
    start = int(m.indptr[i])
    end   = int(m.indptr[i + 1])
    if start == end:
        return None
    return {int(ci): float(d) for ci, d in zip(m.indices[start:end], m.data[start:end])}


def _delta_lift_from_row(row, member_idx_set, freq_by_idx) -> float:
    """Σ (lift - 1)·f_i over partners i in `member_idx_set`, from a pre-built `row`
    ({idx: lift}).  Identical value to _demand_weighted_delta_lift, no CSR slice."""
    if not row or not member_idx_set:
        return 0.0
    # Iterate the smaller side; row (nnz partners) is usually << aisle members.
    if len(row) <= len(member_idx_set):
        return sum((lift - 1.0) * freq_by_idx.get(ci, 0.0)
                   for ci, lift in row.items() if ci in member_idx_set)
    return sum((row[ci] - 1.0) * freq_by_idx.get(ci, 0.0)
               for ci in member_idx_set if ci in row)


# ── load-aware assignment functions ───────────────────────────────────────────

def _placed_union(aisle_idx_sets):
    """Every matrix index placed anywhere, for a pool's frozen `_all_idx`.

    Handed the owner's dict, this is the union it always was -- built once per open, a
    real set, byte-identical to the wave path's history.  Handed the gain evaluator's
    copy-on-write view (`Inbound.gain_cow._CowSets`), it takes the view's `union()`: a lazy
    object over the owner's counted inverse that answers `len`/`in`/truth/iteration without
    materializing every aisle.  The old form did exactly that -- `values()` on the view
    copies all 2,774 aisles at campaign scale, T(T+1) x 12.59 times per drain -- and it was
    the single largest term of `rank_random`'s 39-59x priced multiple
    (`.scratch/phase-2-campaign/issues/02`)."""
    if not aisle_idx_sets:
        return set()
    u = getattr(aisle_idx_sets, 'union', None)
    return u() if u is not None else set().union(*aisle_idx_sets.values())


def _seed_floats(src, keys) -> dict:
    """`{k: float(src.get(k, 0.0)) for k in keys}` -- with ONE Python frame, not one per key.

    A pool seeds two running per-aisle float books at open (`_load`, `_vol_load`), each over
    every live aisle: ~1,400 keys at campaign scale, ~650,000 opens per arm.  Handed the
    manager's own dict that is already all C-level and this is the comprehension it always
    was.  Handed the gain evaluator's copy-on-write view (`Inbound.gain_cow._CowFloats`) it
    was A PYTHON CALL PER KEY, because `_CowView.get` is a Python method -- about 900 million
    of them per arm, for a dict the pool discards after seating ~12 units.

    The view answers `seed_floats` itself instead, reading its two backing dicts at C level.
    Duck-typed rather than imported: `Warehouse/placement/` may not import `Inbound/`
    (`context/architecture.yml`'s boundaries), and the fallback is what a plain dict takes.
    Same keys, same values, same order -- `dict` preserves insertion order and `keys` is
    walked once either way.
    """
    seed = getattr(src, 'seed_floats', None)
    if seed is not None:
        return seed(keys)
    return {k: float(src.get(k, 0.0)) for k in keys}


def _bucket_writer(by_aisle, depth: int):
    """How this pool moves a head: the mapping's own `writable`, or plain indexing.

    A pool opened over a SHARED template (`frozen_tier._CowAisles` / `_CowBuckets`, which
    the gain evaluator's rounds open over) must clone the one cursor a take moves rather
    than mutate the template every other open is reading.  A pool opened over a plain dict
    -- every wave placement, every eager build, every test -- owns its buckets outright
    and indexes them.  Resolved ONCE per open, so the hot path is a bound-method call
    either way and no `getattr` per take.
    """
    w = getattr(by_aisle, 'writable', None)
    if w is not None:
        return w
    if depth == 1:
        return lambda aid: by_aisle[aid]
    return lambda aid, m: by_aisle[aid][m]


def _co_by_aisle(row, idx_sets, partner_aisles, freq_by_idx) -> dict:
    """{aisle: Σ (lift - 1)·f_i over the SKU's partners i placed in that aisle}, for every
    aisle that holds at least one partner -- and NOTHING for the aisles that hold none.

    BIT-IDENTICAL TO `_delta_lift_from_row` PER AISLE, by construction rather than by
    tolerance, because the placement oracles compare exact floats.  That function is a left
    fold from int 0 in one of two orders, chosen per aisle by `len(row) <= len(members)`:

      * row order, filtered by membership, when the row is the shorter side.  The fold here
        walks `row` once in that same order and adds each partner's term to every aisle the
        inverse book says holds it -- so each aisle receives exactly the terms the old
        generator yielded for it, in the same sequence, from the same int 0.
      * SET order over the aisle's members when the members are the shorter side.  A set's
        iteration order is not the row's, so those aisles are re-folded the old way, and only
        those: touched aisles whose member set is shorter than the row.

    An aisle the inverse book never names had an empty intersection, for which the old fold
    returned int 0 (`sum` of nothing); the caller reads a missing key as that same int 0.
    Cost: O(|row| + Σ partners' aisles + Σ members over the re-folded aisles), against the
    old O(live aisles × min(|row|, |members|)) -- the k 1.98 term the scan-width ladder found.
    """
    acc: dict = {}
    for ci, lift in row.items():
        aids = partner_aisles.get(ci)
        if not aids:
            continue
        term = (lift - 1.0) * freq_by_idx.get(ci, 0.0)
        for aid in aids:
            acc[aid] = acc.get(aid, 0) + term
    n_row = len(row)
    for aid in acc:
        members = idx_sets[aid]
        if n_row > len(members):
            acc[aid] = sum((row[ci] - 1.0) * freq_by_idx.get(ci, 0.0)
                           for ci in members if ci in row)
    return acc


def _D_map(cands, x_pace, y_pace) -> dict[int, float]:
    """id(bin) → travel-time D map.  The identical dict-comprehension sat at every
    ranked-impl site; ONE helper so the formula can't drift.  Paces are s/inch
    (sec_per_inch of the ft/s speeds); expression shape preserved exactly."""
    return {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}


def freeze_tier(cands, wp) -> FrozenTier:
    """One tier's candidates sorted ONCE, under the paces and brackets the pools resolve
    from `wp` -- the gain evaluator's per-drain freeze (`Inbound/gain.py:_tier_for`).

    Built HERE, beside the pools that read it, because `Inbound` may not import the
    placement engine: the driver hands the evaluator this function through the bundle
    (`GainBundle.freeze_tier`), the same way `wp_of` and `binkey_of` cross that seam.  A
    pool handed the result checks that its own paces and brackets are the ones the tier was
    frozen under (`_check_tier`), so a mixed warehouse's per-regime `wp` cannot be served
    another regime's geometry."""
    speed = SpeedProfile(wp.x_speed, wp.y_speed)
    return FrozenTier(cands, speed.x_pace, speed.y_pace, getattr(wp, 'height_brackets', ()))


def _check_tier(tier: FrozenTier, x_pace: float, y_pace: float, brackets) -> None:
    """Refuse a slice whose tier was frozen under other paces or brackets.  EXACT float
    equality, deliberately: the pool would compute these same values from the same `wp`
    through the same expression, and a tier that was not frozen from that `wp` carries
    different geometry -- a tolerance here would accept the mixed-warehouse mistake."""
    if (tier.x_pace != x_pace or tier.y_pace != y_pace
            or (brackets is not None and tuple(brackets) != tier.brackets)):
        raise ValueError(
            f'this pool resolves paces ({x_pace!r}, {y_pace!r}) and brackets '
            f'{tuple(brackets) if brackets is not None else "n/a"} from its wp, but the tier '
            f'was frozen under ({tier.x_pace!r}, {tier.y_pace!r}) / {tier.brackets}: a slice '
            f'must be taken from a tier frozen with the SAME resolved wp (freeze_tier(cands, wp))')



def _aisle_index_for_unit(aisle_index, unit, minimize: bool):
    """(best_D, best_bin_map) — one extremal-D representative bin per aisle for *unit*,
    read from the manager's pre-sorted secondary index (the fast-path twin of
    Inventory_Manager._candidates; this identical block used to sit inline at 3 sites).

    Resolves the unit's BinKey tier exactly as _candidates does: singleton -> its one
    bucket; tiered families (pallet / fulfillment) -> the smallest non-empty tier >= the
    unit's own, via the unit's OWN size table (fulfillment sizes are ff_*, absent from the
    pallet _SIZE_RANKS — a pallet-only lookup would silently drop every fulfillment unit).
    minimize picks each aisle deque's min-D head, else its max-D tail (sorted ascending).
    Returns ({}, {}) when no tier has bins.
    """
    shc       = unit.order.storage_handle_config
    unit_type = unit.unit_category
    if unit_type == 'singleton':
        by_aisle = aisle_index.get((shc.handling, shc.category, 'singleton', 'singleton'))
    else:
        ranks, sizes_desc = tier_ranks_for(unit_type)
        min_rank = ranks.get(unit.storage_size, 0) if unit.storage_size else 0
        by_aisle = None
        for size in reversed(sizes_desc):
            if ranks[size] >= min_rank:
                by = aisle_index.get((shc.handling, shc.category, size, unit_type))
                if by and any(by.values()):
                    by_aisle = by
                    break
    best_D: dict[int, float] = {}
    best_bin_map: dict[int, Any] = {}
    if by_aisle:
        for aid, lst in by_aisle.items():
            if lst:
                b = lst[0] if minimize else lst[-1]
                best_D[aid]       = b._D
                best_bin_map[aid] = b
    return best_D, best_bin_map


def _aisle_extremal_bins(
    candidates: list[Any],
    x_speed   : float,
    y_speed   : float,
    minimize  : bool,
) -> tuple[dict[int, float], dict[int, Any]]:
    """Reduce candidates to one bin per aisle — the extremal-D representative.

    Proof of correctness
    --------------------
    For a fixed aisle (fixed ls, dl), the score tuple (delta_l2, old_L) is
    strictly monotone increasing in D = x_pace*x_phys + y_pace*y_phys
    (pace = sec_per_inch(speed); speeds are ft/s):
      old_L  = D + λ(D/k)^γ ls           — increasing in D
      new_L  = D + λ(D/k)^γ (ls+dl)      — increasing in D
      delta_l2 = new_L² − old_L²          — product of two positive increasing
                                             functions, so also increasing in D

    Consequence: within a fixed aisle, the minimum-D bin always yields the
    minimum score (best for minimising) and the maximum-D bin always yields the
    maximum score (best for maximising).  Reducing O(N_bins) candidates to one
    representative per aisle is exact — no approximation.
    """
    best_D  : dict[int, float] = {}
    best_bin: dict[int, Any]   = {}
    x_pace, y_pace = sec_per_inch(x_speed), sec_per_inch(y_speed)   # ft/s -> s/inch
    for b in candidates:
        aid = b.location[0]
        D   = x_pace * b.x_phys + y_pace * b.y_phys
        if aid not in best_D or (D < best_D[aid] if minimize else D > best_D[aid]):
            best_D[aid]   = D
            best_bin[aid] = b
    return best_D, best_bin



# ── trip-cost assignment functions ────────────────────────────────────────────


def _demand_weighted_delta_lift(
    affinity       : AffinityStore,
    sku            : int,
    member_idx_set : set[int],
    freq_by_idx    : dict[int, float],
) -> float:
    """Sum of (lift(s,i) − 1) * f_i for all affinity partners i in the aisle.

    Same CSR row-slice as delta_lift_idxs but weights each partner's association
    ABOVE independence (lift − 1; lift = 1 ⇒ 0) by its demand frequency, so common
    strongly-associated partners dominate over rare or near-independent ones.

    Delegates to the row helpers so the cold path (place_one, sort keys, tests) and the
    hoisted hot path (_delta_lift_from_row with a pre-built row) share ONE arithmetic
    path — bit-identical, both promoting the CSR float to Python float before the sum."""
    return _delta_lift_from_row(_affinity_row(affinity, sku), member_idx_set, freq_by_idx)


# ── shared aisle-scoring core (decoupled; reused by travel + cohesion) ────────

def _pick_extremal_aisle(best_D, score_of, maximize):
    """Return the aisle id whose score_of(aid) is extremal (max if maximize else min)."""
    best_aid = -1
    best = None
    for aid in best_D:
        sc = score_of(aid)
        if best is None or (sc > best if maximize else sc < best):
            best, best_aid = sc, aid
    return best_aid


def _require_affinity(affinity, policy: str) -> None:
    """Fail loudly if an affinity-DRIVEN policy (cohesion / co-demand) is built without a
    usable affinity matrix, rather than silently scoring 0 lift and degrading to uniform.
    Mirrors the batch sampler's refusal to silently fall back (Workload_Builder.Batch).
    Travel/ranked policies are exempt: they only use lift as a minor tie-break and rank
    fine on frequency alone."""
    if affinity is None or getattr(affinity, '_matrix', None) is None:
        raise ValueError(
            f"{policy} placement requires a usable affinity matrix but got "
            f"{'None' if affinity is None else 'an AffinityStore with _matrix=None'}. "
            "Refusing to place without the co-occurrence data it is supposed to use.")


def _require_demand(freq_map, policy: str, what: str) -> None:
    """Fail loudly if a demand-WEIGHTED policy is built with an empty frequency map,
    rather than silently weighting every SKU by 0 and degrading to uniform.  Ranked
    drains are exempt: their priority reads order.demand.relative_frequency directly."""
    if not freq_map:
        raise ValueError(
            f"{policy} placement requires {what} but it is empty. "
            "Refusing to place without the demand frequencies it weights by.")


def _build_aisle_score_fn(name, *, score_kind, maximize, affinity, wp, ledger,
                          freq_by_idx, freq_by_sku, qty_by_sku, beta,
                          aisle_index=None):
    """Compose a per-unit AssignmentFn from a named aisle SCORER + direction.

    Shared by the travel (f_s*D - beta*co_occur) and cohesion (demand-weighted lift)
    policies; they differ only in the score tuple and which D-rank bin represents
    each aisle.  The returned closure carries a programmatic ``.name`` (e.g.
    'travel_min') so downstream processing can build/identify functions by name.

    When ``aisle_index`` (mgr._aisle_index) is supplied, Step 1 reads the
    pre-sorted per-aisle index directly (O(N_aisles)) instead of scanning a flat
    candidates list — the same fast path as build_load_*.  The candidates argument
    is then ignored (the manager passes None).  bin_minimize picks the head (min-D)
    or tail (max-D) of each aisle's ascending-by-D list, identical to
    _aisle_extremal_bins, so placements are unchanged.
    """
    if score_kind == 'cohesion':
        _require_affinity(affinity, name)      # cohesion is meaningless without lift data
        _require_demand(freq_by_idx, name, 'freq_by_idx (the demand-weighted lift)')
    elif score_kind == 'travel':
        _require_demand(freq_by_sku, name, 'freq_by_sku (the f_s*D objective)')
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    # cohesion always uses the front (min-D) bay; travel uses min-D when minimising
    # and max-D when maximising (f_s*D is monotone in D within an aisle).
    bin_minimize = True if score_kind == 'cohesion' else (not maximize)
    # The aisle books this family maintains: membership and the demand level, nothing
    # else.  `over` BINDS the dicts it is handed rather than copying them, so the writes
    # below land wherever the caller's dicts live -- the warehouse's own, or the gain
    # evaluator's copy-on-write wrappers.
    # The ledger ARRIVES now (ticket 21); it was built here out of three dicts threaded
    # down the whole parameter chain to be reassembled into what the caller already held.
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum = ledger.demand_sum
    # THE INVERSE BOOK, bound whenever the ledger was handed the OWNER's `idx_sets` (see
    # `aisle_ledger._IdxSets`): the real arm's view is; the gain evaluator's copy-on-write
    # view is not, so a virtual placement keeps the per-aisle fold.  Hoisted once: `bound` is
    # a derived frozenset and `assign` is the hot path.
    partner_aisles = ledger.partner_aisles if 'partner_aisles' in ledger.bound else None

    def assign(unit, candidates):
        sku = unit.order.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Step 1: one representative bin per aisle (extremal-D).
        if aisle_index is not None:
            best_D, best_bin_map = _aisle_index_for_unit(aisle_index, unit, minimize=bin_minimize)
            if not best_D:
                return None
        else:
            if not candidates:
                return None
            best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=bin_minimize)

        row = _affinity_row(affinity, sku)     # hoist the CSR slice: once per unit, not per aisle
        # One fold per UNIT over the aisles that hold a partner (see `_co_by_aisle`), instead
        # of one fold per AISLE; None when the book is not bound or the SKU has no partners,
        # in which case the per-aisle path below is the old code, untouched.
        co_by_aid = (_co_by_aisle(row, aisle_idx_sets, partner_aisles, freq_by_idx)
                     if (partner_aisles is not None and row) else None)

        def score_of(aid):
            D = best_D[aid]
            if sku in aisle_sku_sets[aid]:
                co = 0.0
            elif co_by_aid is None:
                co = _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx)
            else:
                # `_delta_lift_from_row`'s two early answers, then the fold: an aisle with no
                # members read 0.0 there; one with members but no partner read `sum` of
                # nothing, int 0, which is what a missing key yields here.
                co = 0.0 if not aisle_idx_sets[aid] else co_by_aid.get(aid, 0)
            if score_kind == 'travel':
                primary = f_s * D - beta * co
                secondary = aisle_demand_sum[aid] + f_s * q_s
                return (primary, -secondary) if maximize else (primary, secondary)
            return (co, -D) if maximize else (co, D)   # cohesion; tie-break front bay

        best_aid = _pick_extremal_aisle(best_D, score_of, maximize)
        if best_aid < 0:
            return None
        if sku not in aisle_sku_sets[best_aid]:
            ledger.add_sku(best_aid, sku, affinity._sku_to_idx.get(sku), demand=f_s * q_s)
        return best_bin_map[best_aid]

    assign.name = name
    assign.uses_aisle_index = aisle_index is not None
    #: The books this family commits to, as the object that owns them.  `PlacementPolicy`
    #: DECLARES the same list (`ledger_terms`), and a test compares the two -- a declaration
    #: nobody exercises is how the gain evaluator's copy list drifts from what a pool writes.
    #: The pooled families expose theirs as `pool._led`; this is the per-unit half.
    assign.ledger = ledger
    return assign


def _travel_or_cohesion(name, score_kind, maximize):
    """Make a builder with the legacy (affinity, wp, ...state..., beta) signature that
    routes through the shared core."""
    def builder(affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
                beta=1.0, aisle_index=None):
        return _build_aisle_score_fn(
            name, score_kind=score_kind, maximize=maximize, affinity=affinity, wp=wp,
            ledger=ledger, freq_by_idx=freq_by_idx,
            freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku, beta=beta,
            aisle_index=aisle_index)
    builder.__name__ = f'build_{name}_assignment_fn'
    builder.assignment_name = name
    return builder


# Composed presets (programmatic names) + back-compat aliases used by callers.
build_trip_minimizing_assignment_fn    = _travel_or_cohesion('travel_min',   'travel',   False)
build_trip_maximizing_assignment_fn    = _travel_or_cohesion('travel_max',   'travel',   True)
build_cluster_maximizing_assignment_fn = _travel_or_cohesion('cohesion_max', 'cohesion', True)
build_cluster_minimizing_assignment_fn = _travel_or_cohesion('cohesion_min', 'cohesion', False)


def build_uniform_aisle_trip_min_assignment_fn(wp, rng: random.Random | None = None) -> AssignmentFn:
    """Pick an aisle UNIFORMLY at random among the candidate aisles, then place in
    that aisle's minimum-travel-cost bin.

    Ablation control with no affinity, no demand, no priority — the candidate set
    from _candidates is already scoped to the unit's (handling, category, size,
    unit_type), so the random aisle is always a legal one.  Per-unit; the FIFO
    Placement uses this as its place_one (no place_wave).
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    _rng    = rng or random

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None
        # min-D bin per aisle; pick a random aisle, return its min-D bin.
        _best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)
        if not best_bin_map:
            return None
        return best_bin_map[_rng.choice(list(best_bin_map.keys()))]

    return assign


# ── ranked assignment functions ────────────────────────────────────────────────

class _Pool:
    """Base for the placement pools: `take` is required, `order` defaults to queue order.

    A policy overrides `order` only if it has a genuine opinion about precedence.  The
    drain is free to ignore it -- see the PoolFn note in inventory_common.
    """

    __slots__ = ()

    #: Is a LOWER score better?  Every policy here optimises in one direction, and three of
    #: them flip it by flag (tmax, rank_maxlabor, expn).  Recording the direction is what
    #: lets `bin_placement.score_rank` mean one thing across arms: rank 0 is the best choice
    #: that was available, never "the smallest number".  Without it a consumer would have to
    #: hard-code which arms maximise, which is exactly the knowledge that rots.
    prefers_low = True

    def sort_key(self, unit):
        """The precedence this policy would like, HIGHER FIRST -- or None for no opinion.

        Stated as a key rather than as a sorted list because the drain's K-oldest window
        asks "which of these K next" once per placement, and re-deriving a key inside every
        window would mean N*K affinity slices.  Every key here is a pure function of the
        unit and of state frozen at pool-open, never of what the pool has already placed, so
        computing it once per unit is safe as well as fast.
        """
        return None

    def order(self, units):
        """The full precedence, best first.  A REQUEST -- the drain decides what to grant.

        Stable, so units the key cannot separate keep their queue order: a tie was not an
        opinion, and the older unit should not lose to a younger one for no reason.
        """
        if not units or self.sort_key(units[0]) is None:
            return units                    # no opinion: queue order stands
        return sorted(units, key=self.sort_key, reverse=True)

    def take(self, unit):                                   # pragma: no cover - interface
        raise NotImplementedError


def _ranked_assign_impl(
    units        : list,
    candidates_fn,
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta         : float,
    minimize     : bool,
    aisle_selector = None,
    order_key      = None,
) -> list:
    """Shared core for ranked-minimizing and ranked-maximizing assignment.

    SUPERSEDED by `_RankedAssignPool`, which is what the four arms actually run.  Kept as
    the frozen oracle the port is tested against, and as the straggler-path reference.

    Priority formula (pick-effort x frequency + co-occurrence):
      priority = f_i x (pick_intercept + pick_weight_coef x log(weight)
                                        + pick_volume_coef x log(volume))
                 + beta x co_occur

    Sorted descending by priority; highest-priority unit claims the extremal-D
    bin first within each same-BinKey group.  minimize=True -> lowest-D bin
    (easiest access); minimize=False -> highest-D bin (hardest access).

    W (task workload) remains a measurement metric only; this formula
    drives bin placement at reorder time.

    THIN DRIVER over `_RankedAssignPool` since ticket 04. It was a second implementation of
    the same scoring rule and has had no production caller since the pool inversion. The
    reference it used to BE now lives in `Tests/unit/test_ranked_assign_pool_equivalence.py`
    as `_oracle_ranked_assign_impl`, frozen verbatim -- this family was the last one whose
    test read a live production impl as its oracle.

    `pool.order(units)` is the sort this body did inline. Note the pool takes the SCAN branch
    whenever `aisle_selector` is given and the heap branch otherwise, which is exactly the
    split the wave had; a caller that wants the heap passes `aisle_key` to the pool directly,
    and the wave never knew how to do anything but scan.
    """
    if not units:
        return []
    wp = _wp_for(wp, units[0])       # per-regime cost in a mixed warehouse
    pool = _RankedAssignPool(
        list(candidates_fn(units[0])), affinity, wp,
        ledger.sku_sets, ledger.idx_sets, ledger.demand_sum,
        freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize,
        aisle_selector=aisle_selector, order_key=order_key)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


# ── co-demand compaction / expansion (within-aisle path-span min/max) ──────────
#
# The diagnostic showed makespan is driven by within-aisle work W, and W's travel
# term is the length of the column-sweep path through a batch's demanded bins.  So
# clustering co-demanded SKUs into nearby COLUMNS shortens that path (compaction);
# scattering them lengthens it (expansion).  These ride the ranked drain and commit
# member positions INCREMENTALLY so clusters accumulate within a wave.

def _partner_row(affinity, sku) -> dict:
    """`{partner idx: lift}` for `sku` -- the centroid's CSR slice, as one dict.  `{}` for a
    SKU with no stored partners (or absent from the matrix)."""
    if affinity._matrix is None or sku not in affinity._sku_to_idx:
        return {}
    i     = affinity._sku_to_idx[sku]
    start = int(affinity._matrix.indptr[i])
    end   = int(affinity._matrix.indptr[i + 1])
    if start == end:
        return {}
    return {int(ci): float(d) for ci, d in
            zip(affinity._matrix.indices[start:end], affinity._matrix.data[start:end])}


def _demand_weighted_partner_centroid(affinity, sku, member_pos, freq_by_idx, row=None):
    """Lift-weighted COLUMN centroid of an aisle's already-placed affinity partners of
    `sku`.  ``member_pos`` is the aisle's ``{sku_idx -> [x_phys, ...]}`` (one x per LIVE
    bin; pruned on reclaim so no stale members).  Returns (mass, centroid_x);
    (0.0, None) when `sku` has no placed partners there.

    Mirrors _demand_weighted_delta_lift's CSR row-slice but accumulates a position
    centroid weighted by lift(s, partner) * f_partner.  Iterating per distinct partner
    SKU (not per placement) keeps this O(distinct SKUs in aisle).
    """
    if not member_pos or affinity._matrix is None or sku not in affinity._sku_to_idx:
        return 0.0, None
    if row is None:
        # `row` is the SKU's partner row, built here per call -- or handed in by a caller
        # that keeps one per SKU run (`_MinLaborPool.take`: 425k calls on a six-day coupled
        # unit at campaign scale rebuilt it 425k times).  Built by `_partner_row`, the
        # same expression, so the dict is the same either way; an EMPTY row returns the
        # same (0.0, None) the `start == end` early-out does, through the loop finding
        # nothing.
        row = _partner_row(affinity, sku)
        if not row:
            return 0.0, None
    mass = wx = 0.0
    for idx, xs in member_pos.items():
        lift = row.get(idx)
        if lift:
            f = freq_by_idx.get(idx, 0.0)
            if f:
                w = (lift - 1.0) * f         # association above independence, demand-weighted
                if w:
                    mass += w * len(xs)
                    wx   += w * sum(xs)
    return (mass, wx / mass) if mass > 0 else (0.0, None)


def _co_demand_ranked_impl(units, candidates_fn, affinity, wp, ledger,
                           freq_by_idx, freq_by_sku, qty_by_sku, beta, compact: bool):
    """Ranked co-demand placement.  SUPERSEDED by `_CoDemandPool`, which is what `comp`
    and `expn` actually run; kept as the frozen oracle the port is tested against.

    Units are placed in the same pick-effort order as
    _ranked_assign_impl and membership is committed incrementally, but the BIN choice is
    position-aware: the aisle is scored by demand-weighted lift to its members (MAX for
    compact / MIN for expand), and within it the bin NEAREST (compact) / FARTHEST (expand)
    the partners' column centroid is taken — vs the extremal-D head.  Each placement also
    appends (x_phys, idx) to aisle_member_pos so later units in the wave see it.

    THIN DRIVER over `_CoDemandPool` since ticket 04. It was a second implementation of the same
    scoring rule, and the wave form has had no production caller since the pool inversion --
    14 of 17 shipped rules are pooled and the other three have no group path at all. What it
    is FOR is the equivalence suites, and they gain from this rather than lose: the reference
    they compare against is the FROZEN oracle in `Tests/calltree/test_rank_cache_equivalence.py`, and this leg is now the drain
    contract (pool driven in the wave's own order == wave) instead of an echo of it.

    `pool.order(units)` is the sort the body used to do inline, and driving through it is the
    whole claim: given the same order, the pool makes bit-identical decisions. A drain that
    declines that order gets a different -- and intentionally different -- answer.
    """
    if not units:
        return []
    pool = _CoDemandPool(list(candidates_fn(units[0])), affinity, wp,
                         ledger.sku_sets, ledger.idx_sets, ledger.demand_sum,
                         ledger.member_pos, freq_by_idx, freq_by_sku, qty_by_sku,
                         beta, compact)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


class _CoDemandPool(_Pool):
    """`_co_demand_ranked_impl` as a pool -- compaction (`comp`) and expansion (`expn`).

    This is the first ported policy that scores a unit against what has ALREADY been placed,
    which is the case a pool has to get right to be worth anything.  It turns out to be the
    natural shape: every affinity read in the per-placement body is against LIVE manager
    state (`aisle_idx_sets`, `aisle_member_pos`) that `take` itself mutates.  That is pool
    state by definition.  Exactly one thing was a whole-set snapshot -- `all_idx` -- and it
    is read only by the sort key.

    `all_idx` is therefore computed here, in `__init__`, at the instant the old code took it,
    and is deliberately frozen for the group even though placements mutate `aisle_idx_sets`
    underneath it.  It is derived from the AISLES, not from the units, so a drain that
    narrows or reorders the unit set does not invalidate it; when the sort finally goes, it
    goes with it, because nothing else reads it.

    The SKU-run cache (`key_cache`) carries the same guarantee and the same caveat as
    `_TravelBalancedPool`'s: the guard is a value comparison on the SKU, so losing
    adjacency costs hit rate and not correctness.  The winner refresh after every placement
    is load-bearing and is NOT a redundant recompute -- see the ulp note on
    `_ClusterMapPool`, which is where that lesson was paid for.
    """

    __slots__ = ('_aff', '_ass', '_ais', '_ads', '_amp', '_fbi', '_fbs', '_qbs',
                 '_beta', '_compact', '_x_pace', '_all_idx', '_by_aisle', '_D_of',
                 '_s2i', '_last_sku', '_key_cache', '_cached_row', '_co_by_sku', '_led')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_member_pos, freq_by_idx, freq_by_sku,
                 qty_by_sku, beta, compact: bool):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._amp = aisle_demand_sum, aisle_member_pos
        self._fbi, self._fbs, self._qbs = freq_by_idx, freq_by_sku, qty_by_sku
        self._beta, self._compact = beta, compact
        self._led = AisleLedger.over(sku_sets=aisle_sku_sets, idx_sets=aisle_idx_sets,
                                     demand_sum=aisle_demand_sum,
                                     member_pos=aisle_member_pos)
        self._s2i = affinity._sku_to_idx

        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._x_pace = x_pace
        # Every SKU index placed anywhere. Aisle-derived, so the unit set cannot change it;
        # frozen for the group, so later units rank against the pre-group union.
        self._all_idx = _placed_union(aisle_idx_sets)

        self._D_of = _D_map(cands, x_pace, y_pace)
        by_aisle: dict[int, list] = {}
        for b in cands:
            by_aisle.setdefault(b.location[0], []).append(b)
        for lst in by_aisle.values():
            lst.sort(key=lambda b: b.x_phys)          # ascending column
        self._by_aisle = by_aisle

        self._last_sku = None
        self._key_cache: dict = {}
        self._cached_row = None
        self._co_by_sku: dict = {}      # sort-key memo; all_idx is frozen, so reuse is bit-safe

    def __len__(self):
        return sum(len(lst) for lst in self._by_aisle.values())

    @property
    def prefers_low(self):
        return self._compact           # expansion maximises the distance instead

    def sort_key(self, unit):
        """Pick-effort priority with the co-occurrence term. `all_idx` is frozen for the
        life of the pool, so a SKU's co term is one value and the memo is bit-safe."""
        c = unit.order
        co = self._co_by_sku.get(c.sku)
        if co is None:
            # c.labor_cost = precomputed per-pick effort (pi + pwt*ln w + pv*ln v).
            co = self._beta * _demand_weighted_delta_lift(
                self._aff, c.sku, self._all_idx, self._fbi)
            self._co_by_sku[c.sku] = co
        return c.demand.relative_frequency * c.labor_cost + co

    def take(self, unit):
        """(bin, score) for one unit; (None, None) when no aisle has a bin left.

        `score` is the compaction objective in seconds -- `x_pace * |x(bin) - cx|`, the
        distance from the partners' demand-weighted column centroid that the bin choice
        minimises (compact) or maximises (expand).  None before this SKU has any partner
        placed, because there is then no centroid and the bin was taken by the cold-start
        rule instead.  The AISLE was chosen on lift, which is a different quantity and not
        a property of the bin.
        """
        by_aisle, compact = self._by_aisle, self._compact
        live = [aid for aid, lst in by_aisle.items() if lst]
        if not live:
            return None, None
        sku = unit.order.sku
        f_s = self._fbs.get(sku, 0.0)
        q_s = self._qbs.get(sku, 0.0)

        if sku != self._last_sku:
            self._cached_row = _affinity_row(self._aff, sku)   # CSR slice: once per run
            # aisle: most (compact) / least (expand) lift to members; tie-break toward
            # the front (compact) / back (expand) bay by the aisle's lowest-D rep.
            key_cache = {}
            for aid in live:
                mass = _delta_lift_from_row(self._cached_row, self._ais[aid], self._fbi)
                d0   = self._D_of[id(by_aisle[aid][0])]
                key_cache[aid] = (mass, -d0) if compact else (mass, d0)
            self._key_cache = key_cache
            self._last_sku = sku
        row = self._cached_row
        key_cache = self._key_cache
        best_aid = (max if compact else min)(live, key=key_cache.__getitem__)

        lst = by_aisle[best_aid]
        _mass, cx = _demand_weighted_partner_centroid(
            self._aff, sku, self._amp[best_aid], self._fbi)
        if cx is not None:                        # bin nearest / farthest the partner column
            j = (min if compact else max)(range(len(lst)),
                                          key=lambda k: abs(lst[k].x_phys - cx))
        else:                                     # no partners yet: front / back
            j = 0 if compact else len(lst) - 1
        chosen = lst.pop(j)
        score = None if cx is None else self._x_pace * abs(chosen.x_phys - cx)

        if sku not in self._ass[best_aid]:
            self._led.add_sku(best_aid, sku, demand=f_s * q_s)
        self._led.add_bin(best_aid, self._s2i.get(sku), chosen.x_phys)
        # winner refresh: exactly what the next same-SKU unit's fresh recompute would see
        if lst:
            mass = _delta_lift_from_row(row, self._ais[best_aid], self._fbi)
            d0   = self._D_of[id(lst[0])]
            key_cache[best_aid] = (mass, -d0) if compact else (mass, d0)
        else:
            key_cache.pop(best_aid, None)         # aisle exhausted: leaves `live` next unit
        return chosen, score


def _build_co_demand_pool_fn(affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
                             beta, compact):
    # The books this family commits to, under the names the body already used.  The ledger is
    # built ONCE per policy by the caller, from the same `PlacementPolicy.ledger_terms` the
    # gain evaluator copies -- so the signature and the copy list cannot disagree (ticket 21).
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum, aisle_member_pos = ledger.demand_sum, ledger.member_pos

    def open_pool(candidates, rep=None):
        return _CoDemandPool(candidates, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                             aisle_demand_sum, aisle_member_pos, freq_by_idx,
                             freq_by_sku, qty_by_sku, beta, compact)
    return open_pool


def _build_co_demand_place_one(affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
                               compact, name):
    """Per-unit co-demand fn (place_one) — same scoring as the wave, one unit at a time.
    Used for the ranked policy's stragglers; accumulates positions like the wave."""
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    sku_to_idx = affinity._sku_to_idx
    # The ledger ARRIVES now (ticket 21).  It was built here out of four dicts that had been
    # threaded down the whole parameter chain to be reassembled into the object the caller
    # already had.
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum, aisle_member_pos = ledger.demand_sum, ledger.member_pos

    def assign(unit, candidates):
        if not candidates:
            return None
        by_aisle: dict[int, list] = {}
        for b in candidates:
            by_aisle.setdefault(b.location[0], []).append(b)
        sku = unit.order.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)
        row = _affinity_row(affinity, sku)        # hoist the CSR slice: once per unit, not per aisle

        def aisle_key(aid):
            mass = _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx)
            d0   = min(x_pace * b.x_phys + y_pace * b.y_phys for b in by_aisle[aid])
            return (mass, -d0) if compact else (mass, d0)
        best_aid = (max if compact else min)(by_aisle, key=aisle_key)

        lst = by_aisle[best_aid]
        _mass, cx = _demand_weighted_partner_centroid(
            affinity, sku, aisle_member_pos[best_aid], freq_by_idx)
        if cx is not None:
            chosen = (min if compact else max)(lst, key=lambda b: abs(b.x_phys - cx))
        else:
            chosen = (min if compact else max)(lst, key=lambda b: x_pace * b.x_phys + y_pace * b.y_phys)

        if sku not in aisle_sku_sets[best_aid]:
            ledger.add_sku(best_aid, sku, demand=f_s * q_s)
        ledger.add_bin(best_aid, sku_to_idx.get(sku), chosen.x_phys)
        return chosen

    assign.name = name
    assign.uses_aisle_index = False
    #: The books this family commits to -- the object, beside the `ledger_terms`
    #: its `PlacementPolicy` declares.  A test compares the two.
    assign.ledger = ledger
    return assign


def build_co_demand_placement(compact, affinity, wp, ledger,
                              freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0) -> Placement:
    """One Placement (place_one + ranked place_wave) for co-demand compaction (compact=True)
    or expansion (compact=False).  Wired by strategies._build_compaction/_build_expansion."""
    name = 'compaction' if compact else 'expansion'
    _require_affinity(affinity, name)          # co-demand is meaningless without lift data
    _require_demand(freq_by_idx, name, 'freq_by_idx (the partner-centroid weight)')
    place_one = _build_co_demand_place_one(
        affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, compact, name)

    open_pool = _build_co_demand_pool_fn(
        affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta, compact)
    open_pool.name = name
    return Placement(name, place_one, open_pool=open_pool)


class _RankedAssignPool(_Pool):
    """`_ranked_assign_impl`, split along its real seam.

    The old function did three separable things in one pass: snapshot the group's candidates
    (candidate-derived), decide precedence (unit-derived), and hand out bins (per unit).
    Only the middle one ever needed the whole unit set, and only the middle one is what the
    drain is taking back.  So:

      * `__init__` -- the candidate snapshot: `_D_map`, the per-aisle deques sorted
        extremal-D-first, and the two head dicts.  Plus `all_idx`, the union of every placed
        SKU index, snapshotted at exactly the instant the old code took it: before the sort,
        before any placement in this group.  It is deliberately frozen for the whole group
        even though placements below mutate `aisle_idx_sets`.
      * `order`  -- the descending pick-effort priority.  This is now a request.
      * `take`   -- one aisle choice, one head pop, one commit.

    `head_bin`/`head_D` key order is load-bearing in three places and is preserved exactly:
    `min`/`max` keep the FIRST extremal, so a D tie resolves to whichever aisle appeared
    first in the candidate list; `rank_popularity`'s selector has the same tie behaviour; and
    `rank_random` indexes into `list(head_bin.keys())`.  Insertion order is first-appearance
    in `cands`, refreshed in place on a pop and deleted on exhaustion, so a key never moves
    and never comes back.

    Two dead parameters did not survive the port: `pick_intercept`/`pick_weight_coef`/
    `pick_volume_coef` were unpacked and never read (the priority uses the precomputed
    `c.labor_cost` instead), and `aisle_extra_sum`/`sku_extra_product` had no caller anywhere
    in the repo.
    """

    __slots__ = ('_aff', '_ass', '_ais', '_ads', '_fbi', '_fbs', '_qbs', '_beta',
                 '_minimize', '_selector', '_order_key', '_all_idx',
                 '_by_aisle', '_D_of', '_head_bin', '_head_D',
                 '_key_fn', '_rank', '_sel', '_led', '_writer')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, freq_by_idx, freq_by_sku, qty_by_sku, beta,
                 minimize, aisle_selector=None, order_key=None, aisle_key=None):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._fbi = aisle_demand_sum, freq_by_idx
        self._fbs, self._qbs, self._beta = freq_by_sku, qty_by_sku, beta
        self._led = AisleLedger.over(sku_sets=aisle_sku_sets, idx_sets=aisle_idx_sets,
                                     demand_sum=aisle_demand_sum)
        self._minimize, self._selector, self._order_key = minimize, aisle_selector, order_key
        #: `aisle_key(aid, head_D) -> comparable` -- the SELECTION expressed as a KEY
        #: instead of a scan, so `take` can heap it.  None means the default: the head's
        #: own D.  An arm that supplies `aisle_selector` instead keeps the scan (see
        #: `take`); today that is `rank_random` alone, whose uniform draw needs the live
        #: key sequence and has no key to order by.
        self._key_fn = aisle_key

        # The co-occurrence term ranks each SKU against ALL currently-placed SKU indices.
        # That union is identical for every unit in the group, so build it ONCE -- not once
        # per unit inside the sort key (which was O(U*sigma) per wave).  Only the default
        # pick-effort ordering's co-occurrence term needs it.
        self._all_idx = _placed_union(aisle_idx_sets) if order_key is None else set()

        # The named pair, not two hand conversions — new code crosses the ft/s -> s/inch
        # boundary through the profile (see cost_model.SpeedProfile).
        speed  = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        if isinstance(cands, TierSlice):
            # THE OVERLAY (frozen_tier.py): the tier was sorted once for the drain; this
            # open is one cursor per surviving aisle, in the filtered first-appearance
            # order the eager build below would have produced.  Same deque protocol, so
            # `take` is one code path.
            _check_tier(cands.tier, x_pace, y_pace, None)
            D_of = cands.tier.D_by_id
            by_aisle = cands.aisles(reverse=not minimize)
        else:
            D_of = _D_map(cands, x_pace, y_pace)
            by_aisle = {}
            for b in cands:
                by_aisle.setdefault(b.location[0], []).append(b)
            for aid, lst in by_aisle.items():
                lst.sort(key=lambda bb: D_of[id(bb)], reverse=not minimize)   # head = extremal-D
                by_aisle[aid] = deque(lst)
        self._D_of, self._by_aisle = D_of, by_aisle
        self._writer = _bucket_writer(by_aisle, 1)
        self._head_bin = {aid: dq[0]           for aid, dq in by_aisle.items() if dq}
        self._head_D   = {aid: D_of[id(dq[0])] for aid, dq in by_aisle.items() if dq}

        # ── the selection heap ────────────────────────────────────────────────────
        # `_rank` is the TIE-BREAK, and it is what makes the heap byte-identical rather
        # than merely equivalent.  `min()`/`max()` return the FIRST extremal element in
        # iteration order; `head_D` is built here in `by_aisle` order, reassigning an
        # existing key does not move it, and deleting one does not reorder the rest -- so
        # the scan's tie-break is "lowest original insertion index", for BOTH directions.
        #
        # NO LAZY DELETION and no run-boundary rebuild.  Neither key depends on the SKU
        # (`head_D[aid]`, and `aisle_demand_sum[aid]` for the popularity arm), and `take`
        # moves only the WINNER's -- so one pop and at most one push per placement keeps
        # the heap exact for the whole wave.  `_TravelBalancedPool` needs a rebuild per
        # SKU run because its score carries `fq`/`var`; this one does not.
        self._rank = {aid: i for i, aid in enumerate(self._head_D)}
        self._sel = ([] if aisle_selector is not None else
                     [(self._heap_key(aid), self._rank[aid], aid) for aid in self._head_D])
        if self._sel:
            heapq.heapify(self._sel)

    def _heap_key(self, aid):
        """The ordering the scan computed, as a value.

        Default: the head's D, NEGATED when maximising, so one min-heap serves `tmin` and
        `tmax` alike.  `-0.0 == 0.0`, so a zero-D tie still falls through to `_rank`.
        """
        if self._key_fn is not None:
            return self._key_fn(aid, self._head_D)
        d = self._head_D[aid]
        return d if self._minimize else -d

    def __len__(self):
        return sum(len(dq) for dq in self._by_aisle.values())

    # ── the precedence this policy would like ─────────────────────────────────────
    def _pick_effort_priority(self, unit) -> float:
        c = unit.order
        # c.labor_cost is the precomputed per-pick effort (pi + pw*ln w + pv*ln v),
        # so this avoids re-taking logs per unit per wave.
        co_occur = self._beta * _demand_weighted_delta_lift(
            self._aff, c.sku, self._all_idx, self._fbi)
        return c.demand.relative_frequency * c.labor_cost + co_occur

    @property
    def prefers_low(self):
        return self._minimize          # tmax maximises D on purpose

    def sort_key(self, unit):
        """Pick-effort priority: the highest-effort unit claims the extremal-D bin first.
        A policy may supply its own per-unit score instead."""
        return (self._order_key or self._pick_effort_priority)(unit)

    # ── one placement ─────────────────────────────────────────────────────────────
    def take(self, unit):
        """(bin, score) for one unit; (None, None) when every aisle is drained.

        `score` is the chosen bin's travel cost D -- the float the aisle argmin compared,
        so reporting it is one dict read and no arithmetic.
        """
        head_D, head_bin = self._head_D, self._head_bin
        if not head_D:
            return None, None
        if self._selector is not None:
            # `rank_random` only: a uniform draw over the live aisles has no key to order
            # by, and the `list(...)` order decides which aisle is picked.
            best_aid = self._selector(head_D, head_bin)
        else:
            # A SELECTION, not a scan.  This was
            #   (min if minimize else max)(head_D, key=head_D.__getitem__)
            # -- O(live aisles) on EVERY placement.  Measured on the meso ladder, the
            # width is the live aisle count and it grows with the catalogue: 8.1 aisles
            # per take at 500 SKUs, 115.3 at 8,000, k = 1.963 against the ladder knob.
            _k, _r, best_aid = heapq.heappop(self._sel)
        chosen = head_bin[best_aid]
        score  = head_D[best_aid]

        sku = unit.order.sku
        f_s = self._fbs.get(sku, 0.0)
        q_s = self._qbs.get(sku, 0.0)
        if sku not in self._ass[best_aid]:
            self._led.add_sku(best_aid, sku, self._aff._sku_to_idx.get(sku),
                              demand=f_s * q_s)

        # Advance the chosen aisle's head; drop it when exhausted.  THE ONE SITE THAT
        # MOVES A HEAD, so the one that asks for a writable bucket -- over a shared
        # template that clones this aisle's cursor, over a plain dict it is the dict
        # lookup it always was (`_bucket_writer`).
        dq = self._writer(best_aid)
        dq.popleft()
        if dq:
            head_bin[best_aid] = dq[0]
            head_D[best_aid]   = self._D_of[id(dq[0])]
            # Only the winner's inputs moved -- its head advanced, and `_ads` above may
            # have risen.  Re-key and push it back; every other entry is still exact.
            if self._selector is None:
                heapq.heappush(self._sel,
                               (self._heap_key(best_aid), self._rank[best_aid], best_aid))
        else:
            del head_bin[best_aid]
            del head_D[best_aid]
            # An exhausted aisle is simply NOT pushed back -- that is how it leaves the
            # heap, and it cannot return, because bins only ever leave a pool.
        return chosen, score


def _build_ranked_assign_pool_fn(
    affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize,
    aisle_selector=None, order_key=None, aisle_key=None,
):
    """`open_pool` shared by the four ranked-assign arms (tmin / tmax / rank_random /
    rank_popularity).  Mirrors `_ranked_assign_impl`'s parameter list exactly."""
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum = ledger.demand_sum
    def open_pool(candidates, rep=None):
        return _RankedAssignPool(
            candidates, affinity,
            _wp_for(wp, rep) if rep is not None else wp,   # per-regime cost, mixed warehouse
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize,
            aisle_selector=aisle_selector, order_key=order_key, aisle_key=aisle_key)
    return open_pool


def build_ranked_minimizing_assignment_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Ranked assignment: high pick-effort items get lowest-D (easiest) bins.

    Same parameter signature as build_trip_minimizing_assignment_fn.  Used as the
    place_wave of a ranked Placement (place_one = build_trip_minimizing for stragglers).
    """
    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        )
    return ranked_assign


def build_ranked_minimizing_pool_fn(*a, **kw):
    """Pool twin of build_ranked_minimizing_assignment_fn — same signature."""
    return _build_ranked_assign_pool_fn(*a, minimize=True, **kw)


def build_ranked_maximizing_assignment_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Ranked assignment: high pick-effort items get highest-D (hardest) bins.

    Mirror of build_ranked_minimizing_assignment_fn — strategy-C upper bound.
    """
    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=False,
        )
    return ranked_assign


def build_ranked_maximizing_pool_fn(*a, **kw):
    """Pool twin of build_ranked_maximizing_assignment_fn — same signature."""
    return _build_ranked_assign_pool_fn(*a, minimize=False, **kw)


def build_ranked_uniform_assignment_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
    rng              : random.Random | None = None,
):
    """Ranked assignment: rank units by pick-effort priority (same as
    ranked-minimizing), but place each into a UNIFORM-RANDOM aisle's
    minimum-travel-cost bin instead of the globally min-D aisle.

    Ablation control: keeps the ranking (incl. demand-weighted lift) so the only
    difference from ranked-minimizing is random vs D-optimal aisle selection —
    isolating whether the trip-min aisle choice is necessary.
    """
    _rng = rng or random

    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
            aisle_selector=lambda bw, bb: _rng.choice(list(bb.keys())),
        )
    return ranked_assign


def build_ranked_uniform_pool_fn(*a, rng=None, **kw):
    """Pool twin of build_ranked_uniform_assignment_fn — same signature.

    One RNG draw per unit that finds a live aisle, in the order the drain serves them, so
    the stream re-pairs with different units the moment the order changes.  That is a real
    consequence of the reorder, not a defect of the port."""
    _rng = rng or random
    return _build_ranked_assign_pool_fn(
        *a, minimize=True,
        aisle_selector=lambda bw, bb: _rng.choice(list(bb.keys())), **kw)


# ── per-policy enqueue order-scores (decoupled queue ordering, sorted DESC) ────
# A policy hands one of these to its Placement.order_score; the ranked wave sorts
# the queue by it instead of the default pick-effort priority — so no ordering is
# baked in that fights the policy.  Both read precomputed Order attributes.

def _score_expected_popularity(unit) -> float:
    return unit.order.expected_popularity        # freq * qty

def _score_expected_labor(unit) -> float:
    return unit.order.expected_labor             # freq * qty * cost1


def build_ranked_popularity_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Ablation: order units by expected_popularity (freq*qty) and place each into the
    aisle with the LEAST Σ popularity (aisle_demand_sum); nearest-D bin within it,
    nearest-aisle as the tiebreak.  Disperses demand mass evenly across aisles."""
    def _selector(head_D, head_bin):
        return min(head_D, key=lambda aid: (ledger.demand_sum.get(aid, 0.0), head_D[aid]))

    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
            aisle_selector=_selector, order_key=_score_expected_popularity,
        )
    return ranked_assign


def build_ranked_popularity_pool_fn(
    affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
):
    """Pool twin of build_ranked_popularity_fn — same signature.

    Its selector reads the LIVE `aisle_demand_sum`, which `take` itself increments, so the
    aisle choice depends on what this pool has already placed.  That is pool state and ports
    as-is; what it means is that this arm's result moves when the service order moves."""
    # The same ordering the scan computed, handed over as a KEY so the pool can heap it.
    # It reads the LIVE `aisle_demand_sum` exactly as the scan did; within a wave that
    # dict is written only by `take`, for the winner -- the batch loop runs the reloader's
    # evictions and the reclaim drain in SEPARATE phases before placement
    # (`strategy_runner` calls `reloader.reload` then `check_reorders`).
    def _key(aid, head_D):
        return (ledger.demand_sum.get(aid, 0.0), head_D[aid])
    return _build_ranked_assign_pool_fn(
        affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        aisle_key=_key, order_key=_score_expected_popularity)


#: Sentinel for "no SKU run open yet" — a fresh object so it can never equal
#: a real SKU, whatever the catalogue numbers them.
_NO_RUN_SKU = object()


def _travel_balanced_impl(units, candidates_fn, affinity, wp, ledger,
                          sku_pick_load_product, freq_by_sku, qty_by_sku, cart=None):
    """Travel- AND height-aware LPT load balance (Rank_labor).

    The expected labor of placing a unit in a bin is freq·qty times the per-pick cost
    THERE:  height_mult(y_bin)·(pick_intercept + handle_var) + D_bin, where handle_var is
    the per-unit weight/volume handling, height_mult(y) scales the WHOLE at-location pick
    (intercept + handling), and D_bin = x_speed·x_phys + y_speed·y_phys is travel.  Each
    unit is placed in the specific empty bin that minimises the resulting BUSIEST aisle's
    total labor (greedy LPT).  So a bin is only chosen high/far when the aisle's load is
    low enough to absorb the extra handling/travel — heavy, frequently-picked SKUs gravitate
    to low, near bins.

    Within an aisle the best bin is SKU-dependent (high handle_var prefers low brackets),
    so bins are grouped per aisle by height bracket, each a min-D deque; for each unit we
    scan one min-D representative per (aisle, bracket) — O(units · aisles · n_brackets).

    cart (optional) makes this Rank_cartlabor: a tuple (aisle_vol_sum, sku_vol_product,
    expected_batch_skus, total_freq) that adds each aisle's EXPECTED CART-SWAP cost to its
    balanced load, so the balancer disperses volume that would overflow a cart.  Default
    None ⇒ byte-identical Rank_labor.

    THIN DRIVER over `_TravelBalancedPool` since ticket 04. It was a second implementation of the same
    scoring rule, and the wave form has had no production caller since the pool inversion --
    14 of 17 shipped rules are pooled and the other three have no group path at all. What it
    is FOR is the equivalence suites, and they gain from this rather than lose: the reference
    they compare against is the FROZEN oracle in `Tests/unit/test_travel_balanced_equivalence.py`, and this leg is now the drain
    contract (pool driven in the wave's own order == wave) instead of an echo of it.

    `pool.order(units)` is the sort the body used to do inline, and driving through it is the
    whole claim: given the same order, the pool makes bit-identical decisions. A drain that
    declines that order gets a different -- and intentionally different -- answer.
    """
    if not units:
        return []
    wp = _wp_for(wp, units[0])       # per-regime cost in a mixed warehouse
    pool = _TravelBalancedPool(
        list(candidates_fn(units[0])), affinity, wp,
        ledger.sku_sets, ledger.idx_sets, ledger.demand_sum, ledger.pick_load_sum,
        sku_pick_load_product, freq_by_sku, qty_by_sku, cart=cart)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


class _TravelBalancedPool(_Pool):
    """`_travel_balanced_impl` as a pool -- Rank_labor, and Rank_cartlabor with `cart`.

    Everything this policy remembers between placements is either candidate-derived (the
    per-(aisle, bracket) min-D deques, `D_of`, `M_of`) or a RUNNING TOTAL seeded once from
    manager state (`load`, `vol_load`).  Nothing is an aggregate over the unit set.  So the
    split is clean: the whole prologue is `__init__`, the LPT sort is `order`, and one
    placement is `take`.

    THE SKU-RUN CACHE comes across unchanged, and its validity argument is worth restating
    because it is the thing a reorder threatens.  `ab_cache`/`score_cache` are rebuilt when
    `sku != run_sku` and refreshed for the WINNER after every placement; a non-winning
    aisle's inputs are provably frozen inside a run (`fq`/`var`/`m_s` are per-SKU constants,
    and `load`/`vol_load`/`aisle_sku_sets`/the deque heads move for the winner only).  The
    guard is a value comparison on the SKU, so a drain that stops clustering same-SKU units
    does not make the cache WRONG -- it makes it never hit.  That is the correct failure
    mode, and it is why no `assume_clustered` flag is needed here.

    What would break it: turning the cache into a persistent `{sku: ...}` dict to win the
    hit rate back under FIFO.  Between two units of one SKU, OTHER SKUs commit and grow
    other aisles' state, so a keyed cache would serve stale scores.  Don't.
    """

    __slots__ = ('_ass', '_ais', '_ads', '_apl', '_splp', '_fbs', '_qbs', '_s2i',
                 '_intercept', '_per_item', '_by_aisle', '_geo_memo', '_load', '_vol_load',
                 '_cart_on', '_avs', '_svp', '_cart_coef', '_cap_raw',
                 '_run_sku', '_var', '_fq', '_m_s', '_ab_cache', '_sel', '_led', '_writer',
                 '_pp', '_hv')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_pick_load_sum, sku_pick_load_product,
                 freq_by_sku, qty_by_sku, cart=None, geo_memo=None):
        # `geo_memo` is owned by the factory closure and shared across opens; see the prologue.
        # None means "compute everything", which is what a directly-constructed pool (tests,
        # the equivalence oracle) gets, and is behaviourally identical either way.
        self._geo_memo = geo_memo
        self._ass, self._ais, self._ads = aisle_sku_sets, aisle_idx_sets, aisle_demand_sum
        self._apl, self._splp = aisle_pick_load_sum, sku_pick_load_product
        self._fbs, self._qbs = freq_by_sku, qty_by_sku
        self._s2i = affinity._sku_to_idx
        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._intercept = wp.pick_intercept
        self._per_item = wp.pick_per_item     # the per-unit charge, priced as the sim bills it
        brackets = getattr(wp, 'height_brackets', ())

        # ── optional cart-swap term ──────────────────────────────────────────────
        # Expected picked volume in an aisle per task ~ (k/sum f)*sum f*q*vol; comparing that
        # to the cart capacity is equivalent to comparing the RAW mass V_raw = sum f*q*vol
        # against cap_raw = cap*sum f/k.  Expected cart cost = coef*max(0, V_raw/cap_raw - 1).
        self._cart_on = cart is not None
        self._avs = self._svp = None
        self._cart_coef = self._cap_raw = 0.0
        if self._cart_on:
            aisle_vol_sum, sku_vol_product, expected_batch_skus, total_freq = cart
            self._avs, self._svp = aisle_vol_sum, sku_vol_product
            self._cart_coef = wp.cart_swap_coef
            self._cap_raw = wp.cart_capacity * total_freq / max(expected_batch_skus, 1e-9)
        # `_avs` is None off the cart branch, and `over` skips a book it is handed as None --
        # so a labor pool never materialises a `vol_sum` key its family does not maintain.
        self._led = AisleLedger.over(sku_sets=aisle_sku_sets, idx_sets=aisle_idx_sets,
                                     demand_sum=aisle_demand_sum,
                                     pick_load_sum=aisle_pick_load_sum,
                                     vol_sum=self._avs)

        # ── per aisle: {height_mult: HEAP of (D, seq, bin)}, cheapest-D first ────────
        #
        # A HEAP, not a sorted deque, because the pool reads roughly three bins from a bucket
        # it used to sort in full -- a selection problem solved as a sorting problem.
        #
        # A MEMO, because `x_phys`, `y_phys` and `location` are Python-level property calls on
        # immutable geometry (`location` allocates a fresh tuple every call) and
        # `height_multiplier` is a pure step function with no cache.  A staging floor re-opens
        # this pool 15,509 times over one ladder, so those four were re-evaluated ~24 million
        # times for values that cannot change: a Bin's bay coordinates are fixed for the run.
        # The memo is keyed by RESOLVED cost profile in the factory, because `D` depends on its
        # paces and `M` on its brackets -- a mixed warehouse resolves a different `wp` per
        # regime and must not share entries.  Each value holds its own bin, so `id()` cannot be
        # recycled underneath the key.
        #
        # BOTH ARE BYTE-IDENTICAL, and that is the whole reason this is safe to touch:
        #   * `sort` is stable, so the old bucket order was the unique total order by
        #     (D, append-index).  `_seq` IS that append-index and is unique within a bucket, so
        #     `(D, seq)` is a strict total order and `heappop` reproduces exactly that
        #     sequence, prefix by prefix.  The bin sits third and is never reached by tuple
        #     comparison.
        #   * the memoized `D` is the same `x_pace * b.x_phys + y_pace * b.y_phys` expression
        #     `_D_map` evaluates, on operands that never change -- the same float bit for bit,
        #     however many evaluations are skipped.
        #
        # `heapify` is O(n) at C level and calls no Python key function, so the per-candidate
        # `<lambda>` the old `sort(key=...)` paid -- once per bin, per open -- is gone too.
        #
        # NaN: `sort` and `heapify` disagree on it.  `D` is NaN only if a pace is `inf` and a
        # coordinate is 0; `sec_per_inch` returns `inf` only for a non-positive speed, which
        # `validate_speeds` already rejects.
        if isinstance(cands, TierSlice):
            # THE OVERLAY (frozen_tier.py): one cursor per surviving (aisle, bracket), in
            # the filtered first-appearance order; the heap below is the eager twin.  Both
            # answer `top()` / `pop_top()`, so `_aisle_best` and `take` are one code path.
            _check_tier(cands.tier, x_pace, y_pace, brackets)
            by_aisle = cands.aisle_buckets()
        else:
            geo = self._geo_memo
            if geo is None:
                geo = {}
            by_aisle = {}
            _seq = 0
            for b in cands:
                e = geo.get(id(b))
                if e is None or e[0] is not b:
                    e = geo[id(b)] = (b, b.location[0],
                                      x_pace * b.x_phys + y_pace * b.y_phys,
                                      height_multiplier(brackets, b.y_phys))
                by_aisle.setdefault(e[1], {}).setdefault(e[3], []).append((e[2], _seq, b))
                _seq += 1
            for groups in by_aisle.values():
                for m, lst in groups.items():
                    heapq.heapify(lst)
                    groups[m] = HeapBucket(lst)
        self._by_aisle = by_aisle
        self._writer = _bucket_writer(by_aisle, 2)
        # THE AISLE'S FIRST-APPEARANCE RANK is what makes the selection heap in `take`
        # byte-identical rather than merely equivalent.  The scan it replaces was
        # `for aid in by_aisle: if score < best_score` -- a strict `<` over a dict in insertion
        # order, so among EQUAL scores the earliest-inserted aisle wins.  A heap is not stable
        # on equal keys, so ordering it by `score` alone would pick an arbitrary one of the
        # tied aisles.  Ordering by `(score, rank)` restores the scan's rule exactly: an equal
        # score falls through to the smaller rank, which is the earlier insertion, which is the
        # aisle the scan kept.  Ranks are unique, so the pair is a strict total order.
        #
        # IT IS NO LONGER A DICT.  The rank was `{aid: i for i, aid in enumerate(by_aisle)}` --
        # one ~1,400-entry dict built per pool open at campaign scale, to seat ~12 units.  It
        # had exactly two readers and neither needs the mapping: the run-boundary loop already
        # walks `by_aisle` in order, so `enumerate` hands it the same integer; and the winner
        # refresh pushes back the rank it just POPPED off the heap, which came from the same
        # enumeration for the same aisle.  Same integers, same tie-break, no dict.
        #
        # running per-aisle total (handling+travel) labor, seeded from the maintained sum
        self._load = _seed_floats(aisle_pick_load_sum, by_aisle)
        # running per-aisle expected picked-volume mass (raw f*q*vol), seeded likewise
        self._vol_load = (_seed_floats(self._avs, by_aisle) if self._cart_on else None)

        self._run_sku = _NO_RUN_SKU
        self._var = self._fq = self._m_s = 0.0
        self._ab_cache: dict = {}
        self._pp: dict = {}           # per_pick per height multiplier, for the current run
        # THE HEAD VECTOR, per aisle: `((m, D, bin), ...)` for the buckets that still have a
        # head, in `by_aisle[aid]` order.  Var-independent, so it survives a run boundary --
        # see `_aisle_best`, which builds an entry lazily and is the only reader.  Dropped
        # for ONE aisle at the one site that moves a head.
        self._hv: dict = {}
        self._sel: list = []          # (score, rank, aid) min-heap; see `take`

    def __len__(self):
        return sum(len(h) for g in self._by_aisle.values() for h in g.values())

    def sort_key(self, unit):
        """Longest-processing-time first: the highest expected-labor unit is placed while
        the most aisles are still cheap.  A FIFO window degrades LPT to arbitrary-order
        greedy; the balance mechanics below are untouched by that."""
        return unit.order.expected_labor

    # ── the scoring expressions, verbatim ─────────────────────────────────────────
    def _cart_cost(self, v_raw):
        """Expected cart-swap cost for an aisle holding raw volume mass v_raw."""
        return self._cart_coef * max(0.0, v_raw / self._cap_raw - 1.0)

    def _aisle_best(self, aid, var, pp=None):
        """(cost, mult, bin) of the cheapest available bin in the aisle for this var.
        Height scales the whole at-location pick: cost = m*(intercept + per_item + var) + D.

        `pp` memoises `per_pick(m, ...)` per height multiplier for THIS var -- the value
        depends on (m, var) and not on the aisle, so a SKU run evaluates it at most once per
        bracket instead of once per aisle per bracket.  Same call, same float.

        THE HEAD VECTOR is what this reads instead of the bucket dict.  The body used to walk
        `self._by_aisle[aid].items()` and pull `h.head` through an attribute per bracket, and
        it is called once per live aisle at every SKU-run boundary -- ~1,400 x ~12 per open,
        ~650,000 opens per arm, the largest single term in the gain evaluator.  The `(m, D,
        bin)` triples it was re-extracting are invariant between head moves, so they are
        cached per aisle and the loop is a tuple walk plus `pp[m] + D`.

        The cache is var-INDEPENDENT on purpose: `var` changes at every run boundary, so a
        cached COST would be wrong there, while the heads have not moved.  Invalidation is
        one aisle at the one site that moves a head (`pop_top` in `take`), and that is
        complete because bins only ever leave a pool and `_Cursor`'s exclusion set is fixed
        for the pool's life.

        Same operands in the same order, so the strict `<` keeps the same winner on an exact
        tie, and an empty vector is the `best is None` the `continue` used to produce."""
        hv = self._hv.get(aid)
        if hv is None:
            hv = self._hv[aid] = tuple(
                (m, t[0], t[1]) for m, h in self._by_aisle[aid].items()
                for t in (h.head,) if t is not None)
        if not hv:
            return None
        best = None
        if pp is None:
            pp = {}
        intercept, per_item = self._intercept, self._per_item
        for m, D, b in hv:
            p = pp.get(m)
            if p is None:
                p = pp[m] = per_pick(m, intercept, var, 1, per_item)
            cost = p + D
            if best is None or cost < best[0]:
                best = (cost, m, b)
        return best

    def _score_of(self, aid, ab, sku, fq, m_s):
        """The per-(unit, aisle) score -- the ORIGINAL expressions verbatim.

        balance TOTAL expected aisle labor = handling+travel + expected cart swaps,
        so an aisle nearing a full cart is penalised and further volume disperses.
        The SKU's volume mass counts ONCE per aisle (a second bin of a SKU already
        here adds no new expected picked volume), mirroring aisle_pick_load_sum."""
        score = self._load[aid] + fq * ab[0]
        if self._cart_on:
            add = 0.0 if sku in self._ass[aid] else m_s
            score += self._cart_cost(self._vol_load[aid] + add)
        return score

    # ── one placement ─────────────────────────────────────────────────────────────
    def take(self, unit):
        """(bin, score) for one unit; (None, None) when no aisle has a bin left.

        `score` is the MARGINAL expected labor this placement adds, `fq * cost` -- the
        quantity the running balance is updated with, so reporting it is free.  The aisle
        total `best_score` would be the wrong number to persist: it carries every prior
        placement in that aisle and is not comparable across aisles, waves or arms.
        """
        by_aisle = self._by_aisle
        c = unit.order
        sku = c.sku
        if sku != self._run_sku:                 # run boundary: rebuild both caches
            self._run_sku = sku
            self._var = var = c.handle_var
            self._fq = fq = self._fbs.get(sku, 0.0) * self._qbs.get(sku, 0.0)
            self._m_s = m_s = self._svp.get(sku, 0.0) if self._cart_on else 0.0
            self._ab_cache.clear()
            sel = []
            # THE HOT LOOP.  At campaign scale under the gain evaluator this ran 29 million
            # aisle evaluations on a six-day coupled unit (nearly every unit opens a SKU run,
            # because a load carries mostly distinct SKUs), and the method calls cost more
            # than the arithmetic.  So: `pp` memoises `per_pick` per height multiplier for
            # this var, the bucket `head` is a plain attribute, and `_score_of` is inlined
            # here expression for expression (the method itself still serves the winner
            # refresh below, which is what pins the two to the same floats).
            pp = self._pp = {}
            load, cart_on = self._load, self._cart_on
            ab_cache = self._ab_cache
            if cart_on:
                ass, vol_load = self._ass, self._vol_load
                cart_coef, cap_raw = self._cart_coef, self._cap_raw
            # `enumerate` IS the rank -- see `_rank` in `__init__`.  `by_aisle` is walked in
            # its own insertion order here exactly as the retired dict comprehension walked
            # it, so `i` is the integer that dict would have returned for `aid`.
            for i, aid in enumerate(by_aisle):
                # ONE `_aisle_best` CALL PER LIVE AISLE, deliberately not inlined: the call
                # is what `test_placement_selection_is_not_a_scan.py` counts to tell this
                # rebuild from a per-take scan, and the per-call cost is now the loop over
                # ~3 bucket heads (plain attributes) with `per_pick` memoised in `pp`.
                best = self._aisle_best(aid, var, pp)
                ab_cache[aid] = best
                if best is not None:
                    sc = load[aid] + fq * best[0]
                    if cart_on:
                        add = 0.0 if sku in ass[aid] else m_s
                        sc += cart_coef * max(0.0, (vol_load[aid] + add) / cap_raw - 1.0)
                    sel.append((sc, i, aid))
            heapq.heapify(sel)                   # O(A) at C level, same as the old rebuild
            self._sel = sel
        else:
            var, fq, m_s = self._var, self._fq, self._m_s

        # ── the argmin, as a SELECTION rather than a SCAN ──────────────────────────
        # This was `for aid in by_aisle:` over every aisle, on every placement -- an O(A)
        # linear scan solving a selection problem, and at campaign scale it was the single
        # largest cost in the inbound gain evaluator: 4,852,858 takes x 224 aisles =
        # 1.09 BILLION iterations, 71.2% of the receive drain (the candidate slice that was
        # built to attack the other 28.8% could not touch one iteration of it).
        #
        # A heap is correct here for the reason the class docstring already states about the
        # caches: within a SKU run every input a NON-winning aisle's score reads is frozen --
        # `fq`/`var`/`m_s` are per-SKU constants, and `load`/`vol_load`/`aisle_sku_sets` and
        # the deque heads all move for the WINNING aisle only.  So exactly one entry changes
        # per placement, which is precisely the update a heap does cheaply.
        #
        # NO LAZY DELETION, and that is worth stating because it is the usual cost of this
        # pattern: the heap holds exactly ONE entry per live aisle at all times.  The run
        # boundary seeds one per aisle; each placement pops the winner and pushes back at most
        # one refreshed entry; a non-winner is never touched.  So the top of the heap is always
        # current and there is nothing stale to skip.
        #
        # Ordering is `(score, rank)` -- see `_rank` in `__init__` for why the rank is what
        # keeps this byte-identical on a score tie rather than merely equivalent.
        sel = self._sel
        if not sel:
            return None, None
        _sc, _rk, best_aid = heapq.heappop(sel)   # score and rank ordered the pop, nothing more
        # Non-None by construction: only aisles with a non-None `_aisle_best` are ever pushed,
        # at the run boundary and on refresh alike, so the popped aisle always has a choice.
        best_choice = self._ab_cache[best_aid]
        cost, m, chosen = best_choice
        marginal = fq * cost
        self._load[best_aid] += marginal

        # commit manager aisle state (travel-blind sums; mirrors _RankedAssignPool).
        # `_vol_load` is the pool's own drain-scoped copy -- see `_travel_balanced_impl`.
        if sku not in self._ass[best_aid]:
            if self._cart_on:                 # SKU-once, in lockstep with pick_load_sum
                self._vol_load[best_aid] += m_s
            self._led.add_sku(best_aid, sku, self._s2i.get(sku), demand=fq,
                              pick_load=self._splp.get(sku, 0.0),
                              vol=(m_s if self._cart_on else None))

        self._writer(best_aid, m).pop_top()
        # THE ONE SITE THAT MOVES A HEAD, so the one site that drops a head vector -- and
        # the one that asks for a WRITABLE bucket: over a shared template `_writer` clones
        # this bucket's cursor so the template every other open is reading is untouched;
        # over a plain dict it is the two lookups it always was (`_bucket_writer`).  Bins
        # only ever leave a pool and the exclusion set is fixed for its life, so no other
        # path can invalidate this cache -- see `_aisle_best`.
        del self._hv[best_aid]
        # Only the winner's inputs changed (head advanced; load; maybe sku-set/vol_load):
        # refresh its cache entries; an exhausted aisle goes None and is skipped exactly
        # like the original `continue`.
        ab = self._aisle_best(best_aid, var, self._pp)
        self._ab_cache[best_aid] = ab
        # An exhausted aisle is simply NOT pushed back -- that is how it leaves the heap, and it
        # is exactly the `continue` the old scan did on a None `_aisle_best`.  It cannot come
        # back, because bins only ever leave a pool; `by_aisle` keeps the (now empty) key, which
        # is why the scan needed the None check at all and the heap does not.
        if ab is not None:
            sc = self._score_of(best_aid, ab, sku, fq, m_s)
            # `_rk` is this aisle's rank, popped two dozen lines up from the entry this push
            # replaces.  It is the same integer the retired `_rank` dict held for `best_aid`,
            # because both come from one `enumerate(by_aisle)` and an aisle's rank never moves.
            heapq.heappush(sel, (sc, _rk, best_aid))
        return chosen, marginal


def _build_travel_balanced_pool_fn(affinity, wp, ledger, sku_pick_load_product,
                                   freq_by_sku, qty_by_sku, cart=None, geo_memos=None):
    # `sku_pick_load_product` is NOT on the ledger, and that is the line ticket 21 draws:
    # the ledger carries the AISLE books this family COMMITS to (`ledger_terms`), and `take`
    # only READS the per-SKU tables -- which is exactly why they are absent from the gain
    # evaluator's copy list too.
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum, aisle_pick_load_sum = ledger.demand_sum, ledger.pick_load_sum
    # id(resolved wp) -> (wp, {id(bin): (bin, aisle_id, D, height_mult)}).  Keyed by profile
    # because D and M depend on it; the wp is kept in the value so a recycled id() cannot alias
    # a stale table.  Lives as long as the placement function, i.e. one arm.
    #
    # `geo_memos` hands that lifetime to the CALLER, and exists for one of them: the inbound
    # gain evaluator rebuilds this policy over fresh COPIES of the aisle state on every
    # evaluation (`Inbound.gain._make_pool`), so a builder-local memo would be born empty
    # every time and every candidate bin's geometry recomputed -- inside a loop that opens a
    # pool O(yard^2) times per drain.  What the memo holds is BIN GEOMETRY, which no copy of
    # the aisle state can move, so sharing it across those rebuilds is the same value by the
    # same argument that makes it safe across opens within one arm.  None -- every production
    # call -- keeps the memo builder-local, byte for byte as before.
    _geo_memos: dict = {} if geo_memos is None else geo_memos

    def open_pool(candidates, rep=None):
        w = _wp_for(wp, rep) if rep is not None else wp   # per-regime cost, mixed warehouse
        ent = _geo_memos.get(id(w))
        if ent is None or ent[0] is not w:
            ent = _geo_memos[id(w)] = (w, {})
        return _TravelBalancedPool(
            candidates, affinity, w,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_pick_load_sum,
            sku_pick_load_product, freq_by_sku, qty_by_sku, cart=cart, geo_memo=ent[1])
    return open_pool


def build_ranked_labor_fn(
    affinity,
    wp,
    ledger,
    sku_pick_load_product : dict,
    freq_by_idx           : dict,
    freq_by_sku           : dict,
    qty_by_sku            : dict,
    beta                  : float = 1.0,
):
    """Travel-aware LPT labor balancer (see _travel_balanced_impl): places each unit in
    the specific empty bin that minimises the busiest aisle's total expected labor,
    where labor = freq·qty·(pick_time + travel_time).  Returns (unit, bin) pairs."""
    def ranked_assign(units: list, candidates_fn) -> list:
        return _travel_balanced_impl(
            units, candidates_fn, affinity, wp, ledger,
            sku_pick_load_product, freq_by_sku, qty_by_sku)
    return ranked_assign


def build_ranked_labor_pool_fn(
    affinity, wp, ledger, sku_pick_load_product, freq_by_idx, freq_by_sku,
    qty_by_sku, beta: float = 1.0, geo_memos=None,
):
    """Pool twin of build_ranked_labor_fn — same signature, plus the optional caller-owned
    geometry memo (`_build_travel_balanced_pool_fn`, which carries the whole argument).
    Omitted, as every production call omits it, this is the builder it always was."""
    return _build_travel_balanced_pool_fn(
        affinity, wp, ledger, sku_pick_load_product, freq_by_sku, qty_by_sku,
        geo_memos=geo_memos)


def build_ranked_cartlabor_fn(
    affinity,
    wp,
    ledger,
    sku_pick_load_product : dict,
    sku_vol_product       : dict,
    expected_batch_skus   : float,
    freq_by_idx           : dict,
    freq_by_sku           : dict,
    qty_by_sku            : dict,
    beta                  : float = 1.0,
):
    """Cart-swap-aware LPT labor balancer (Rank_cartlabor).  Same as build_ranked_labor_fn
    but the balanced aisle load also includes each aisle's EXPECTED cart-swap cost
    (cart_swap_coef·max(0, expected_aisle_volume/cart_capacity − 1)), so high-volume demand
    that would overflow a cart is dispersed across aisles.  With the big store cart the term
    is ~0 (aisles rarely fill a cart) so store plans barely move; with the small fulfillment
    cart it bites.  Returns (unit, bin) pairs."""
    total_freq = sum(freq_by_sku.values())
    def ranked_assign(units: list, candidates_fn) -> list:
        return _travel_balanced_impl(
            units, candidates_fn, affinity, wp, ledger,
            sku_pick_load_product, freq_by_sku, qty_by_sku,
            cart=(ledger.vol_sum, sku_vol_product, expected_batch_skus, total_freq))
    return ranked_assign


def build_ranked_cartlabor_pool_fn(
    affinity, wp, ledger, sku_pick_load_product, sku_vol_product,
    expected_batch_skus, freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
    total_freq=None, geo_memos=None,
):
    """Pool twin of build_ranked_cartlabor_fn — same signature.

    `total_freq` is summed HERE, once when the policy is built, exactly as the wave builder
    does it: a per-pool sum over the same dict would be the same value today but would make
    a dict-order change silently repricing every cart penalty.

    Which is the whole reason it can be passed IN.  A caller that rebuilds this policy per
    evaluation rather than per arm -- there is one, the inbound gain evaluator -- would turn
    "once when the policy is built" into once per pool open, an O(catalogue) sum in an
    O(yard^2)-per-drain loop, and would re-expose exactly the dict-order hazard the
    paragraph above closes.  Such a caller hoists the sum to its own arm scope and hands it
    down; it must be `sum(freq_by_sku.values())` over this same dict and nothing else.
    None -- every production call -- sums here as before."""
    total_freq = sum(freq_by_sku.values()) if total_freq is None else float(total_freq)
    return _build_travel_balanced_pool_fn(
        affinity, wp, ledger, sku_pick_load_product, freq_by_sku, qty_by_sku,
        cart=(ledger.vol_sum, sku_vol_product, expected_batch_skus, total_freq),
        geo_memos=geo_memos)


def _ranked_minlabor_impl(units, candidates_fn, affinity, wp, ledger,
                          freq_by_idx, freq_by_sku, qty_by_sku, lam, maximize=False):
    """Greedy MINIMISER (or, with maximize=True, MAXIMISER) of expected total task labor.

    SUPERSEDED by `_MinLaborPool`, which is what `rank_minlabor` and `rank_maxlabor` run;
    kept as the frozen oracle the port is tested against.

    Models the objective E[task labor] = Σ_s f_s·[ M(y_s)·(intercept + q_s·v_s) + D ] and
    places each unit in the (aisle, bin) that minimises its MARGINAL contribution:

        cost(s, b) = f_s·( M(y_b)·(intercept + v_s) + D_b )  −  λ·Σ_{partners p in aisle} lift(s,p)·f_p

    where v_s = handle_var (per-unit weight/volume term), M(y) = height bracket multiplier,
    D_b = x_speed·x_phys + y_speed·y_phys.  This fuses three slotting levers into one score:
      • golden-zone height  — the M(y_b)·v_s term pushes heavy/frequent SKUs to low brackets;
      • effort-to-front     — the D_b term pulls them to near, low bins;
      • affinity compaction — the −λ·delta_lift aisle reward co-locates demand partners in the
        SAME aisle (the big travel win: fewer aisle-tasks), and within the chosen aisle the bin
        is pulled toward the partners' column centroid (+x_speed·|x−cx|) to shorten the sweep.

    maximize=True flips every extremum (argmax score, farthest/highest bins, scatter partners)
    to deliberately MAXIMISE the objective — a worst-case upper-bound sanity control (Rank_maxlabor)
    that brackets how much the minimiser is worth.  Unlike _travel_balanced_impl (Rank_labor) there
    is NO LPT load term — this MINIMISES (or maximises) total labor, it does not BALANCE it.  Units
    are placed highest expected_labor first so the costliest SKUs claim the best (or worst) slots.

    Both the aisle pre-screen AND the final bin choice scan only per-(aisle,bracket) extremal-D
    deque ends — O(units·aisles·brackets), independent of bins-per-aisle.  The chosen bin is popped
    from its deque end, so the ends are always live (no stale-bin bookkeeping).

    THIN DRIVER over `_MinLaborPool` since ticket 04. It was a second implementation of the same
    scoring rule, and the wave form has had no production caller since the pool inversion --
    14 of 17 shipped rules are pooled and the other three have no group path at all. What it
    is FOR is the equivalence suites, and they gain from this rather than lose: the reference
    they compare against is the FROZEN oracle in `Tests/calltree/test_rank_cache_equivalence.py`, and this leg is now the drain
    contract (pool driven in the wave's own order == wave) instead of an echo of it.

    `pool.order(units)` is the sort the body used to do inline, and driving through it is the
    whole claim: given the same order, the pool makes bit-identical decisions. A drain that
    declines that order gets a different -- and intentionally different -- answer.
    """
    if not units:
        return []
    wp = _wp_for(wp, units[0])       # per-regime cost in a mixed warehouse
    pool = _MinLaborPool(
        list(candidates_fn(units[0])), affinity, wp,
        ledger.sku_sets, ledger.idx_sets, ledger.demand_sum, ledger.member_pos,
        freq_by_idx, freq_by_sku, qty_by_sku, lam, maximize=maximize)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


class _MinLaborPool(_Pool):
    """`_ranked_minlabor_impl` as a pool -- `rank_minlabor`, and `rank_maxlabor` with
    `maximize=True` (one function, both arms, every extremum flipped).

    Unlike `_TravelBalancedPool` there is NO per-aisle running load: this policy MINIMISES
    total labor rather than BALANCING it.  Its path dependence flows entirely through the
    deques (pool state) and through two manager dicts that `take` commits to --
    `aisle_idx_sets`, which the affinity reward reads, and `aisle_member_pos`, whose appended
    columns the partner centroid sums in placement order.  That second one is the quieter of
    the two and worth naming: `cx` is not a tie-break, it is the term that picks the bin.

    THE DELTA SUM IS INLINED ON PURPOSE (`for ci, w in row_items: if ci in ais`).  It looks
    like a candidate for `_delta_lift_from_row`, and unifying them would be a bug:
    `_delta_lift_from_row` switches which side it iterates on `len(row) <= len(member_set)`,
    so its summation ORDER flips as an aisle fills.  That flip is the one-ulp drift b91cf38
    was written to fix, pinned by Tests/calltree/test_rank_cache_equivalence.py.  This loop
    iterates CSR column order unconditionally and must keep doing so.

    The SKU-run cache (`bc_by_aid`, `row_items`, `max_reward`) is rebuilt at the run
    boundary and carries `_sel` alongside it: one entry per live aisle, SORTED by
    `(fq*bc, rank)` and read front-to-back without being consumed.  `take` walks a prefix of
    it until the affinity prune fires, so the structure has to survive the walk -- see the
    selection block for why that rules out a heap.  The winner's `bc` is refreshed EAGERLY
    at the end of the take that moved it, and its one entry is bisected out and reinserted;
    a unit that finds no bin leaves the list untouched, because the walk never wrote to it.
    """

    __slots__ = ('_aff', '_ass', '_ais', '_ads', '_amp', '_fbi', '_fbs', '_qbs', '_lam',
                 '_maximize', '_intercept', '_per_item', '_x_pace', '_D_of', '_by_aisle_brkt',
                 '_s2i', '_matrix', '_rep', '_drop', '_last_sku',
                 '_bc_by_aid', '_row_items', '_max_reward', '_led',
                 '_pp', '_row', '_deltas', '_sel', '_writer')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_member_pos, freq_by_idx, freq_by_sku,
                 qty_by_sku, lam, maximize=False):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._amp = aisle_demand_sum, aisle_member_pos
        self._fbi, self._fbs, self._qbs = freq_by_idx, freq_by_sku, qty_by_sku
        self._lam, self._maximize = lam, maximize
        self._led = AisleLedger.over(sku_sets=aisle_sku_sets, idx_sets=aisle_idx_sets,
                                     demand_sum=aisle_demand_sum,
                                     member_pos=aisle_member_pos)
        self._s2i, self._matrix = affinity._sku_to_idx, affinity._matrix

        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._x_pace = x_pace
        self._intercept = wp.pick_intercept
        self._per_item = wp.pick_per_item     # the per-unit charge, priced as the sim bills it
        brackets = getattr(wp, 'height_brackets', ())

        # minimise -> near (min-D) deque head; maximise -> far (max-D) deque tail.
        self._rep  = (lambda dq: dq[-1]) if maximize else (lambda dq: dq[0])
        self._drop = (lambda dq: dq.pop()) if maximize else (lambda dq: dq.popleft())

        if isinstance(cands, TierSlice):
            # THE OVERLAY (frozen_tier.py): cursors speak the deque protocol `_rep` /
            # `_drop` read (`[0]`, `[-1]`, `popleft`, `pop`), so `take` is one code path.
            _check_tier(cands.tier, x_pace, y_pace, brackets)
            D_of = cands.tier.D_by_id
            by_aisle_brkt = cands.aisle_buckets()
        else:
            D_of = _D_map(cands, x_pace, y_pace)
            M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
            by_aisle_brkt = {}                            # {aisle: {mult: D-sorted deque}}
            for b in cands:
                by_aisle_brkt.setdefault(b.location[0], {}).setdefault(
                    M_of[id(b)], []).append(b)
            for groups in by_aisle_brkt.values():
                for m, lst in list(groups.items()):
                    lst.sort(key=lambda bb: D_of[id(bb)])
                    groups[m] = deque(lst)
        self._D_of, self._by_aisle_brkt = D_of, by_aisle_brkt
        self._writer = _bucket_writer(by_aisle_brkt, 2)

        self._last_sku = None
        self._sel: list = []          # (score, rank, aid) min-heap; see `take`
        self._bc_by_aid: dict = {}
        self._row_items: list = []
        self._max_reward = 0.0
        # THE PER-SKU-RUN CACHES (ticket 03, phase-2 campaign), all three rebuilt at the run
        # boundary in `take` and read for every unit of the run: `per_pick` per height
        # multiplier for this SKU's var; the centroid's partner row; and the per-aisle
        # affinity deltas, folded once through the ledger's inverse (`_partner_deltas`).
        self._pp: dict = {}
        self._row: dict = {}
        self._deltas: dict | None = None

    def __len__(self):
        return sum(len(dq) for g in self._by_aisle_brkt.values() for dq in g.values())

    @property
    def prefers_low(self):
        return not self._maximize      # rank_maxlabor is a worst-case control

    def sort_key(self, unit):
        """Costliest SKUs claim the best (or, for maxlabor, the worst) slots first."""
        return unit.order.expected_labor

    def _better(self, a, b):                 # is a a better (more extreme) score than b?
        return a > b if self._maximize else a < b

    def _aisle_best_cost(self, aid, var, pp=None):
        """Extremal (min, or max if maximize) over the aisle's bracket ends of the
        per-pick labor + travel:  M*(intercept + per_item + var) + D  (height scales the whole pick).

        `pp` memoises `per_pick(m, ...)` per height multiplier for THIS var -- the value
        depends on (m, var), never on the aisle, so a SKU run evaluates it at most once per
        bracket instead of once per aisle per bracket (26 M calls on a six-day coupled unit
        at campaign scale).  Same call, same float."""
        best = None
        if pp is None:
            pp = {}
        intercept, per_item, D_of, rep = self._intercept, self._per_item, self._D_of, self._rep
        for m, dq in self._by_aisle_brkt[aid].items():
            if not dq:
                continue
            p = pp.get(m)
            if p is None:
                p = pp[m] = per_pick(m, intercept, var, 1, per_item)
            cost = p + D_of[id(rep(dq))]
            if best is None or self._better(cost, best):
                best = cost
        return best

    def _partner_deltas(self, row_items):
        """`{aisle: sum of w over the SKU's partners placed there}`, folded ONCE per SKU run
        through the ledger's inverse -- or None when the aisle book carries no inverse, in
        which case `take` folds per aisle as it always did.

        BIT-IDENTICAL TO THE PER-AISLE LOOP.  That loop is `delta = 0.0; for ci, w in
        row_items: if ci in ais: delta += w` -- a left fold from 0.0 in ROW order over the
        partners the aisle holds.  This walks `row_items` once in that same order and adds
        each partner's term to every aisle the inverse says holds it, so each aisle receives
        exactly the terms the loop would have summed, in the same sequence, from the same
        0.0 (the `_co_by_aisle` argument, W8 stage 2).  Aisles holding no partner are absent
        and read as 0.0, which is what the loop computed for them.

        Under the gain evaluator the book is a copy-on-write view: its live half IS the
        owner's dict, whose inverse is exact for every aisle the view has NOT overridden;
        an overridden aisle (one this pool's earlier SKU runs virtually placed into) is
        folded the old way over the view's own set.  Within one SKU run the only membership
        that moves is this SKU's own index, which is no partner of itself, so the fold is
        valid for the whole run."""
        ais = self._ais
        inv = getattr(ais, 'inverse', None)
        over = None
        if inv is None:
            live = getattr(ais, '_live', None)
            inv = getattr(live, 'inverse', None)
            over = getattr(ais, '_over', None)
        if inv is None:
            return None
        deltas: dict = {}
        for ci, w in row_items:
            held = inv.get(ci)
            if held:
                for aid in held:
                    deltas[aid] = deltas.get(aid, 0.0) + w
        if over:
            for aid in over:
                members = ais[aid]
                delta = 0.0
                for ci, w in row_items:
                    if ci in members:
                        delta += w
                deltas[aid] = delta
        return deltas

    def take(self, unit):
        """(bin, score) for one unit; (None, None) when nothing is placeable.

        `score` is the AISLE decision score, `fq*bc - lam*delta` -- the marginal labor of
        the aisle's best bracket end minus the affinity reward, which is the objective the
        argmin actually compared.  It is deliberately not `cbest`: the bin is chosen in a
        second pass that adds a centroid term, and that term is a tie-shaping device rather
        than labor, so persisting it would put a different quantity in the same column.
        """
        by_aisle_brkt, lam = self._by_aisle_brkt, self._lam
        maximize = self._maximize
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = self._fbs.get(sku, 0.0) * self._qbs.get(sku, 0.0)

        if sku != self._last_sku:
            # Slice the SKU's affinity row ONCE per run (not once per unit/aisle):
            # partners as (partner_idx, f_p*(lift-1)) pairs, all non-negative
            # (association above independence, mirroring _demand_weighted_delta_lift).
            # max_reward bounds lam*delta over any aisle (all partners present),
            # enabling the early-termination prune below.
            row_items = []
            si = self._s2i.get(sku)
            matrix = self._matrix
            if si is not None and matrix is not None:
                st = int(matrix.indptr[si]); e = int(matrix.indptr[si + 1])
                for ci, d in zip(matrix.indices[st:e], matrix.data[st:e]):
                    w = (float(d) - 1.0) * self._fbi.get(int(ci), 0.0)
                    if w:
                        row_items.append((int(ci), w))
            self._row_items = row_items
            self._max_reward = lam * sum(w for _, w in row_items)

            # Cheap per-aisle bin cost (O(brackets)); ordered so the affinity prune can fire.
            pp = self._pp = {}
            bc_by_aid = {}
            sel = []
            for i, aid in enumerate(by_aisle_brkt):
                bc = self._aisle_best_cost(aid, var, pp)
                if bc is not None:
                    bc_by_aid[aid] = bc
                    # `i` IS the rank, and it is the tie-break that makes the heap below
                    # byte-identical to the stable sort it replaces -- see `take`'s
                    # selection block.  Ranks come from one `enumerate(by_aisle_brkt)` and
                    # never move, so an aisle's rank is the same integer wherever it is read.
                    sel.append(((-(fq * bc) if maximize else fq * bc), i, aid))
            # SORTED, not heapified.  See the selection block in `take` for why a heap is
            # the wrong structure for this pool: one `sort` of plain tuples here, with no
            # key function, and the list is then read front-to-back without being consumed.
            sel.sort()
            self._sel = sel
            self._bc_by_aid = bc_by_aid
            self._row = _partner_row(self._aff, sku)
            self._deltas = self._partner_deltas(row_items) if row_items else {}
            self._last_sku = sku

        bc_by_aid, row_items = self._bc_by_aid, self._row_items
        max_reward = self._max_reward
        deltas = self._deltas
        if not bc_by_aid:
            return None, None
        # minimise: ascending fq*bc, prune once base - max_reward >= best (reward can't save
        # it).  maximise: descending fq*bc, prune once base <= best (reward only lowers it).
        #
        # THIS WAS A FULL SORT, PER UNIT -- `sorted(bc_by_aid, key=lambda a: fq*bc_by_aid[a])`,
        # ~1,400 lambda calls and ~14,700 comparisons on every placement, when within a SKU
        # run only the WINNER's `bc` ever moves.  `self._sel` is that order, sorted ONCE at
        # the run boundary and repaired one entry at a time.
        #
        # A SORTED LIST, AND NOT A HEAP, BECAUSE OF WHAT THIS LOOP DOES.  It walks a PREFIX
        # of unknown length until the affinity prune fires; it does not extract a minimum.
        # A heap can only be read in order by destroying it, so a deep walk costs a full
        # drain and a full rebuild -- and that is not hypothetical: converted to a heap on
        # 2026-09-20 this pool ran 28% SLOWER at 400k SKUs on a `uni_` warehouse, where the
        # aisles are uniformly filled, an affinity row finds most of its partners placed,
        # and `max_reward` swamps the spread of `fq*bc` so the prune never fires.  The
        # sibling `_TravelBalancedPool` DOES extract exactly one minimum per take, which is
        # why a heap is right there and wrong here; the two are not the same conversion.
        #
        # A list is at least as good in every regime: the walk reads tuples with no calls at
        # all (against one `heappop` each), and the repair is one `bisect` delete plus one
        # `insort` -- two memmoves of a few KB -- against a drain and a rebuild.
        #
        # BYTE-IDENTICAL, on two clauses rather than on hope.  (1) `sorted(key=...)` was
        # stable, so equal `fq*bc` kept `bc_by_aid` insertion order, which is
        # `by_aisle_brkt` order; the list carries that order as an explicit rank, and ranks
        # are unique, so `(key, rank)` is a strict total order reproducing the stable sort.
        # (2) The prune breaks on the same test at the same point because the list yields
        # the aisles in that same sequence -- so the aisles VISITED, and the `self._better`
        # first-wins-on-ties among them, are unchanged.
        sel = self._sel
        best_aid = None
        best_score = None
        win_ent = None
        for ent in sel:
            aid = ent[2]
            base = fq * bc_by_aid[aid]
            if best_score is not None:
                if maximize:
                    if base <= best_score:
                        break
                elif base - max_reward >= best_score:
                    break
            if row_items:
                if deltas is not None:
                    delta = deltas.get(aid, 0.0)
                else:
                    ais = self._ais[aid]
                    delta = 0.0
                    for ci, w in row_items:
                        if ci in ais:
                            delta += w
            else:
                delta = 0.0
            score = base - lam * delta
            if best_score is None or self._better(score, best_score):
                best_score, best_aid, win_ent = score, aid, ent
        if best_aid is None:
            return None, None

        # Final bin in the winning aisle: extremal bracket end (golden-zone min-D / worst
        # max-D per height band), with the centroid term pulling toward (min) or away from
        # (max) partners.
        _mass, cx = _demand_weighted_partner_centroid(
            self._aff, sku, self._amp[best_aid], self._fbi, row=self._row)
        chosen = chosen_m = None
        cbest = None
        intercept, per_item = self._intercept, self._per_item
        D_of, x_pace = self._D_of, self._x_pace
        # `pp` already holds `per_pick(m, ..., var, ...)` for this run -- `_aisle_best_cost`
        # filled it at the boundary three dozen lines up, keyed on the same `m` for the same
        # `var`.  This pass was calling `per_pick` again, per unit per bracket, for a float
        # the memo already had.  Same call, same float; `_aisle_best` states the identity.
        pp = self._pp
        for m, dq in by_aisle_brkt[best_aid].items():
            if not dq:
                continue
            b = self._rep(dq)
            p = pp.get(m)
            if p is None:
                p = pp[m] = per_pick(m, intercept, var, 1, per_item)
            cost = p + D_of[id(b)]
            if cx is not None:
                cost += x_pace * abs(b.x_phys - cx)
            if cbest is None or self._better(cost, cbest):
                cbest, chosen, chosen_m = cost, b, m
        if chosen is None:
            # Nothing moved, so the winner's entry is still current and `sel` was never
            # disturbed -- the walk above only READ it.  This is where the heap had to push
            # back what it had popped; a list has nothing to undo.
            return None, None
        # THE ONE SITE THAT MOVES A BIN, so the one that asks for a WRITABLE bucket:
        # over a shared template `_writer` clones this bucket's cursor so the template
        # every other open is reading is untouched; over a plain dict it is the two
        # lookups it always was (`_bucket_writer`).
        self._drop(self._writer(best_aid, chosen_m))
        # THE ONE-ENTRY UPDATE, done here rather than at the head of the next `take`.  The
        # retired `elif` branch refreshed the winner with the NEXT unit's `var`; within a SKU
        # run `var` is constant, and across one the boundary rebuilds everything, so the
        # value is the same wherever it is computed -- and doing it here is what lets the
        # heap hold no stale entry at any point.
        bc = self._aisle_best_cost(best_aid, var, self._pp)
        # THE ONE-ENTRY REPAIR.  `win_ent` is in `sel` and the list is sorted, so its
        # position is a binary search; entries are unique because the rank is, so the search
        # lands on it exactly.  A mismatch means the list stopped being sorted, which would
        # mis-price silently -- it raises instead, the way `_check_tier` does.
        i = bisect.bisect_left(sel, win_ent)
        if i >= len(sel) or sel[i] is not win_ent:
            raise RuntimeError(
                f'the min-labor pool\'s aisle order is no longer sorted: {win_ent!r} is not '
                f'at its own bisect position {i}. Every write to it goes through this one '
                f'site, so this means the order key moved without the list being repaired')
        del sel[i]
        if bc is None:
            bc_by_aid.pop(best_aid, None)       # exhausted: it leaves both books together
        else:
            bc_by_aid[best_aid] = bc
            bisect.insort(sel, ((-(fq * bc) if maximize else fq * bc),
                                win_ent[1], best_aid))

        if sku not in self._ass[best_aid]:
            self._led.add_sku(best_aid, sku, demand=fq)
        self._led.add_bin(best_aid, self._s2i.get(sku), chosen.x_phys)
        return chosen, best_score


def _build_minlabor_pool_fn(affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
                            lam, maximize=False):
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_demand_sum, aisle_member_pos = ledger.demand_sum, ledger.member_pos

    def open_pool(candidates, rep=None):
        return _MinLaborPool(
            candidates, affinity,
            _wp_for(wp, rep) if rep is not None else wp,   # per-regime cost, mixed warehouse
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
            freq_by_idx, freq_by_sku, qty_by_sku, lam, maximize=maximize)
    return open_pool


def build_ranked_minlabor_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Greedy minimiser of expected task labor (see _ranked_minlabor_impl): golden-zone
    height + effort-to-front + affinity compaction fused into one marginal-cost score.
    The opposite of build_ranked_labor_fn (which BALANCES aisle load).  `beta` is λ, the
    affinity-reward weight that converts lift·freq into the labor (time) units of the score."""
    _require_affinity(affinity, 'rank_minlabor')
    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_minlabor_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, lam=beta)
    return ranked_assign


def build_ranked_minlabor_pool_fn(
    affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_minlabor_fn — same signature."""
    _require_affinity(affinity, 'rank_minlabor')
    return _build_minlabor_pool_fn(
        affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
        lam=beta, maximize=False)


def build_ranked_maxlabor_fn(
    affinity,
    wp,
    ledger,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Worst-case sanity control: greedy MAXIMISER of expected task labor (Rank_maxlabor) —
    the exact mirror of build_ranked_minlabor_fn (high/far bins, scatter co-demanded SKUs).
    It should land WORST on the objective_task_labor metric, bracketing the minimiser so the
    lever's direction and magnitude are verifiable."""
    _require_affinity(affinity, 'rank_maxlabor')
    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_minlabor_impl(
            units, candidates_fn, affinity, wp, ledger,
            freq_by_idx, freq_by_sku, qty_by_sku, lam=beta, maximize=True)
    return ranked_assign


def build_ranked_maxlabor_pool_fn(
    affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_maxlabor_fn — same signature."""
    _require_affinity(affinity, 'rank_maxlabor')
    return _build_minlabor_pool_fn(
        affinity, wp, ledger, freq_by_idx, freq_by_sku, qty_by_sku,
        lam=beta, maximize=True)


def build_optmap_fn(mgr, capped=False):
    """Optimal-map (soft, score-matched) placement.  Places each unit in the free candidate
    bin whose quantity-free preferred score `mgr._bin_pref[bin]` matches the SKU's optimal
    target `mgr._map_target[sku]` (the pref of its labor-optimal bin from the full LAP).

    capped=False (default, the `map` arm): symmetric match — argmin |pref(b) − target|.
    Non-greedy in spirit (a low-value SKU's target is far from prime bins), but with no hard
    cap it can still UPGRADE into a prime bin when its own tier is full and the nearest free
    bin happens to be prime.

    capped=True (the `map_rank` arm): rank-relative, upgrade-CAPPED — the SKU never settles in
    a bin more prime (lower pref) than its own optimal rank.  Among free candidates it takes
    the closest one AT OR BELOW its tier (pref ≥ target); only if none are free does it fall
    back to the least-prime remaining bin.  This keeps prime spots open for the higher-ranked
    SKUs that future orders will bring.  (`_candidates` returns a single BinKey tier and pref
    is monotone with goodness within it, so the pref-floor is exactly the relative-rank cap.)

    Reads mgr state at call time, so build_optimal_map may run before or after this is wired.
    Per-unit (not a ranked wave); the drain places one unit at a time over live free bins."""
    def place_one(unit, candidates):
        if not candidates:
            return None
        pref = mgr._bin_pref
        tgt  = mgr._map_target.get(unit.order.sku)
        if tgt is None:                      # unknown SKU: no rank → don't waste a prime bin
            return (max(candidates, key=lambda b: pref.get(id(b), 0.0)) if capped
                    else min(candidates, key=lambda b: pref.get(id(b), 0.0)))
        if not capped:
            return min(candidates, key=lambda b: abs(pref.get(id(b), 0.0) - tgt))
        eligible = [b for b in candidates if pref.get(id(b), 0.0) >= tgt]   # tier or worse
        if eligible:                          # closest from the worse side (cap upgrades)
            return min(eligible, key=lambda b: pref.get(id(b), 0.0) - tgt)
        return max(candidates, key=lambda b: pref.get(id(b), 0.0))          # least-prime last resort
    place_one.name = 'optmap_rank' if capped else 'optmap'
    return place_one


class _OptMapPool(_Pool):
    """The optmap objective as a POOL: one `_PrefPool` over the group's candidates, one
    `take` per unit, and the order left entirely to the caller.

    Body-for-body the loop `build_optmap_wave_fn` already ran -- that function was written
    serving units in QUEUE ORDER with no priority re-sort, so it is the one ranked policy
    whose behaviour a pool reproduces exactly rather than approximately.  It is the proof
    that the seam is real before the four impls that DO re-sort are moved onto it.
    """

    __slots__ = ('_pool', '_target', '_pref', '_capped')
    # order(): inherited. optmap has no precedence opinion — it never had one, which is
    # exactly why it was the honest first port.

    def __init__(self, bins, target, bin_pref, capped: bool):
        self._pool   = _PrefPool(bins, bin_pref)
        self._target = target
        self._pref   = bin_pref
        self._capped = capped

    def __len__(self):
        return len(self._pool)

    def take(self, unit):
        """(bin, score) for one unit; (None, None) once the pool is exhausted.

        `score` is the objective this policy actually minimises -- the gap between the bin's
        pref and the SKU's map target -- read at the moment of choice, so persisting it
        costs a dict lookup and not a second scoring pass.  None target means the SKU has no
        map entry, and there is no gap to report.
        """
        tgt = self._target.get(unit.order.sku)
        if tgt is None:                       # unknown SKU: don't waste a prime bin
            b = self._pool.take_max() if self._capped else self._pool.take_min()
        elif self._capped:                    # nearest with pref >= target (else least-prime)
            b = self._pool.take_ge(tgt)
        else:                                 # symmetric closest match
            b = self._pool.take_closest(tgt)
        if b is None:
            return None, None
        score = None if tgt is None else abs(self._pref.get(id(b), 0.0) - tgt)
        return b, score


def build_optmap_pool_fn(mgr, capped=False):
    """`open_pool` for the optimal-map policies -- the pool twin of build_optmap_wave_fn."""
    def open_pool(candidates, rep=None):
        return _OptMapPool(candidates, mgr._map_target, mgr._bin_pref, capped)
    open_pool.name = 'optmap_rank' if capped else 'optmap'
    return open_pool


def build_optmap_wave_fn(mgr, capped=False):
    """Ranked-wave twin of build_optmap_fn: the SAME per-unit objective (argmin |pref−target|,
    or capped nearest-with-pref≥target), but the O(B) scan is amortized — one ``_PrefPool`` sort
    over the group's candidates per wave, then an O(log B) bisect per unit.  Units are served in
    QUEUE ORDER (no priority re-sort) so placement matches the per-unit ``place_one`` path except
    on exact pref ties.  A None (pool exhausted) falls to the per-unit straggler path, which
    re-fetches candidates and spills up tiers exactly as today."""
    def place_wave(units, candidates_fn):
        result: list = []
        if not units:
            return result
        pool   = _PrefPool(candidates_fn(units[0]), mgr._bin_pref)   # one BinKey tier per wave
        target = mgr._map_target
        for unit in units:
            tgt = target.get(unit.order.sku)
            if tgt is None:                       # unknown SKU: don't waste a prime bin
                b = pool.take_max() if capped else pool.take_min()
            elif capped:                          # nearest with pref ≥ target (else least-prime)
                b = pool.take_ge(tgt)
            else:                                 # symmetric closest match
                b = pool.take_closest(tgt)
            result.append((unit, b))
        return result
    place_wave.name = 'optmap_rank' if capped else 'optmap'
    return place_wave


# ── cluster_map: map-favored cluster anchoring + intra-aisle compaction ────────
#
# Mixes `map` (each SKU has a favored location: the optimal-map preferred score
# mgr._map_target[sku] over the bin basis mgr._bin_pref) with `clusters` (co-locate
# affinity partners and compact them within the aisle).  Per unit:
#   • aisle  — COHESION-FIRST: the aisle with the most demand-weighted association above
#     independence to its members (Σ(lift−1)·f via _demand_weighted_delta_lift); ties (and
#     the cold start where no aisle holds a partner yet) break toward the aisle whose best
#     bin pref is closest to the SKU's map target → degrades to `map`.
#   • bin    — anchor at the SKU's favored map location AND pull toward the partners' column
#     centroid:  cost(b) = |pref(b) − target| + W·x_pace·|x(b) − cx|.
#     capped=True (cluster_map_rank) reserves prime spots like map_rank: never settle in a
#     bin more prime than the target (pref ≥ target), fallback least-prime.
_CLUSTER_MAP_W_CENT = 1.0   # weight on the centroid-compaction term (pref & centroid are both s)


def _cluster_map_pick_bin(lst, pref, target, cx, x_pace, capped):
    """Choose the cluster's bin within one aisle: anchor at the favored map location and
    compact toward the partner centroid; honour the prime-spot cap when capped.

    Returns `(bin, cost)`.  The cost is RETURNED rather than recomputed by the caller, and
    that is deliberate: `_CLUSTER_MAP_W_CENT * x_pace * abs(...)` re-associated as
    `(W * x_pace) * abs(...)` is a different float, so a caller reconstructing the number
    would eventually persist something that is not what decided.  `None` on the capped
    least-prime fallback, which is chosen by max-pref and not by this cost at all --
    reporting the cost of a bin that was picked on a different rule would be a lie."""
    def cost(b):
        p = pref.get(id(b), 0.0)
        c = abs(p - target) if target is not None else p
        if cx is not None:
            c += _CLUSTER_MAP_W_CENT * x_pace * abs(b.x_phys - cx)
        return c
    if capped and target is not None:
        eligible = [b for b in lst if pref.get(id(b), 0.0) >= target]   # tier or worse
        if eligible:
            b = min(eligible, key=cost)
            return b, cost(b)
        b = max(lst, key=lambda b: pref.get(id(b), 0.0))    # least-prime last resort
        return b, None
    b = min(lst, key=cost)
    return b, cost(b)


def _cluster_map_choose_aisle(by_aisle, prefs_by_aisle, row, aisle_idx_sets, freq_by_idx, target,
                              lifts=None, cold_index=None):
    """Cohesion-first aisle: max Σ(lift−1)·f to members, tie-break / cold-start by anchor gap.

    Same argmax as ``max(live, key=(lift, -anchor_gap))`` but LAZY: the O(B) anchor-gap scan is
    replaced by an O(log) ``_closest_abs`` bisect on the aisle's pre-sorted ``prefs_by_aisle`` and
    is evaluated ONLY for the aisles tied at the max lift (in the warm case, one aisle wins on lift
    → no gap work at all).  ``row`` is the SKU's pre-sliced affinity row (hoisted once per unit).

    ``lifts`` may carry the delta values precomputed by a caller's same-SKU run cache (a
    superset keyed by aisle); values for the current live aisles are read from it instead
    of recomputed — identical numbers, because within a same-SKU run the member idx-sets
    gain only the SKU's own index, which (self-pairs are not stored) is never in ``row``."""
    # ONE pass, not four.  This block built `live`, rebuilt `lifts` onto it, took a `max`,
    # then re-filtered for `tied` -- four O(|live|) scans per placement, and the ladder could
    # see NONE of them, because a comprehension is one frame entry however wide it is.  The
    # cluster cells measured the real scan volume directly: Sum|live| = 8,141,090 over 76,514
    # calls at the 8k rung, fitting k=1.99 with local exponents pinned at 2.04.  The tracer
    # saw 14.4% of that, through `_closest_abs` alone, and still ranked it top.
    #
    # Fusing changes no result.  `best` is still the first maximal value under `>` (so NaN is
    # skipped exactly as `max` skips it), and `tied` is still in `by_aisle` iteration order,
    # which is what the `min(tied, ...)` tie-break below depends on -- `min` returns the
    # FIRST element achieving the minimum, so that order is load-bearing, not incidental.
    #
    # The two branches are written out rather than sharing a lookup closure: this is the hot
    # path, and a per-element Python call to fetch the lift would cost more than the pass it
    # saves.  It does NOT change the complexity class -- the scan is still O(|live|) per
    # placement and |live| still grows linearly with the catalogue.  See
    # docs/design/COMPLEXITY_ROUND_FINDINGS.md for the structural fix and why it is separate.
    # THE COLD START, answered without scanning (ticket 05).  `cold_index` is not None only
    # when the caller has established that this SKU has NO partner placed ANYWHERE -- and then
    # `_delta_lift_from_row` returns exactly 0.0 for every aisle (the intersection is empty in
    # all of them), so `best` is 0.0 and `tied` is every live aisle in `by_aisle` order.  That
    # is the whole of both scans, and the tie-break that remains -- "the live aisle whose pref
    # is closest to `target`" -- is a nearest-neighbour query the merged index answers in
    # O(log N + k) against O(A) here.
    #
    # This is the GROWING case, not a corner: the tied fraction was measured rising 3.0% ->
    # 14.4% across a 16x ladder (`docs/design/COMPLEXITY_ROUND_FINDINGS.md` section 3.5.1),
    # because `tied` is aisles whose lift compares exactly equal and a SKU with no partners
    # placed gives every one of them 0.0.
    if cold_index is not None:
        return cold_index.closest(target)
    best: float = -math.inf
    tied: list = []
    if lifts is None:
        for aid, lst in by_aisle.items():
            if not lst:
                continue
            v = _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx)
            if v > best:
                best, tied = v, [aid]
            elif v == best:
                tied.append(aid)
    else:
        for aid, lst in by_aisle.items():
            if not lst:
                continue
            v = lifts[aid]
            if v > best:
                best, tied = v, [aid]
            elif v == best:
                tied.append(aid)
    if not tied:
        return None
    if len(tied) == 1:
        return tied[0]
    # tie-break: min anchor gap.  target None ⇒ gap = min pref = prefs[0] (ascending list).
    if target is None:
        return min(tied, key=lambda a: prefs_by_aisle[a][0])
    return min(tied, key=lambda a: _closest_abs(prefs_by_aisle[a], target))


def build_cluster_map_placement(mgr, affinity, wp, ledger,
                                freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0, *, capped) -> Placement:
    """One Placement (ranked place_wave + per-unit place_one) for cluster_map (capped=False)
    or cluster_map_rank (capped=True).  Reads mgr._bin_pref / mgr._map_target at call time, so
    build_optimal_map must have run (wired in strategies._build_cluster_map[_rank])."""
    name = 'cluster_map_rank' if capped else 'cluster_map'
    _require_affinity(affinity, name)          # cohesion is meaningless without lift data
    _require_demand(freq_by_idx, name, 'freq_by_idx (the cohesion weight)')
    x_pace = sec_per_inch(wp.x_speed)
    # The ledger ARRIVES now (ticket 21).
    aisle_sku_sets, aisle_idx_sets = ledger.sku_sets, ledger.idx_sets
    aisle_member_pos = ledger.member_pos

    pref = mgr._bin_pref

    def _group(candidates):
        """Group candidate bins by aisle + build each aisle's ascending pref list (for the
        O(log) anchor bisect).  The pref list is kept in multiset-sync with by_aisle on removal."""
        by_aisle: dict[int, list] = {}
        for b in candidates:
            by_aisle.setdefault(b.location[0], []).append(b)
        prefs_by_aisle = {aid: sorted(pref.get(id(b), 0.0) for b in lst)
                          for aid, lst in by_aisle.items()}
        return by_aisle, prefs_by_aisle

    def _place(sku, by_aisle, prefs_by_aisle, f_s, q_s, run_cache=None,
               pref_index=None, live_idx=None):
        """Shared aisle+bin choice; mutates the chosen aisle's bin list + pref list + aisle state.

        `run_cache` (place_wave only) reuses the SKU's affinity row AND the per-aisle
        delta-lift values across a same-SKU run (sorted_units clusters same-SKU units:
        identical priority, stable sort).  Byte-identity argument: within a run the ONLY
        idx-set that mutates is the winner's (commit adds this SKU's own index), and the
        winner's cached delta is recomputed fresh right after each commit below.  Every
        other aisle's set is untouched, so its cached value is bit-for-bit what a fresh
        recompute would return — same operands AND same summation order (an unmutated
        set iterates identically; _delta_lift_from_row's iterate-the-smaller-side branch
        sees the same lengths).  A value-only argument is NOT enough here: the first cut
        cached across the winner's set growth and drifted by one ulp when the summation
        order flipped, moving one placement at 8k-SKU meso scale."""
        target = mgr._map_target.get(sku)
        lifts = None
        # IS THIS SKU COLD?  One `isdisjoint` against the LIVE union of placed indices, instead
        # of one `_delta_lift_from_row` per live aisle.  `live_idx` is maintained by the pool
        # (seeded at open, one add per commit) -- the straggler path has no pool and passes
        # None, keeping the scan it always had.
        #
        # `_delta_lift_from_row` sums over `row & aisle_idx_sets[aid]`, and every aisle's set is
        # a SUBSET of the live union, so an empty intersection with the union is an empty
        # intersection with every aisle: exactly 0.0 each, with no float addition performed at
        # all.  That is what makes this byte-identical rather than merely equal -- the concern
        # `_place`'s run-cache paragraph was written about is summation ORDER, and here there is
        # no summation.
        cold = (pref_index is not None and live_idx is not None
                and not _affinity_row(affinity, sku).keys() & live_idx)
        if run_cache is not None and run_cache.get('sku') == sku:
            row, lifts = run_cache['row'], run_cache['lifts']
        else:
            row = _affinity_row(affinity, sku)            # hoist the CSR slice: once per run
            if run_cache is not None:
                lifts = {a: _delta_lift_from_row(row, aisle_idx_sets[a], freq_by_idx)
                         for a, lst in by_aisle.items() if lst}
                run_cache.update(sku=sku, row=row, lifts=lifts)
        aid = _cluster_map_choose_aisle(by_aisle, prefs_by_aisle, row,
                                        aisle_idx_sets, freq_by_idx, target, lifts=lifts,
                                        cold_index=pref_index if cold else None)
        if aid is None:
            return None, None
        _mass, cx = _demand_weighted_partner_centroid(
            affinity, sku, aisle_member_pos[aid], freq_by_idx)
        chosen, cost = _cluster_map_pick_bin(by_aisle[aid], pref, target, cx, x_pace, capped)
        by_aisle[aid].remove(chosen)
        _p_chosen = pref.get(id(chosen), 0.0)
        plst = prefs_by_aisle[aid]                        # drop the chosen bin's pref (multiset-sync)
        k = bisect.bisect_left(plst, _p_chosen)
        if k < len(plst) and plst[k] == _p_chosen:
            del plst[k]
        if pref_index is not None:
            # The THIRD structure in multiset-sync, and the reason the merged index is a piece
            # of work rather than a line: it has to be maintained as bins are consumed, or the
            # next cold query answers from bins this wave already handed out.
            pref_index.remove(aid, _p_chosen)
        if sku not in aisle_sku_sets[aid]:
            ledger.add_sku(aid, sku, demand=f_s * q_s)
        _idx_s = affinity._sku_to_idx.get(sku)
        ledger.add_bin(aid, _idx_s, chosen.x_phys)
        if live_idx is not None and _idx_s is not None:
            # The union the cold check reads, kept LIVE.  `_ClusterMapPool._all_idx` is frozen
            # for the group ON PURPOSE (it ranks the order), so this is a second set with a
            # different job rather than a reuse of that one.
            live_idx.add(_idx_s)
        if run_cache is not None and run_cache.get('sku') == sku:
            # The commit may have grown THIS aisle's idx-set: recompute its delta fresh so
            # the next same-SKU unit sees exactly what a full per-unit recompute would.
            run_cache['lifts'][aid] = _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx)
        return chosen, cost

    def place_one(unit, candidates):
        if not candidates:
            return None
        by_aisle, prefs_by_aisle = _group(candidates)
        c = unit.order
        # The straggler path wants the bin only; the cost has nowhere to go from here.
        return _place(c.sku, by_aisle, prefs_by_aisle,
                      freq_by_sku.get(c.sku, 0.0), qty_by_sku.get(c.sku, 0.0))[0]

    class _ClusterMapPool(_Pool):
        """`place_wave`'s body, split into open / order / take.

        The thinnest port of the four, because `_place` was already the per-unit body and is
        shared with the straggler path -- there is nothing to restructure, only to relocate.
        `_group` and `all_idx` move into `__init__`, the priority sort becomes `order`, and
        `run_cache` becomes an attribute instead of a wave-local dict.

        THE RUN CACHE IS THE ONE THING TO BE CAREFUL WITH, and this policy is where the
        lesson was paid for. Its validity argument is written out in `_place`'s docstring:
        within a same-SKU run the only idx-set that mutates is the winner's, and the winner's
        cached delta is recomputed immediately after each commit. The first cut instead
        cached ACROSS the winner's set growth, and drifted by one ulp -- not because the
        value was wrong, but because `_delta_lift_from_row` iterates the smaller side, so
        adding one member flipped the summation ORDER. That moved a real placement at 8k-SKU
        meso scale. The argument has to be about summation order, not about values.

        The cache keys on `run_cache['sku'] == sku`, so losing same-SKU adjacency costs the
        hit rate and not correctness. Making it a persistent `{sku: lifts}` dict to win that
        back would reintroduce exactly the bug above.
        """

        __slots__ = ('_by_aisle', '_prefs', '_all_idx', '_run_cache',
                     '_pref_index', '_live_idx')

        def __init__(self, candidates):
            self._by_aisle, self._prefs = _group(candidates)   # one tier, once per group
            self._all_idx = _placed_union(aisle_idx_sets)
            self._run_cache: dict = {}          # same-SKU run reuse; _place owns the rules
            # THE COLD-START PAIR (ticket 05).  `_live_idx` is the union `_all_idx` is a frozen
            # copy of -- frozen there by decision, because `sort_key` ranks the whole group
            # against the pre-group union and must not drift as the wave places.  This one
            # tracks, because "has this SKU any partner placed YET" is a question about now.
            self._live_idx = set(self._all_idx)
            self._pref_index = _AislePrefIndex(self._by_aisle, self._prefs)

        def __len__(self):
            return sum(len(lst) for lst in self._by_aisle.values())

        def sort_key(self, unit):
            c = unit.order
            co = beta * _demand_weighted_delta_lift(affinity, c.sku, self._all_idx,
                                                    freq_by_idx)
            return c.demand.relative_frequency * c.labor_cost + co

        def take(self, unit):
            """(bin, score) for one unit.

            `score` is `_cluster_map_pick_bin`'s own cost -- |pref - target| plus the
            weighted centroid pull -- handed back from the comparison that chose the bin
            rather than reconstructed here. None on the capped least-prime fallback, which
            is decided by max-pref instead."""
            c = unit.order
            return _place(c.sku, self._by_aisle, self._prefs,
                          freq_by_sku.get(c.sku, 0.0), qty_by_sku.get(c.sku, 0.0),
                          self._run_cache, self._pref_index, self._live_idx)

    def open_pool(candidates, rep=None):
        return _ClusterMapPool(candidates)

    place_one.name = name
    open_pool.name = name
    #: The books this family commits to -- the object, beside the `ledger_terms`
    #: its `PlacementPolicy` declares.  A test compares the two.
    place_one.ledger = ledger
    return Placement(name, place_one, open_pool=open_pool)


