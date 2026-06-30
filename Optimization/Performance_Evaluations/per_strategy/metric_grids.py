"""per_strategy.metric_grids — five rectangular grids (one panel per strategy): batch
duration, layout efficiency, churn, task-duration distribution, task-duration-over-time.
Writes grid_*.png under per_strategy/."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _grid, _stitle
from Performance_Evaluations.per_strategy.panels import (
    panel_duration, panel_eff, panel_churn, panel_taskdur, panel_task_overtime,
)


@evaluation(key='per_strategy.metric_grids', label='Per-strategy metric grids',
            scope='per_strategy', needs=('batch', 'task', 'series'), out_subdir='per_strategy')
def render(ctx, params):
    strategies = ctx.strategies
    n = len(strategies)
    optimal = ctx.optimal
    ps_dir = os.path.join(ctx.run_dir, 'per_strategy')

    def _metric_grid(fname, title, panel, ylabel, legend=False):
        fig, axes = _grid(n)
        fig.suptitle(f'{title}  [{ctx.title}]', fontsize=13, fontweight='bold')
        for ax, s in zip(axes, strategies):
            panel(ctx, ax, s)
            ax.set_title(_stitle(s), fontsize=7)
            ax.tick_params(labelsize=6)
            ax.grid(alpha=0.3)
        if axes:
            axes[0].set_ylabel(ylabel, fontsize=8)
            if legend:
                axes[0].legend(fontsize=6, loc='upper right')
        plt.tight_layout(rect=(0, 0, 1, 0.98))
        _save_close(fig, os.path.join(ps_dir, fname))

    _metric_grid('grid_batch_duration.png', 'Batch duration (rolling mean)', panel_duration, 'sim time')
    _metric_grid('grid_sigma_fd.png',
                 'Layout efficiency: optimal / realised Sigma f*D (%)' if optimal > 0 else 'Sigma f*D (lower better)',
                 panel_eff, '% of optimal' if optimal > 0 else 'Sigma f*D')
    _metric_grid('grid_churn.png', 'Inventory churn (% of bins moved / batch)', panel_churn, '% / batch')
    _metric_grid('grid_task_duration.png', 'Task (aisle) duration distribution (mean + median)',
                 panel_taskdur, 'count')
    _metric_grid('grid_task_over_time.png', 'Task duration over time (median, mean, IQR)',
                 panel_task_overtime, 'task duration', legend=True)
