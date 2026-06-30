"""per_strategy.delta_over_time — one panel per strategy: cumulative % production-time saved
(bold) + per-batch Δ% trend (light) vs the FIFO baseline.  Mirror of compare.delta_over_time.
Writes per_strategy/grid_prodtime_delta_over_time.png."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _grid, _stitle
from Performance_Evaluations.common.series import _prodtime_delta


@evaluation(key='per_strategy.delta_over_time',
            label='Per-strategy production-time Δ vs baseline, over time',
            scope='per_strategy', needs=('series',), out_subdir='per_strategy')
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    strategies = ctx.strategies
    fig, axes = _grid(len(strategies))
    fig.suptitle(f'Production-time improvement vs FIFO  (bold=cumulative, light=per-batch)  '
                 f'[{ctx.title}]', fontsize=13, fontweight='bold')
    for ax, s in zip(axes, strategies):
        ax.axhline(0, color='grey', lw=0.8, ls='--')
        batches, pb, cum = _prodtime_delta(S, s['key'], base['key'])
        if batches.size:
            ax.plot(batches, pb, color=s['color'], lw=0.9, alpha=0.4)
            ax.plot(batches, cum, color=s['color'], lw=1.8)
        ax.set_title(_stitle(s), fontsize=7)
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.3)
    if axes:
        axes[0].set_ylabel('% vs FIFO (↑ better)', fontsize=8)
    plt.tight_layout(rect=(0, 0, 1, 0.98))
    _save_close(fig, os.path.join(ctx.run_dir, 'per_strategy',
                                  'grid_prodtime_delta_over_time.png'))
