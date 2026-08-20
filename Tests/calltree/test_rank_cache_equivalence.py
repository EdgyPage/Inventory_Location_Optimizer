"""test_rank_cache_equivalence.py — frozen oracles for the Phase-6 SKU-run caches.

`_ranked_minlabor_impl` and `_cluster_map_choose_aisle` gained same-SKU-run caches
(bc_by_aid with winner-only refresh; per-aisle delta-lift with post-commit winner
refresh).  The oracles below are the PRE-CACHE implementations preserved verbatim; each
test runs the full meso arm (production placement wiring, 8k SKUs, 10 batches) twice —
new code, then oracle-patched — and demands an identical end state: placement/pick/
reorder counts AND a hash over every occupied bin's (aisle, bayX, bayY, sku, qty).

The `*_detectable` tests are the built-in mutation proof: each re-runs with the cache
bug the fix round actually hit (cluster: a cache held across the winner set's growth —
`_delta_lift_from_row` iterates the smaller side, so the summation ORDER flips and the
value drifts by one ulp, moving a placement; minlabor: a bc cache never refreshed after
the winner's deque pop) and demands the end state DIFFER from the oracle's.  If a future
rescale of this file stops exposing those, the test FAILS rather than passing vacuously.

    cd Tests/calltree && python -m pytest test_rank_cache_equivalence.py
"""
from __future__ import annotations

import hashlib
from collections import deque

import calltree_scenarios as cs

from Warehouse.placement import Assignment_Functions as af
from Warehouse.placement.Assignment_Functions import (
    _closest_abs, _delta_lift_from_row, _demand_weighted_partner_centroid, _D_map)
from Warehouse.inventory.inventory_common import _wp_for
from Warehouse.kernel.cost_model import height_multiplier, per_pick, sec_per_inch

N_SKUS    = 8_000     # the scale at which the ulp staleness demonstrably moves a placement
N_BATCHES = 10


# ── frozen oracle 1: _cluster_map_choose_aisle as of commit 831571f, verbatim ─────────
# (accepts and IGNORES the new `lifts` kwarg — recomputing per call IS the old behavior)

def _oracle_choose_aisle(by_aisle, prefs_by_aisle, row, aisle_idx_sets, freq_by_idx, target,
                         lifts=None):
    live = [aid for aid, lst in by_aisle.items() if lst]
    if not live:
        return None
    lifts = {a: _delta_lift_from_row(row, aisle_idx_sets[a], freq_by_idx) for a in live}
    best  = max(lifts.values())
    tied  = [a for a in live if lifts[a] == best]
    if len(tied) == 1:
        return tied[0]
    if target is None:
        return min(tied, key=lambda a: prefs_by_aisle[a][0])
    return min(tied, key=lambda a: _closest_abs(prefs_by_aisle[a], target))




# ── frozen oracle 2: _ranked_minlabor_impl as of commit 831571f, verbatim ─────────────

def _oracle_ranked_minlabor_impl(units, candidates_fn, affinity, wp,
                                 aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                                 aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku, lam,
                                 maximize=False):
    wp = _wp_for(wp, units[0]) if units else wp
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)
    intercept = wp.pick_intercept
    brackets  = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]

    _rep  = (lambda dq: dq[-1]) if maximize else (lambda dq: dq[0])
    _drop = (lambda dq: dq.pop()) if maximize else (lambda dq: dq.popleft())
    def _better(a, b):
        return a > b if maximize else a < b

    D_of = _D_map(cands, x_pace, y_pace)
    M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
    by_aisle_brkt: dict[int, dict] = {}
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

        row_items: list = []
        si = sku_to_idx.get(sku)
        if si is not None and matrix is not None:
            s = int(matrix.indptr[si]); e = int(matrix.indptr[si + 1])
            for ci, d in zip(matrix.indices[s:e], matrix.data[s:e]):
                w = (float(d) - 1.0) * freq_by_idx.get(int(ci), 0.0)
                if w:
                    row_items.append((int(ci), w))
        max_reward = lam * sum(w for _, w in row_items)

        bc_by_aid = {}
        for aid in by_aisle_brkt:
            bc = _aisle_best_cost(aid, var)
            if bc is not None:
                bc_by_aid[aid] = bc
        if not bc_by_aid:
            result.append((unit, None))
            continue
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

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += fq
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        result.append((unit, chosen))
    return result


