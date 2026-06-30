"""breakdown.travel_handling — stacked travel-vs-handling picker-time bar per strategy,
annotated with travel %.  Consumes ctx.breakdown() (picker-event decomposition, memoized
and shared with the stats suite).  Writes compare/breakdown/task_time_breakdown.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close


def _task_time_breakdown_plot(strategies, th, title, path):
    """Stacked travel-vs-handling bar per strategy, annotated with travel %."""
    keys = [s['key'] for s in strategies if s['key'] in th]
    if not keys:
        return
    travel   = np.array([th[k][0] for k in keys], float)
    handling = np.array([th[k][1] for k in keys], float)
    idx = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(keys)), 5))
    ax.bar(idx, travel,   label='travel',   color='#4c72b0')
    ax.bar(idx, handling, bottom=travel, label='handling', color='#dd8452')
    tot = travel + handling
    for i in range(len(keys)):
        if tot[i] > 0:
            ax.text(i, tot[i], f'{travel[i] / tot[i] * 100:.0f}% trv',
                    ha='center', va='bottom', fontsize=7)
    ax.set_xticks(idx)
    ax.set_xticklabels([k[:-6] if k.endswith('_norsl') else k for k in keys],
                       rotation=40, ha='right', fontsize=8)
    ax.set_ylabel('Σ picker time (steady-state sample)')
    ax.grid(axis='y', alpha=0.3)
    ax.legend()
    ax.set_title(title, fontsize=12, fontweight='bold')
    _save_close(fig, path)


@evaluation(key='breakdown.travel_handling', label='Task time: travel vs handling',
            scope='config', needs=('breakdown',), out_subdir='compare/breakdown')
def render(ctx, params):
    out = os.path.join(ctx.run_dir, 'compare', 'breakdown')
    os.makedirs(out, exist_ok=True)
    _task_time_breakdown_plot(
        ctx.strategies, ctx.breakdown(),
        f'Task time: travel vs handling  [{ctx.title}]',
        os.path.join(out, 'task_time_breakdown.png'))
