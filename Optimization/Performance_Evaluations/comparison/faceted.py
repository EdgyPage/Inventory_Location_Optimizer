"""compare.faceted — over-time trajectories faceted by (initial × reslot), colored by
assignment function.  One PNG per over-time metric under compare/faceted/."""
import os

from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _assign_color_map
from Performance_Evaluations.comparison import overtime_metrics


def _facet_metric(strategies, S, m, title, path):
    inits = sorted({s['initial'] for s in strategies})
    resl  = sorted({s['reslot'] for s in strategies})
    acmap = _assign_color_map(strategies)
    nrow, ncol = max(1, len(inits)), max(1, len(resl))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.0 * ncol, 3.4 * nrow),
                             squeeze=False, sharex=True)
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        ax = axes[inits.index(s['initial'])][resl.index(s['reslot'])]
        ax.plot(d[m['x']], d[m['y']], color=acmap[s['assignment']], lw=1.3)
        if m['blo'] and d.get(m['blo']) is not None:
            ax.fill_between(d[m['x']], d[m['blo']], d[m['bhi']],
                            color=acmap[s['assignment']], alpha=0.12)
    for r, ini in enumerate(inits):
        for c, rs in enumerate(resl):
            ax = axes[r][c]
            ax.set_title(f'{ini} | {rs}', fontsize=9)
            ax.grid(alpha=0.3)
            if r == nrow - 1:
                ax.set_xlabel('batch')
            if c == 0:
                ax.set_ylabel(m['yl'], fontsize=8)
    handles = [Line2D([], [], color=acmap[a], lw=2, label=a) for a in sorted(acmap)]
    axes[0][ncol - 1].legend(handles=handles, fontsize=7, title='assignment')
    fig.suptitle(title, fontsize=13, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.97))
    _save_close(fig, path)


@evaluation(key='compare.faceted', label='Over-time, faceted by initial×reslot',
            scope='config', needs=('series',), out_subdir='compare/faceted')
def render(ctx, params):
    S = ctx.series()
    out = os.path.join(ctx.run_dir, 'compare', 'faceted')
    os.makedirs(out, exist_ok=True)
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _facet_metric(ctx.strategies, S, m, ttl, os.path.join(out, m['f'] + '.png'))
