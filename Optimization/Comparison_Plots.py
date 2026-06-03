"""
Comparison_Plots.py — graphing and analysis for run_comparison.py.

Called from run_comparison.py via `run_config_analysis`.  Kept separate so
matplotlib/scipy concerns don't live inside the simulation orchestration module.
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

# ── strategy colours ───────────────────────────────────────────────────────────
_A_COL      = '#5b9bd5'
_B_COL      = '#f4a030'
_C_COL      = '#70ad47'
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


# ── main analysis entry point ──────────────────────────────────────────────────

def run_config_analysis(
    sim_result : dict,
    shared     : dict,
    log        : logging.Logger,
) -> None:
    """Run the analysis + plotting phase for one regression config.

    Must be called sequentially (matplotlib pyplot is not thread-safe).
    Reads sim_result returned by _run_config_sim.
    """
    name               = sim_result['name']
    run_dir            = sim_result['run_dir']
    db_path_A          = sim_result['db_path_A']
    db_path_B          = sim_result['db_path_B']
    db_path_C          = sim_result['db_path_C']
    run_a              = sim_result['run_a']
    run_b              = sim_result['run_b']
    run_c              = sim_result['run_c']
    aisle_unittype_map = shared['aisle_unittype_map']
    aisle_handling_map = shared['aisle_handling_map']
    k_pickers          = shared.get('k_pickers', 25)

    # ── analysis ──────────────────────────────────────────────────────────────
    bs_fA = flag_batch_outliers(load_batch_stats(db_path_A, run_a))
    bs_fB = flag_batch_outliers(load_batch_stats(db_path_B, run_b))
    bs_fC = flag_batch_outliers(load_batch_stats(db_path_C, run_c))
    ts_fA = flag_task_outliers(load_task_stats(db_path_A, run_a))
    ts_fB = flag_task_outliers(load_task_stats(db_path_B, run_b))
    ts_fC = flag_task_outliers(load_task_stats(db_path_C, run_c))

    df_bA = _bdf([s for s in bs_fA if not s.is_outlier])
    df_bB = _bdf([s for s in bs_fB if not s.is_outlier])
    df_bC = _bdf([s for s in bs_fC if not s.is_outlier])
    df_tA = _tdf([s for s in ts_fA if not s.is_outlier], aisle_unittype_map, aisle_handling_map)
    df_tB = _tdf([s for s in ts_fB if not s.is_outlier], aisle_unittype_map, aisle_handling_map)
    df_tC = _tdf([s for s in ts_fC if not s.is_outlier], aisle_unittype_map, aisle_handling_map)

    # summary CSVs
    bcols  = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols  = ['duration', 'W_a', 'lift_sum', 'num_bins']
    summ_b = pd.concat(
        [df_bA[bcols].agg(['mean','median','std']).T,
         df_bB[bcols].agg(['mean','median','std']).T,
         df_bC[bcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)']).round(3)
    summ_t = pd.concat(
        [df_tA[tcols].agg(['mean','median','std']).T,
         df_tB[tcols].agg(['mean','median','std']).T,
         df_tC[tcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)']).round(3)
    summ_b.to_csv(os.path.join(run_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(run_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    # ── plot 1: batch duration ─────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Batch Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['duration'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['duration'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_bC['duration'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=40)
        ax.set_xlabel('Batch duration  (sim time units)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot1_batch_duration.png'))

    # ── plot 2: task duration ──────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Task (Aisle) Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_tA['duration'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_tB['duration'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_tC['duration'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=50)
        ax.set_xlabel('Task duration  (sim time units)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot2_task_duration.png'))

    # ── plot 3: completion rate + batch duration ──────────────────────────────
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))
    fig.suptitle(f'Batch Completion  (dots = per-batch, line = rolling {_WIN}-batch mean)  [{name}]',
                 fontsize=13, fontweight='bold')

    for df, c, lbl in [(df_bA, _A_COL, 'Uniform (A)'),
                       (df_bB, _B_COL, 'Trip-Min (B)'),
                       (df_bC, _C_COL, 'Trip-Max (C)')]:
        x = df.sort_values('batch_id')['batch_id'].values
        ax1.plot(x, _roll(df, 'completion_rate', _WIN), color=c, lw=2, label=lbl)
    ax1.set_ylabel('Items / time unit', fontsize=10)
    ax1.set_title('Throughput rate', fontsize=10)
    ax1.legend(fontsize=9);  ax1.grid(alpha=0.3)

    for df, c, lbl in [(df_bA, _A_COL, 'Uniform (A)'),
                       (df_bB, _B_COL, 'Trip-Min (B)'),
                       (df_bC, _C_COL, 'Trip-Max (C)')]:
        x     = df.sort_values('batch_id')['batch_id'].values
        y_raw = df.sort_values('batch_id')['duration'].values
        ax2.scatter(x, y_raw, color=c, alpha=0.25, s=10, zorder=2)
        ax2.plot(x, _roll(df, 'duration', _WIN), color=c, lw=2, label=lbl, zorder=3)
    ax2.set_xlabel('Batch ID', fontsize=10)
    ax2.set_ylabel('Duration (sim time units)', fontsize=10)
    ax2.set_title('Batch completion time', fontsize=10)
    ax2.legend(fontsize=9);  ax2.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot3_completion_rate.png'))

    # ── plot 4: picker concurrency ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Picker Concurrency  [{name}]', fontsize=12, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['avg_concurrent_pickers'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['avg_concurrent_pickers'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_bC['avg_concurrent_pickers'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=35)
        ax.axvline(k_pickers, color='grey', lw=1.0, linestyle='-.', label=f'Max ({k_pickers})')
        ax.set_xlabel('Avg concurrent pickers', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot4_picker_concurrency.png'))

    # ── plot 5: picker utilisation ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    fig.suptitle(f'Picker Utilisation Breakdown  [{name}]', fontsize=13, fontweight='bold')
    bp = axes[0].boxplot(
        [df_bA['picking_pct'].values,   df_bB['picking_pct'].values,   df_bC['picking_pct'].values,
         df_bA['traveling_pct'].values, df_bB['traveling_pct'].values, df_bC['traveling_pct'].values],
        labels=['Pick A', 'Pick B', 'Pick C', 'Travel A', 'Travel B', 'Travel C'],
        patch_artist=True, medianprops=dict(color='black', lw=2),
    )
    for patch, c in zip(bp['boxes'], [_A_COL, _B_COL, _C_COL, _A_COL, _B_COL, _C_COL]):
        patch.set_facecolor(c);  patch.set_alpha(0.7)
    axes[0].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[0].set_title('Picking vs Traveling %', fontsize=10);  axes[0].grid(axis='y', alpha=0.3)
    for dfb, c, lbl in [(df_bA, _A_COL, 'Uniform'), (df_bB, _B_COL, 'Trip-Min'), (df_bC, _C_COL, 'Trip-Max')]:
        axes[1].hist(dfb['picking_pct'].values, bins=30, color=c, alpha=0.55, edgecolor='white',
                     label=f'{lbl}  μ={dfb["picking_pct"].mean():.1f}%')
    axes[1].xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[1].set_xlabel('Picking %', fontsize=10);  axes[1].set_ylabel('Count', fontsize=10)
    axes[1].set_title('Picking % — overlaid', fontsize=10)
    axes[1].legend(fontsize=8);  axes[1].grid(axis='y', alpha=0.3)
    x  = np.arange(3)
    pk = [df_bA['picking_pct'].mean(),   df_bB['picking_pct'].mean(),   df_bC['picking_pct'].mean()]
    tr = [df_bA['traveling_pct'].mean(), df_bB['traveling_pct'].mean(), df_bC['traveling_pct'].mean()]
    axes[2].bar(x, pk, width=0.5, color=[_A_COL, _B_COL, _C_COL], alpha=0.85, label='Picking')
    axes[2].bar(x, tr, width=0.5, bottom=pk, color=_TRAVEL_COL, alpha=0.85, label='Traveling')
    axes[2].set_xticks(x);  axes[2].set_xticklabels(['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)'])
    axes[2].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[2].set_ylabel('Mean fraction (%)', fontsize=10);  axes[2].set_title('Aggregate mean split', fontsize=10)
    axes[2].legend(fontsize=8);  axes[2].grid(axis='x', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot5_picker_utilisation.png'))

    # ── plot 6: task duration by aisle type (handling × unit-type) ───────────
    _AISLE_GROUPS  = [('conveyable', 'pallet'), ('conveyable', 'singleton'),
                      ('non-conveyable', 'pallet'), ('non-conveyable', 'singleton')]
    _GROUP_LABELS  = ['Conv\nPallet', 'Conv\nSingleton',
                      'Non-Conv\nPallet', 'Non-Conv\nSingleton']

    def _mean_by_group(df, h, u):
        v = df[(df['handling'] == h) & (df['unit_type'] == u)]['duration']
        return float(v.mean()) if len(v) > 0 else 0.0

    x6  = np.arange(len(_AISLE_GROUPS));  w6 = 0.25
    mA6 = [_mean_by_group(df_tA, h, u) for h, u in _AISLE_GROUPS]
    mB6 = [_mean_by_group(df_tB, h, u) for h, u in _AISLE_GROUPS]
    mC6 = [_mean_by_group(df_tC, h, u) for h, u in _AISLE_GROUPS]

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(f'Mean Task Duration by Aisle Type  [{name}]', fontsize=13, fontweight='bold')

    axes[0].bar(x6 - w6, mA6, width=w6, color=_A_COL, alpha=0.85, label='Uniform (A)')
    axes[0].bar(x6,       mB6, width=w6, color=_B_COL, alpha=0.85, label='Trip-Min (B)')
    axes[0].bar(x6 + w6, mC6, width=w6, color=_C_COL, alpha=0.85, label='Trip-Max (C)')
    axes[0].set_xticks(x6);  axes[0].set_xticklabels(_GROUP_LABELS, fontsize=9)
    axes[0].set_ylabel('Mean task duration', fontsize=10)
    axes[0].set_title('Mean task duration by handling × unit type', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    dB6 = [(b - a) / abs(a) * 100 if a else 0 for a, b in zip(mA6, mB6)]
    dC6 = [(c - a) / abs(a) * 100 if a else 0 for a, c in zip(mA6, mC6)]
    axes[1].bar(x6 - w6/2, dB6, width=w6,
                color=[_B_COL if d < 0 else '#c00000' for d in dB6], alpha=0.85, label='B vs A')
    axes[1].bar(x6 + w6/2, dC6, width=w6,
                color=[_C_COL if d > 0 else '#c00000' for d in dC6], alpha=0.85, label='C vs A')
    axes[1].axhline(0, color='black', lw=1)
    for j, (dB, dC) in enumerate(zip(dB6, dC6)):
        if abs(dB) > 0.1:
            axes[1].text(j - w6/2, dB + (0.3 if dB >= 0 else -0.8), f'{dB:.1f}%',
                         ha='center', fontsize=8)
        if abs(dC) > 0.1:
            axes[1].text(j + w6/2, dC + (0.3 if dC >= 0 else -0.8), f'{dC:.1f}%',
                         ha='center', fontsize=8)
    axes[1].set_xticks(x6);  axes[1].set_xticklabels(_GROUP_LABELS, fontsize=9)
    axes[1].set_ylabel('Δ (X − A) / A  %', fontsize=10)
    axes[1].set_title('Duration delta vs Uniform (A)', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter())
    axes[1].legend(fontsize=8);  axes[1].grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot6_aisle_type.png'))

    # ── plot 7: per-aisle ──────────────────────────────────────────────────────
    acmp = pd.concat([
        df_tA.groupby('aisle_id')['duration'].mean().rename('A'),
        df_tB.groupby('aisle_id')['duration'].mean().rename('B'),
        df_tC.groupby('aisle_id')['duration'].mean().rename('C'),
    ], axis=1).dropna()
    acmp['dB'] = (acmp['B'] - acmp['A']) / acmp['A'].abs() * 100
    acmp['dC'] = (acmp['C'] - acmp['A']) / acmp['A'].abs() * 100

    fig, axes = plt.subplots(1, 3, figsize=(19, 5))
    fig.suptitle(f'Per-Aisle Mean Task Duration  [{name}]', fontsize=13, fontweight='bold')
    for v, lbl, c in [(acmp['A'], 'Uniform (A)',   _A_COL),
                      (acmp['B'], 'Trip-Min (B)', _B_COL),
                      (acmp['C'], 'Trip-Max (C)', _C_COL)]:
        axes[0].hist(v, bins=50, color=c, alpha=0.50, edgecolor='white', label=f'{lbl}  μ={v.mean():.1f}')
    axes[0].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[0].set_ylabel('Aisle count', fontsize=10)
    axes[0].set_title('Distribution of aisle mean durations', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)
    for v, lbl, c in [(np.sort(np.asarray(acmp['A'], dtype=float)), 'Uniform (A)',   _A_COL),
                      (np.sort(np.asarray(acmp['B'], dtype=float)), 'Trip-Min (B)', _B_COL),
                      (np.sort(np.asarray(acmp['C'], dtype=float)), 'Trip-Max (C)', _C_COL)]:
        axes[1].plot(v, np.arange(1, len(v)+1)/len(v), color=c, lw=2, label=lbl)
    axes[1].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[1].set_ylabel('Cumulative fraction', fontsize=10)
    axes[1].set_title('CDF of per-aisle mean duration', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    axes[1].legend(fontsize=9);  axes[1].grid(alpha=0.3)
    dBv = np.asarray(acmp['dB'], dtype=float);  dCv = np.asarray(acmp['dC'], dtype=float)
    axes[2].hist(dBv, bins=50, color=_B_COL, alpha=0.55, edgecolor='white', label=f'B vs A  mean={dBv.mean():.2f}%')
    axes[2].hist(dCv, bins=50, color=_C_COL, alpha=0.55, edgecolor='white', label=f'C vs A  mean={dCv.mean():.2f}%')
    axes[2].axvline(0,         color='black', lw=1.5, linestyle='--')
    axes[2].axvline(dBv.mean(), color=_B_COL, lw=2,   linestyle='--')
    axes[2].axvline(dCv.mean(), color=_C_COL, lw=2,   linestyle='--')
    axes[2].set_xlabel('Δ (X − A) / A  %', fontsize=10)
    axes[2].set_ylabel('Aisle count', fontsize=10)
    axes[2].set_title('Per-aisle % duration change (vs Uniform A)', fontsize=10)
    axes[2].xaxis.set_major_formatter(mticker.PercentFormatter())
    axes[2].legend(fontsize=9);  axes[2].grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot7_per_aisle.png'))

    imp_B = (acmp['dB'] < 0).sum();  imp_C = (acmp['dC'] > 0).sum()
    log.info(f'  Aisles faster with B: {imp_B}/{len(acmp)}   slower with C: {imp_C}/{len(acmp)}')
    log.info(f'  Mean delta  B: {dBv.mean():.2f}%   C: {dCv.mean():.2f}%')

    # ── plot 8: mean task duration per batch ──────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle(
        f'Mean Aisle Task Duration per Batch  '
        f'(dots = per-batch mean, line = rolling {_WIN}-batch mean)  [{name}]',
        fontsize=13, fontweight='bold',
    )
    for df_t, c, lbl in [(df_tA, _A_COL, 'Uniform (A)'),
                         (df_tB, _B_COL, 'Trip-Min (B)'),
                         (df_tC, _C_COL, 'Trip-Max (C)')]:
        tpb   = (df_t.groupby('batch_id')['duration']
                 .mean().reset_index().sort_values('batch_id'))
        x     = np.asarray(tpb['batch_id'])
        y_raw = np.asarray(tpb['duration'])
        y_roll = np.asarray(pd.Series(y_raw).rolling(_WIN, min_periods=1).mean())
        ax.scatter(x, y_raw,  color=c, alpha=0.25, s=10, zorder=2)
        ax.plot(x,    y_roll, color=c, lw=2, label=lbl, zorder=3)
    ax.set_xlabel('Batch ID', fontsize=10)
    ax.set_ylabel('Mean task duration (sim time units)', fontsize=10)
    ax.legend(fontsize=9);  ax.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot8_task_duration_per_batch.png'))

    log.info(f'  Saved → {run_dir}')
