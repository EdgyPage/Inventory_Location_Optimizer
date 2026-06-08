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
        'sigma_fw'              : s.sigma_fw,
        'reload_moves'          : s.reload_moves,
        'reorder_placements'    : s.reorder_placements,
    } for s in stats])


def _tdf(stats, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W_a'        : s.W_a,
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
    tcols = ['duration', 'W_a', 'lift_sum', 'num_bins']
    summ_b = pd.concat([df_b[s['key']][bcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_t = pd.concat([df_t[s['key']][tcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_b.to_csv(os.path.join(run_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(run_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    n = len(strategies)

    # ── plot 1: batch duration distributions ──────────────────────────────────
    fig, axes = _row(n)
    fig.suptitle(f'Batch Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, s in zip(axes, strategies):
        data = df_b[s['key']]['duration'].values
        if len(data):
            _kde_plot(ax, data, s['color'], bins=40)
        ax.set_xlabel('Batch duration (sim time)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f"{s['label']}  (n={len(data):,})", fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot1_batch_duration.png'))

    # ── plot 2: task duration distributions ────────────────────────────────────
    fig, axes = _row(n)
    fig.suptitle(f'Task (Aisle) Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, s in zip(axes, strategies):
        data = df_t[s['key']]['duration'].values
        if len(data):
            _kde_plot(ax, data, s['color'], bins=50)
        ax.set_xlabel('Task duration (sim time)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f"{s['label']}  (n={len(data):,})", fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot2_task_duration.png'))

    # ── plot 3: throughput rate + batch duration over batches ──────────────────
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))
    fig.suptitle(f'Batch Completion  (rolling {_WIN}-batch mean)  [{name}]',
                 fontsize=13, fontweight='bold')
    for s in strategies:
        df = df_b[s['key']]
        if df.empty:
            continue
        x = df.sort_values('batch_id')['batch_id'].values
        ax1.plot(x, _roll(df, 'completion_rate', _WIN), color=s['color'], lw=2, label=s['label'])
    ax1.set_ylabel('Items / time unit', fontsize=10);  ax1.set_title('Throughput rate', fontsize=10)
    ax1.legend(fontsize=9);  ax1.grid(alpha=0.3)
    for s in strategies:
        df = df_b[s['key']]
        if df.empty:
            continue
        x = df.sort_values('batch_id')['batch_id'].values
        ax2.scatter(x, df.sort_values('batch_id')['duration'].values,
                    color=s['color'], alpha=0.20, s=8, zorder=2)
        ax2.plot(x, _roll(df, 'duration', _WIN), color=s['color'], lw=2, label=s['label'], zorder=3)
    ax2.set_xlabel('Batch ID', fontsize=10);  ax2.set_ylabel('Duration (sim time)', fontsize=10)
    ax2.set_title('Batch completion time', fontsize=10)
    ax2.legend(fontsize=9);  ax2.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot3_completion_rate.png'))

    # ── plot 4: picker concurrency ─────────────────────────────────────────────
    fig, axes = _row(n)
    fig.suptitle(f'Picker Concurrency  [{name}]', fontsize=12, fontweight='bold')
    for ax, s in zip(axes, strategies):
        data = df_b[s['key']]['avg_concurrent_pickers'].values
        if len(data):
            _kde_plot(ax, data, s['color'], bins=35)
        ax.axvline(k_pickers, color='grey', lw=1.0, linestyle='-.', label=f'Max ({k_pickers})')
        ax.set_xlabel('Avg concurrent pickers', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f"{s['label']}  (n={len(data):,})", fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot4_picker_concurrency.png'))

    # ── plot 5: picker utilisation (picking vs traveling) ──────────────────────
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(f'Picker Utilisation Breakdown  [{name}]', fontsize=13, fontweight='bold')
    for s in strategies:
        df = df_b[s['key']]
        if df.empty:
            continue
        axL.hist(df['picking_pct'].values, bins=30, color=s['color'], alpha=0.55,
                 edgecolor='white', label=f"{s['label']}  μ={df['picking_pct'].mean():.1f}%")
    axL.xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axL.set_xlabel('Picking %', fontsize=10);  axL.set_ylabel('Count', fontsize=10)
    axL.set_title('Picking % — overlaid', fontsize=10)
    axL.legend(fontsize=8);  axL.grid(axis='y', alpha=0.3)
    x  = np.arange(n)
    pk = [df_b[s['key']]['picking_pct'].mean()   for s in strategies]
    tr = [df_b[s['key']]['traveling_pct'].mean() for s in strategies]
    axR.bar(x, pk, width=0.6, color=[s['color'] for s in strategies], alpha=0.85, label='Picking')
    axR.bar(x, tr, width=0.6, bottom=pk, color=_TRAVEL_COL, alpha=0.85, label='Traveling')
    axR.set_xticks(x);  axR.set_xticklabels(labels, rotation=20, ha='right', fontsize=8)
    axR.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axR.set_ylabel('Mean fraction (%)', fontsize=10);  axR.set_title('Aggregate mean split', fontsize=10)
    axR.legend(fontsize=8);  axR.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot5_picker_utilisation.png'))

    # ── plot 6: task duration by aisle type (handling × unit) ──────────────────
    groups = [('conveyable', 'pallet'), ('conveyable', 'singleton'),
              ('non-conveyable', 'pallet'), ('non-conveyable', 'singleton')]
    glabels = ['Conv\nPallet', 'Conv\nSingleton', 'Non-Conv\nPallet', 'Non-Conv\nSingleton']

    def _mean_by_group(df, h, u):
        v = df[(df['handling'] == h) & (df['unit_type'] == u)]['duration']
        return float(v.mean()) if len(v) > 0 else 0.0

    means = {s['key']: [_mean_by_group(df_t[s['key']], h, u) for h, u in groups] for s in strategies}
    xg = np.arange(len(groups));  w = 0.8 / n
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(16, 5))
    fig.suptitle(f'Mean Task Duration by Aisle Type  [{name}]', fontsize=13, fontweight='bold')
    for i, s in enumerate(strategies):
        axL.bar(xg + (i - (n - 1) / 2) * w, means[s['key']], width=w,
                color=s['color'], alpha=0.85, label=s['label'])
    axL.set_xticks(xg);  axL.set_xticklabels(glabels, fontsize=9)
    axL.set_ylabel('Mean task duration', fontsize=10)
    axL.set_title('Mean task duration by handling × unit type', fontsize=10)
    axL.legend(fontsize=8);  axL.grid(axis='y', alpha=0.3)
    others = strategies[1:]
    wd = 0.8 / max(len(others), 1)
    for i, s in enumerate(others):
        deltas = [_pct_delta(means[s['key']][j], means[base_key][j]) for j in range(len(groups))]
        axR.bar(xg + (i - (len(others) - 1) / 2) * wd, deltas, width=wd,
                color=s['color'], alpha=0.85, label=f"{s['label']} vs {base_label}")
    axR.axhline(0, color='black', lw=1)
    axR.set_xticks(xg);  axR.set_xticklabels(glabels, fontsize=9)
    axR.set_ylabel(f'Δ vs {base_label}  %', fontsize=10)
    axR.yaxis.set_major_formatter(mticker.PercentFormatter())
    axR.set_title(f'Duration delta vs {base_label}', fontsize=10)
    axR.legend(fontsize=8);  axR.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot6_aisle_type.png'))

    # ── plot 7: per-aisle mean task duration ───────────────────────────────────
    per_aisle = pd.concat(
        [df_t[s['key']].groupby('aisle_id')['duration'].mean().rename(s['key']) for s in strategies],
        axis=1).dropna()
    fig, axes = plt.subplots(1, 3, figsize=(19, 5))
    fig.suptitle(f'Per-Aisle Mean Task Duration  [{name}]', fontsize=13, fontweight='bold')
    for s in strategies:
        if s['key'] not in per_aisle:
            continue
        v = per_aisle[s['key']]
        axes[0].hist(v, bins=50, color=s['color'], alpha=0.45, edgecolor='white',
                     label=f"{s['label']}  μ={v.mean():.1f}")
    axes[0].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[0].set_ylabel('Aisle count', fontsize=10)
    axes[0].set_title('Distribution of aisle mean durations', fontsize=10)
    axes[0].legend(fontsize=8);  axes[0].grid(axis='y', alpha=0.3)
    for s in strategies:
        if s['key'] not in per_aisle:
            continue
        v = np.sort(np.asarray(per_aisle[s['key']], dtype=float))
        axes[1].plot(v, np.arange(1, len(v) + 1) / len(v), color=s['color'], lw=2, label=s['label'])
    axes[1].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[1].set_ylabel('Cumulative fraction', fontsize=10)
    axes[1].set_title('CDF of per-aisle mean duration', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    axes[1].legend(fontsize=8);  axes[1].grid(alpha=0.3)
    if base_key in per_aisle:
        for s in strategies[1:]:
            if s['key'] not in per_aisle:
                continue
            d = (per_aisle[s['key']] - per_aisle[base_key]) / per_aisle[base_key].abs() * 100
            dv = np.asarray(d, dtype=float)
            axes[2].hist(dv, bins=50, color=s['color'], alpha=0.5, edgecolor='white',
                         label=f"{s['label']}  mean={dv.mean():.2f}%")
        axes[2].axvline(0, color='black', lw=1.5, linestyle='--')
    axes[2].set_xlabel(f'Δ (X − {base_label}) / {base_label}  %', fontsize=10)
    axes[2].set_ylabel('Aisle count', fontsize=10)
    axes[2].set_title(f'Per-aisle % duration change (vs {base_label})', fontsize=10)
    axes[2].xaxis.set_major_formatter(mticker.PercentFormatter())
    axes[2].legend(fontsize=8);  axes[2].grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot7_per_aisle.png'))

    # ── plot 8: mean task duration per batch ───────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle(f'Mean Aisle Task Duration per Batch  (rolling {_WIN}-batch mean)  [{name}]',
                 fontsize=13, fontweight='bold')
    for s in strategies:
        df = df_t[s['key']]
        if df.empty:
            continue
        tpb = (df.groupby('batch_id')['duration'].mean().reset_index().sort_values('batch_id'))
        x   = np.asarray(tpb['batch_id'])
        y   = np.asarray(tpb['duration'])
        ax.scatter(x, y, color=s['color'], alpha=0.20, s=8, zorder=2)
        ax.plot(x, np.asarray(pd.Series(y).rolling(_WIN, min_periods=1).mean()),
                color=s['color'], lw=2, label=s['label'], zorder=3)
    ax.set_xlabel('Batch ID', fontsize=10);  ax.set_ylabel('Mean task duration (sim time)', fontsize=10)
    ax.legend(fontsize=9);  ax.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot8_task_duration_per_batch.png'))

    # ── plot 9: strategy headline summary (the one-glance ablation chart) ──────
    mean_dur = [df_b[s['key']]['duration'].mean()        if not df_b[s['key']].empty else 0.0
                for s in strategies]
    mean_thr = [df_b[s['key']]['completion_rate'].mean() if not df_b[s['key']].empty else 0.0
                for s in strategies]
    cols = [s['color'] for s in strategies]
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(7 + 1.4 * n, 5))
    fig.suptitle(f'Strategy Summary  (Δ vs {base_label})  [{name}]', fontsize=13, fontweight='bold')

    def _bars(ax, vals, title, ylab, lower_is_better):
        bars  = ax.bar(np.arange(n), vals, color=cols, alpha=0.85)
        base_v = vals[0]
        for i, (b, v) in enumerate(zip(bars, vals)):
            xc = b.get_x() + b.get_width() / 2
            if i == 0:
                ax.annotate('baseline', (xc, v), ha='center', va='bottom',
                            fontsize=8, color='grey')
            else:
                d    = _pct_delta(v, base_v)
                good = (d < 0) if lower_is_better else (d > 0)
                ax.annotate(f'{d:+.1f}%', (xc, v), ha='center', va='bottom',
                            fontsize=8, color='#1a7a1a' if good else '#c00000')
        ax.set_xticks(np.arange(n));  ax.set_xticklabels(labels, rotation=20, ha='right', fontsize=8)
        ax.set_ylabel(ylab, fontsize=10);  ax.set_title(title, fontsize=11)
        ax.grid(axis='y', alpha=0.3)

    _bars(axL, mean_dur, 'Mean batch duration (lower better)', 'Sim time', lower_is_better=True)
    _bars(axR, mean_thr, 'Mean throughput (higher better)', 'Items / time', lower_is_better=False)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot9_strategy_summary.png'))

    # ── plot 10: Σf·W layout quality (the re-slot catch-up headline) ───────────
    # Realised demand-weighted within-aisle travel per batch (lower = better).
    # The dashed line is the full-stock pure-global-W optimum (a floor); a uniform
    # start sits high, optimal_reslot starts at the floor, and the question is
    # whether uniform_reslot descends toward it.  Σf·W is fill-dependent (depletion
    # lowers it for every strategy alike), so compare the curves' relative gap.
    optimal = float(sim_result.get('optimal_sigma_fw') or 0.0)
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle(f'Layout quality: demand-weighted within-aisle travel Σf·W  '
                 f'(rolling {_WIN}-batch mean)  [{name}]', fontsize=13, fontweight='bold')
    for s in strategies:
        df = df_b[s['key']]
        if df.empty:
            continue
        d = df.sort_values('batch_id')
        ax.plot(d['batch_id'].values, _roll(df, 'sigma_fw', _WIN),
                color=s['color'], lw=2, label=s['label'])
    if optimal > 0:
        ax.axhline(optimal, color='grey', lw=1.2, linestyle='--',
                   label='Optimal (full-stock floor)')
    ax.set_xlabel('Batch ID', fontsize=10)
    ax.set_ylabel('Σf·W  (lower = better)', fontsize=10)
    ax.legend(fontsize=9);  ax.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot10_sigma_fw.png'))

    # ── plot 11: inventory churn (how much moved per batch) ────────────────────
    total_bins = float(shared.get('total_bins') or 0)
    fig, (axT, axB) = plt.subplots(2, 1, figsize=(14, 8))
    fig.suptitle(f'Inventory Churn  (re-slot moves + reorder placements)  [{name}]',
                 fontsize=13, fontweight='bold')
    for s in strategies:
        df = df_b[s['key']]
        if df.empty:
            continue
        d     = df.sort_values('batch_id')
        x     = d['batch_id'].values
        moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
        if total_bins > 0:
            frac = moved / total_bins * 100.0
            axT.plot(x, frac.rolling(_WIN, min_periods=1).mean(), color=s['color'], lw=2, label=s['label'])
            axB.plot(x, frac.cumsum().values, color=s['color'], lw=2, label=s['label'])
        else:
            axT.plot(x, moved.rolling(_WIN, min_periods=1).mean(), color=s['color'], lw=2, label=s['label'])
            axB.plot(x, moved.cumsum().values, color=s['color'], lw=2, label=s['label'])
    axT.set_ylabel('% of bins moved / batch' if total_bins > 0 else 'moves / batch', fontsize=10)
    axT.set_title('Per-batch churn (rolling mean)', fontsize=10)
    axT.legend(fontsize=9);  axT.grid(alpha=0.3)
    axB.set_xlabel('Batch ID', fontsize=10)
    axB.set_ylabel('cumulative % of bins moved' if total_bins > 0 else 'cumulative moves', fontsize=10)
    axB.set_title('Cumulative churn', fontsize=10)
    axB.legend(fontsize=9);  axB.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot11_churn.png'))

    log.info(f'  Saved → {run_dir}')
