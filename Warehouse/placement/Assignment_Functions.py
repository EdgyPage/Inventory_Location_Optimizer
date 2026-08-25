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
from collections import deque
from typing import Any

from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.kernel.cost_model import (
    SpeedProfile, height_multiplier, per_pick, sec_per_inch)
from Warehouse.inventory.Inventory_Management import (
    _SIZE_RANKS, _SIZES_DESCENDING, BinKey, tier_ranks_for,
    AssignmentFn, RankedAssignmentFn, LoadParams, Placement, _wp_for,
)


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

def _D_map(cands, x_pace, y_pace) -> dict[int, float]:
    """id(bin) → travel-time D map.  The identical dict-comprehension sat at every
    ranked-impl site; ONE helper so the formula can't drift.  Paces are s/inch
    (sec_per_inch of the ft/s speeds); expression shape preserved exactly."""
    return {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}



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


def _build_load_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
    aisle_index    : dict | None = None,
    *,
    maximize       : bool = False,
) -> AssignmentFn:
    """Shared core for the load-minimising/maximising AssignmentFns (they differed only
    in extremum direction + the min-only early-termination prune).

    Greedily extremises the L2 norm of predicted aisle loads
    L_a = W + λ*(W/k)^γ * lift_sum.

    Dual-optimisation algorithm
    ---------------------------
    1. Reduce candidates to one bin per aisle (extremal-D bin) — exact by
       monotonicity of delta_l2 in D within a fixed aisle.
    2. Sort the O(N_aisles) representatives by D (ascending when minimising;
       descending when maximising — largest travel cost first has the highest
       potential delta_l2).
    3. Evaluate aisles in that order with LAZY CSR queries (delta_lift computed
       only when the aisle is actually reached, not upfront for all aisles).
    4. Early termination (MINIMISING ONLY): once the best score has delta_l2 = 0
       (no affinity partners in the winning aisle), any remaining aisle with
       D ≥ best_old_L cannot improve — old_L ≥ D ≥ best_old_L and
       delta_l2 ≥ 0 = best_delta_l2.  With sparse top-20 affinity most aisles
       have delta_lift = 0, so the termination typically fires after the first
       few aisles.  No such prune exists when maximising: a low-D aisle can
       still win on very high affinity lift.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(D: float, ls: float) -> float:
        return D + lam * (D / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any] | None) -> Any | None:
        sku = unit.order.sku

        # Step 1: one representative bin per aisle (extremal-D).
        # Fast path: derive BinKey from unit, read directly from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            best_D, best_bin_map = _aisle_index_for_unit(aisle_index, unit, minimize=not maximize)
            if not best_D:
                return None
        else:
            if not candidates:
                return None
            best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed,
                                                        minimize=not maximize)

        # Step 2: sort aisles by D — O(N_aisles log N_aisles)
        sorted_aids = sorted(best_D, key=best_D.__getitem__, reverse=maximize)

        _inf = float('-inf') if maximize else float('inf')
        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (_inf, _inf)
        best_delta_lift : float               = 0.0

        # Step 3+4: lazy CSR queries (+ min-only early termination)
        for aid in sorted_aids:
            D = best_D[aid]

            if not maximize and best_score[0] == 0.0 and D >= best_score[1]:
                # best has delta_l2=0; remaining D ≥ best old_L means
                # score ≥ (0, D) ≥ (0, best_old_L) = best — prune the rest.
                break

            ls = aisle_lift_sum[aid]
            # Marginal lift is zero when the SKU already lives in this aisle —
            # it's already counted in aisle_lift_sum and adding a duplicate bin
            # does not create a new unique SKU pair.
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(D, ls)
            new_L    = _L(D, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if (score > best_score) if maximize else (score < best_score):
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        # Only update lift state when this is a genuinely new SKU for the aisle.
        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    # Coupling tag: True only when this closure reads mgr._aisle_index (the fast
    # path).  The manager's _stock guard asserts this equals _travel_costs_ready
    # so the two halves can never be armed independently.
    assign.uses_aisle_index = aisle_index is not None
    return assign


def build_load_minimizing_assignment_fn(params, affinity, wp, aisle_sku_sets,
                                        aisle_lift_sum, aisle_idx_sets,
                                        aisle_index=None) -> AssignmentFn:
    """Greedily MINIMISE the L2 norm of predicted aisle loads (see _build_load_assignment_fn)."""
    return _build_load_assignment_fn(params, affinity, wp, aisle_sku_sets, aisle_lift_sum,
                                     aisle_idx_sets, aisle_index, maximize=False)


def build_load_maximizing_assignment_fn(params, affinity, wp, aisle_sku_sets,
                                        aisle_lift_sum, aisle_idx_sets,
                                        aisle_index=None) -> AssignmentFn:
    """Greedily MAXIMISE the L2 norm of predicted aisle loads (see _build_load_assignment_fn)."""
    return _build_load_assignment_fn(params, affinity, wp, aisle_sku_sets, aisle_lift_sum,
                                     aisle_idx_sets, aisle_index, maximize=True)


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


def _commit_aisle(aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, affinity, aid, sku, f_s, q_s):
    """Record a newly-placed SKU in an aisle's state (idempotent if already present)."""
    if sku not in aisle_sku_sets[aid]:
        aisle_sku_sets[aid].add(sku)
        idx = affinity._sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[aid].add(idx)
        aisle_demand_sum[aid] += f_s * q_s


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


def _build_aisle_score_fn(name, *, score_kind, maximize, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
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

        def score_of(aid):
            D = best_D[aid]
            co = (0.0 if sku in aisle_sku_sets[aid]
                  else _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx))
            if score_kind == 'travel':
                primary = f_s * D - beta * co
                secondary = aisle_demand_sum[aid] + f_s * q_s
                return (primary, -secondary) if maximize else (primary, secondary)
            return (co, -D) if maximize else (co, D)   # cohesion; tie-break front bay

        best_aid = _pick_extremal_aisle(best_D, score_of, maximize)
        if best_aid < 0:
            return None
        _commit_aisle(aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, affinity, best_aid, sku, f_s, q_s)
        return best_bin_map[best_aid]

    assign.name = name
    assign.uses_aisle_index = aisle_index is not None
    return assign


