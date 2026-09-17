"""strategies.py — data-driven registry of placement (assignment) strategies.

Each Strategy names an assignment policy and knows how to wire it onto an
Inventory_Manager for the reorder phase.  The whole comparison pipeline
(run_simulation orchestration, strategy_runner worker, run_analysis/Comparison_Plots
labels via sim_meta) iterates this list, so adding a strategy is one entry here.

Strategies share the same uniform INITIAL stocking by default (done in the worker
before build() is called) and differ in how reorder waves are placed.  A strategy
may override stocking with a `stock` hook (e.g. optimal_reslot stocks at the
pure-global-W optimum) and may enable bounded per-batch re-slotting via `reslot_frac`.
"""
from __future__ import annotations

import colorsys
import random
from dataclasses import dataclass
from typing import Any, Callable

from Warehouse.placement.Assignment_Functions import (
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
    build_ranked_minimizing_pool_fn,
    build_ranked_maximizing_pool_fn,
    build_uniform_aisle_trip_min_assignment_fn,
    build_ranked_uniform_pool_fn,
    build_ranked_popularity_pool_fn,
    build_ranked_labor_pool_fn,
    build_ranked_cartlabor_pool_fn,
    build_ranked_minlabor_pool_fn,
    build_ranked_maxlabor_pool_fn,
    build_optmap_fn,
    build_optmap_wave_fn,
    build_optmap_pool_fn,
    build_cluster_map_placement,
    build_cluster_maximizing_assignment_fn,
    build_cluster_minimizing_assignment_fn,
    build_co_demand_placement,
    _score_expected_popularity,
    _score_expected_labor,
)
from Warehouse.inventory.Inventory_Management import Placement, _uniform_assignment
from Warehouse.placement.policy import PlacementPolicy


@dataclass
class StrategyContext:
    """Everything a strategy's build() may need, assembled by the worker."""
    affinity     : Any
    wp           : Any
    freq_by_idx  : dict
    freq_by_sku  : dict
    qty_by_sku   : dict
    beta         : float = 1.0
    orders      : Any = None   # inventory.orders — needed to build the optimal map
    expected_batch_skus : float = 0.0   # k = mean_fraction·N: expected distinct SKUs per
                                        # batch, for the Rank_cartlabor expected-cart term


@dataclass
class Strategy:
    key            : str       # db/file id + run-id dict key (e.g. 'uniform_trip_min')
    label          : str       # plot label (e.g. 'Uniform+Min')
    color          : str       # plot colour hex
    run_type       : str       # create_run run_type string
    needs_affinity : bool      # rebuild aisle sku counts + lift sums before build()
    needs_demand   : bool      # init_demand_state + freq/qty maps before build()
    build          : Callable  # (mgr, ctx: StrategyContext) -> None
    stock_mode     : str = 'uniform'  # 'uniform' = random enqueue_all; 'policy' = stock via
                                      # the strategy's own assignment fn (opt_* runs)
    reslot_frac    : float = 0.0             # >0 enables the capacity reloader (budget = % of XL aisle)
    reloader       : str = 'rebalance'       # named reloader variant: promote_popular | demote_unpopular | rebalance
    uses_aisle_index : bool = False          # per-unit _stock strategy that consumes mgr._aisle_index;
                                             # worker arms init_travel_costs() before build() (cluster only —
                                             # ranked/FIFO drains do not use the per-aisle index fast path)
    restock        : str = ''                # the restock-rule key (e.g. 'fifo', 'rank_labor'); the
                                             # initial×reslot-invariant component used for per-channel subsets


# ── build helpers: each sets exactly ONE named mgr.placement ─────────────────────
# place_one (per-unit) is always present — it drives the per-unit drain and places
# ranked stragglers.  place_wave (optional) makes the policy a ranked wave.  No None.

def _build_uniform(mgr, ctx: StrategyContext) -> None:
    # FIFO: per-unit uniform-random placement (also the manager's default).
    mgr.placement = Placement('uniform_fifo', _uniform_assignment)


def _build_uniform_trip_min_ranked(mgr, ctx: StrategyContext) -> None:
    # Ranked wave by pick-effort priority, placed in a uniform-random aisle;
    # per-unit fallback (uniform-aisle + min-bin) places stragglers.
    mgr.placement = Placement(
        'ranked_uniform',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_uniform_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta))


