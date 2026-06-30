"""Shared per-strategy panel painters (verbatim from the retired Comparison_Plots
closures), rewritten to take the context so the metric_grids and scorecards graphs
both reuse them.  Not graphs themselves — pure helpers."""
import numpy as np

from Performance_Evaluations.common.frames import _roll
from Performance_Evaluations.common.style import _WIN


def _eff_series(df, optimal):
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    if optimal > 0:
        y = (optimal / d['sigma_fd'].clip(lower=1e-9) * 100.0).rolling(_WIN, min_periods=1).mean()
    else:
        y = d['sigma_fd'].rolling(_WIN, min_periods=1).mean()
    return d['batch_id'].values, y.values


def _churn_series(df, total_bins):
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
    if total_bins > 0:
        moved = moved / total_bins * 100.0
    return d['batch_id'].values, moved.rolling(_WIN, min_periods=1).mean().values


def panel_duration(ctx, ax, s):
    df = ctx.batch_df(s['key'])
    if not df.empty:
        d = df.sort_values('batch_id')
        ax.plot(d['batch_id'].values, _roll(df, 'duration', _WIN), color=s['color'], lw=1.2)


def panel_eff(ctx, ax, s):
    ser = _eff_series(ctx.batch_df(s['key']), ctx.optimal)
    if ser is not None:
        ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)
        if ctx.optimal > 0:
            ax.axhline(100.0, color='grey', lw=0.7, ls='--')


def panel_churn(ctx, ax, s):
    ser = _churn_series(ctx.batch_df(s['key']), ctx.total_bins)
    if ser is not None:
        ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)


def panel_taskdur(ctx, ax, s):
    data = ctx.task_df(s['key'])['duration'].values
    if not len(data):
        return
    ax.hist(data, bins=30, color=s['color'], alpha=0.7, edgecolor='white')
    mean, med = float(np.mean(data)), float(np.median(data))
    ax.axvline(mean, color='red',   lw=1.4, ls='--', label=f'mean {mean:.0f}')
    ax.axvline(med,  color='black', lw=1.2, ls=':',  label=f'med {med:.0f}')
    ax.legend(fontsize=5, loc='upper right')


def panel_task_overtime(ctx, ax, s):
    d = ctx.series().get(s['key'])
    if d is None:
        return
    x = d['task_batch']
    ax.fill_between(x, d['task_p25'], d['task_p75'], color=s['color'], alpha=0.18, label='IQR')
    ax.plot(x, d['task_median'], color=s['color'], lw=1.3, label='median')
    ax.plot(x, d['task_mean'],   color='black',    lw=1.0, ls='--', label='mean')
