"""diagnostics.metric_grids — small-multiple grids, one panel per arm.

Four grids (batch duration over time, layout efficiency, task-duration distribution,
task duration over time), ported from the retired per-strategy grids MINUS the churn
grid, which moved to the layout family as a single overlay.  What changed beyond the
move: every grid now FORCES one shared y scale across its panels — the retired grids
let every facet autoscale, the cardinal small-multiples sin, so two arms whose panels
looked identical could differ by half their range.  Durations render in hours, only
edge panels carry axis labels, the histogram grid additionally bins every arm on one
common edge set, and any line-role encoding (median / mean / IQR) gets one shared
legend below the panels instead of thirty in-panel copies.

Raw operational read-outs for inspection, not comparison — the diagnostics exemption
in the family grammar.
"""
import math
import os

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.frames import _roll
from Optimization.Performance_Evaluations.common.style import _stitle, _WIN


# ── series builders (the retired shared panel helpers, inlined) ─────────────────

def _eff_series(df, optimal):
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    if optimal > 0:
        y = (optimal / d['sigma_fd'].clip(lower=1e-9) * 100.0).rolling(
            _WIN, min_periods=1).mean()
    else:
        y = d['sigma_fd'].rolling(_WIN, min_periods=1).mean()
    return d['batch_id'].values, y.values


# ── grid scaffolding ────────────────────────────────────────────────────────────

def _grid_chart(n, legend_labels=()):
    ncols = min(8, max(1, math.ceil(math.sqrt(n))))
    ch = chartkit.make(panels=n, ncols=ncols, panel_w=3.0, panel_h=2.3,
                      legend=('bottom' if legend_labels else 'none'),
                      legend_labels=legend_labels)
    return ch, ncols


def _finish_panels(ch, ncols, n, strategies, xlabel, ylabel):
    """Arm-name panel titles + the edge-only labeling rule."""
    for i, (ax, s) in enumerate(zip(ch.axes, strategies)):
        ax.set_title(_stitle(s), fontsize=7)
        ax.tick_params(labelsize=6)
        if i % ncols == 0:
            ax.set_ylabel(ylabel, fontsize=7)
        else:
            ax.tick_params(labelleft=False)
        if i + ncols >= n:
            ax.set_xlabel(xlabel, fontsize=7)
        else:
            ax.tick_params(labelbottom=False)


@evaluation(key='diagnostics.metric_grids', label='Per-arm metric grids (shared scales)',
            scope='per_strategy', needs=('batch', 'task', 'series'),
            family='diagnostics', views=('absolute',))