def _travel_or_cohesion(name, score_kind, maximize):
    """Make a builder with the legacy (affinity, wp, ...state..., beta) signature that
    routes through the shared core."""
    def builder(affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0, aisle_index=None):
        return _build_aisle_score_fn(
            name, score_kind=score_kind, maximize=maximize, affinity=affinity, wp=wp,
            aisle_sku_sets=aisle_sku_sets, aisle_idx_sets=aisle_idx_sets,
            aisle_demand_sum=aisle_demand_sum, freq_by_idx=freq_by_idx,
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

    def order(self, units):
        return units

    def take(self, unit):                                   # pragma: no cover - interface
        raise NotImplementedError


def _ranked_assign_impl(
    units        : list,
    candidates_fn,
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
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
    """
    wp      = _wp_for(wp, units[0]) if units else wp   # per-regime cost in a mixed warehouse
    x_pace  = sec_per_inch(wp.x_speed)   # ft/s -> s/inch
    y_pace  = sec_per_inch(wp.y_speed)
    # Fix 1: the co-occurrence term ranks each SKU against ALL currently-placed
    # SKU indices.  That union is identical for every unit in the wave (placement
    # is deferred to the caller, so aisle_idx_sets is static here), so build it
    # ONCE — not once per unit inside the sort key (which was O(U·Σ) per wave).
    # Only needed for the default pick-effort ordering's co-occurrence term.
    all_idx = (set().union(*aisle_idx_sets.values()) if (order_key is None and aisle_idx_sets)
               else set())

    def pick_effort_priority(unit) -> float:
        c = unit.order
        # c.labor_cost is the precomputed per-pick effort (pi + pw*ln w + pv*ln v),
        # so this avoids re-taking logs per unit per wave.
        co_occur = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
        return c.demand.relative_frequency * c.labor_cost + co_occur

    # A policy may supply its own per-unit order score (decoupled enqueue ordering);
    # otherwise fall back to the default pick-effort priority.  Both sort DESCENDING.
    sorted_units = sorted(units, key=(order_key or pick_effort_priority), reverse=True)
    result: list = []
    if not sorted_units:
        return result

    # Fix 2: the candidate pool is constant for this whole call (placement is
    # deferred) and every unit shares one BinKey, so compute it ONCE instead of
    # re-copying / re-scanning it per unit (was O(U·bucket_bins) per wave).
    # Pre-sort each aisle's bins by travel cost D (extremal-D first) and hand them
    # out by popping the head — equivalent to picking the extremal-D available bin
    # per aisle each step, but O(bucket log bucket + U·n_aisles) overall.
    cands = candidates_fn(sorted_units[0])
    D_of  = _D_map(cands, x_pace, y_pace)
    by_aisle: dict[int, deque] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], []).append(b)
    for aid, lst in by_aisle.items():
        lst.sort(key=lambda bb: D_of[id(bb)], reverse=not minimize)   # head = extremal-D
        by_aisle[aid] = deque(lst)
    head_bin = {aid: dq[0]          for aid, dq in by_aisle.items() if dq}
    head_D   = {aid: D_of[id(dq[0])] for aid, dq in by_aisle.items() if dq}

    for unit in sorted_units:
        if not head_D:
            result.append((unit, None))
            continue
        if aisle_selector is not None:
            best_aid = aisle_selector(head_D, head_bin)
        else:
            best_aid = (min if minimize else max)(head_D, key=head_D.__getitem__)
        chosen = head_bin[best_aid]

        sku = unit.order.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)
        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        # Advance the chosen aisle's head; drop it when exhausted.
        dq = by_aisle[best_aid]
        dq.popleft()
        if dq:
            head_bin[best_aid] = dq[0]
            head_D[best_aid]   = D_of[id(dq[0])]
        else:
            del head_bin[best_aid]
            del head_D[best_aid]

        result.append((unit, chosen))

    return result


# ── co-demand compaction / expansion (within-aisle path-span min/max) ──────────
#
# The diagnostic showed makespan is driven by within-aisle work W, and W's travel
# term is the length of the column-sweep path through a batch's demanded bins.  So
# clustering co-demanded SKUs into nearby COLUMNS shortens that path (compaction);
# scattering them lengthens it (expansion).  These ride the ranked drain and commit
# member positions INCREMENTALLY so clusters accumulate within a wave.

def _demand_weighted_partner_centroid(affinity, sku, member_pos, freq_by_idx):
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
    i     = affinity._sku_to_idx[sku]
    start = int(affinity._matrix.indptr[i])
    end   = int(affinity._matrix.indptr[i + 1])
    if start == end:
        return 0.0, None
    row = {int(ci): float(d) for ci, d in
           zip(affinity._matrix.indices[start:end], affinity._matrix.data[start:end])}
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


def _co_demand_ranked_impl(units, candidates_fn, affinity, wp,
                           aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                           freq_by_idx, freq_by_sku, qty_by_sku, beta, compact: bool):
    """Ranked co-demand placement.  SUPERSEDED by `_CoDemandPool`, which is what `comp`
    and `expn` actually run; kept as the frozen oracle the port is tested against.

    Units are placed in the same pick-effort order as
    _ranked_assign_impl and membership is committed incrementally, but the BIN choice is
    position-aware: the aisle is scored by demand-weighted lift to its members (MAX for
    compact / MIN for expand), and within it the bin NEAREST (compact) / FARTHEST (expand)
    the partners' column centroid is taken — vs the extremal-D head.  Each placement also
    appends (x_phys, idx) to aisle_member_pos so later units in the wave see it.
    """
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    all_idx = set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set()

    _co_by_sku: dict = {}                     # sort-key memo: all_idx is frozen during the sort,
                                              # so a SKU's co term is one value — reuse is bit-safe
    def priority(unit):
        c = unit.order
        co = _co_by_sku.get(c.sku)
        if co is None:
            # c.labor_cost = precomputed per-pick effort (pi + pwt*ln w + pv*ln v).
            co = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
            _co_by_sku[c.sku] = co
        return c.demand.relative_frequency * c.labor_cost + co

    sorted_units = sorted(units, key=priority, reverse=True)
    result: list = []
    if not sorted_units:
        return result

    cands = candidates_fn(sorted_units[0])
    D_of  = _D_map(cands, x_pace, y_pace)
    by_aisle: dict[int, list] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], []).append(b)
    for lst in by_aisle.values():
        lst.sort(key=lambda b: b.x_phys)          # ascending column
    sku_to_idx = affinity._sku_to_idx

    # ── SKU-run cache (the Phase-6 precedent, both key components) ────────────
    # sorted_units clusters same-SKU units (equal priority, stable sort).  aisle_key is
    # (mass, ±d0): mass mutates only for the WINNER (commit adds this SKU's own index to
    # its idx-set) and d0 only for the WINNER (its bin list pops) — so cache both per
    # run and refresh just the winner after each placement.  The refresh recomputes with
    # the same operands AND the same summation order a full per-unit recompute would use
    # (the ulp lesson from the cluster_map cache: a value-only argument is not enough).
    last_sku = None
    key_cache: dict = {}                        # {aid: (mass, ±d0)} for the current run
    cached_row = None
    for unit in sorted_units:
        live = [aid for aid, lst in by_aisle.items() if lst]
        if not live:
            result.append((unit, None))
            continue
        sku = unit.order.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        if sku != last_sku:
            cached_row = _affinity_row(affinity, sku)   # hoist the CSR slice: once per run
            # aisle: most (compact) / least (expand) lift to members; tie-break toward
            # the front (compact) / back (expand) bay by the aisle's lowest-D rep.
            key_cache = {}
            for aid in live:
                mass = _delta_lift_from_row(cached_row, aisle_idx_sets[aid], freq_by_idx)
                d0   = D_of[id(by_aisle[aid][0])]
                key_cache[aid] = (mass, -d0) if compact else (mass, d0)
            last_sku = sku
        row = cached_row
        best_aid = (max if compact else min)(live, key=key_cache.__getitem__)

        lst = by_aisle[best_aid]
        _mass, cx = _demand_weighted_partner_centroid(
            affinity, sku, aisle_member_pos[best_aid], freq_by_idx)
        if cx is not None:                        # bin nearest / farthest the partner column
            j = (min if compact else max)(range(len(lst)), key=lambda k: abs(lst[k].x_phys - cx))
        else:                                     # no partners yet: front (compact) / back (expand)
            j = 0 if compact else len(lst) - 1
        chosen = lst.pop(j)

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += f_s * q_s
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        result.append((unit, chosen))
        # winner refresh: exactly what the next same-SKU unit's fresh recompute would see
        if lst:
            mass = _delta_lift_from_row(row, aisle_idx_sets[best_aid], freq_by_idx)
            d0   = D_of[id(lst[0])]
            key_cache[best_aid] = (mass, -d0) if compact else (mass, d0)
        else:
            key_cache.pop(best_aid, None)         # aisle exhausted: leaves `live` next unit

    return result


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
                 '_s2i', '_last_sku', '_key_cache', '_cached_row')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_member_pos, freq_by_idx, freq_by_sku,
                 qty_by_sku, beta, compact: bool):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._amp = aisle_demand_sum, aisle_member_pos
        self._fbi, self._fbs, self._qbs = freq_by_idx, freq_by_sku, qty_by_sku
        self._beta, self._compact = beta, compact
        self._s2i = affinity._sku_to_idx

        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._x_pace = x_pace
        # Every SKU index placed anywhere. Aisle-derived, so the unit set cannot change it;
        # frozen for the group, so later units rank against the pre-group union.
        self._all_idx = (set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set())

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

    def __len__(self):
        return sum(len(lst) for lst in self._by_aisle.values())

    def order(self, units):
        """Pick-effort priority with the co-occurrence term, descending -- the same key
        `_ranked_assign_impl` uses. `all_idx` is frozen for the whole sort, so a SKU's co
        term is one value and the memo below is bit-safe."""
        aff, fbi, beta, all_idx = self._aff, self._fbi, self._beta, self._all_idx
        co_by_sku: dict = {}
        def priority(unit):
            c = unit.order
            co = co_by_sku.get(c.sku)
            if co is None:
                # c.labor_cost = precomputed per-pick effort (pi + pwt*ln w + pv*ln v).
                co = beta * _demand_weighted_delta_lift(aff, c.sku, all_idx, fbi)
                co_by_sku[c.sku] = co
            return c.demand.relative_frequency * c.labor_cost + co
        return sorted(units, key=priority, reverse=True)

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
            self._ass[best_aid].add(sku)
            self._ads[best_aid] += f_s * q_s
        idx = self._s2i.get(sku)
        if idx is not None:
            self._ais[best_aid].add(idx)
            self._amp[best_aid][idx].append(chosen.x_phys)
        # winner refresh: exactly what the next same-SKU unit's fresh recompute would see
        if lst:
            mass = _delta_lift_from_row(row, self._ais[best_aid], self._fbi)
            d0   = self._D_of[id(lst[0])]
            key_cache[best_aid] = (mass, -d0) if compact else (mass, d0)
        else:
            key_cache.pop(best_aid, None)         # aisle exhausted: leaves `live` next unit
        return chosen, score


def _build_co_demand_pool_fn(affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                             aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku,
                             beta, compact):
    def open_pool(candidates, rep=None):
        return _CoDemandPool(candidates, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                             aisle_demand_sum, aisle_member_pos, freq_by_idx,
                             freq_by_sku, qty_by_sku, beta, compact)
    return open_pool


def _build_co_demand_place_one(affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                               aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku,
                               compact, name):
    """Per-unit co-demand fn (place_one) — same scoring as the wave, one unit at a time.
    Used for the ranked policy's stragglers; accumulates positions like the wave."""
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    sku_to_idx = affinity._sku_to_idx

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
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += f_s * q_s
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        return chosen

    assign.name = name
    assign.uses_aisle_index = False
    return assign


def build_co_demand_placement(compact, affinity, wp,
                              aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                              freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0) -> Placement:
    """One Placement (place_one + ranked place_wave) for co-demand compaction (compact=True)
    or expansion (compact=False).  Wired by strategies._build_compaction/_build_expansion."""
    name = 'compaction' if compact else 'expansion'
    _require_affinity(affinity, name)          # co-demand is meaningless without lift data
    _require_demand(freq_by_idx, name, 'freq_by_idx (the partner-centroid weight)')
    place_one = _build_co_demand_place_one(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
        freq_by_idx, freq_by_sku, qty_by_sku, compact, name)

    open_pool = _build_co_demand_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
        freq_by_idx, freq_by_sku, qty_by_sku, beta, compact)
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
                 '_by_aisle', '_D_of', '_head_bin', '_head_D')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, freq_by_idx, freq_by_sku, qty_by_sku, beta,
                 minimize, aisle_selector=None, order_key=None):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._fbi = aisle_demand_sum, freq_by_idx
        self._fbs, self._qbs, self._beta = freq_by_sku, qty_by_sku, beta
        self._minimize, self._selector, self._order_key = minimize, aisle_selector, order_key

        # The co-occurrence term ranks each SKU against ALL currently-placed SKU indices.
        # That union is identical for every unit in the group, so build it ONCE -- not once
        # per unit inside the sort key (which was O(U*sigma) per wave).  Only the default
        # pick-effort ordering's co-occurrence term needs it.
        self._all_idx = (set().union(*aisle_idx_sets.values())
                         if (order_key is None and aisle_idx_sets) else set())

        # The named pair, not two hand conversions — new code crosses the ft/s -> s/inch
        # boundary through the profile (see cost_model.SpeedProfile).
        speed  = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        D_of = _D_map(cands, x_pace, y_pace)
        by_aisle: dict[int, deque] = {}
        for b in cands:
            by_aisle.setdefault(b.location[0], []).append(b)
        for aid, lst in by_aisle.items():
            lst.sort(key=lambda bb: D_of[id(bb)], reverse=not minimize)   # head = extremal-D
            by_aisle[aid] = deque(lst)
        self._D_of, self._by_aisle = D_of, by_aisle
        self._head_bin = {aid: dq[0]           for aid, dq in by_aisle.items() if dq}
        self._head_D   = {aid: D_of[id(dq[0])] for aid, dq in by_aisle.items() if dq}

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

    def order(self, units):
        """Descending pick-effort priority: the highest-effort unit claims the extremal-D
        bin first.  A policy may supply its own per-unit score instead."""
        return sorted(units, key=(self._order_key or self._pick_effort_priority),
                      reverse=True)

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
            best_aid = self._selector(head_D, head_bin)
        else:
            best_aid = (min if self._minimize else max)(head_D, key=head_D.__getitem__)
        chosen = head_bin[best_aid]
        score  = head_D[best_aid]

        sku = unit.order.sku
        f_s = self._fbs.get(sku, 0.0)
        q_s = self._qbs.get(sku, 0.0)
        if sku not in self._ass[best_aid]:
            self._ass[best_aid].add(sku)
            idx = self._aff._sku_to_idx.get(sku)
            if idx is not None:
                self._ais[best_aid].add(idx)
            self._ads[best_aid] += f_s * q_s

        # Advance the chosen aisle's head; drop it when exhausted.
        dq = self._by_aisle[best_aid]
        dq.popleft()
        if dq:
            head_bin[best_aid] = dq[0]
            head_D[best_aid]   = self._D_of[id(dq[0])]
        else:
            del head_bin[best_aid]
            del head_D[best_aid]
        return chosen, score


