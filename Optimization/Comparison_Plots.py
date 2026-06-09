"""
Comparison_Plots.py — graphing and analysis for the strategy comparison.

Called via `run_config_analysis(sim_result, shared, log)`.  `sim_result` carries a
self-describing `strategies` list (each {key, label, color, db_path, run_id}) read
from sim_meta.json, so the plots are N-wide and driven entirely by that list —
no hardcoded A/B/C.  strategies[0] is the baseline for delta comparisons.
"""

import matplotlib
matplotlib.use('Agg')  # must come before pyplot import

import logging
import math
import os

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

from Picking_Data        import load_batch_stats, load_task_stats
from Simulation_Analytics import flag_batch_outliers, flag_task_outliers

_TRAVEL_COL = '#a9a9a9'

# ── rolling-average window ─────────────────────────────────────────────────────
_WIN = 50   # batches


# ── DataFrame helpers ──────────────────────────────────────────────────────────

def _bdf(stats):
    return pd.DataFrame([{
        'batch_id'              : s.batch_id,
        'duration'              : s.duration,
        'num_tasks'             : s.num_tasks,
        'total_items'           : s.total_items,
        'completion_rate'       : s.total_items / s.duration if s.duration > 0 else 0.0,
        'avg_concurrent_pickers': s.avg_concurrent_pickers,
        'picking_pct'           : s.picking_pct   * 100,
        'traveling_pct'         : s.traveling_pct * 100,
        'sigma_fd'              : s.sigma_fd,
        'reload_moves'          : s.reload_moves,
        'reorder_placements'    : s.reorder_placements,
    } for s in stats])


def _tdf(stats, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W'        : s.W,
        'lift_sum'   : s.lift_sum,
        'num_bins'   : s.num_bins_visited,
        'total_items': s.total_items,
        'unit_type'  : aisle_unittype_map.get(s.aisle_id),
        'handling'   : aisle_handling_map.get(s.aisle_id),
    } for s in stats])


def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


# ── plot helpers ───────────────────────────────────────────────────────────────

