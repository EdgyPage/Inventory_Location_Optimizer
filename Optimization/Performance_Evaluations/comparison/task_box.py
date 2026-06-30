"""compare.task_box — box plot of steady-state task durations, one box per strategy
(color = assignment, mean markers).  Writes compare/breakdown/task_duration_by_strategy.png.

Param: win (steady-state tail width in batches)."""
import os

import numpy as np
from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _assign_color_map, _WIN


def _task_box(strategies, df_t, title, path, win=_WIN):
    """Box plot of steady-state task durations, one box per strategy (color =
    assignment), with mean markers — task time per strategy at a glance."""
    avail, data = [], []
    for s in strategies:
        df = df_t[s['key']]
        if df.empty:
            continue
        d = df[df['batch_id'] >= df['batch_id'].max() - win]['duration'].values
        if len(d):
            avail.append(s)
            data.append(d)
    if not avail:
        return
    acmap = _assign_color_map(avail)
    xs = np.arange(1, len(avail) + 1)
    fig, ax = plt.subplots(figsize=(max(10.0, len(avail) * 0.55), 6))
    bp = ax.boxplot(data, showfliers=False, patch_artist=True, widths=0.6,
                    medianprops=dict(color='black'))
    for patch, s in zip(bp['boxes'], avail):
        patch.set_facecolor(acmap[s['assignment']])
        patch.set_alpha(0.8)
    ax.plot(xs, [float(np.mean(d)) for d in data], 'D', color='black', ms=4, label='mean')
    ax.set_xticks(xs)
    ax.set_xticklabels([_stitle(s) for s in avail], rotation=90, fontsize=6)
    ax.set_ylabel('task duration (steady state)')
    ax.grid(axis='y', alpha=0.3)
    ax.set_title(title, fontsize=12, fontweight='bold')
    handles = [Line2D([], [], color=acmap[a], lw=6, label=a) for a in sorted(acmap)]
    handles.append(Line2D([], [], marker='D', color='black', ls='', label='mean'))
    ax.legend(handles=handles, fontsize=7, ncol=2)
    plt.tight_layout()
    _save_close(fig, path)


@evaluation(key='compare.task_box', label='Steady-state task duration by strategy',
            scope='config', needs=('task',), out_subdir='compare/breakdown',
            defaults={'win': _WIN})
def render(ctx, params):
    out = os.path.join(ctx.run_dir, 'compare', 'breakdown')
    os.makedirs(out, exist_ok=True)
    _task_box(ctx.strategies, ctx.task_frames(),
              f'Steady-state task duration by strategy  [{ctx.title}]',
              os.path.join(out, 'task_duration_by_strategy.png'),
              win=int(params.get('win', _WIN)))