def _build_ranked_assign_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
    freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize,
    aisle_selector=None, order_key=None,
):
    """`open_pool` shared by the four ranked-assign arms (tmin / tmax / rank_random /
    rank_popularity).  Mirrors `_ranked_assign_impl`'s parameter list exactly."""
    def open_pool(candidates, rep=None):
        return _RankedAssignPool(
            candidates, affinity,
            _wp_for(wp, rep) if rep is not None else wp,   # per-regime cost, mixed warehouse
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize,
            aisle_selector=aisle_selector, order_key=order_key)
    return open_pool


def build_ranked_minimizing_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        )
    return ranked_assign


def build_ranked_minimizing_pool_fn(*a, **kw):
    """Pool twin of build_ranked_minimizing_assignment_fn — same signature."""
    return _build_ranked_assign_pool_fn(*a, minimize=True, **kw)


def build_ranked_maximizing_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=False,
        )
    return ranked_assign


def build_ranked_maximizing_pool_fn(*a, **kw):
    """Pool twin of build_ranked_maximizing_assignment_fn — same signature."""
    return _build_ranked_assign_pool_fn(*a, minimize=False, **kw)


def build_ranked_uniform_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
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
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Ablation: order units by expected_popularity (freq*qty) and place each into the
    aisle with the LEAST Σ popularity (aisle_demand_sum); nearest-D bin within it,
    nearest-aisle as the tiebreak.  Disperses demand mass evenly across aisles."""
    def _selector(head_D, head_bin):
        return min(head_D, key=lambda aid: (aisle_demand_sum.get(aid, 0.0), head_D[aid]))

    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
            aisle_selector=_selector, order_key=_score_expected_popularity,
        )
    return ranked_assign


def build_ranked_popularity_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
    freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
):
    """Pool twin of build_ranked_popularity_fn — same signature.

    Its selector reads the LIVE `aisle_demand_sum`, which `take` itself increments, so the
    aisle choice depends on what this pool has already placed.  That is pool state and ports
    as-is; what it means is that this arm's result moves when the service order moves."""
    def _selector(head_D, head_bin):
        return min(head_D, key=lambda aid: (aisle_demand_sum.get(aid, 0.0), head_D[aid]))
    return _build_ranked_assign_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
        freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        aisle_selector=_selector, order_key=_score_expected_popularity)