def _kde_plot(ax, data, color, bins):
    ax.hist(data, bins=bins, color=color, alpha=0.65, edgecolor='white')
    if len(data) > 1 and data.max() > data.min():
        kde = gaussian_kde(data, bw_method='silverman')
        xs  = np.linspace(data.min(), data.max(), 400)
        ax.plot(xs, kde(xs) * len(data) * (data.max() - data.min()) / bins, color=color, lw=2)
    ax.axvline(data.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {data.mean():.1f}')
    ax.axvline(np.median(data), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(data):.1f}')


def _save_close(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _row(n, figw=4.8, h=4.5):
    """A row of n subplots; always returns a flat list of axes."""
    fig, axes = plt.subplots(1, n, figsize=(figw * n, h), squeeze=False)
    return fig, list(axes[0])


def _pct_delta(val, base):
    return (val - base) / abs(base) * 100 if base else 0.0


def _grid(n, panel_w=3.0, panel_h=2.3, max_cols=8):
    """A roughly-square subplot grid for n strategies; returns (fig, n flat axes)."""
    cols = min(max_cols, max(1, math.ceil(math.sqrt(n))))
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(panel_w * cols, panel_h * rows), squeeze=False)
    flat = [ax for row in axes for ax in row]
    for ax in flat[n:]:
        ax.axis('off')
    return fig, flat[:n]


# ── main analysis entry point ──────────────────────────────────────────────────

def run_config_analysis(
    sim_result : dict,
    shared     : dict,
    log        : logging.Logger,
) -> None:
    """Run the analysis + plotting phase for one config across all strategies.

    Must be called sequentially (matplotlib pyplot is not thread-safe).
    """
    name               = sim_result['name']
    run_dir            = sim_result['run_dir']
    strategies         = sim_result['strategies']        # [{key,label,color,db_path,run_id}]
    aisle_unittype_map = shared['aisle_unittype_map']
    aisle_handling_map = shared['aisle_handling_map']
    k_pickers          = shared.get('k_pickers', 25)

    base       = strategies[0]                            # delta baseline
    base_key   = base['key']
    base_label = base['label']

    # ── load per-strategy DataFrames (outliers flagged out) ───────────────────
    df_b: dict[str, pd.DataFrame] = {}
    df_t: dict[str, pd.DataFrame] = {}
    for s in strategies:
        bs = flag_batch_outliers(load_batch_stats(s['db_path'], s['run_id']))
        ts = flag_task_outliers(load_task_stats(s['db_path'], s['run_id']))
        df_b[s['key']] = _bdf([x for x in bs if not x.is_outlier])
        df_t[s['key']] = _tdf([x for x in ts if not x.is_outlier],
                              aisle_unittype_map, aisle_handling_map)

    labels = [s['label'] for s in strategies]

    # ── summary CSVs ──────────────────────────────────────────────────────────
    bcols = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols = ['duration', 'W', 'lift_sum', 'num_bins']
    summ_b = pd.concat([df_b[s['key']][bcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_t = pd.concat([df_t[s['key']][tcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_b.to_csv(os.path.join(run_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(run_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    n = len(strategies)
    inv = sim_result.get('inventory', '') or name
    optimal = float(sim_result.get('optimal_sigma_fd') or 0.0)
    total_bins = float(shared.get('total_bins') or 0)

    def _stitle(s):
        parts = [p for p in (s.get('initial', ''), s.get('assignment', ''),
                             s.get('reslot', '')) if p]
        return '|'.join(parts) if parts else s.get('label', s['key'])

    def _full_title(s):
        bits = [inv, s.get('initial', ''), s.get('assignment', ''), s.get('reslot', '')]
        return '_'.join(b for b in bits if b)

    def _eff_series(df):
        if df.empty:
            return None
        d = df.sort_values('batch_id')
        if optimal > 0:
            y = (optimal / d['sigma_fd'].clip(lower=1e-9) * 100.0).rolling(_WIN, min_periods=1).mean()
        else:
            y = d['sigma_fd'].rolling(_WIN, min_periods=1).mean()
        return d['batch_id'].values, y.values

    def _churn_series(df):
        if df.empty:
            return None
        d = df.sort_values('batch_id')
        moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
        if total_bins > 0:
            moved = moved / total_bins * 100.0
        return d['batch_id'].values, moved.rolling(_WIN, min_periods=1).mean().values

    def _panel_duration(ax, s):
        df = df_b[s['key']]
        if not df.empty:
            d = df.sort_values('batch_id')
            ax.plot(d['batch_id'].values, _roll(df, 'duration', _WIN), color=s['color'], lw=1.2)

    def _panel_eff(ax, s):
        ser = _eff_series(df_b[s['key']])
        if ser is not None:
            ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)
            if optimal > 0:
                ax.axhline(100.0, color='grey', lw=0.7, ls='--')

    def _panel_churn(ax, s):
        ser = _churn_series(df_b[s['key']])
        if ser is not None:
            ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)

    def _panel_taskdur(ax, s):
        data = df_t[s['key']]['duration'].values
        if len(data):
            ax.hist(data, bins=30, color=s['color'], alpha=0.7, edgecolor='white')

    # ── per-metric GRID images: one rectangular grid, one panel per strategy ───
    def _metric_grid(fname, title, panel, ylabel):
        fig, axes = _grid(n)
        fig.suptitle(f'{title}  [{inv} / {name}]', fontsize=13, fontweight='bold')
        for ax, s in zip(axes, strategies):
            panel(ax, s)
            ax.set_title(_stitle(s), fontsize=7)
            ax.tick_params(labelsize=6)
            ax.grid(alpha=0.3)
        if axes:
            axes[0].set_ylabel(ylabel, fontsize=8)
        plt.tight_layout(rect=(0, 0, 1, 0.98))
        _save_close(fig, os.path.join(run_dir, fname))

    _metric_grid('grid_batch_duration.png', 'Batch duration (rolling mean)', _panel_duration, 'sim time')
    _metric_grid('grid_sigma_fd.png',
                 'Layout efficiency: optimal / realised Sigma f*D (%)' if optimal > 0 else 'Sigma f*D (lower better)',
                 _panel_eff, '% of optimal' if optimal > 0 else 'Sigma f*D')
    _metric_grid('grid_churn.png', 'Inventory churn (% of bins moved / batch)', _panel_churn, '% / batch')
    _metric_grid('grid_task_duration.png', 'Task (aisle) duration distribution', _panel_taskdur, 'count')

    # ── per-strategy SCORECARDS: one image per strategy ────────────────────────
    for s in strategies:
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 3.4))
        fig.suptitle(_full_title(s), fontsize=12, fontweight='bold')
        _panel_duration(a1, s); a1.set_title('Batch duration', fontsize=10)
        a1.set_xlabel('batch'); a1.grid(alpha=0.3)
        _panel_eff(a2, s)
        a2.set_title('Sigma f*D ' + ('% of optimal' if optimal > 0 else '(raw)'), fontsize=10)
        a2.set_xlabel('batch'); a2.grid(alpha=0.3)
        _panel_churn(a3, s); a3.set_title('Churn (% bins/batch)', fontsize=10)
        a3.set_xlabel('batch'); a3.grid(alpha=0.3)
        plt.tight_layout(rect=(0, 0, 1, 0.92))
        _save_close(fig, os.path.join(run_dir, f"strat_{s['key']}.png"))

    # ── rectangular SUMMARY: headline metrics across strategies, side by side ──
    ylabels = [_stitle(s) for s in strategies]
    yc      = [s['color'] for s in strategies]
    ypos    = np.arange(n)
    mean_dur = [df_b[s['key']]['duration'].mean()        if not df_b[s['key']].empty else 0.0 for s in strategies]
    mean_thr = [df_b[s['key']]['completion_rate'].mean() if not df_b[s['key']].empty else 0.0 for s in strategies]

    def _mean_eff(s):
        df = df_b[s['key']]
        if df.empty or optimal <= 0:
            return 0.0
        return float((optimal / df['sigma_fd'].clip(lower=1e-9) * 100.0).mean())
    mean_eff = [_mean_eff(s) for s in strategies]

    fig, (b1, b2, b3) = plt.subplots(1, 3, figsize=(16, max(6.0, n * 0.24)), sharey=True)
    fig.suptitle(f'Strategy summary  [{inv} / {name}]  (n={n}, baseline {_stitle(strategies[0])})',
                 fontsize=13, fontweight='bold')
    b1.barh(ypos, mean_dur, color=yc); b1.set_title('Mean batch duration (lower better)', fontsize=10)
    b1.set_yticks(ypos); b1.set_yticklabels(ylabels, fontsize=6); b1.invert_yaxis(); b1.grid(axis='x', alpha=0.3)
    b2.barh(ypos, mean_thr, color=yc); b2.set_title('Mean throughput (higher better)', fontsize=10)
    b2.grid(axis='x', alpha=0.3)
    b3.barh(ypos, mean_eff, color=yc); b3.set_title('Mean Sigma f*D efficiency % (higher better)', fontsize=10)
    b3.grid(axis='x', alpha=0.3)
    plt.tight_layout(rect=(0, 0, 1, 0.98))
    _save_close(fig, os.path.join(run_dir, 'summary.png'))

    log.info(f'  Saved {n} strategies (grids + scorecards + summary) -> {run_dir}')