# ── the minlabor bug, reproduced: rebuild on sku change, never refresh the winner ─────

def _unrefreshed_ranked_minlabor_impl(units, candidates_fn, affinity, wp,
                                      aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
                                      aisle_member_pos, freq_by_idx, freq_by_sku, qty_by_sku,
                                      lam, maximize=False):
    wp = _wp_for(wp, units[0]) if units else wp
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)
    intercept = wp.pick_intercept
    brackets  = getattr(wp, 'height_brackets', ())
    sorted_units = sorted(units, key=lambda u: u.order.expected_labor, reverse=True)
    if not sorted_units:
        return []
    cands = candidates_fn(sorted_units[0])
    if not cands:
        return [(u, None) for u in sorted_units]
    _rep  = (lambda dq: dq[-1]) if maximize else (lambda dq: dq[0])
    _drop = (lambda dq: dq.pop()) if maximize else (lambda dq: dq.popleft())
    def _better(a, b):
        return a > b if maximize else a < b
    D_of = _D_map(cands, x_pace, y_pace)
    M_of = {id(b): height_multiplier(brackets, b.y_phys) for b in cands}
    by_aisle_brkt: dict[int, dict] = {}
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
        best = None
        for m, dq in by_aisle_brkt[aid].items():
            if not dq:
                continue
            cost = per_pick(m, intercept, var) + D_of[id(_rep(dq))]
            if best is None or _better(cost, best):
                best = cost
        return best

    last_sku = None
    bc_by_aid: dict = {}
    row_items: list = []
    max_reward = 0.0
    for unit in sorted_units:
        c = unit.order
        sku = c.sku
        var = c.handle_var
        fq = freq_by_sku.get(sku, 0.0) * qty_by_sku.get(sku, 0.0)
        if sku != last_sku:
            row_items = []
            si = sku_to_idx.get(sku)
            if si is not None and matrix is not None:
                s = int(matrix.indptr[si]); e = int(matrix.indptr[si + 1])
                for ci, d in zip(matrix.indices[s:e], matrix.data[s:e]):
                    w = (float(d) - 1.0) * freq_by_idx.get(int(ci), 0.0)
                    if w:
                        row_items.append((int(ci), w))
            max_reward = lam * sum(w for _, w in row_items)
            bc_by_aid = {}
            for aid in by_aisle_brkt:
                bc = _aisle_best_cost(aid, var)
                if bc is not None:
                    bc_by_aid[aid] = bc
            last_sku = sku
        # THE BUG UNDER TEST: no winner refresh — stale bc after the winner's deque pop.
        if not bc_by_aid:
            result.append((unit, None))
            continue
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
        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            aisle_demand_sum[best_aid] += fq
        idx = sku_to_idx.get(sku)
        if idx is not None:
            aisle_idx_sets[best_aid].add(idx)
            aisle_member_pos[best_aid][idx].append(chosen.x_phys)
        result.append((unit, chosen))
    return result


# ── harness ────────────────────────────────────────────────────────────────────────────

def _end_state(assets) -> str:
    """Order-independent hash of every occupied bin: the spatial end state of the arm."""
    rows = []
    for b in assets.mgr._unavailable.values():
        if b.storage is None:
            continue
        rows.append((b.location[0], b.bayX, b.bayY, b.storage.order.sku, b.storage.quantity))
    return hashlib.sha256(repr(sorted(rows)).encode()).hexdigest()


def _run_arm(strategy, patch=None):
    saved = (af._ranked_minlabor_impl, af._cluster_map_choose_aisle)
    try:
        if patch is not None:
            patch()
        assets = cs.build_assets(n_skus=N_SKUS, strategy=strategy, seed=42)
        res = cs.run_meso(assets, n_batches=N_BATCHES)
        return (res.placements, res.picks, res.reorders, _end_state(assets))
    finally:
        af._ranked_minlabor_impl, af._cluster_map_choose_aisle = saved


# ── the gates ──────────────────────────────────────────────────────────────────────────

