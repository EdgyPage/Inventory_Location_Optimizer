"""Assignment_Functions.py -- placement (assignment) functions.

Separated from Inventory_Management so the placement POLICIES the simulation
compares live in one module.  Inventory_Manager merely *receives* a per-unit
assignment fn (and optional ranked_assignment_fn for ranked drains); these builders
produce them.  The shared constants/types they need (BinKey, _SIZE_RANKS, ...) stay
in Inventory_Management and are imported here (one-way -- no import cycle).
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any

from Affinity_Store import AffinityStore
from Inventory_Management import (
    _SIZE_RANKS, _SIZES_DESCENDING, BinKey,
    AssignmentFn, RankedAssignmentFn, LoadParams,
)


# ── load-aware assignment functions ───────────────────────────────────────────

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
    strictly monotone increasing in D = x_speed*x_phys + y_speed*y_phys:
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
    for b in candidates:
        aid = b.location[0]
        D   = x_speed * b.x_phys + y_speed * b.y_phys
        if aid not in best_D or (D < best_D[aid] if minimize else D > best_D[aid]):
            best_D[aid]   = D
            best_bin[aid] = b
    return best_D, best_bin


def build_load_minimizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
    aisle_index    : dict | None = None,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily minimises the L2 norm of predicted
    aisle loads  L_a = W + λ*(W/k)^γ * lift_sum.

    Dual-optimisation algorithm
    ---------------------------
    1. Reduce candidates to one bin per aisle (minimum-D bin) — exact by
       monotonicity of delta_l2 in D within a fixed aisle.
    2. Sort the O(N_aisles) representatives by D ascending.
    3. Evaluate aisles in D order with LAZY CSR queries (delta_lift computed
       only when the aisle is actually reached, not upfront for all aisles).
    4. Early termination: once the best score has delta_l2 = 0 (no affinity
       partners in the winning aisle), any remaining aisle with D ≥ best_old_L
       cannot improve — old_L ≥ D ≥ best_old_L and delta_l2 ≥ 0 = best_delta_l2.
       With sparse top-20 affinity most aisles have delta_lift = 0, so the
       termination typically fires after the first few aisles.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(D: float, ls: float) -> float:
        return D + lam * (D / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any] | None) -> Any | None:
        sku = unit.carton.sku

        # Step 1: one representative bin per aisle (min-D).
        # Fast path: derive BinKey from unit, read directly from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.carton.storage_handle_config
            unit_type = unit.unit_category
            if unit_type == 'singleton':
                by_aisle = aisle_index.get((shc.handling, shc.category, 'singleton', 'singleton'))
            else:
                min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
                by_aisle = None
                for size in reversed(_SIZES_DESCENDING):
                    if _SIZE_RANKS[size] >= min_rank:
                        by = aisle_index.get((shc.handling, shc.category, size, unit_type))
                        if by and any(by.values()):
                            by_aisle = by
                            break
            best_D: dict[int, float] = {}
            best_bin_map: dict[int, Any] = {}
            if by_aisle:
                for aid, lst in by_aisle.items():
                    if lst:
                        b = lst[0]  # sorted ascending — first is min-D
                        best_D[aid]       = b._D
                        best_bin_map[aid] = b
            if not best_D:
                return None
        else:
            if not candidates:
                return None
            best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)

        # Step 2: sort aisles by ascending min-D — O(N_aisles log N_aisles)
        sorted_aids = sorted(best_D, key=best_D.__getitem__)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('inf'), float('inf'))
        best_delta_lift : float               = 0.0

        # Step 3+4: lazy CSR queries + early termination
        for aid in sorted_aids:
            D = best_D[aid]

            # Early termination: best has delta_l2=0; remaining D ≥ best old_L
            # means score ≥ (0, D) ≥ (0, best_old_L) = best — prune the rest.
            if best_score[0] == 0.0 and D >= best_score[1]:
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

            if score < best_score:
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

    return assign


def build_load_maximizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
    aisle_index    : dict | None = None,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily maximises the L2 norm of predicted
    aisle loads  L_a = W + λ*(W/k)^γ * lift_sum.

    Same dual-optimisation structure as the minimising variant:
    one bin per aisle (min-D) + aisles sorted by D descending (largest
    travel cost first — highest potential delta_l2) + lazy CSR queries.
    No early termination for maximising: a low-D aisle can still win if it
    has very high affinity lift, so the sorted order does not guarantee
    pruning.  The one-bin-per-aisle reduction still eliminates O(N_bins)
    evaluations, leaving O(N_aisles) CSR queries.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(D: float, ls: float) -> float:
        return D + lam * (D / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any] | None) -> Any | None:
        sku = unit.carton.sku

        # One representative bin per aisle (max-D) — exact by monotonicity.
        # Fast path: derive BinKey from unit, read from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.carton.storage_handle_config
            unit_type = unit.unit_category
            if unit_type == 'singleton':
                by_aisle = aisle_index.get((shc.handling, shc.category, 'singleton', 'singleton'))
            else:
                min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
                by_aisle = None
                for size in reversed(_SIZES_DESCENDING):
                    if _SIZE_RANKS[size] >= min_rank:
                        by = aisle_index.get((shc.handling, shc.category, size, unit_type))
                        if by and any(by.values()):
                            by_aisle = by
                            break
            best_D: dict[int, float] = {}
            best_bin_map: dict[int, Any] = {}
            if by_aisle:
                for aid, lst in by_aisle.items():
                    if lst:
                        b = lst[-1]  # sorted ascending — last is max-D
                        best_D[aid]       = b._D
                        best_bin_map[aid] = b
            if not best_D:
                return None
        else:
            if not candidates:
                return None
            best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=False)

        # Sort descending: high-D aisles have the largest potential delta_l2
        sorted_aids = sorted(best_D, key=best_D.__getitem__, reverse=True)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('-inf'), float('-inf'))
        best_delta_lift : float               = 0.0

        for aid in sorted_aids:
            D  = best_D[aid]
            ls = aisle_lift_sum[aid]
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(D, ls)
            new_L    = _L(D, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if score > best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign


# ── trip-cost assignment functions ────────────────────────────────────────────


def _demand_weighted_delta_lift(
    affinity       : AffinityStore,
    sku            : int,
    member_idx_set : set[int],
    freq_by_idx    : dict[int, float],
) -> float:
    """Sum of affinity(s, i) * f_i for all affinity partners i in the aisle.

    Uses the same CSR row-slice pattern as delta_lift_idxs but multiplies
    each affinity score by the partner's demand frequency.  This weights the
    co-location benefit by how often the partner actually appears in a batch,
    so rare-but-high-affinity pairs do not dominate over common low-affinity ones.
    """
    if not member_idx_set or affinity._matrix is None or sku not in affinity._sku_to_idx:
        return 0.0
    i     = affinity._sku_to_idx[sku]
    start = int(affinity._matrix.indptr[i])
    end   = int(affinity._matrix.indptr[i + 1])
    if start == end:
        return 0.0
    col_indices = affinity._matrix.indices[start:end]
    data        = affinity._matrix.data[start:end]
    return float(sum(
        d * freq_by_idx.get(int(ci), 0.0)
        for ci, d in zip(col_indices, data)
        if ci in member_idx_set
    ))


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


def _build_aisle_score_fn(name, *, score_kind, maximize, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                          freq_by_idx, freq_by_sku, qty_by_sku, beta):
    """Compose a per-unit AssignmentFn from a named aisle SCORER + direction.

    Shared by the travel (f_s*D - beta*co_occur) and cohesion (demand-weighted lift)
    policies; they differ only in the score tuple and which D-rank bin represents
    each aisle.  The returned closure carries a programmatic ``.name`` (e.g.
    'travel_min') so downstream processing can build/identify functions by name.
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    # cohesion always uses the front (min-D) bay; travel uses min-D when minimising
    # and max-D when maximising (f_s*D is monotone in D within an aisle).
    bin_minimize = True if score_kind == 'cohesion' else (not maximize)

    def assign(unit, candidates):
        if not candidates:
            return None
        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)
        best_D, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=bin_minimize)

        def score_of(aid):
            D = best_D[aid]
            co = (0.0 if sku in aisle_sku_sets[aid]
                  else _demand_weighted_delta_lift(affinity, sku, aisle_idx_sets[aid], freq_by_idx))
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
    return assign


