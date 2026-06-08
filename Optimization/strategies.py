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

import random
from dataclasses import dataclass
from typing import Any, Callable

from Assignment_Functions import (
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
    build_batch_minimizing_assignment_fn,
    build_batch_maximizing_assignment_fn,
    build_uniform_aisle_trip_min_assignment_fn,
    build_batch_uniform_ranked_assignment_fn,
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


# ── build helpers: set mgr.assignment_fn / mgr.batch_assignment_fn ──────────────

def _build_uniform(mgr, ctx: StrategyContext) -> None:
    # Keep the manager's default _uniform_assignment; no batch (FIFO drain).
    mgr.batch_assignment_fn = None


def _build_uniform_trip_min(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_uniform_aisle_trip_min_assignment_fn(ctx.wp)
    mgr.batch_assignment_fn = None          # FIFO — no ranking


def _build_uniform_trip_min_ranked(mgr, ctx: StrategyContext) -> None:
    # Per-unit fallback: uniform-aisle + min-bin.  Reorder waves: ranked (same
    # pick-effort priority as trip-min) but placed in a uniform-random aisle.
    mgr.assignment_fn = build_uniform_aisle_trip_min_assignment_fn(ctx.wp)
    mgr.batch_assignment_fn = build_batch_uniform_ranked_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_trip_min(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_trip_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.batch_assignment_fn = build_batch_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_trip_max(mgr, ctx: StrategyContext) -> None:
    mgr.assignment_fn = build_trip_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.batch_assignment_fn = build_batch_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)


def _build_max_cluster(mgr, ctx: StrategyContext) -> None:
    # Affinity-cohesion placement: each SKU goes to the aisle where its
    # demand-weighted lift to existing members is HIGHEST (co-locate partners).
    mgr.assignment_fn = build_cluster_maximizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.batch_assignment_fn = None      # FIFO; cohesion accumulates as units place


def _build_min_cluster(mgr, ctx: StrategyContext) -> None:
    # Anti-affinity control: each SKU goes to the aisle where its cohesion is LOWEST.
    mgr.assignment_fn = build_cluster_minimizing_assignment_fn(
        ctx.affinity, ctx.wp,
        mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        ctx.freq_by_idx, ctx.freq_by_sku, ctx.qty_by_sku, beta=ctx.beta)
    mgr.batch_assignment_fn = None


# ── stock hooks ─────────────────────────────────────────────────────────────

def _stock_optimal(mgr, ctx: StrategyContext, inventory) -> None:
    """Place initial stock at the pure-global-W optimal layout (hottest SKUs in the
    lowest-W bins).  Stores nothing on mgr; reorders use the normal placement."""
    mgr.place_optimal(inventory.cartons, ctx.freq_by_sku,
                      ctx.wp.x_speed, ctx.wp.y_speed)


# ── the registry (STRATEGIES[0] is the plot baseline) ───────────────────────────
# Re-slot "catch-up" experiment: all warehouses share the same dynamics (uniform
# reorder + same re-slot budget) except the initial layout — optimal_reslot starts
# perfect and uses Uniform+Min+Rank reorders to hold it; uniform_reslot tries to
# catch up from a uniform start; uniform is the no-re-slot baseline.

STRATEGIES: list[Strategy] = [
    # ── already run in the last comparison set (commented out so an additive
    # --resume run only simulates the new control below; _finalize_config_run
    # merges this new strategy into the existing sim_meta.json). ──────────────
    # Strategy('uniform',        'Uniform',        '#5b9bd5',
    #          'uniform_assignment',         False, False, _build_uniform),
    # Strategy('uniform_reslot', 'Uniform+Reslot', '#f4a030',
    #          'uniform_reslot',             False, False, _build_uniform,
    #          reslot_frac=0.005),
    # Strategy('optimal_reslot', 'Optimal+Reslot', '#70ad47',
    #          'optimal_reslot',             True,  True,  _build_uniform_trip_min_ranked,
    #          stock=_stock_optimal, reslot_frac=0.005),
    # Strategy('trip_min_reslot', 'Trip-Min+Reslot', '#9966cc',
    #          'trip_minimizing_reslot',     True,  True,  _build_trip_min,
    #          reslot_frac=0.005),
    # Strategy('trip_max_reslot', 'Trip-Max+Reslot', '#c0504d',
    #          'trip_maximizing_reslot',     True,  True,  _build_trip_max,
    #          reslot_frac=0.005),

    # ── NEW control: uniform initial stock + Uniform+Min+Rank reorder (uniform-
    # random aisle + min-W bin, ranked queue) + re-slot.  A no-lift baseline to
    # compare the Trip-Min/Max (affinity-aware aisle selection) against. ───────
    Strategy('uniform_rank_reslot', 'Uniform+Rank+Reslot', '#4bacc6',
             'uniform_aisle_ranked_reslot', True, True, _build_uniform_trip_min_ranked,
             reslot_frac=0.005),

    # ── affinity-scoring assignment set: place by co-occurrence cohesion.
    # max_cluster co-locates strong-lift SKUs (fewer aisle visits on correlated
    # batches); min_cluster scatters them (anti-affinity control).  Affinity picks
    # the aisle, popularity (low-W) the bay.  Reorder rule only (no re-slot — the
    # popularity re-slot would fight clustering; a cohesion re-slot is a follow-up).
    Strategy('max_cluster', 'Max-Cluster', '#e377c2',
             'cluster_maximizing', True, True, _build_max_cluster),
    Strategy('min_cluster', 'Min-Cluster', '#8c564b',
             'cluster_minimizing', True, True, _build_min_cluster),
]

STRATEGY_BY_KEY: dict[str, Strategy] = {s.key: s for s in STRATEGIES}
