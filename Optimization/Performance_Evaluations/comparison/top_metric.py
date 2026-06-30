"""compare.top_metric — overlay only the top-N strategies (global, or top-N within each
value of a dimension) against the baseline, per over-time metric.  Under compare/top/.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot')."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _LINESTYLES, _TOP_DIMS
from Performance_Evaluations.common.series import _select_top
from Performance_Evaluations.comparison import overtime_metrics, top_tag


def _top_metric(strategies, S, top_n, m, title, baseline, path, top_by='global'):
    selected, gof = _select_top(strategies, S, top_n, top_by)
    # in grouped mode, linestyle encodes the group so the families are distinguishable
    gstyle = {g: _LINESTYLES[i % len(_LINESTYLES)] for i, g in enumerate(sorted(set(gof.values())))}
    fig, ax = plt.subplots(figsize=(11, 6))
    db = S.get(baseline['key'])
    if db is not None:
        ax.plot(db[m['x']], db[m['y']], color='grey', lw=1.3, ls='--',
                label=f"baseline · {_stitle(baseline)}")
    solo = len(selected) <= 3
    for s in selected:
        d = S.get(s['key'])
        if d is None:
            continue
        ls = gstyle.get(gof.get(s['key']), '-')
        ax.plot(d[m['x']], d[m['y']], color=s['color'], lw=1.8, ls=ls, label=_stitle(s))
        if m['blo'] and solo and d.get(m['blo']) is not None:
            ax.fill_between(d[m['x']], d[m['blo']], d[m['bhi']], color=s['color'], alpha=0.12)
    ax.set_xlabel('batch')
    ax.set_ylabel(m['yl'])
    ax.grid(alpha=0.3)
    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    ax.set_title(title + sub, fontsize=12, fontweight='bold')
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_close(fig, path)


@evaluation(key='compare.top_metric', label='Top-N strategies vs baseline, over time',
            scope='config', needs=('series',), out_subdir='compare/top',
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    S = ctx.series()
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'
    out = os.path.join(ctx.run_dir, 'compare', 'top')
    os.makedirs(out, exist_ok=True)
    tag = top_tag(top_n, top_by)
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _top_metric(ctx.strategies, S, top_n, m, ttl, ctx.base,
                    os.path.join(out, f"{tag}_{m['f']}.png"), top_by=top_by)
