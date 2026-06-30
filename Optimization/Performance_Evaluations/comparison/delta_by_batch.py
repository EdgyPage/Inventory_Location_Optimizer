"""compare.delta_by_batch — per-batch production-time improvement (%) vs the FIFO baseline,
grouped bars for the top-N strategies.  Under compare/top.  (Dense at large top_n by nature;
per_strategy.delta_by_batch is the clean one-panel-per-strategy read.)

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot')."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _TOP_DIMS, legend_right
from Performance_Evaluations.common.series import _select_top, _prodtime_delta
from Performance_Evaluations.comparison import top_tag


@evaluation(key='compare.delta_by_batch',
            label='Per-batch production-time Δ vs baseline (bars, top-N)',
            scope='config', needs=('series',), out_subdir='compare/top',
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    S = ctx.series()
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'
    selected, _ = _select_top(ctx.strategies, S, top_n, top_by)
    series = []
    for s in selected:
        if s['key'] == ctx.base['key']:
            continue
        batches, pb, _cum = _prodtime_delta(S, s['key'], ctx.base['key'])
        if batches.size:
            series.append((s, batches, pb))
    if not series:
        return
    out = os.path.join(ctx.run_dir, 'compare', 'top')
    os.makedirs(out, exist_ok=True)
    n = len(series)
    width = 0.8 / n
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.axhline(0, color='k', lw=0.8)
    for i, (s, batches, pb) in enumerate(series):
        ax.bar(batches + (i - (n - 1) / 2) * width, pb, width=width,
               color=s['color'], label=_stitle(s))
    ax.set_xlabel('batch')
    ax.set_ylabel('% improvement vs baseline (↑ better)')
    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    ax.set_title(f'Per-batch production-time Δ vs FIFO{sub}  [{ctx.title}]',
                 fontsize=12, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)
    legend_right(ax, fontsize=8)
    plt.tight_layout()
    _save_close(fig, os.path.join(out, f'{top_tag(top_n, top_by)}_prodtime_delta_by_batch.png'))