def test_cluster_map_cache_matches_frozen_oracle():
    new = _run_arm('uni_cluster_map_rank_norsl')
    def _patch():
        af._cluster_map_choose_aisle = _oracle_choose_aisle
    oracle = _run_arm('uni_cluster_map_rank_norsl', _patch)
    assert new == oracle, (
        f'cluster_map SKU-run cache diverged from the frozen oracle: {new[:3]} vs {oracle[:3]}')


def test_delta_lift_summation_order_is_value_bearing():
    """The mechanism that makes the post-commit winner refresh load-bearing, pinned
    directly: _delta_lift_from_row iterates the SMALLER side, so when the member set
    grows past the row size the branch — and with it the summation ORDER — flips, and
    float addition is not associative: the value moves by an ulp over the very same
    contributing members.  This is the drift the fix round measured in production shape
    (one placement moved at 8k-SKU meso scale) before the winner refresh restored
    per-unit exactness."""
    # dict `row` iterates in insertion order (7, 6, 5); a small-int set iterates
    # ascending (5, 6, 7) — small ints hash to themselves.  lift == 2.0 makes
    # (lift − 1) exactly 1.0, so each contribution IS its freq, and the two orders sum
    # to different floats: 0.3+0.2+0.1 == 0.6 but 0.1+0.2+0.3 == 0.6000000000000001.
    freq = {5: 0.1, 6: 0.2, 7: 0.3}
    row3 = {7: 2.0, 6: 2.0, 5: 2.0}
    row4 = {7: 2.0, 6: 2.0, 5: 2.0, 9: 1.0}          # 9 contributes 0 (lift == 1)
    members = {5, 6, 7}
    a = _delta_lift_from_row(row3, members, freq)    # len(row) <= len(set): ROW order
    b = _delta_lift_from_row(row4, members, freq)    # len(row) >  len(set): SET order
    assert abs(a - b) < 1e-12, 'same members, same contributions — only order differs'
    assert a != b, (
        'expected an ulp-level summation-order difference over identical members; if '
        'this stops holding, re-verify whether the winner refresh is still load-bearing')


def test_choose_aisle_consumes_cached_values():
    """The cached `lifts` really is decision-bearing: an ulp-stale value must be able
    to flip the argmax.  Two aisles whose true deltas tie — a cache that carries one
    aisle a hair higher steals the win.  Proves choose_aisle reads the cache rather
    than recomputing (the equivalence gates above prove the real cache is never stale);
    if this fails, those gates are vacuous."""
    b1, b2 = object(), object()
    by_aisle = {1: [b1], 2: [b2]}
    prefs    = {1: [0.0], 2: [0.0]}
    row      = {5: 1.5}
    idx_sets = {1: {5}, 2: {5}}
    freq     = {5: 1.0}
    fresh = af._cluster_map_choose_aisle(by_aisle, prefs, row, idx_sets, freq, None)
    assert fresh == 1, 'tie on lift -> tie-break by min pref gap -> first tied aisle'
    stale = {1: 0.5, 2: 0.5000000000000001}          # one-ulp advantage for aisle 2
    forced = af._cluster_map_choose_aisle(by_aisle, prefs, row, idx_sets, freq, None,
                                          lifts=stale)
    assert forced == 2, 'a stale cached value did not reach the decision'


def test_minlabor_cache_matches_frozen_oracle():
    new = _run_arm('uni_rank_minlabor_norsl')
    def _patch():
        af._ranked_minlabor_impl = _oracle_ranked_minlabor_impl
    oracle = _run_arm('uni_rank_minlabor_norsl', _patch)
    assert new == oracle, (
        f'minlabor SKU-run cache diverged from the frozen oracle: {new[:3]} vs {oracle[:3]}')


def test_minlabor_unrefreshed_cache_is_detectable():
    def _patch():
        af._ranked_minlabor_impl = _unrefreshed_ranked_minlabor_impl
    stale = _run_arm('uni_rank_minlabor_norsl', _patch)
    def _opatch():
        af._ranked_minlabor_impl = _oracle_ranked_minlabor_impl
    oracle = _run_arm('uni_rank_minlabor_norsl', _opatch)
    assert stale != oracle, (
        'the never-refreshed bc cache no longer diverges at this scale — '
        'the equivalence gate above has gone vacuous; re-tune N_SKUS/N_BATCHES')
