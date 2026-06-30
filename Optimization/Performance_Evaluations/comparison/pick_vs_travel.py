"""compare.pick_vs_travel — horizontal stacked picking%/traveling% bar per strategy
(steady-state aggregate picker-time split).  Writes compare/breakdown/pick_vs_travel.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, legend_right


def _pick_travel_bars(strategies, S, title, path):
    avail = [s for s in strategies if S.get(s['key'])]
    ypos  = np.arange(len(avail))
    pk = [S[s['key']]['picking_pct']   for s in avail]
    tv = [S[s['key']]['traveling_pct'] for s in avail]
    fig, ax = plt.subplots(figsize=(10, max(6.0, len(avail) * 0.3)))
    ax.barh(ypos, pk, color='#4c72b0', label='picking %')
    ax.barh(ypos, tv, left=pk, color='#dd8452', label='traveling %')
    ax.set_yticks(ypos)
    ax.set_yticklabels([_stitle(s) for s in avail], fontsize=6)
    ax.invert_yaxis()
    ax.set_xlabel('% of aggregate picker-time')
    ax.grid(axis='x', alpha=0.3)
    legend_right(ax)
    ax.set_title(title, fontsize=12, fontweight='bold')
    plt.tight_layout()
    _save_close(fig, path)


@evaluation(key='compare.pick_vs_travel', label='Picking vs traveling split',
            scope='config', needs=('series',), out_subdir='compare/breakdown')
def render(ctx, params):
    out = os.path.join(ctx.run_dir, 'compare', 'breakdown')
    os.makedirs(out, exist_ok=True)
    _pick_travel_bars(ctx.strategies, ctx.series(),
                      f'Pick vs travel  [{ctx.title}]',
                      os.path.join(out, 'pick_vs_travel.png'))