def _build_rank_popularity(mgr, ctx: StrategyContext) -> None:
    # Ranked wave ordered by expected_popularity (freq*qty), placed into the aisle with
    # the least Σ popularity (aisle_demand_sum) — disperses demand mass across aisles.
    mgr.placement = Placement(
        'ranked_popularity',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_popularity_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        order_score=_score_expected_popularity)


def _build_rank_labor(mgr, ctx: StrategyContext) -> None:
    # Travel-aware LPT labor balance: each unit goes to the specific empty bin that
    # minimises the busiest aisle's total labor = freq*qty*(pick_time + travel_D).
    # Candidate bins are ordered by travel time within each aisle (nearest first).
    mgr.placement = Placement(
        'ranked_labor',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_labor_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            mgr._aisle_pick_load_sum, mgr._sku_pick_load_product,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        order_score=_score_expected_labor)


def _build_rank_cartlabor(mgr, ctx: StrategyContext) -> None:
    # Cart-swap-aware LPT balance: like rank_labor, but each aisle's balanced load also
    # carries its EXPECTED cart-swap cost (cart_swap_coef*max(0, exp_aisle_vol/cap - 1)), so
    # volume that would overflow a cart disperses across aisles.  Inert for the big store
    # cart (term ~0); bites for the small fulfillment cart.
    mgr.placement = Placement(
        'ranked_cartlabor',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_cartlabor_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            mgr._aisle_pick_load_sum, mgr._sku_pick_load_product,
            mgr._aisle_vol_sum, mgr._sku_vol_product, ctx.expected_batch_skus,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        order_score=_score_expected_labor)


def _build_rank_minlabor(mgr, ctx: StrategyContext) -> None:
    # Minimiser of expected total task labor (consolidation): each unit goes to the
    # (aisle, bin) that minimises its marginal cost = freq*qty*(intercept + M(y)*handle_var
    # + travel_D) − λ*affinity_reward — fusing golden-zone height, effort-to-front, and
    # co-demand compaction.  The OPPOSITE of rank_labor (which LPT-balances aisle load).
    mgr.placement = Placement(
        'ranked_minlabor',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_minlabor_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            mgr._aisle_member_pos,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        order_score=_score_expected_labor)


def _build_rank_maxlabor(mgr, ctx: StrategyContext) -> None:
    # Worst-case sanity control: the exact mirror of rank_minlabor, MAXIMISING marginal labor
    # (high/far bins, scatter co-demanded SKUs).  Should land worst on objective_task_labor.
    mgr.placement = Placement(
        'ranked_maxlabor',
        build_uniform_aisle_trip_min_assignment_fn(ctx.wp),
        open_pool=build_ranked_maxlabor_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            mgr._aisle_member_pos,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        order_score=_score_expected_labor)


def _build_map(mgr, ctx: StrategyContext) -> None:
    # Optimal-map reloading: build the labor-minimizing map (quantity-free per-bin
    # preferred-score basis + each SKU's optimal target), then place by SCORE MATCHING
    # — each unit goes to the free bin whose pref is closest to its SKU's target, so
    # reorders reload toward the optimum instead of grabbing whatever bin is free.
    mgr.build_optimal_map(ctx.orders, ctx.freq_by_sku, ctx.qty_by_sku, ctx.wp)
    # place_one stays the spill fallback; the wave amortizes the closest-pref scan
    # (O(B log B) sort + O(log B)/unit) over each reorder group.  See build_optmap_wave_fn.
    mgr.placement = Placement('optmap', build_optmap_fn(mgr),
                              open_pool=build_optmap_pool_fn(mgr))


def _build_map_rank(mgr, ctx: StrategyContext) -> None:
    # Upgrade-capped optimal-map: same target as `map`, but a SKU never reloads into a bin
    # more prime than its optimal rank — prime spots are saved for higher-ranked SKUs that
    # future orders bring (rank-relative, non-greedy).  See build_optmap_fn(capped=True).
    mgr.build_optimal_map(ctx.orders, ctx.freq_by_sku, ctx.qty_by_sku, ctx.wp)
    mgr.placement = Placement('optmap_rank', build_optmap_fn(mgr, capped=True),
                              open_pool=build_optmap_pool_fn(mgr, capped=True))