def _travel_or_cohesion(name, score_kind, maximize):
    """Make a builder with the legacy (affinity, wp, ...state..., beta) signature that
    routes through the shared core."""
    def builder(affinity, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0):
        return _build_aisle_score_fn(
            name, score_kind=score_kind, maximize=maximize, affinity=affinity, wp=wp,
            aisle_sku_sets=aisle_sku_sets, aisle_idx_sets=aisle_idx_sets,
            aisle_demand_sum=aisle_demand_sum, freq_by_idx=freq_by_idx,
            freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku, beta=beta)
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
    unit_type), so the random aisle is always a legal one.  Per-unit; pair with
    ranked_assignment_fn = None for a FIFO drain.
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
) -> list:
    """Shared core for ranked-minimizing and ranked-maximizing assignment.

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
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    pi      = wp.pick_intercept
    pw      = wp.pick_weight_coef
    pv      = wp.pick_volume_coef

    # Fix 1: the co-occurrence term ranks each SKU against ALL currently-placed
    # SKU indices.  That union is identical for every unit in the wave (placement
    # is deferred to the caller, so aisle_idx_sets is static here), so build it
    # ONCE — not once per unit inside the sort key (which was O(U·Σ) per wave).
    all_idx = set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set()

    def pick_effort_priority(unit) -> float:
        c    = unit.carton
        f_i  = c.demand.frequency
        w    = max(1, c.weight)
        v    = max(1, c.volume())
        effort = pi + pw * math.log(w) + pv * math.log(v)
        co_occur = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
        return f_i * effort + co_occur

    sorted_units = sorted(units, key=pick_effort_priority, reverse=True)
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
    D_of  = {id(b): x_speed * b.x_phys + y_speed * b.y_phys for b in cands}
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

        sku = unit.carton.sku
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

    Same parameter signature as build_trip_minimizing_assignment_fn.
    Assign manager.ranked_assignment_fn = build_ranked_minimizing_assignment_fn(...)
    """
    def ranked_assign(units: list, candidates_fn) -> list:
        return _ranked_assign_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        )
    return ranked_assign


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
