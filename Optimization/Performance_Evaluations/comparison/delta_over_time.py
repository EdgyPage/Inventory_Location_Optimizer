"""compare.delta_over_time — production-time (Σ task time / batch) improvement vs the FIFO
baseline for the top-N strategies, as two line views: cumulative % saved through batch N, and
the per-batch Δ% trend.  Additive companion to compare.top_metric; under compare/top.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot')."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _LINESTYLES, _TOP_DIMS, legend_right
from Performance_Evaluations.common.series import _select_top, _prodtime_delta
from Performance_Evaluations.comparison import top_tag


def _delta_line(strategies, S, baseline, top_n, top_by, which, title, ylabel, path):
    selected, gof = _select_top(strategies, S, top_n, top_by)
    gstyle = {g: _LINESTYLES[i % len(_LINESTYLES)] for i, g in enumerate(sorted(set(gof.values())))}
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.axhline(0, color='grey', lw=1.0, ls='--', label='baseline (FIFO)')
    drew = False
    for s in selected:
        if s['key'] == baseline['key']:                 # its Δ vs itself is 0
            continue
        batches, pb, cum = _prodtime_delta(S, s['key'], baseline['key'])
        if batches.size == 0:
            continue
        y = cum if which == 'cum' else pb
        ls = gstyle.get(gof.get(s['key']), '-')
        ax.plot(batches, y, color=s['color'], lw=1.8, ls=ls, label=_stitle(s))
        drew = True
    ax.set_xlabel('batch')
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    ax.set_title(title + sub, fontsize=12, fontweight='bold')
    if drew:
        legend_right(ax, fontsize=8)
    plt.tight_layout()
    _save_close(fig, path)


@evaluation(key='compare.delta_over_time',
            label='Production-time improvement vs baseline, over time (top-N)',
            scope='config', needs=('series',), out_subdir='compare/top',
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    S = ctx.series()
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'
    out = os.path.join(ctx.run_dir, 'compare', 'top')
    os.makedirs(out, exist_ok=True)
    tag = top_tag(top_n, top_by)
    _delta_line(ctx.strategies, S, ctx.base, top_n, top_by, 'cum',
                f'Cumulative production-time saved vs FIFO  [{ctx.title}]',
                '% of baseline task-time saved (↑ better)',
                os.path.join(out, f'{tag}_prodtime_cum_improvement.png'))
    _delta_line(ctx.strategies, S, ctx.base, top_n, top_by, 'trend',
                f'Per-batch production-time Δ vs FIFO  [{ctx.title}]',
                '% improvement vs baseline (↑ better)',
                os.path.join(out, f'{tag}_prodtime_delta_trend.png'))
