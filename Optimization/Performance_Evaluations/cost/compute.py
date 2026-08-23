"""cost.compute — three views of what a placement rule costs to RUN.

THE QUESTION THIS ANSWERS, and who asked it.  A WMS engineer reviewing the published
experiment could not tell whether the winning rules are cheap enough to run: the site
described the `Map` family's offline address-map build without a number, and nothing said
what per-arrival scoring costs either.  Both were measured all along — the per-arrival
cost sat in the run's runtime DB, unread by anything but a set of absolute-second bar
charts nobody staged; the offline build was outside every clock until the setup span was
added.

WHY THE PRIMARY VIEW IS A MULTIPLE.  Absolute seconds do not survive leaving the machine
that produced them: they are contended against the sweep's worker pool, they scale with
catalogue size, and on a 210-second bar the difference between the best and second-best
rule is invisible.  The FIFO floor is the right denominator because FIFO does the same
reorder bookkeeping and then picks a slot at random — so the RATIO isolates the scoring.

THE ONE THING NOT TO DO.  `precomp_s` is not a slice of `total_s` (the batch loop's clock
starts after setup), so it must never be stacked onto the section breakdown.  Every view
here keeps the two spans visibly separate; `runtime_metrics.OUTSIDE_TOTAL` is the
declaration that makes that checkable rather than remembered.
"""
import os

import matplotlib.pyplot as plt
import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.cost.rollup import BASE_RULE, cost_rows
from Optimization.persistence.runtime_metrics import SECTIONS

#: Rules whose scoring cost is a ceiling rather than a proposal — deliberate worst-case
#: controls.  Drawn, because the bracket is the point, but hatched so nobody quotes one.
_CONTROL_HATCH = '///'


def _by_channel(rows):
    return [(ch, [r for r in rows if r['channel'] == ch])
            for ch in sorted({r['channel'] for r in rows})]


def _rule_order(rows):
    """One rule order for every panel of the family, cheapest-first by the FIFO multiple.

    Sorting each panel by its own values scrambles cross-channel reading: a reader
    following one rule has to hunt for it again in the next panel.  The multiple is the
    right key because it is the family's primary view and it is unit-free.
    """
    seen: dict = {}
    for r in rows:
        x = r.get('x_reord_vs_fifo')
        if x is not None:
            seen.setdefault(r['rule'], []).append(x)
    # The MEAN across channels, not the min: the channels genuinely disagree about a few
    # rules, and taking either channel's own order (or the extreme) leaves the other panel
    # visibly out of sequence.  The mean keeps both close to monotone, and every bar carries
    # its value so a reader never has to infer one from position.
    return [k for k, _v in sorted(seen.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))]


def _in_order(sub, order, key):
    by = {r['rule']: r for r in sub if r.get(key) is not None}
    return [by[k] for k in order if k in by]


def _bar_style(r):
    return dict(color=('#b0b7c3' if r['control'] else '#4c78a8'),
                hatch=(_CONTROL_HATCH if r['control'] else None),
                edgecolor='#33383f', linewidth=0.6)


def _percent(ctx, rows, out):
    """Scoring cost as a multiple of the do-nothing floor — the view that travels."""
    panels = _by_channel(rows)
    if not panels:
        return
    order = _rule_order(rows)
    n = max(len(sub) for _ch, sub in panels)
    ch = chartkit.make(panels=len(panels), panel_w=5.4,
                       panel_h=chartkit.height_for_categories(n), legend='none',
                       panel_titles=True)
    axes = ch.fig.axes
    for ax, (chan, sub) in zip(axes, panels):
        got = _in_order(sub, order, 'x_reord_vs_fifo')
        if not got:
            continue
        y = np.arange(len(got))
        ax.barh(y, [r['x_reord_vs_fifo'] for r in got], **_bar_style(got[0]))
        for i, r in enumerate(got):
            ax.barh(i, r['x_reord_vs_fifo'], **_bar_style(r))
        ax.set_yticks(y)
        ax.set_yticklabels([r['label'] for r in got], fontsize=8)
        # 1.0 is the floor, not zero: a bar at 1.0 costs exactly what doing nothing costs.
        ax.axvline(1.0, **{**chartkit.BASELINE_STYLE, 'lw': 1.4})
        ax.text(1.0, len(got) - 0.3, f' {BASE_RULE} = 1.0x', fontsize=7,
                color=chartkit.BASELINE_STYLE['color'], va='top')
        for i, r in enumerate(got):
            ax.text(r['x_reord_vs_fifo'], i, f"  {r['x_reord_vs_fifo']:.2f}x",
                    va='center', fontsize=7)
        ax.set_xlabel('placement scoring cost, multiple of the do-nothing rule')
        ax.set_title(chan, fontsize=10)
        ax.set_xlim(0, max(r['x_reord_vs_fifo'] for r in got) * 1.18)
    ch.title('What each placement rule costs to run',
             subtitle=('real CPU wall time, not modeled warehouse labor - '
                       f'hatched = deliberate worst-case control - {ctx.run_workers() or "?"}'
                       ' workers, so contended'))
    ch.save(os.path.join(out, 'percent_cost_vs_fifo.png'), view='percent')


