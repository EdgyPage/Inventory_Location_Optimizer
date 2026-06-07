"""strategies.py — data-driven registry of placement (assignment) strategies.

Each Strategy names an assignment policy and knows how to wire it onto an
Inventory_Manager for the reorder phase.  The whole comparison pipeline
(run_simulation orchestration, strategy_runner worker, run_analysis/Comparison_Plots
labels via sim_meta) iterates this list, so adding a strategy is one entry here.

All strategies share the same uniform INITIAL stocking (done in the worker before
build() is called); they differ only in how reorder waves are placed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

from Inventory_Management import (
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
    build_batch_minimizing_assignment_fn,
    build_batch_maximizing_assignment_fn,
    build_uniform_aisle_trip_min_assignment_fn,
    build_batch_uniform_ranked_assignment_fn,
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


# ── the registry (order = ablation ladder; STRATEGIES[0] is the plot baseline) ──

STRATEGIES: list[Strategy] = [
    Strategy('uniform',                 'Uniform',          '#5b9bd5',
             'uniform_assignment',         False, False, _build_uniform),
    Strategy('uniform_trip_min',        'Uniform+Min',      '#9966cc',
             'uniform_aisle_trip_min',     False, False, _build_uniform_trip_min),
    Strategy('uniform_trip_min_ranked', 'Uniform+Min+Rank', '#f4a030',
             'uniform_aisle_ranked',       True,  True,  _build_uniform_trip_min_ranked),
    Strategy('trip_min',                'Trip-Min',         '#70ad47',
             'trip_minimizing_assignment', True,  True,  _build_trip_min),
    Strategy('trip_max',                'Trip-Max',         '#c0504d',
             'trip_maximizing_assignment', True,  True,  _build_trip_max),
]

STRATEGY_BY_KEY: dict[str, Strategy] = {s.key: s for s in STRATEGIES}