#: Sentinel for "no SKU run open yet" — a fresh object so it can never equal
#: a real SKU, whatever the catalogue numbers them.
_NO_RUN_SKU = object()


def _travel_balanced_impl(units, candidates_fn, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                          aisle_pick_load_sum, sku_pick_load_product,
                          freq_by_sku, qty_by_sku, cart=None):
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
    """
    wp = _wp_for(wp, units[0]) if units else wp   # per-regime cost in a mixed warehouse
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    intercept = wp.pick_intercept
    brackets  = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]

    # ── optional cart-swap term ──────────────────────────────────────────────
    # Expected picked volume in an aisle per task ≈ (k/Σf)·Σ f·q·vol; comparing that to the
    # cart capacity is equivalent to comparing the RAW mass V_raw = Σ f·q·vol against
    # cap_raw = cap·Σf/k.  Expected cart cost of an aisle = coef·max(0, V_raw/cap_raw − 1).
    cart_on = cart is not None
    if cart_on:
        aisle_vol_sum, sku_vol_product, expected_batch_skus, total_freq = cart
        cart_coef = wp.cart_swap_coef
        cap_raw   = wp.cart_capacity * total_freq / max(expected_batch_skus, 1e-9)

    D_of = _D_map(cands, x_pace, y_pace)
    M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
    # per aisle: {height_mult: deque of bins (that bracket) sorted by D ascending}
    by_aisle: dict[int, dict] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], {}).setdefault(M_of[id(b)], []).append(b)
    for groups in by_aisle.values():
        for m, lst in list(groups.items()):
            lst.sort(key=lambda bb: D_of[id(bb)])
            groups[m] = deque(lst)
    # running per-aisle total (handling+travel) labor, seeded from the maintained sum
    load = {aid: float(aisle_pick_load_sum.get(aid, 0.0)) for aid in by_aisle}
    # running per-aisle expected picked-volume mass (raw f·q·vol), seeded likewise
    vol_load = ({aid: float(aisle_vol_sum.get(aid, 0.0)) for aid in by_aisle}
                if cart_on else None)
    sku_to_idx = affinity._sku_to_idx
    result: list = []

    def _cart_cost(v_raw):
        """Expected cart-swap cost for an aisle holding raw volume mass v_raw."""
        return cart_coef * max(0.0, v_raw / cap_raw - 1.0)

    def _aisle_best(aid, var):
        """(cost, mult, bin) of the cheapest available bin in the aisle for this var.
        Height scales the whole at-location pick: cost = m·(intercept+var) + D."""
        best = None
        for m, dq in by_aisle[aid].items():
            if not dq:
                continue
            b = dq[0]
            cost = per_pick(m, intercept, var) + D_of[id(b)]
            if best is None or cost < best[0]:
                best = (cost, m, b)
        return best

    def _score_of(aid, ab, sku, fq, m_s):
        """The per-(unit, aisle) score — the ORIGINAL expressions verbatim.

        balance TOTAL expected aisle labor = handling+travel + expected cart swaps,
        so an aisle nearing a full cart is penalised and further volume disperses.
        The SKU's volume mass counts ONCE per aisle (a second bin of a SKU already
        here adds no new expected picked volume), mirroring aisle_pick_load_sum."""
        score = load[aid] + fq * ab[0]
        if cart_on:
            add = 0.0 if sku in aisle_sku_sets[aid] else m_s
            score += _cart_cost(vol_load[aid] + add)
        return score

    # ── SKU-run caching ──────────────────────────────────────────────────────
    # sorted_units is a stable sort on a per-order key, so units of one SKU are contiguous.
    # Within such a run, every input a NON-winning aisle's score reads is provably frozen:
    # fq/var/m_s are per-SKU constants; load/vol_load/aisle_sku_sets mutate for the WINNING
    # aisle only; deque heads advance for the winning aisle only; candidates were fetched
    # once for the wave.  So _aisle_best and the score are computed once per aisle at each
    # run boundary and refreshed only for the aisle that just won — the argmin sequence
    # (and every float, computed by the verbatim expressions above) is byte-identical to
    # the per-unit rescan this replaces; only redundant recomputation is skipped.  Guarded
    # by Tests/unit/test_travel_balanced_equivalence.py's frozen-oracle suite.
    run_sku = _NO_RUN_SKU
    var = fq = m_s = 0.0
    ab_cache: dict = {}
    score_cache: dict = {}

    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        if sku != run_sku:                       # run boundary: rebuild both caches
            run_sku = sku
            var = c.handle_var
            fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)
            m_s = sku_vol_product.get(sku, 0.0) if cart_on else 0.0
            ab_cache.clear()
            score_cache.clear()
            for aid in by_aisle:
                ab = _aisle_best(aid, var)
                ab_cache[aid] = ab
                if ab is not None:
                    score_cache[aid] = _score_of(aid, ab, sku, fq, m_s)
        best_aid = best_choice = None
        best_score = None
        for aid in by_aisle:                     # original order ⇒ original tie-breaks
            ab = ab_cache[aid]
            if ab is None:
                continue
            score = score_cache[aid]
            if best_score is None or score < best_score:
                best_score, best_aid, best_choice = score, aid, ab
        if best_aid is None:
            result.append((unit, None))
            continue
        cost, m, chosen = best_choice
        load[best_aid] += fq * cost

        # commit manager aisle state (travel-blind sums; mirrors _ranked_assign_impl)
        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += fq
            aisle_pick_load_sum[best_aid] += sku_pick_load_product.get(sku, 0.0)
            if cart_on:                       # SKU-once, in lockstep with pick_load_sum
                vol_load[best_aid] += m_s
                aisle_vol_sum[best_aid] += m_s

        by_aisle[best_aid][m].popleft()
        # Only the winner's inputs changed (head advanced; load; maybe sku-set/vol_load):
        # refresh its cache entries; an exhausted aisle goes None and is skipped exactly
        # like the original `continue`.
        ab = _aisle_best(best_aid, var)
        ab_cache[best_aid] = ab
        if ab is not None:
            score_cache[best_aid] = _score_of(best_aid, ab, sku, fq, m_s)
        else:
            score_cache.pop(best_aid, None)
        result.append((unit, chosen))
    return result


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
                 '_intercept', '_by_aisle', '_D_of', '_load', '_vol_load',
                 '_cart_on', '_avs', '_svp', '_cart_coef', '_cap_raw',
                 '_run_sku', '_var', '_fq', '_m_s', '_ab_cache', '_score_cache')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_pick_load_sum, sku_pick_load_product,
                 freq_by_sku, qty_by_sku, cart=None):
        self._ass, self._ais, self._ads = aisle_sku_sets, aisle_idx_sets, aisle_demand_sum
        self._apl, self._splp = aisle_pick_load_sum, sku_pick_load_product
        self._fbs, self._qbs = freq_by_sku, qty_by_sku
        self._s2i = affinity._sku_to_idx
        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._intercept = wp.pick_intercept
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

        D_of = _D_map(cands, x_pace, y_pace)
        M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
        # per aisle: {height_mult: deque of bins (that bracket) sorted by D ascending}
        by_aisle: dict[int, dict] = {}
        for b in cands:
            by_aisle.setdefault(b.location[0], {}).setdefault(M_of[id(b)], []).append(b)
        for groups in by_aisle.values():
            for m, lst in list(groups.items()):
                lst.sort(key=lambda bb: D_of[id(bb)])
                groups[m] = deque(lst)
        self._D_of, self._by_aisle = D_of, by_aisle
        # running per-aisle total (handling+travel) labor, seeded from the maintained sum
        self._load = {aid: float(aisle_pick_load_sum.get(aid, 0.0)) for aid in by_aisle}
        # running per-aisle expected picked-volume mass (raw f*q*vol), seeded likewise
        self._vol_load = ({aid: float(self._avs.get(aid, 0.0)) for aid in by_aisle}
                          if self._cart_on else None)

        self._run_sku = _NO_RUN_SKU
        self._var = self._fq = self._m_s = 0.0
        self._ab_cache: dict = {}
        self._score_cache: dict = {}

    def __len__(self):
        return sum(len(dq) for g in self._by_aisle.values() for dq in g.values())

    def order(self, units):
        """Longest-processing-time first: the highest expected-labor unit is placed while
        the most aisles are still cheap.  A FIFO window degrades LPT to arbitrary-order
        greedy; the balance mechanics below are untouched by that."""
        return sorted(units, key=lambda u: u.order.expected_labor, reverse=True)

    # ── the scoring expressions, verbatim ─────────────────────────────────────────
    def _cart_cost(self, v_raw):
        """Expected cart-swap cost for an aisle holding raw volume mass v_raw."""
        return self._cart_coef * max(0.0, v_raw / self._cap_raw - 1.0)

    def _aisle_best(self, aid, var):
        """(cost, mult, bin) of the cheapest available bin in the aisle for this var.
        Height scales the whole at-location pick: cost = m*(intercept+var) + D."""
        best = None
        intercept, D_of = self._intercept, self._D_of
        for m, dq in self._by_aisle[aid].items():
            if not dq:
                continue
            b = dq[0]
            cost = per_pick(m, intercept, var) + D_of[id(b)]
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
            self._score_cache.clear()
            for aid in by_aisle:
                ab = self._aisle_best(aid, var)
                self._ab_cache[aid] = ab
                if ab is not None:
                    self._score_cache[aid] = self._score_of(aid, ab, sku, fq, m_s)
        else:
            var, fq, m_s = self._var, self._fq, self._m_s

        best_aid = best_choice = None
        best_score = None
        for aid in by_aisle:                     # original order => original tie-breaks
            ab = self._ab_cache[aid]
            if ab is None:
                continue
            score = self._score_cache[aid]
            if best_score is None or score < best_score:
                best_score, best_aid, best_choice = score, aid, ab
        if best_aid is None:
            return None, None
        cost, m, chosen = best_choice
        marginal = fq * cost
        self._load[best_aid] += marginal

        # commit manager aisle state (travel-blind sums; mirrors _RankedAssignPool)
        if sku not in self._ass[best_aid]:
            self._ass[best_aid].add(sku)
            idx = self._s2i.get(sku)
            if idx is not None:
                self._ais[best_aid].add(idx)
            self._ads[best_aid] += fq
            self._apl[best_aid] += self._splp.get(sku, 0.0)
            if self._cart_on:                 # SKU-once, in lockstep with pick_load_sum
                self._vol_load[best_aid] += m_s
                self._avs[best_aid] += m_s

        by_aisle[best_aid][m].popleft()
        # Only the winner's inputs changed (head advanced; load; maybe sku-set/vol_load):
        # refresh its cache entries; an exhausted aisle goes None and is skipped exactly
        # like the original `continue`.
        ab = self._aisle_best(best_aid, var)
        self._ab_cache[best_aid] = ab
        if ab is not None:
            self._score_cache[best_aid] = self._score_of(best_aid, ab, sku, fq, m_s)
        else:
            self._score_cache.pop(best_aid, None)
        return chosen, marginal


def _build_travel_balanced_pool_fn(affinity, wp, aisle_sku_sets, aisle_idx_sets,
                                   aisle_demand_sum, aisle_pick_load_sum,
                                   sku_pick_load_product, freq_by_sku, qty_by_sku,
                                   cart=None):
    def open_pool(candidates, rep=None):
        return _TravelBalancedPool(
            candidates, affinity,
            _wp_for(wp, rep) if rep is not None else wp,   # per-regime cost, mixed warehouse
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_pick_load_sum,
            sku_pick_load_product, freq_by_sku, qty_by_sku, cart=cart)
    return open_pool


def build_ranked_labor_fn(
    affinity,
    wp,
    aisle_sku_sets        : dict,
    aisle_idx_sets        : dict,
    aisle_demand_sum      : dict,
    aisle_pick_load_sum   : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            aisle_pick_load_sum, sku_pick_load_product, freq_by_sku, qty_by_sku)
    return ranked_assign


def build_ranked_labor_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
    aisle_pick_load_sum, sku_pick_load_product, freq_by_idx, freq_by_sku,
    qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_labor_fn — same signature."""
    return _build_travel_balanced_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
        aisle_pick_load_sum, sku_pick_load_product, freq_by_sku, qty_by_sku)


