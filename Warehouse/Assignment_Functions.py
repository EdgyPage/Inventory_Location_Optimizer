"""Assignment_Functions.py -- placement (assignment) functions.

Separated from Inventory_Management so the placement POLICIES the simulation
compares live in one module.  A strategy bundles these builders into one
``Placement`` (per-unit ``place_one`` + optional ranked ``place_wave``) and hands it
to the manager.  The shared constants/types they need (BinKey, Placement, _SIZE_RANKS,
...) stay in Inventory_Management and are imported here (one-way -- no import cycle).
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any

from Affinity_Store import AffinityStore
from cost_model import height_multiplier, sec_per_inch
from Inventory_Management import (
    _SIZE_RANKS, _SIZES_DESCENDING, BinKey,
    AssignmentFn, RankedAssignmentFn, LoadParams, Placement,
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
        sku = unit.order.sku

        # Step 1: one representative bin per aisle (min-D).
        # Fast path: derive BinKey from unit, read directly from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.order.storage_handle_config
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

    # Coupling tag: True only when this closure reads mgr._aisle_index (the fast
    # path).  The manager's _stock guard asserts this equals _travel_costs_ready
    # so the two halves can never be armed independently.
    assign.uses_aisle_index = aisle_index is not None
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
        sku = unit.order.sku

        # One representative bin per aisle (max-D) — exact by monotonicity.
        # Fast path: derive BinKey from unit, read from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.order.storage_handle_config
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

    assign.uses_aisle_index = aisle_index is not None
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
    drains are exempt: their priority reads order.demand.frequency directly."""
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
            shc       = unit.order.storage_handle_config
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
                        b = lst[0] if bin_minimize else lst[-1]
                        best_D[aid]       = b._D
                        best_bin_map[aid] = b
            if not best_D:
                return None
        else:
            if not candidates:
                return None
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
    aisle_extra_sum     = None,
    sku_extra_product   = None,
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
    x_pace  = sec_per_inch(wp.x_speed)   # ft/s -> s/inch
    y_pace  = sec_per_inch(wp.y_speed)
    pi      = wp.pick_intercept
    pw      = wp.pick_weight_coef
    pv      = wp.pick_volume_coef

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
        return c.demand.frequency * c.labor_cost + co_occur

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
    D_of  = {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}
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
            # Cost-weighted twin (Rank_labor): keep _aisle_pick_load_sum live within
            # the wave so later units see the running labor balance.
            if aisle_extra_sum is not None:
                aisle_extra_sum[best_aid] += sku_extra_product.get(sku, 0.0)

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
                w = lift * f                 # one bin → one position; weight each equally
                mass += w * len(xs)
                wx   += w * sum(xs)
    return (mass, wx / mass) if mass > 0 else (0.0, None)


def _co_demand_ranked_impl(units, candidates_fn, affinity, wp,
                           aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
                           freq_by_idx, freq_by_sku, qty_by_sku, beta, compact: bool):
    """Ranked co-demand placement.  Units are placed in the same pick-effort order as
    _ranked_assign_impl and membership is committed incrementally, but the BIN choice is
    position-aware: the aisle is scored by demand-weighted lift to its members (MAX for
    compact / MIN for expand), and within it the bin NEAREST (compact) / FARTHEST (expand)
    the partners' column centroid is taken — vs the extremal-D head.  Each placement also
    appends (x_phys, idx) to aisle_member_pos so later units in the wave see it.
    """
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    pi, pwt, pv = wp.pick_intercept, wp.pick_weight_coef, wp.pick_volume_coef
    all_idx = set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set()

    def priority(unit):
        c = unit.order
        # c.labor_cost = precomputed per-pick effort (pi + pwt*ln w + pv*ln v).
        co = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
        return c.demand.frequency * c.labor_cost + co

    sorted_units = sorted(units, key=priority, reverse=True)
    result: list = []
    if not sorted_units:
        return result

    cands = candidates_fn(sorted_units[0])
    D_of  = {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}
    by_aisle: dict[int, list] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], []).append(b)
    for lst in by_aisle.values():
        lst.sort(key=lambda b: b.x_phys)          # ascending column
    sku_to_idx = affinity._sku_to_idx

    for unit in sorted_units:
        live = [aid for aid, lst in by_aisle.items() if lst]
        if not live:
            result.append((unit, None))
            continue
        sku = unit.order.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # aisle: most (compact) / least (expand) lift to members; tie-break toward the
        # front (compact) / back (expand) bay by the aisle's lowest-D representative.
        def aisle_key(aid):
            mass = _demand_weighted_delta_lift(affinity, sku, aisle_idx_sets[aid], freq_by_idx)
            d0   = D_of[id(by_aisle[aid][0])]
            return (mass, -d0) if compact else (mass, d0)
        best_aid = (max if compact else min)(live, key=aisle_key)

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

    return result


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

        def aisle_key(aid):
            mass = _demand_weighted_delta_lift(affinity, sku, aisle_idx_sets[aid], freq_by_idx)
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

    def place_wave(units, candidates_fn):
        return _co_demand_ranked_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, aisle_member_pos,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, compact=compact)
    place_wave.name = name
    return Placement(name, place_one, place_wave)


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


