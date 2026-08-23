"""headline.all_arms — every arm's % improvement vs the FIFO baseline, at a glance.

Two horizontal-bar panels (throughput improvement, duration improvement) that SHARE
one y category order — arms sorted by duration improvement, best on top — so a reader
scans one ranking, not two.  The panels also share the x scale when the magnitudes
allow it; when one panel's swings would flatten the other, each keeps its own scale
and says so out loud.  Sign is carried by the zero reference line and the sort, not
by red/green bars: bars wear their arm's suite color.

NO LEGEND, DELIBERATELY.  Every arm is already named on the y axis, so a colour key is
one row of restatement per arm — a third of the canvas spent telling the reader what
the tick label beside the bar just told them.  The single thing the axis cannot say is
which row IS the reference, and that is marked IN PLACE on the FIFO row (dotted rule,
bold tick label, a note at the zero line).  The gutter those rows used to occupy goes
back to the panels.

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
            family='headline', shape='ranked',
            quantities=('throughput', 'makespan'),
            views_suppressed=(
                ('absolute', 'this is the 34-bar wall headline/rollup.py retired: at '
                             'full arm count the absolute values differ by a few '
                             'percent, so every bar is the same length and the '
                             'figure carries no information the rollup does not. '
                             'The absolute level of these two quantities is '
                             'headline.rollup\'s job, at top-N.'),))
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
    # legend='none': the y axis is the colour key.  The freed gutter widens the panels
    # rather than shrinking the figure — 34 bars need the horizontal room more than a
    # restatement of their names does.
    ch = chartkit.make(panels=2, ncols=2, panel_w=5.5,
                       panel_h=chartkit.height_for_categories(n),
                       legend='none')
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
        chartkit.reference_line(ax, 0.0, orient='x')
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

    # Mark the reference IN PLACE.  Its bars are zero-length by construction, so the row
    # is otherwise empty and a legend row would be the only thing identifying it.
    b_i = next((i for i, r in enumerate(rows) if r['s']['key'] == base_key), None)
    if b_i is not None:
        for ax in ch.axes:
            ax.axhline(b_i, color=chartkit.BASELINE_STYLE['color'], lw=1.0, ls=':',
                       alpha=0.55, zorder=2)
        ax0 = ch.axes[0]
        x0, x1 = ax0.get_xlim()
        right = (x1 - 0.0) >= (0.0 - x0)     # annotate into whichever side has room
        ax0.annotate('FIFO baseline — zero by definition', xy=(0.0, b_i),
                     xytext=(6 if right else -6, 0), textcoords='offset points',
                     ha='left' if right else 'right', va='center', fontsize=6.5,
                     color=chartkit.BASELINE_STYLE['color'], zorder=4,
                     bbox=dict(facecolor='white', edgecolor='none', alpha=0.85, pad=1.0))
        tick = ax0.get_yticklabels()[b_i]
        tick.set_color(chartkit.BASELINE_STYLE['color'])
        tick.set_fontweight('bold')
    ch.title('All arms vs FIFO baseline',
             'sorted by duration improvement — best on top; the FIFO row is the zero line')
    ch.save(os.path.join(io.out_dir(ctx), 'percent_all_arms_vs_baseline.png'),
            view='percent')