def _build_cluster_map(mgr, ctx: StrategyContext) -> None:
    # Mix map + clusters: build the optimal map (per-bin pref + per-SKU target), then place
    # cohesion-first into the aisle holding co-demanded partners, anchoring each unit at its
    # favored map location and compacting it toward the partners' column centroid.  Uncapped
    # score-match (mirrors `map`); may upgrade into prime bins.
    mgr.build_optimal_map(ctx.orders, ctx.freq_by_sku, ctx.qty_by_sku, ctx.wp)
    mgr.placement = build_cluster_map_placement(
        mgr, ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum, mgr._aisle_member_pos,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta, capped=False)


def _build_cluster_map_rank(mgr, ctx: StrategyContext) -> None:
    # Upgrade-capped cluster_map: same cohesion + compaction, but a unit never settles in a bin
    # more prime than its map target — reserving prime spots for higher-ranked SKUs/clusters
    # future orders bring (mirrors `map_rank`).
    mgr.build_optimal_map(ctx.orders, ctx.freq_by_sku, ctx.qty_by_sku, ctx.wp)
    mgr.placement = build_cluster_map_placement(
        mgr, ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum, mgr._aisle_member_pos,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta, capped=True)


def _build_trip_min(mgr, ctx: StrategyContext) -> None:
    mgr.placement = Placement(
        'ranked_min',
        build_trip_minimizing_assignment_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        open_pool=build_ranked_minimizing_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta))


def _build_trip_max(mgr, ctx: StrategyContext) -> None:
    mgr.placement = Placement(
        'ranked_max',
        build_trip_maximizing_assignment_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta),
        open_pool=build_ranked_maximizing_pool_fn(
            ctx.affinity, ctx.wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta))


def _build_max_cluster(mgr, ctx: StrategyContext) -> None:
    # Cohesion placement (per-unit, no ranked wave): each SKU goes to the aisle where
    # its demand-weighted lift to existing members is HIGHEST (co-locate partners).
    # Reads mgr._aisle_index when the worker armed it (init_travel_costs ran).
    mgr.placement = Placement('cohesion_max', build_cluster_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None)))


def _build_min_cluster(mgr, ctx: StrategyContext) -> None:
    # Anti-affinity control: each SKU goes to the aisle where its cohesion is LOWEST.
    mgr.placement = Placement('cohesion_min', build_cluster_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None)))