def build_ranked_cartlabor_fn(
    affinity,
    wp,
    aisle_sku_sets        : dict,
    aisle_idx_sets        : dict,
    aisle_demand_sum      : dict,
    aisle_pick_load_sum   : dict,
    sku_pick_load_product : dict,
    aisle_vol_sum         : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            aisle_pick_load_sum, sku_pick_load_product, freq_by_sku, qty_by_sku,
            cart=(aisle_vol_sum, sku_vol_product, expected_batch_skus, total_freq))
    return ranked_assign


def build_ranked_cartlabor_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
    aisle_pick_load_sum, sku_pick_load_product, aisle_vol_sum, sku_vol_product,
    expected_batch_skus, freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_cartlabor_fn — same signature.

    `total_freq` is summed HERE, once when the policy is built, exactly as the wave builder
    does it: a per-pool sum over the same dict would be the same value today but would make
    a dict-order change silently repricing every cart penalty."""
    total_freq = sum(freq_by_sku.values())
    return _build_travel_balanced_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
        aisle_pick_load_sum, sku_pick_load_product, freq_by_sku, qty_by_sku,
        cart=(aisle_vol_sum, sku_vol_product, expected_batch_skus, total_freq))


def _ranked_minlabor_impl(units, candidates_fn, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                          aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam,
                          maximize=False):
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
    """
    wp = _wp_for(wp, units[0]) if units else wp   # per-regime cost in a mixed warehouse
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    intercept = wp.pick_intercept
    brackets  = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]

    # minimise → near (min-D) deque head; maximise → far (max-D) deque tail.
    _rep  = (lambda dq: dq[-1]) if maximize else (lambda dq: dq[0])
    _drop = (lambda dq: dq.pop()) if maximize else (lambda dq: dq.popleft())
    def _better(a, b):                       # is a a better (more extreme) score than b?
        return a > b if maximize else a < b

    D_of = _D_map(cands, x_pace, y_pace)
    M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
    by_aisle_brkt: dict[int, dict] = {}          # {aisle: {mult: D-sorted deque}}
    for b in cands:
        by_aisle_brkt.setdefault(b.location[0], {}).setdefault(M_of[id(b)], []).append(b)
    for groups in by_aisle_brkt.values():
        for m, lst in list(groups.items()):
            lst.sort(key=lambda bb: D_of[id(bb)])
            groups[m] = deque(lst)
    sku_to_idx = affinity._sku_to_idx
    matrix     = affinity._matrix
    result: list = []

    # ── SKU-run cache (the _travel_balanced_impl precedent) ──────────────────
    # sorted_units clusters same-SKU units (equal expected_labor, stable sort), and
    # within such a run the per-aisle base cost bc_by_aid is a pure function of
    # (var, deque ends): var is the SKU's handle_var (constant across the run) and the
    # only deque that changes is the WINNER's (its end is popped).  So rebuild the dict
    # only at a SKU change and refresh just the last winner inside a run.  Dict ORDER is
    # part of byte-identity (fq·bc ties resolve by insertion order in sorted()): the
    # rebuild iterates by_aisle_brkt exactly as the old per-unit build did, a value
    # refresh keeps its slot, and a pop removes it — the same key sequence the old
    # build would produce.  row_items/max_reward ride the same cache: the CSR row is
    # static and self-pairs are not stored, so a run's row never changes.
    last_sku    = None
    last_winner = None
    bc_by_aid: dict = {}
    row_items: list = []
    max_reward  = 0.0

    def _aisle_best_cost(aid, var):
        """Extremal (min, or max if maximize) over the aisle's bracket ends of the
        per-pick labor + travel:  M·(intercept + var) + D  (height scales the whole pick)."""
        best = None
        for m, dq in by_aisle_brkt[aid].items():
            if not dq:
                continue
            cost = per_pick(m, intercept, var) + D_of[id(_rep(dq))]
            if best is None or _better(cost, best):
                best = cost
        return best

    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)

        if sku != last_sku:
            # Slice the SKU's affinity row ONCE per run (not once per unit/aisle):
            # partners as (partner_idx, f_p·(lift−1)) pairs, all non-negative
            # (association above independence, mirroring _demand_weighted_delta_lift).
            # max_reward bounds lam·delta over any aisle (all partners present),
            # enabling the early-termination prune below.
            row_items = []
            si = sku_to_idx.get(sku)
            if si is not None and matrix is not None:
                s = int(matrix.indptr[si]); e = int(matrix.indptr[si + 1])
                for ci, d in zip(matrix.indices[s:e], matrix.data[s:e]):
                    w = (float(d) - 1.0) * freq_by_idx.get(int(ci), 0.0)
                    if w:
                        row_items.append((int(ci), w))
            max_reward = lam * sum(w for _, w in row_items)

            # Cheap per-aisle bin cost (O(brackets)); sort so the affinity prune can fire.
            bc_by_aid = {}
            for aid in by_aisle_brkt:
                bc = _aisle_best_cost(aid, var)
                if bc is not None:
                    bc_by_aid[aid] = bc
            last_sku = sku
        elif last_winner is not None:
            # Same SKU as the previous unit: only the winner aisle's deque changed.
            bc = _aisle_best_cost(last_winner, var)
            if bc is None:
                bc_by_aid.pop(last_winner, None)
            else:
                bc_by_aid[last_winner] = bc
        last_winner = None                      # set again only on a successful pop
        if not bc_by_aid:
            result.append((unit, None))
            continue
        # minimise: ascending fq·bc, prune once base − max_reward ≥ best (reward can't save it).
        # maximise: descending fq·bc, prune once base ≤ best (reward only lowers the score).
        order = sorted(bc_by_aid, key=lambda a: fq * bc_by_aid[a], reverse=maximize)

        best_aid = None
        best_score = None
        for aid in order:
            base = fq * bc_by_aid[aid]
            if best_score is not None:
                if maximize:
                    if base <= best_score:
                        break
                elif base - max_reward >= best_score:
                    break
            if row_items:
                ais = aisle_idx_sets[aid]
                delta = 0.0
                for ci, w in row_items:
                    if ci in ais:
                        delta += w
            else:
                delta = 0.0
            score = base - lam * delta
            if best_score is None or _better(score, best_score):
                best_score, best_aid = score, aid
        if best_aid is None:
            result.append((unit, None))
            continue

        # Final bin in the winning aisle: extremal bracket end (golden-zone min-D / worst max-D
        # per height band), with the centroid term pulling toward (min) or away from (max) partners.
        _mass, cx = _demand_weighted_partner_centroid(
            affinity, sku, aisle_member_pos[best_aid], freq_by_idx)
        chosen = chosen_m = None
        cbest = None
        for m, dq in by_aisle_brkt[best_aid].items():
            if not dq:
                continue
            b = _rep(dq)
            cost = per_pick(m, intercept, var) + D_of[id(b)]
            if cx is not None:
                cost += x_pace * abs(b.x_phys - cx)
            if cbest is None or _better(cost, cbest):
                cbest, chosen, chosen_m = cost, b, m
        if chosen is None:
            result.append((unit, None))
            continue
        _drop(by_aisle_brkt[best_aid][chosen_m])
        last_winner = best_aid                  # the one aisle whose cached bc is now stale

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += fq
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        result.append((unit, chosen))
    return result


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

    The SKU-run cache (`bc_by_aid`, `row_items`, `max_reward`) refreshes the last winner
    LAZILY -- on the next unit rather than eagerly after the pop -- because a unit that finds
    no bin leaves nothing stale to repair.  `_last_winner` is cleared every call and re-set
    only after a successful drop.
    """

    __slots__ = ('_aff', '_ass', '_ais', '_ads', '_amp', '_fbi', '_fbs', '_qbs', '_lam',
                 '_maximize', '_intercept', '_x_pace', '_D_of', '_by_aisle_brkt',
                 '_s2i', '_matrix', '_rep', '_drop', '_last_sku', '_last_winner',
                 '_bc_by_aid', '_row_items', '_max_reward')

    def __init__(self, cands, affinity, wp, aisle_sku_sets, aisle_idx_sets,
                 aisle_demand_sum, aisle_member_pos, freq_by_idx, freq_by_sku,
                 qty_by_sku, lam, maximize=False):
        self._aff, self._ass, self._ais = affinity, aisle_sku_sets, aisle_idx_sets
        self._ads, self._amp = aisle_demand_sum, aisle_member_pos
        self._fbi, self._fbs, self._qbs = freq_by_idx, freq_by_sku, qty_by_sku
        self._lam, self._maximize = lam, maximize
        self._s2i, self._matrix = affinity._sku_to_idx, affinity._matrix

        speed = SpeedProfile(wp.x_speed, wp.y_speed)
        x_pace, y_pace = speed.x_pace, speed.y_pace
        self._x_pace = x_pace
        self._intercept = wp.pick_intercept
        brackets = getattr(wp, 'height_brackets', ())

        # minimise -> near (min-D) deque head; maximise -> far (max-D) deque tail.
        self._rep  = (lambda dq: dq[-1]) if maximize else (lambda dq: dq[0])
        self._drop = (lambda dq: dq.pop()) if maximize else (lambda dq: dq.popleft())

        D_of = _D_map(cands, x_pace, y_pace)
        M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
        by_aisle_brkt: dict[int, dict] = {}          # {aisle: {mult: D-sorted deque}}
        for b in cands:
            by_aisle_brkt.setdefault(b.location[0], {}).setdefault(
                M_of[id(b)], []).append(b)
        for groups in by_aisle_brkt.values():
            for m, lst in list(groups.items()):
                lst.sort(key=lambda bb: D_of[id(bb)])
                groups[m] = deque(lst)
        self._D_of, self._by_aisle_brkt = D_of, by_aisle_brkt

        self._last_sku = None
        self._last_winner = None
        self._bc_by_aid: dict = {}
        self._row_items: list = []
        self._max_reward = 0.0

    def __len__(self):
        return sum(len(dq) for g in self._by_aisle_brkt.values() for dq in g.values())

    def order(self, units):
        """Costliest SKUs claim the best (or, for maxlabor, the worst) slots first."""
        return sorted(units, key=lambda u: u.order.expected_labor, reverse=True)

    def _better(self, a, b):                 # is a a better (more extreme) score than b?
        return a > b if self._maximize else a < b

    def _aisle_best_cost(self, aid, var):
        """Extremal (min, or max if maximize) over the aisle's bracket ends of the
        per-pick labor + travel:  M*(intercept + var) + D  (height scales the whole pick)."""
        best = None
        intercept, D_of, rep = self._intercept, self._D_of, self._rep
        for m, dq in self._by_aisle_brkt[aid].items():
            if not dq:
                continue
            cost = per_pick(m, intercept, var) + D_of[id(rep(dq))]
            if best is None or self._better(cost, best):
                best = cost
        return best

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

            # Cheap per-aisle bin cost (O(brackets)); sort so the affinity prune can fire.
            bc_by_aid = {}
            for aid in by_aisle_brkt:
                bc = self._aisle_best_cost(aid, var)
                if bc is not None:
                    bc_by_aid[aid] = bc
            self._bc_by_aid = bc_by_aid
            self._last_sku = sku
        elif self._last_winner is not None:
            # Same SKU as the previous unit: only the winner aisle's deque changed.
            bc = self._aisle_best_cost(self._last_winner, var)
            if bc is None:
                self._bc_by_aid.pop(self._last_winner, None)
            else:
                self._bc_by_aid[self._last_winner] = bc
        self._last_winner = None                # set again only on a successful pop

        bc_by_aid, row_items = self._bc_by_aid, self._row_items
        max_reward = self._max_reward
        if not bc_by_aid:
            return None, None
        # minimise: ascending fq*bc, prune once base - max_reward >= best (reward can't save
        # it).  maximise: descending fq*bc, prune once base <= best (reward only lowers it).
        order = sorted(bc_by_aid, key=lambda a: fq * bc_by_aid[a], reverse=maximize)

        best_aid = None
        best_score = None
        for aid in order:
            base = fq * bc_by_aid[aid]
            if best_score is not None:
                if maximize:
                    if base <= best_score:
                        break
                elif base - max_reward >= best_score:
                    break
            if row_items:
                ais = self._ais[aid]
                delta = 0.0
                for ci, w in row_items:
                    if ci in ais:
                        delta += w
            else:
                delta = 0.0
            score = base - lam * delta
            if best_score is None or self._better(score, best_score):
                best_score, best_aid = score, aid
        if best_aid is None:
            return None, None

        # Final bin in the winning aisle: extremal bracket end (golden-zone min-D / worst
        # max-D per height band), with the centroid term pulling toward (min) or away from
        # (max) partners.
        _mass, cx = _demand_weighted_partner_centroid(
            self._aff, sku, self._amp[best_aid], self._fbi)
        chosen = chosen_m = None
        cbest = None
        intercept, D_of, x_pace = self._intercept, self._D_of, self._x_pace
        for m, dq in by_aisle_brkt[best_aid].items():
            if not dq:
                continue
            b = self._rep(dq)
            cost = per_pick(m, intercept, var) + D_of[id(b)]
            if cx is not None:
                cost += x_pace * abs(b.x_phys - cx)
            if cbest is None or self._better(cost, cbest):
                cbest, chosen, chosen_m = cost, b, m
        if chosen is None:
            return None, None
        self._drop(by_aisle_brkt[best_aid][chosen_m])
        self._last_winner = best_aid            # the one aisle whose cached bc is now stale

        if sku not in self._ass[best_aid]:
            self._ass[best_aid].add(sku)
            self._ads[best_aid] += fq
        idx = self._s2i.get(sku)
        if idx is not None:
            self._ais[best_aid].add(idx)
            self._amp[best_aid][idx].append(chosen.x_phys)
        return chosen, best_score


def _build_minlabor_pool_fn(affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                            aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam,
                            maximize=False):
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
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    aisle_member_pos : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam=beta)
    return ranked_assign


def build_ranked_minlabor_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
    freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_minlabor_fn — same signature."""
    _require_affinity(affinity, 'rank_minlabor')
    return _build_minlabor_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
        freq_by_idx, freq_by_sku, qty_by_sku, lam=beta, maximize=False)


