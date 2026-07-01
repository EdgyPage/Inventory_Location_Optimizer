"""compare.overlay — all strategies overlaid on one axis per over-time metric: color =
assignment function, linestyle = (initial, reslot).  One PNG per metric under compare/overlay/."""
import os

from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _assign_color_map, _ir_style_map, legend_right
from Performance_Evaluations.comparison import overtime_metrics


def _overlay_metric(strategies, S, m, title, path):
    acmap = _assign_color_map(strategies)
    smap  = _ir_style_map(strategies)
    fig, ax = plt.subplots(figsize=(11, 6))
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        ax.plot(d[m['x']], d[m['y']], color=acmap[s['assignment']],
                ls=smap[(s['initial'], s['reslot'])], lw=1.1, alpha=0.9)
    ax.set_xlabel('batch')
    ax.set_ylabel(m['yl'])
    ax.grid(alpha=0.3)
    ax.set_title(title, fontsize=13, fontweight='bold')
    ch = [Line2D([], [], color=acmap[a], lw=2, label=a) for a in sorted(acmap)]
    sh = [Line2D([], [], color='k', ls=smap[ir], lw=1.5, label=f'{ir[0]}|{ir[1]}')
          for ir in sorted(smap)]
    leg1 = legend_right(ax, ch, anchor=(1.02, 1.0), fontsize=8, title='assignment')
    ax.add_artist(leg1)
    legend_right(ax, sh, anchor=(1.02, 0.45), fontsize=8, title='initial|reslot')
    plt.tight_layout()
    _save_close(fig, path)


@evaluation(key='compare.overlay', label='Over-time, all strategies overlaid',
            scope='config', needs=('series',), out_subdir='compare/overlay')
def render(ctx, params):
    S = ctx.series()
    out = os.path.join(ctx.run_dir, 'compare', 'overlay')
    os.makedirs(out, exist_ok=True)
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _overlay_metric(ctx.strategies, S, m, ttl, os.path.join(out, m['f'] + '.png'))