def _build_compaction(mgr, ctx: StrategyContext) -> None:
    # Co-demand compaction (ranked): cluster co-demanded SKUs into nearby columns so the
    # within-aisle sweep path is short (min ΣW).  Reads/maintains mgr._aisle_member_pos.
    mgr.placement = build_co_demand_placement(
        True, ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum, mgr._aisle_member_pos,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_expansion(mgr, ctx: StrategyContext) -> None:
    # Counter control: scatter co-demanded SKUs into far columns (max sweep path) — the
    # upper bound that brackets how much the co-demand placement lever is worth.
    mgr.placement = build_co_demand_placement(
        False, ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum, mgr._aisle_member_pos,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


# ── stock hooks ─────────────────────────────────────────────────────────────

def _stock_optimal(mgr, ctx: StrategyContext, inventory) -> None:
    """Place initial stock at the pure-global-W optimal layout (hottest SKUs in the
    lowest-W bins).  Stores nothing on mgr; reorders use the normal placement."""
    mgr.place_optimal(inventory.orders, ctx.freq_by_sku,
                      ctx.wp.x_speed, ctx.wp.y_speed)


# ── colour helper: spread N distinct hues ──────────────────────────────────────

def _hsv_hex(i: int, n: int) -> str:
    """A distinct-ish hex colour for index i of n (HSV hue sweep)."""
    r, g, b = colorsys.hsv_to_rgb((i / max(1, n)) % 1.0, 0.58, 0.85)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


# ── combinatorial strategy grid ─────────────────────────────────────────────────
# The pipeline runs this grid against each regression config:
#     initial assignment × restock (reorder) rule × re-slot (capacity reloader).
# STRATEGIES[0] (uni|FIFO|noRSL) is the plot baseline.  needs_affinity/needs_demand
# come from the restock rule (the optimal stock hook only needs freq_by_sku, which
# the worker builds unconditionally).

# initial assignment: (key, label, stock_mode)
#   'uniform' ⇒ random enqueue_all;  'policy' ⇒ stock the full inventory through the
#   strategy's OWN assignment fn (so opt_X starts at X's ideal layout).
_INITIALS = [
    ('uni', 'Uni', 'uniform'),
    ('opt', 'Opt', 'policy'),              # stock via the strategy's own assignment fn
]

# restock (reorder) rule: one `PlacementPolicy` record each (Warehouse/placement/policy.py).
#
# This was a positional 6-tuple until ticket 03, with the same facts restated in three other
# places: `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, `_gain_bundle_for`'s per-family
# `aisle_state=` dicts, and a `FAMILIES` table in `test_gain_bundle_labor_families.py`. All
# three are now DERIVED from these records.
#
# `uses_aisle_index=True` only for the per-unit _stock cluster policies: the worker arms
# init_travel_costs() before build() and the cluster fn reads mgr._aisle_index. FIFO (random,
# RNG-order sensitive) and the ranked drains (tmin/tmax/rank, which use the already-optimised
# _stock_ranked path) stay on the candidates scan.
#
# `ledger_terms` names the AisleLedger books this family's placement COMMITS to. The gain
# evaluator copies exactly those before every virtual placement, so a missing term is not a
# refusal -- it is a virtual placement advancing the real warehouse. Declared rather than read
# off a pool because the evaluator needs it BEFORE it builds one, and checked against the
# built pool's own ledger in `Tests/unit/test_placement_policy.py`.
#
# `gain` says how the inbound evaluator prices the arm faithfully, and None means it REFUSES
# rather than pricing a fiction under the arm's name.

#: The three books every ranked pool's `take` commits to.
_RANKED3 = ('sku_sets', 'idx_sets', 'demand_sum')
#: ...plus the placement POSITIONS the partner centroid sums in placement order.
_RANKED3_POS = _RANKED3 + ('member_pos',)

_RESTOCKS: list[PlacementPolicy] = [
    PlacementPolicy('fifo', 'FIFO', _build_uniform,                      # uniform random, FIFO drain
                    gain='uniform'),
    # ── full ablation (8 arms): placement functions + worst-case bracket ──
    PlacementPolicy('rank_random', 'Rank_random', _build_uniform_trip_min_ranked,  # random aisle
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3,
                    gain='pool', gain_expect_heads=True),
    PlacementPolicy('rank_popularity', 'Rank_popularity', _build_rank_popularity,  # min Σ freq*qty
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3,
                    gain='pool'),
    PlacementPolicy('rank_labor', 'Rank_labor', _build_rank_labor,       # travel-aware LPT
                    needs_affinity=True, needs_demand=True, gain='pool',
                    ledger_terms=_RANKED3 + ('pick_load_sum',)),
    PlacementPolicy('rank_cartlabor', 'Rank_cartlabor', _build_rank_cartlabor,     # + cart-swap
                    needs_affinity=True, needs_demand=True, gain='pool',
                    ledger_terms=_RANKED3 + ('pick_load_sum', 'vol_sum')),
    PlacementPolicy('rank_minlabor', 'Rank_minlabor', _build_rank_minlabor,        # MINIMISER
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS,
                    gain='pool'),
    PlacementPolicy('rank_maxlabor', 'Rank_maxlabor', _build_rank_maxlabor,        # MAXIMISER
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS),
    PlacementPolicy('map', 'Map', _build_map),                           # score-matched reloading
    PlacementPolicy('map_rank', 'Map_rank', _build_map_rank),            # upgrade-capped
    PlacementPolicy('cluster_map', 'CluMap', _build_cluster_map,         # map + cohesion + compaction
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS),
    PlacementPolicy('cluster_map_rank', 'CluMapRk', _build_cluster_map_rank,       # upgrade-capped
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS),
    PlacementPolicy('tmin', 'TripMin', _build_trip_min,
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3,
                    gain='merge', gain_minimize=True),
    PlacementPolicy('tmax', 'TripMax', _build_trip_max,
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3,
                    gain='merge', gain_minimize=False),
    PlacementPolicy('cmax', 'MaxClu', _build_max_cluster,
                    needs_affinity=True, needs_demand=True, uses_aisle_index=True,
                    ledger_terms=_RANKED3),
    PlacementPolicy('cmin', 'MinClu', _build_min_cluster,
                    needs_affinity=True, needs_demand=True, uses_aisle_index=True,
                    ledger_terms=_RANKED3),
    PlacementPolicy('comp', 'Compact', _build_compaction,                # co-demand min-span
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS),
    PlacementPolicy('expn', 'Expand', _build_expansion,                  # co-demand max-span
                    needs_affinity=True, needs_demand=True, ledger_terms=_RANKED3_POS),
]

#: restock key -> its policy record.  The one lookup every derived list below goes through.
POLICY_BY_KEY: dict[str, PlacementPolicy] = {p.key: p for p in _RESTOCKS}

#: The families the inbound gain evaluator can price FAITHFULLY -- DERIVED from which
#: records declare a gain adapter, not typed beside them.
#:
#: It lived in `Inbound/gain.py` as a hand-written tuple until ticket 03, which is the wrong
#: home twice over: `Inbound/` never read it (every consumer is in `Optimization/`), and it
#: restated a fact the driver's own bundle branches already decided. Moving it here makes
#: "this family has a faithful bundle" and "this family declares a gain adapter" the same
#: statement instead of two that can disagree -- and they HAD disagreed silently in the
#: direction that matters: a rule missing here is a campaign spec refusing a run it could
#: have priced, and a rule wrongly here is a fiction published under that arm's name.
FAITHFUL_GAIN_FAMILIES: tuple[str, ...] = tuple(p.key for p in _RESTOCKS if p.gain)

# re-slot (capacity reloader): (key, label, reslot_frac, reloader variant)
_RESLOT_FRAC = 0.005
_RESLOTS = [
    ('norsl', 'noRSL',   0.0,          'rebalance'),         # no re-slot  (sweep: noReslot only)
    #('rmin',  'RSLmin',  _RESLOT_FRAC, 'demote_unpopular'),  # re-slot least-popular (min performers)
    #('rmax',  'RSLmax',  _RESLOT_FRAC, 'promote_popular'),   # re-slot most-popular (max performers)
    #('rboth', 'RSLboth', _RESLOT_FRAC, 'rebalance'),         # both ends
]

#: EVERY restock-rule key, in grid order.  The universe a campaign spec's arm set or rule-pair
#: list is checked against (`whatif_config.validate_spec`), derived from the grid rather than
#: typed beside it: a hand-kept copy drifts silently the moment a rule is added or renamed, and
#: a mistyped rule in a ten-cell campaign spec is a run that sweeps a suite nobody chose.
RESTOCK_KEYS: tuple[str, ...] = tuple(_p.key for _p in _RESTOCKS)

#: Each INITIAL's position in the grid, keyed by its `stock_mode` — the outer loop of the
#: strategy grid, and therefore the outer key `strategies_for` orders by.  Keyed on stock_mode
#: because that is the only handle a `Strategy` carries back to its initial (site-dock 06
#: section 0: the uni/opt axis partitions cleanly on `stock_mode`, which is why no key-prefix
#: parse is needed).  That handle is only sound while the modes are DISTINCT, so it is asserted
#: rather than assumed — two initials sharing a mode would silently collapse the ordering.
_INITIAL_RANK: dict[str, int] = {_sm: _i for _i, (_ik, _il, _sm) in enumerate(_INITIALS)}
assert len(_INITIAL_RANK) == len(_INITIALS), (
    '_INITIALS must have one distinct stock_mode each; strategies_for orders the arm list by '
    'stock_mode and a shared mode would merge two initials into one rank')

_N_STRATEGIES = len(_INITIALS) * len(_RESTOCKS) * len(_RESLOTS)

STRATEGIES: list[Strategy] = []
for _ik, _il, _stock_mode in _INITIALS:
    for _pol in _RESTOCKS:
        for _sk, _sl, _frac, _rld in _RESLOTS:
            _key = f'{_ik}_{_pol.key}_{_sk}'
            STRATEGIES.append(Strategy(
                key=_key, label=f'{_il}|{_pol.label}|{_sl}',
                color=_hsv_hex(len(STRATEGIES), _N_STRATEGIES), run_type=_key,
                needs_affinity=_pol.needs_affinity, needs_demand=_pol.needs_demand,
                build=_pol.build,
                stock_mode=_stock_mode, reslot_frac=_frac, reloader=_rld,
                uses_aisle_index=_pol.uses_aisle_index, restock=_pol.key,
            ))

STRATEGY_BY_KEY: dict[str, Strategy] = {s.key: s for s in STRATEGIES}


def strategies_for(restocks) -> list[Strategy]:
    """Subset of STRATEGIES whose restock rule is in `restocks` (None ⇒ all), IN THE ORDER
    `restocks` states it — initial (uni/opt) outer, the caller's rule order inner.

    Lets one channel run only a subset of restock rules (e.g. store: fifo + rank_labor)
    while another runs the full suite, without perturbing the global grid used elsewhere.

    THE ORDER IS LOAD-BEARING UNDER COUPLING, and it was not before.  `_prepare_site_run`
    (`Optimization/simdriver/workunits.py`) builds the site's arm PAIRS by zipping the two
    channels' lists as this function returns them, so position i of the store list is run
    against position i of the fulfillment list.  A filter that re-imposed the GRID's order —
    which the old `[s for s in STRATEGIES if s.restock in restocks]` did — would throw away
    the rank order `run_restock_selection` spent a whole phase-1 run computing and pair rank 1
    against whichever of the other channel's rules happens to sit earliest in `_RESTOCKS`.
    Same shape as site-dock 06 section 0's `sorted(set(arms))` finding, one level down: a sort
    that destroys the only thing the diagonal reads, with nothing raising.

    BYTE-IDENTICAL when the caller's order IS the grid order, which every committed
    `CHANNEL_RESTOCKS` value and every registered spec's arm set is (asserted in
    Tests/unit/test_funnel_spec_pairs.py) — so this re-ordering moves no run that exists today.

    The inner order comes from ITERATION, so an unordered container has no defined arm order
    and is refused: a `set` would pair the two channels by whatever its hash order happened to
    be, which is a different campaign on a different Python build.
    """
    if restocks is None:
        return list(STRATEGIES)
    if isinstance(restocks, (set, frozenset)):
        raise TypeError(
            'strategies_for needs an ORDERED restock list: under coupling the arm order IS '
            'the rank pairing (see _prepare_site_run), and a set has no order. Pass a tuple '
            'or list in rank order, or None for the full suite.')
    # First occurrence wins, so a repeated rule cannot open a second rank slot.
    rank: dict[str, int] = {}
    for _r in restocks:
        rank.setdefault(_r, len(rank))
    # `sorted` is stable and STRATEGIES is already in (initial, restock, reslot) grid order, so
    # arms sharing an (initial, rule) — the re-slot axis — keep their committed relative order.
    return sorted((s for s in STRATEGIES if s.restock in rank),
                  key=lambda s: (_INITIAL_RANK[s.stock_mode], rank[s.restock]))


# ── per-channel strategy selection ──────────────────────────────────────────────
# Which restock (assignment-function) subset each channel sweeps.  Lives HERE — the
# strategies setup file that owns the `restock` keys — so the runner has no special-case
# strategy constants.  None ⇒ the full assignment-function suite.
#   store       : full sweep — every assignment function is compared on the big-cart channel.
#   fulfillment : full sweep — every assignment function is compared on the small-cart channel.
# To re-restrict a channel to a curated subset, set its value to a tuple of restock keys,
# e.g. store: ('fifo', 'rank_labor', 'rank_cartlabor').
#
# THE TUPLE IS ORDERED, and under a COUPLED run the order is the rank pairing: `strategies_for`
# hands each channel its arms in this order and `_prepare_site_run` zips the two lists.  A
# campaign therefore never authors these two tuples by hand — `whatif_config.channel_restocks_for`
# DERIVES them from the spec's rule-pair list (site-dock 06 section 2), so the per-channel arm
# sets cannot drift from the pairing they are supposed to express.
CHANNEL_RESTOCKS: dict[str, tuple[str, ...] | None] = {
    'store'      : None,   # full assignment-function suite
    'fulfillment': None,   # full assignment-function suite
}


def restocks_for(channel: str) -> tuple[str, ...] | None:
    """The restock-rule subset a channel sweeps (None ⇒ full suite).  Unknown channel ⇒
    None (full suite), so a new channel runs everything until curated here."""
    return CHANNEL_RESTOCKS.get(channel)