def build_ranked_maxlabor_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    aisle_member_pos : dict,
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
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam=beta,
            maximize=True)
    return ranked_assign


def build_ranked_maxlabor_pool_fn(
    affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
    freq_by_idx, freq_by_sku, qty_by_sku, beta: float = 1.0,
):
    """Pool twin of build_ranked_maxlabor_fn — same signature."""
    _require_affinity(affinity, 'rank_maxlabor')
    return _build_minlabor_pool_fn(
        affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
        freq_by_idx, freq_by_sku, qty_by_sku, lam=beta, maximize=True)


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


def _aisle_anchor_gap(lst, pref, target):
    """Best achievable map-anchor gap in one aisle's candidate bins (lower = more favored):
    min |pref − target|, or min pref when the SKU has no target (prefer prime)."""
    if target is None:
        return min(pref.get(id(b), 0.0) for b in lst)
    return min(abs(pref.get(id(b), 0.0) - target) for b in lst)


def _cluster_map_pick_bin(lst, pref, target, cx, x_pace, capped):
    """Choose the cluster's bin within one aisle: anchor at the favored map location and
    compact toward the partner centroid; honour the prime-spot cap when capped."""
    def cost(b):
        p = pref.get(id(b), 0.0)
        c = abs(p - target) if target is not None else p
        if cx is not None:
            c += _CLUSTER_MAP_W_CENT * x_pace * abs(b.x_phys - cx)
        return c
    if capped and target is not None:
        eligible = [b for b in lst if pref.get(id(b), 0.0) >= target]   # tier or worse
        if eligible:
            return min(eligible, key=cost)
        return max(lst, key=lambda b: pref.get(id(b), 0.0))             # least-prime last resort
    return min(lst, key=cost)