def _absolute(ctx, rows, out):
    """The cost in units a WMS reader can size against, INCLUDING the offline build.

    Three panels per channel, because the family's charter asks two questions and the old
    two-panel form answered one and a half.  Left: absolute milliseconds of placement per
    arriving unit — absolute, not the delta over FIFO, which is a different quantity and
    was previously drawn in an `absolute` view (FIFO showed a real per-wave bar beside a
    zero-width per-unit one, in the same figure).  Middle: seconds per wave.  Right: the
    OFFLINE build, the one span no view carried at all despite being the reason half this
    family exists — a rule with a bar there is a job somebody has to schedule.

    Every bar carries its value, because the interesting rules are the cheap ones and a
    linear axis scaled to the most expensive rule compresses them into a sliver.
    """
    panels = _by_channel(rows)
    order = _rule_order(rows)
    cols = (('reord_ms_per_unit', 'ms of placement per unit put away', '{:.3f}'),
            ('reord_s_per_wave', 'seconds of placement per wave', '{:.2f}'),
            ('precomp_s', 'seconds of OFFLINE build, once per plan', '{:.0f}'))
    ch = chartkit.make(panels=len(cols) * len(panels), ncols=len(cols), panel_w=4.2,
                       panel_h=chartkit.height_for_categories(
                           max((len(s) for _c, s in panels), default=4)),
                       legend='none', panel_titles=True)
    axes, drew = ch.fig.axes, False
    for i, (chan, sub) in enumerate(panels):
        for j, (col, xlabel, fmt) in enumerate(cols):
            ax = axes[len(cols) * i + j]
            got = _in_order(sub, order, col)
            if not got:
                ax.set_axis_off()
                continue
            drew = True
            for k, r in enumerate(got):
                ax.barh(k, r[col], **_bar_style(r))
                ax.text(r[col], k, '  ' + fmt.format(r[col]), va='center', fontsize=6.5)
            ax.set_yticks(np.arange(len(got)))
            ax.set_yticklabels([r['label'] for r in got], fontsize=7)
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_title(chan, fontsize=9.5)
            hi = max(r[col] for r in got)
            ax.set_xlim(0, hi * 1.28 if hi else 1.0)
    if not drew:
        return ch.abandon()
    units = {r['channel']: r['units_per_wave'] for r in rows if r['units_per_wave']}
    ch.title('Placement cost in operational units',
             subtitle=('absolute, not against the floor - the offline build is a SEPARATE '
                       'span, not part of the per-wave column - '
                       + ' - '.join(f'{c}: {u:,.0f} units/wave' for c, u in units.items())))
    ch.save(os.path.join(out, 'absolute_compute_cost.png'), view='absolute')


def _delta(ctx, rows, out):
    """Where the extra seconds go, as a DIFFERENCE from the floor rather than a stack.

    A stacked absolute breakdown is the natural chart here and the wrong one twice over:
    every rule's stack is dominated by the sections it shares with FIFO, and the setup
    span is not in the total the stack adds up to.  Differences against the floor show
    only what the rule changed, and the setup span gets its own marker beside them.
    """
    rt_rows = ctx.runtime_rows()
    if not rt_rows:
        return
    import statistics as st
    sects = [c for c, _lbl in SECTIONS]
    labels = dict(SECTIONS)
    panels = _by_channel(rows)
    ch = chartkit.make(panels=len(panels), panel_w=5.6,
                       panel_h=chartkit.height_for_categories(
                           max((len(s) for _c, s in panels), default=4)),
                       legend='gutter', legend_labels=[labels[c] for c in sects],
                       panel_titles=True)
    drew = False
    for ax, (chan, sub) in zip(ch.fig.axes, panels):
        per = {}
        for r in rt_rows:
            if r.get('channel') != chan:
                continue
            per.setdefault(r.get('assignment'), []).append(r)
        if BASE_RULE not in per:
            continue
        base = {c: st.median([float(x.get(c) or 0.0) for x in per[BASE_RULE]])
                for c in sects}
        order = [r['rule'] for r in _in_order(sub, _rule_order(rows),
                                              'x_reord_vs_fifo')]
        y = np.arange(len(order))
        left_pos = np.zeros(len(order))
        left_neg = np.zeros(len(order))
        for si, col in enumerate(sects):
            vals = np.array([st.median([float(x.get(col) or 0.0) for x in per[rule]])
                             - base[col] for rule in order])
            starts = np.where(vals >= 0, left_pos, left_neg + vals)
            ax.barh(y, vals, left=starts, color=plt.cm.tab10.colors[si % 10],
                    edgecolor='none', label=labels[col])
            left_pos = left_pos + np.clip(vals, 0, None)
            left_neg = left_neg + np.clip(vals, None, 0)
            drew = True
        chartkit.reference_line(ax, 0.0, orient='x')
        ax.set_yticks(y)
        # Controls are hatched in the other two views; this one colours by SECTION, so the
        # marker has to move to the label or the same family says two things with one cue.
        lab = {r['rule']: (r['label'] + (' *' if r['control'] else '')) for r in sub}
        ax.set_yticklabels([lab[k] for k in order], fontsize=7.5)
        ax.set_xlabel(f'seconds more (or less) than {BASE_RULE}, by section')
        ax.set_title(chan, fontsize=10)
    if not drew:
        return ch.abandon()
    ch.legend(title='section')
    ch.title('Where a rule spends its extra compute',
             subtitle=('difference from the do-nothing rule, per loop section - * marks a '
                       'worst-case control - the offline build is NOT here: it sits outside '
                       'this total (see the absolute view)'))
    ch.save(os.path.join(out, 'delta_cost_sections.png'), view='delta')


@evaluation(key='cost.compute', label='What each placement rule costs to run',
            scope='run', needs=('runtime',),
            family='cost', shape='ranked',
            quantities=('reord_ms_per_unit', 'scoring_ms_per_unit',
                        'x_reord_vs_fifo'))
def render(ctx, params):
    rows = cost_rows(ctx)
    if not rows:
        return
    out = io.out_dir(ctx)
    _percent(ctx, rows, out)
    _absolute(ctx, rows, out)
    _delta(ctx, rows, out)
