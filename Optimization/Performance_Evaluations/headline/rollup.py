"""headline.rollup — production-time-first per-run rollups as sorted dot plots.

The per-arm aggregations are ported from the retired per-run report (total production
time = Σ task time over every batch, production-time-per-item's siblings, mean
completion rate, put-away queue depth — the honesty metric: a strategy that defers
placement carries a standing queue).  The table outputs moved to another module; this
one draws ONLY figures, and replaces the unreadable 34-bar walls with sorted dot
plots: one dot per arm, FIFO as the zero reference line on the percent views, best at
the top, panel height computed from the category count so labels stay readable.

The queue-depth figure is written only when any arm ever carried a queue (the ported
conditional) — an all-zero chart would claim an honesty check that never ran.
Baseline = strategies[0] (the FIFO arm).
"""
import os

import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle


def _summaries(ctx):
    """Per-arm rollup scalars, ported from the retired per-run report."""
    out = []
    for s in ctx.strategies:
        b = ctx.batch_df(s['key'])
        t = ctx.task_df(s['key'])
        if b is None or b.empty:
            continue
        bb = b.set_index('batch_id').sort_index()
        prod = (t.groupby('batch_id')['duration'].sum().reindex(bb.index).fillna(0.0)
                if t is not None and not t.empty
                else pd.Series(0.0, index=bb.index))
        qd = bb['queue_depth'] if 'queue_depth' in bb else pd.Series(0.0, index=bb.index)
        out.append(dict(
            s=s,
            total_production_time=float(prod.sum()),
            mean_completion_rate=float(bb['completion_rate'].mean()),
            mean_queue_depth=float(qd.mean()),
            max_queue_depth=float(qd.max()),
        ))
    return out


def _dot_chart(rows, xlabel, title, subtitle, path, view, *, strategies,
               base_key=None, zero_line=False):
    """One sorted horizontal dot plot: rows = [(strategy, value)], best already first."""
    n = len(rows)
    legend_labels = ['FIFO baseline (zero line)'] if zero_line else ['FIFO arm']
    ch = chartkit.make(panels=1, panel_w=5.8,
                       panel_h=chartkit.height_for_categories(n),
                       legend='gutter', legend_labels=legend_labels)
    ax = ch.ax
    ypos = np.arange(n)
    for y, (s, v) in zip(ypos, rows):
        is_base = base_key is not None and s['key'] == base_key
        ax.plot([v], [y], 'o', ms=7,
                color=chartkit.strategy_color(s, strategies),
                markeredgecolor=(chartkit.BASELINE_STYLE['color'] if is_base
                                 else 'white'),
                markeredgewidth=1.4 if is_base else 0.5, zorder=3)
    ylabels = [_stitle(s) for s, _v in rows]
    ax.set_yticks(ypos)
    ax.set_yticklabels(ylabels, fontsize=6)
    ax.margins(y=max(0.04, 0.5 / max(1, n)))
    ax.invert_yaxis()
    ax.grid(axis='x', alpha=0.3)
    ax.grid(axis='y', alpha=0.0)
    # widen the reserved left margin for the category labels (chartkit sizes the
    # grid from panel geometry and cannot see tick-label extents)
    need = 0.35 + 0.05 * max((len(t) for t in ylabels), default=0)
    ax.get_subplotspec().get_gridspec().update(
        left=min(2.6, need) / ch.fig.get_figwidth())
    handles = []
    if zero_line:
        chartkit.reference_line(ax, 0.0, orient='x')
        tag = chartkit.pct_axis(ax, better='right', axis='x')
        ax.set_xlabel(f'{xlabel} {tag}', fontsize=8)
        handles.append(chartkit.baseline_handle('FIFO baseline (zero line)'))
    else:
        ax.set_xlabel(xlabel, fontsize=8)
        handles.append(Line2D([], [], marker='o', ls='', ms=7, color='#bbbbbb',
                              markeredgecolor=chartkit.BASELINE_STYLE['color'],
                              markeredgewidth=1.4, label='FIFO arm'))
    ch.legend(handles=handles)
    ch.title(title, subtitle)
    ch.save(path, view=view)


@evaluation(key='headline.rollup', label='Per-run rollup dot plots',
            scope='per_strategy', needs=('batch', 'task'),
            family='headline', shape='ranked',
            quantities=('production_time', 'throughput'))
def render(ctx, params):
    summ = _summaries(ctx)
    if not summ:
        return
    base_key = ctx.base['key']
    base = next((r for r in summ if r['s']['key'] == base_key), None)
    out = io.out_dir(ctx)

    if base is not None:
        # (a) total production time — % improvement vs FIFO, best on top
        rows = [(r['s'], chartkit.improvement_pct(
                    r['total_production_time'], base['total_production_time'],
                    lower_is_better=True))
                for r in summ if r['s']['key'] != base_key]
        rows.sort(key=lambda t: (t[1] if np.isfinite(t[1]) else -np.inf),
                  reverse=True)
        if rows:
            _dot_chart(rows, 'total production time improvement vs FIFO',
                       'Total production time vs FIFO',
                       'Σ task time over all batches — FIFO is the zero line',
                       os.path.join(out, 'percent_production_time_dots.png'),
                       'percent', strategies=ctx.strategies, zero_line=True)

        # (b) mean completion rate — % improvement vs FIFO
        rows = [(r['s'], chartkit.improvement_pct(
                    r['mean_completion_rate'], base['mean_completion_rate'],
                    lower_is_better=False))
                for r in summ if r['s']['key'] != base_key]
        rows.sort(key=lambda t: (t[1] if np.isfinite(t[1]) else -np.inf),
                  reverse=True)
        if rows:
            _dot_chart(rows, 'mean completion rate improvement vs FIFO',
                       'Completion rate vs FIFO',
                       'per-batch items ÷ batch makespan, meaned — FIFO is the zero line',
                       os.path.join(out, 'percent_completion_rate_dots.png'),
                       'percent', strategies=ctx.strategies, zero_line=True)

    # (c) mean put-away queue depth — absolute, only when a queue ever existed
    if float(max((r['max_queue_depth'] for r in summ), default=0.0) or 0.0) > 0:
        rows = [(r['s'], r['mean_queue_depth']) for r in summ]
        rows.sort(key=lambda t: (t[1] if np.isfinite(t[1]) else np.inf))
        _dot_chart(rows, 'mean put-away queue depth (units)',
                   'Put-away queue depth per arm',
                   'the honesty metric — deferred placement carries a standing queue',
                   os.path.join(out, 'absolute_queue_depth_dots.png'),
                   'absolute', strategies=ctx.strategies, base_key=base_key)