def _cluster_map_choose_aisle(by_aisle, prefs_by_aisle, row, aisle_idx_sets, freq_by_idx, target,
                              lifts=None):
    """Cohesion-first aisle: max Σ(lift−1)·f to members, tie-break / cold-start by anchor gap.

    Same argmax as ``max(live, key=(lift, -anchor_gap))`` but LAZY: the O(B) anchor-gap scan is
    replaced by an O(log) ``_closest_abs`` bisect on the aisle's pre-sorted ``prefs_by_aisle`` and
    is evaluated ONLY for the aisles tied at the max lift (in the warm case, one aisle wins on lift
    → no gap work at all).  ``row`` is the SKU's pre-sliced affinity row (hoisted once per unit).

    ``lifts`` may carry the delta values precomputed by a caller's same-SKU run cache (a
    superset keyed by aisle); values for the current live aisles are read from it instead
    of recomputed — identical numbers, because within a same-SKU run the member idx-sets
    gain only the SKU's own index, which (self-pairs are not stored) is never in ``row``."""
    live = [aid for aid, lst in by_aisle.items() if lst]
    if not live:
        return None
    if lifts is None:
        lifts = {a: _delta_lift_from_row(row, aisle_idx_sets[a], freq_by_idx) for a in live}
    else:
        lifts = {a: lifts[a] for a in live}
    best  = max(lifts.values())
    tied  = [a for a in live if lifts[a] == best]
    if len(tied) == 1:
        return tied[0]
    # tie-break: min anchor gap.  target None ⇒ gap = min pref = prefs[0] (ascending list).
    if target is None:
        return min(tied, key=lambda a: prefs_by_aisle[a][0])
    return min(tied, key=lambda a: _closest_abs(prefs_by_aisle[a], target))