def render(ctx, params):
    strategies = [s for s in ctx.strategies if not ctx.batch_df(s['key']).empty]
    n = len(strategies)
    if not n:
        return
    out = io.out_dir(ctx)
    optimal = ctx.optimal

    # ── grid 1: batch duration over time (hours) ────────────────────────────────
    ch, ncols = _grid_chart(n)
    vals = []
    for ax, s in zip(ch.axes, strategies):
        df = ctx.batch_df(s['key'])
        d = df.sort_values('batch_id')
        y = chartkit.to_hours(_roll(df, 'duration', _WIN))
        ax.plot(d['batch_id'].values, y, color=chartkit.strategy_color(s, strategies),
                lw=1.2)
        vals.append(y)
    chartkit.shared_ylim(ch.axes, vals)
    _finish_panels(ch, ncols, n, strategies, 'batch',
                   f'batch duration ({chartkit.HOURS})')
    ch.legend()
    ch.title('Batch duration per arm — rolling mean, shared y')
    ch.save(os.path.join(out, 'absolute_grid_batch_duration.png'), view='absolute')

    # ── grid 2: layout efficiency (or raw f·D) over time ───────────────────────
    ch, ncols = _grid_chart(n)
    vals = []
    for ax, s in zip(ch.axes, strategies):
        ser = _eff_series(ctx.batch_df(s['key']), optimal)
        if ser is None:
            continue
        ax.plot(ser[0], ser[1], color=chartkit.strategy_color(s, strategies), lw=1.2)
        if optimal > 0:
            ax.axhline(100.0, color='grey', lw=0.7, ls='--')
        vals.append(ser[1])
    chartkit.shared_ylim(ch.axes, vals, include=((100.0,) if optimal > 0 else ()))
    _finish_panels(ch, ncols, n, strategies, 'batch',
                   '% of optimal' if optimal > 0 else 'total f·D')
    ch.legend()
    ch.title('Layout efficiency per arm — shared y' if optimal > 0
             else 'Total f·D per arm — shared y')
    ch.save(os.path.join(out, 'absolute_grid_sigma_fd.png'), view='absolute')

    # ── grid 3: task-duration distribution (hours, common bins) ────────────────
    ch, ncols = _grid_chart(n, legend_labels=('mean', 'median'))
    hours_by_key = {s['key']: chartkit.to_hours(ctx.task_df(s['key'])['duration'].values)
                    for s in strategies}
    pooled = np.concatenate([h for h in hours_by_key.values() if len(h)] or
                            [np.array([0.0])])
    edges = np.histogram_bin_edges(pooled[np.isfinite(pooled)], bins=30)
    counts = []
    for ax, s in zip(ch.axes, strategies):
        h = hours_by_key[s['key']]
        if not len(h):
            continue
        cnt, _e, _p = ax.hist(h, bins=edges,
                              color=chartkit.strategy_color(s, strategies),
                              alpha=0.7, edgecolor='white')
        ax.axvline(float(np.mean(h)), color='red', lw=1.2, ls='--')
        ax.axvline(float(np.median(h)), color='black', lw=1.0, ls=':')
        counts.append(cnt)
    chartkit.shared_ylim(ch.axes, counts, include=(0.0,))
    _finish_panels(ch, ncols, n, strategies,
                   f'task duration ({chartkit.HOURS})', 'count')
    ch.legend(handles=[Line2D([], [], color='red', lw=1.2, ls='--', label='mean'),
                       Line2D([], [], color='black', lw=1.0, ls=':', label='median')])
    ch.title('Task duration distribution — shared bins & y')
    ch.save(os.path.join(out, 'absolute_grid_task_duration.png'), view='absolute')

    # ── grid 4: task duration over time (median, mean, IQR — hours) ────────────
    S = ctx.series()
    ch, ncols = _grid_chart(n, legend_labels=('median', 'mean', 'IQR'))
    vals = []
    for ax, s in zip(ch.axes, strategies):
        d = S.get(s['key'])
        if d is None:
            continue
        x = d['task_batch']
        p25 = chartkit.to_hours(d['task_p25'])
        p75 = chartkit.to_hours(d['task_p75'])
        med = chartkit.to_hours(d['task_median'])
        mean = chartkit.to_hours(d['task_mean'])
        chartkit.draw_ci(ax, x, p25, p75, band=True,
                         color=chartkit.strategy_color(s, strategies))
        ax.plot(x, med, color=chartkit.strategy_color(s, strategies), lw=1.3)
        ax.plot(x, mean, color='black', lw=1.0, ls='--')
        vals.extend([p25, p75, med, mean])
    chartkit.shared_ylim(ch.axes, [v[np.isfinite(v)] for v in vals if len(v)])
    _finish_panels(ch, ncols, n, strategies, 'batch',
                   f'task duration ({chartkit.HOURS})')
    ch.legend(handles=[Line2D([], [], color='#555555', lw=1.3, label='median'),
                       Line2D([], [], color='black', lw=1.0, ls='--', label='mean'),
                       Patch(color='#555555', alpha=0.25, label='IQR')])
    ch.title('Task duration over time per arm — shared y')
    ch.save(os.path.join(out, 'absolute_grid_task_over_time.png'), view='absolute')
