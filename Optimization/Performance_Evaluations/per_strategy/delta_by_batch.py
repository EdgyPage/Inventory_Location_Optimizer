"""per_strategy.delta_by_batch — one panel per strategy: per-batch production-time Δ% vs the
FIFO baseline (green ≥0 / red <0, matching compare.delta_bars).  Mirror of
compare.delta_by_batch.  Writes per_strategy/grid_prodtime_delta_by_batch.png."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _grid, _stitle
from Performance_Evaluations.common.series import _prodtime_delta


@evaluation(key='per_strategy.delta_by_batch',
            label='Per-strategy per-batch production-time Δ vs baseline (bars)',
            scope='per_strategy', needs=('series',), out_subdir='per_strategy')
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    strategies = ctx.strategies
    fig, axes = _grid(len(strategies))
    fig.suptitle(f'Per-batch production-time Δ% vs FIFO  (↑ better)  [{ctx.title}]',
                 fontsize=13, fontweight='bold')
    for ax, s in zip(axes, strategies):
        ax.axhline(0, color='k', lw=0.6)
        batches, pb, _cum = _prodtime_delta(S, s['key'], base['key'])
        if batches.size:
            ax.bar(batches, pb, width=1.0,
                   color=['#55a868' if v >= 0 else '#c44e52' for v in pb])
        ax.set_title(_stitle(s), fontsize=7)
        ax.tick_params(labelsize=6)
        ax.grid(axis='y', alpha=0.3)
    if axes:
        axes[0].set_ylabel('% vs FIFO', fontsize=8)
    plt.tight_layout(rect=(0, 0, 1, 0.98))
    _save_close(fig, os.path.join(ctx.run_dir, 'per_strategy',
                                  'grid_prodtime_delta_by_batch.png'))
