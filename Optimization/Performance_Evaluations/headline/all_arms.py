"""headline.all_arms — every arm's % improvement vs the FIFO baseline, at a glance.

Two horizontal-bar panels (throughput improvement, duration improvement) that SHARE
one y category order — arms sorted by duration improvement, best on top — so a reader
scans one ranking, not two.  The panels also share the x scale when the magnitudes
allow it; when one panel's swings would flatten the other, each keeps its own scale
and says so out loud.  Sign is carried by the zero reference line and the sort, not
by red/green bars: bars wear their arm's suite color, and the FIFO row (always at 0)
is edge-marked in the baseline style.

Ported data logic: the retired delta-bars chart — throughput Δ% off the steady-state
throughput scalar and duration improvement % off the steady-state batch-makespan
scalar, both against strategies[0] (the FIFO arm).
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle

#: share the x scale across the two panels only while the wider span is at most this
#: multiple of the narrower one — beyond that, forcing one scale flattens a panel.
_SHARE_RATIO = 3.0


@evaluation(key='headline.all_arms', label='All arms vs FIFO (% improvement panels)',
            scope='config', needs=('series',),
            family='headline', views=('percent',))
def render(ctx, params):
    S = ctx.series()
    baseline = ctx.base
    bd = S.get(baseline['key'])
    if bd is None:
        return
    avail = [s for s in ctx.strategies if S.get(s['key'])]
    if not avail:
        return

    rows = []
    for s in avail:
        d = S[s['key']]
        rows.append(dict(
            s=s,
            thr=chartkit.improvement_pct(d.get('ss_thr'), bd.get('ss_thr'),
                                         lower_is_better=False),
            dur=chartkit.improvement_pct(d.get('ss_dur'), bd.get('ss_dur'),
                                         lower_is_better=True)))
    # one shared category order: sorted by duration improvement, best on top
    rows.sort(key=lambda r: (r['dur'] if np.isfinite(r['dur']) else -np.inf),
              reverse=True)

    n = len(rows)
    labels = ['FIFO baseline (zero line)']
    ch = chartkit.make(panels=2, ncols=2, panel_w=4.6,
                       panel_h=chartkit.height_for_categories(n),
                       legend='gutter', legend_labels=labels)
    ypos = np.arange(n)
    base_key = baseline['key']
    panels = (('thr', 'throughput improvement vs FIFO'),
              ('dur', 'duration improvement vs FIFO'))
    for ax, (field, xlabel) in zip(ch.axes, panels):
        vals = [r[field] for r in rows]
        colors = [chartkit.strategy_color(r['s'], ctx.strategies) for r in rows]
        edges = [chartkit.BASELINE_STYLE['color'] if r['s']['key'] == base_key
                 else 'none' for r in rows]
        widths = [1.4 if r['s']['key'] == base_key else 0.0 for r in rows]
        ax.barh(ypos, vals, color=colors, edgecolor=edges, linewidth=widths,
                height=0.72)
        ax.axvline(0, color=chartkit.BASELINE_STYLE['color'], lw=1.4, zorder=3)
        ax.margins(y=0.03)
        ax.invert_yaxis()
        tag = chartkit.pct_axis(ax, better='right', axis='x')
        ax.set_xlabel(f'{xlabel} {tag}', fontsize=8)
        ax.grid(axis='x', alpha=0.3)
        ax.grid(axis='y', alpha=0.0)
    ylabels = [_stitle(r['s']) + (' (FIFO)' if r['s']['key'] == base_key else '')
               for r in rows]
    ch.axes[0].set_yticks(ypos)
    ch.axes[0].set_yticklabels(ylabels, fontsize=6)
    ch.axes[1].set_yticks(ypos)
    ch.axes[1].tick_params(labelleft=False)
    for ax in ch.axes:
        ax.tick_params(axis='x', labelsize=8)
    # widen the reserved left margin for the category labels (chartkit sizes the
    # grid from panel geometry and cannot see tick-label extents)
    need = 0.35 + 0.05 * max((len(t) for t in ylabels), default=0)
    ch.axes[0].get_subplotspec().get_gridspec().update(
        left=min(2.6, need) / ch.fig.get_figwidth())

    # share the x scale when the magnitudes allow it — else say so out loud
    spans = []
    for field in ('thr', 'dur'):
        v = np.asarray([r[field] for r in rows], float)
        v = v[np.isfinite(v)]
        spans.append((min(0.0, v.min()), max(0.0, v.max())) if v.size else (0.0, 0.0))
    widths_ = [hi - lo for lo, hi in spans]
    if min(widths_) > 0 and max(widths_) / min(widths_) <= _SHARE_RATIO:
        lo = min(s[0] for s in spans)
        hi = max(s[1] for s in spans)
        pad = 0.06 * ((hi - lo) or 1.0)
        for ax in ch.axes:
            ax.set_xlim(lo - pad, hi + pad)
    else:
        chartkit.annotate_unshared(ch.axes[1], axis='x')

    ch.legend(handles=[chartkit.baseline_handle('FIFO baseline (zero line)')])
    ch.title('All arms vs FIFO baseline',
             'sorted by duration improvement — best on top; color = arm')
    ch.save(os.path.join(io.out_dir(ctx), 'percent_all_arms_vs_baseline.png'),
            view='percent')