def _cluster_map_commit(aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                        affinity, aid, sku, f_s, q_s, x_phys):
    if sku not in aisle_sku_sets[aid]:
        aisle_sku_sets[aid].add(sku)
        aisle_demand_sum[aid] += f_s * q_s
    idx = affinity._sku_to_idx.get(sku)
    if idx is not None:
        aisle_idx_sets[aid].add(idx)
        aisle_member_pos[aid][idx].append(x_phys)


def build_cluster_map_placement(mgr, affinity, wp,
                                aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                                freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0, *, capped) -> Placement:
    """One Placement (ranked place_wave + per-unit place_one) for cluster_map (capped=False)
    or cluster_map_rank (capped=True).  Reads mgr._bin_pref / mgr._map_target at call time, so
    build_optimal_map must have run (wired in strategies._build_cluster_map[_rank])."""
    name = 'cluster_map_rank' if capped else 'cluster_map'
    _require_affinity(affinity, name)          # cohesion is meaningless without lift data
    _require_demand(freq_by_idx, name, 'freq_by_idx (the cohesion weight)')
    x_pace = sec_per_inch(wp.x_speed)

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

    def _place(sku, by_aisle, prefs_by_aisle, f_s, q_s, run_cache=None):
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
        if run_cache is not None and run_cache.get('sku') == sku:
            row, lifts = run_cache['row'], run_cache['lifts']
        else:
            row = _affinity_row(affinity, sku)            # hoist the CSR slice: once per run
            if run_cache is not None:
                lifts = {a: _delta_lift_from_row(row, aisle_idx_sets[a], freq_by_idx)
                         for a, lst in by_aisle.items() if lst}
                run_cache.update(sku=sku, row=row, lifts=lifts)
        aid = _cluster_map_choose_aisle(by_aisle, prefs_by_aisle, row,
                                        aisle_idx_sets, freq_by_idx, target, lifts=lifts)
        if aid is None:
            return None
        _mass, cx = _demand_weighted_partner_centroid(
            affinity, sku, aisle_member_pos[aid], freq_by_idx)
        chosen = _cluster_map_pick_bin(by_aisle[aid], pref, target, cx, x_pace, capped)
        by_aisle[aid].remove(chosen)
        plst = prefs_by_aisle[aid]                        # drop the chosen bin's pref (multiset-sync)
        k = bisect.bisect_left(plst, pref.get(id(chosen), 0.0))
        if k < len(plst) and plst[k] == pref.get(id(chosen), 0.0):
            del plst[k]
        _cluster_map_commit(aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                            affinity, aid, sku, f_s, q_s, chosen.x_phys)
        if run_cache is not None and run_cache.get('sku') == sku:
            # The commit may have grown THIS aisle's idx-set: recompute its delta fresh so
            # the next same-SKU unit sees exactly what a full per-unit recompute would.
            run_cache['lifts'][aid] = _delta_lift_from_row(row, aisle_idx_sets[aid], freq_by_idx)
        return chosen

    def place_one(unit, candidates):
        if not candidates:
            return None
        by_aisle, prefs_by_aisle = _group(candidates)
        c = unit.order
        return _place(c.sku, by_aisle, prefs_by_aisle,
                      freq_by_sku.get(c.sku, 0.0), qty_by_sku.get(c.sku, 0.0))

    def place_wave(units, candidates_fn):
        all_idx = set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set()

        def priority(unit):
            c = unit.order
            co = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
            return c.demand.relative_frequency * c.labor_cost + co

        sorted_units = sorted(units, key=priority, reverse=True)
        result: list = []
        if not sorted_units:
            return result
        by_aisle, prefs_by_aisle = _group(candidates_fn(sorted_units[0]))   # one tier, once per wave
        run_cache: dict = {}                    # same-SKU run reuse; _place owns the rules
        for unit in sorted_units:
            c = unit.order
            result.append((unit, _place(c.sku, by_aisle, prefs_by_aisle,
                                        freq_by_sku.get(c.sku, 0.0), qty_by_sku.get(c.sku, 0.0),
                                        run_cache)))
        return result

    place_one.name = name
    place_wave.name = name
    return Placement(name, place_one, place_wave)


# ── programmatic name → builder registries (robust downstream lookup) ──────
ASSIGNMENT_BUILDERS = {
    'travel_min':   build_trip_minimizing_assignment_fn,
    'travel_max':   build_trip_maximizing_assignment_fn,
    'cohesion_max': build_cluster_maximizing_assignment_fn,
    'cohesion_min': build_cluster_minimizing_assignment_fn,
    'uniform_min':  build_uniform_aisle_trip_min_assignment_fn,
    'load_min':     build_load_minimizing_assignment_fn,
    'load_max':     build_load_maximizing_assignment_fn,
}
RANKED_BUILDERS = {
    'travel_min':     build_ranked_minimizing_assignment_fn,
    'travel_max':     build_ranked_maximizing_assignment_fn,
    'uniform_ranked': build_ranked_uniform_assignment_fn,
}
# (needs_affinity, needs_demand) state required before each scorer can be used.
SCORER_NEEDS = {
    'travel_min': (True, True),   'travel_max': (True, True),
    'cohesion_max': (True, True), 'cohesion_min': (True, True),
    'uniform_min': (False, False),
    'load_min': (True, False),    'load_max': (True, False),
}