def _travel_balanced_impl(units, candidates_fn, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                          aisle_pick_load_sum, sku_pick_load_product,
                          freq_by_sku, qty_by_sku):
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
    """
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch
    intercept = wp.pick_intercept
    brackets  = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]

    D_of = {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}
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
    sku_to_idx = affinity._sku_to_idx
    result: list = []

    def _aisle_best(aid, var):
        """(cost, mult, bin) of the cheapest available bin in the aisle for this var.
        Height scales the whole at-location pick: cost = m·(intercept+var) + D."""
        best = None
        for m, dq in by_aisle[aid].items():
            if not dq:
                continue
            b = dq[0]
            cost = m * (intercept + var) + D_of[id(b)]
            if best is None or cost < best[0]:
                best = (cost, m, b)
        return best

    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)
        best_aid = best_choice = None
        best_score = None
        for aid in by_aisle:
            ab = _aisle_best(aid, var)
            if ab is None:
                continue
            score = load[aid] + fq * ab[0]
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

        by_aisle[best_aid][m].popleft()
        result.append((unit, chosen))
    return result


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


def _ranked_minlabor_impl(units, candidates_fn, affinity, wp,
                          aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                          aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam,
                          maximize=False):
    """Greedy MINIMISER (or, with maximize=True, MAXIMISER) of expected total task labor.

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

    D_of = {id(b): x_pace * b.x_phys + y_pace * b.y_phys for b in cands}
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

    def _aisle_best_cost(aid, var):
        """Extremal (min, or max if maximize) over the aisle's bracket ends of the
        per-pick labor + travel:  M·(intercept + var) + D  (height scales the whole pick)."""
        best = None
        for m, dq in by_aisle_brkt[aid].items():
            if not dq:
                continue
            cost = m * (intercept + var) + D_of[id(_rep(dq))]
            if best is None or _better(cost, best):
                best = cost
        return best

    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)

        # Slice the SKU's affinity row ONCE per unit (not once per aisle): partners as
        # (partner_idx, f_p·lift) pairs, all non-negative.  max_reward bounds lam·delta
        # over any aisle (all partners present), enabling the early-termination prune
        # below — the same idea as build_load_*'s lazy CSR queries.
        row_items: list = []
        si = sku_to_idx.get(sku)
        if si is not None and matrix is not None:
            s = int(matrix.indptr[si]); e = int(matrix.indptr[si + 1])
            for ci, d in zip(matrix.indices[s:e], matrix.data[s:e]):
                w = float(d) * freq_by_idx.get(int(ci), 0.0)
                if w:
                    row_items.append((int(ci), w))
        max_reward = lam * sum(w for _, w in row_items)

        # Cheap per-aisle bin cost (O(brackets)); sort so the affinity prune can fire.
        bc_by_aid = {}
        for aid in by_aisle_brkt:
            bc = _aisle_best_cost(aid, var)
            if bc is not None:
                bc_by_aid[aid] = bc
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
            cost = m * (intercept + var) + D_of[id(b)]
            if cx is not None:
                cost += x_pace * abs(b.x_phys - cx)
            if cbest is None or _better(cost, cbest):
                cbest, chosen, chosen_m = cost, b, m
        if chosen is None:
            result.append((unit, None))
            continue
        _drop(by_aisle_brkt[best_aid][chosen_m])

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += fq
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        result.append((unit, chosen))
    return result


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
