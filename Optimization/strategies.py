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

from Assignment_Functions import (
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
    build_ranked_minimizing_assignment_fn,
    build_ranked_maximizing_assignment_fn,
    build_uniform_aisle_trip_min_assignment_fn,
    build_ranked_uniform_assignment_fn,
    build_cluster_maximizing_assignment_fn,
    build_cluster_minimizing_assignment_fn,
)


@dataclass
class StrategyContext:
    """Everything a strategy's build() may need, assembled by the worker."""
    affinity     : Any
    wp           : Any
    freq_by_idx  : dict
    freq_by_sku  : dict
    qty_by_sku   : dict
    beta         : float = 1.0


@dataclass
class Strategy:
    key            : str       # db/file id + run-id dict key (e.g. 'uniform_trip_min')
    label          : str       # plot label (e.g. 'Uniform+Min')
    color          : str       # plot colour hex
    run_type       : str       # create_run run_type string
    needs_affinity : bool      # rebuild aisle sku counts + lift sums before build()
    needs_demand   : bool      # init_demand_state + freq/qty maps before build()
    build          : Callable  # (mgr, ctx: StrategyContext) -> None
    stock          : Callable | None = None  # (mgr, ctx, inventory) -> None; None = uniform enqueue_all
    reslot_frac    : float = 0.0             # >0 enables the capacity reloader (budget = % of XL aisle)
    reloader       : str = 'rebalance'       # named reloader variant: promote_popular | demote_unpopular | rebalance
    uses_aisle_index : bool = False          # per-unit _drain strategy that consumes mgr._aisle_index;
                                             # worker arms init_travel_costs() before build() (cluster only —
                                             # ranked/FIFO drains do not use the per-aisle index fast path)


# ── build helpers: set mgr.assignment_fn / mgr.ranked_assignment_fn ──────────────

def _build_uniform(mgr, ctx: StrategyContext) -> None:
    # Keep the manager's default _uniform_assignment; no ranked drain (FIFO).
    mgr.ranked_assignment_fn = None


def _build_uniform_trip_min(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_uniform_aisle_trip_min_assignment_fn(ctx.wp)
    mgr.ranked_assignment_fn = None          # FIFO — no ranking


def _build_uniform_trip_min_ranked(mgr, ctx: StrategyContext) -> None:
    # Per-unit fallback: uniform-aisle + min-bin.  Reorder waves: ranked (same
    # pick-effort priority as trip-min) but placed in a uniform-random aisle.
    mgr.assignment_fn = build_uniform_aisle_trip_min_assignment_fn(ctx.wp)
    mgr.ranked_assignment_fn = build_ranked_uniform_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_trip_min(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_trip_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.ranked_assignment_fn = build_ranked_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_trip_max(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_trip_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.ranked_assignment_fn = build_ranked_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_max_cluster(mgr, ctx: StrategyContext) -> None:
    # Affinity-cohesion placement: each SKU goes to the aisle where its
    # demand-weighted lift to existing members is HIGHEST (co-locate partners).
    # Per-unit _drain path: pass the pre-sorted index when the worker armed it
    # (init_travel_costs ran) so assign reads it in O(N_aisles); None otherwise.
    mgr.assignment_fn = build_cluster_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None))
    mgr.ranked_assignment_fn = None      # FIFO; cohesion accumulates as units place


def _build_min_cluster(mgr, ctx: StrategyContext) -> None:
    # Anti-affinity control: each SKU goes to the aisle where its cohesion is LOWEST.
    mgr.assignment_fn = build_cluster_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta,
        aisle_index=(mgr._aisle_index if mgr._travel_costs_ready else None))
    mgr.ranked_assignment_fn = None


# ── stock hooks ─────────────────────────────────────────────────────────────

def _stock_optimal(mgr, ctx: StrategyContext, inventory) -> None:
    """Place initial stock at the pure-global-W optimal layout (hottest SKUs in the
    lowest-W bins).  Stores nothing on mgr; reorders use the normal placement."""
    mgr.place_optimal(inventory.cartons, ctx.freq_by_sku,
                      ctx.wp.x_speed, ctx.wp.y_speed)


# ── colour helper: spread N distinct hues ──────────────────────────────────────

def _hsv_hex(i: int, n: int) -> str:
    """A distinct-ish hex colour for index i of n (HSV hue sweep)."""
    r, g, b = colorsys.hsv_to_rgb((i / max(1, n)) % 1.0, 0.58, 0.85)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


# ── combinatorial strategy grid ─────────────────────────────────────────────────
# The pipeline runs this grid against each regression config:
#     initial assignment × restock (reorder) rule × re-slot (capacity reloader)
#     = 2 × 6 × 4 = 48 strategies.  STRATEGIES[0] (uni|FIFO|noRSL) is the plot
# baseline.  needs_affinity/needs_demand come from the restock rule (the optimal
# stock hook only needs freq_by_sku, which the worker builds unconditionally).

# initial assignment: (key, label, stock hook)   — None ⇒ uniform enqueue_all
_INITIALS = [
    ('uni', 'Uni', None),
    ('opt', 'Opt', _stock_optimal),        # optimal pure-global-D initial layout
]

# restock (reorder) rule: (key, label, build fn, needs_affinity, needs_demand, uses_aisle_index)
# uses_aisle_index=True only for the per-unit _drain cluster policies: the worker
# arms init_travel_costs() before build() and the cluster fn reads mgr._aisle_index.
# FIFO (random, RNG-order sensitive) and the ranked drains (tmin/tmax/rank, which use
# the already-optimised _drain_ranked path) stay on the candidates scan → False.
_RESTOCKS = [
    ('fifo', 'FIFO',    _build_uniform,                 False, False, False),  # uniform random, FIFO drain
    ('rank', 'Rank',    _build_uniform_trip_min_ranked, True,  True,  False),  # uniform aisle + min-D bin, ranked
    ('tmin', 'TripMin', _build_trip_min,                True,  True,  False),
    ('tmax', 'TripMax', _build_trip_max,                True,  True,  False),
    ('cmax', 'MaxClu',  _build_max_cluster,             True,  True,  True),
    ('cmin', 'MinClu',  _build_min_cluster,             True,  True,  True),
]

# re-slot (capacity reloader): (key, label, reslot_frac, reloader variant)
_RESLOT_FRAC = 0.005
_RESLOTS = [
    ('norsl', 'noRSL',   0.0,          'rebalance'),         # no re-slot
    ('rmin',  'RSLmin',  _RESLOT_FRAC, 'demote_unpopular'),  # re-slot least-popular (min performers)
    #('rmax',  'RSLmax',  _RESLOT_FRAC, 'promote_popular'),   # re-slot most-popular (max performers)
    #('rboth', 'RSLboth', _RESLOT_FRAC, 'rebalance'),         # both ends
]

STRATEGIES: list[Strategy] = []
for _ik, _il, _stock in _INITIALS:
    for _rk, _rl, _bld, _na, _nd, _uix in _RESTOCKS:
        for _sk, _sl, _frac, _rld in _RESLOTS:
            _key = f'{_ik}_{_rk}_{_sk}'
            STRATEGIES.append(Strategy(
                key=_key, label=f'{_il}|{_rl}|{_sl}',
                color=_hsv_hex(len(STRATEGIES), 48), run_type=_key,
                needs_affinity=_na, needs_demand=_nd, build=_bld,
                stock=_stock, reslot_frac=_frac, reloader=_rld,
                uses_aisle_index=_uix,
            ))

STRATEGY_BY_KEY: dict[str, Strategy] = {s.key: s for s in STRATEGIES}
