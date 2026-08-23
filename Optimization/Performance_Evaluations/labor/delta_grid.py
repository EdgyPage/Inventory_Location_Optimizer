"""labor.delta_grid — the task-time delta vs FIFO, one small panel per arm.

The retired per-strategy pair drew two separate grids (a line grid and a bar grid) over
the SAME `series._prodtime_delta` numbers, wasted panels on the baseline's identically
flat delta-vs-itself facets, and — worst — let every facet autoscale independently, so a
±0.3% arm looked exactly like a ±2.8% arm.  This merge fixes all three:

  * one grid, one panel per NON-baseline arm — bold cumulative % saved over the light
    per-batch % line, so level and swing read together;
  * a FORCED shared y scale across every panel (`chartkit.shared_ylim`), which is the
    small-multiples rule: panel-to-panel comparison IS the chart;
  * a zero reference per panel — the FIFO baseline appears as that line, not as a facet.

Panel titles are the compact arm names (`style._stitle`); only edge panels carry axis
labels, and the bottom legend explains the bold/light encoding once for the whole grid.
"""
import math
import os

import numpy as np
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.series import _prodtime_delta
from Optimization.Performance_Evaluations.common.style import _stitle

_LEGEND_ROWS = ('cumulative % saved (bold)', 'per-batch % (light)')


@evaluation(key='labor.delta_grid',
            label='Task-time delta vs FIFO, one panel per arm',
            scope='per_strategy', needs=('series',),
            family='labor', shape='facet', quantities=('production_time',),
            views_pending=(
                ('absolute', 'the level of each arm over batches, one panel per arm. '
                             'trajectories.overtime already draws it overlaid; the '
                             'facet version is worth having at 34 arms, where the '
                             'overlay is one opaque ribbon, and is not written yet'),
                ('percent', 'the same facet expressed as improvement rather than as '
                            'hours saved — the ratio a reader compares across arms '
                            'of different absolute size; not written yet'),))
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    rows = []                                        # (strategy, batches, pb, cum)
    for s in ctx.strategies:
        if s['key'] == base['key']:                  # its delta vs itself is flat 0
            continue
        batches, pb, cum = _prodtime_delta(S, s['key'], base['key'])
        if batches.size:
            rows.append((s, batches, pb, cum))
    if not rows:
        return

    n = len(rows)
    ncols = n if n <= 3 else math.ceil(math.sqrt(n))     # mirror chartkit.make's layout
    nrows = math.ceil(n / ncols)
    ch = chartkit.make(panels=n, ncols=ncols, panel_w=2.7, panel_h=2.1,
                       sharex=True, sharey=True,
                       legend='bottom', legend_labels=_LEGEND_ROWS)

    all_vals = []
    for ax, (s, batches, pb, cum) in zip(ch.axes, rows):
        col = chartkit.strategy_color(s, ctx.strategies)
        ax.plot(batches, pb, color=col, lw=0.8, alpha=0.35)
        ax.plot(batches, cum, color=col, lw=1.8)
        chartkit.reference_line(ax, 0.0, orient='y', lw=0.9)
        ax.set_title(_stitle(s), fontsize=7)
        ax.tick_params(labelsize=6)
        all_vals.append(np.concatenate([pb, cum]))

    # THE fix over the retired grids: one honest scale, forced, across every panel.
    chartkit.shared_ylim(ch.axes, all_vals, include=(0.0,))

    for i, ax in enumerate(ch.axes):
        r, c = divmod(i, ncols)
        if c == 0:
            ax.set_ylabel('% vs FIFO (↑ better)', fontsize=7)
        else:
            ax.tick_params(labelleft=False)
        if r == nrows - 1 or i + ncols >= n:             # bottom row / last in column
            ax.set_xlabel('batch', fontsize=7)
        else:
            ax.tick_params(labelbottom=False)

    ch.legend(handles=[Line2D([], [], color='#333333', lw=1.8),
                       Line2D([], [], color='#333333', lw=0.8, alpha=0.35)],
              labels=list(_LEGEND_ROWS))
    # No subtitle: on a grid this short the subtitle band would sit on the panel titles.
    ch.title('Task time vs FIFO by arm — shared y scale')
    ch.save(os.path.join(io.out_dir(ctx), 'delta_prodtime_by_arm_grid.png'),
            view='delta')
