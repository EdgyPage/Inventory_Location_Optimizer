"""task_time.breakdown — where picker time goes, in two complementary cuts.

One render, two figures, ONE shared category order (the strategy declaration order),
so a reader can scan the same arm across both without re-finding it:

  * the absolute view — the stacked per-arm picker-time decomposition (travel vs
    handling, reconstructed from picker events over the steady-state sample), in the
    time unit `chartkit.time_units` picks from both components of every bar pooled
    together, each bar annotated with its travel share.
  * the second absolute view — the picking% / traveling% split of aggregate picker time
    (already percent-of-time in the series scalars), as horizontal stacked bars.

Movement is orange in both figures and stationary work is blue, so the two cuts read
as one vocabulary.  Data logic ported from the retired travel-vs-handling and
pick-vs-travel charts.  The travel/handling decomposition degrades gracefully: an
empty reconstruction skips that figure and the percent split still renders.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle

_MOVE_COL = '#dd8452'      # travel / traveling — movement
_WORK_COL = '#4c72b0'      # handling / picking — stationary work


def _breakdown_figure(ctx, out):
    th = ctx.breakdown()
    keys = [s['key'] for s in ctx.strategies if s['key'] in th]
    if not keys:
        return
    by_key = {s['key']: s for s in ctx.strategies}
    travel_ms = np.asarray([th[k][0] for k in keys], dtype=float)
    handling_ms = np.asarray([th[k][1] for k in keys], dtype=float)
    # travel and handling stack on ONE axis, so they pick their unit together — the
    # sample's magnitude is what decides it, not a fixed hours assumption
    div, unit = chartkit.time_units(np.concatenate([travel_ms, handling_ms]))
    travel, handling = travel_ms / div, handling_ms / div
    idx = np.arange(len(keys))

    ch = chartkit.make(panels=1, panel_w=chartkit.width_for_categories(len(keys)),
                       panel_h=4.4, legend='gutter',
                       legend_labels=['travel', 'handling'])
    ax = ch.ax
    ax.bar(idx, travel, label='travel', color=_MOVE_COL)
    ax.bar(idx, handling, bottom=travel, label='handling', color=_WORK_COL)
    tot = travel + handling
    for i in range(len(keys)):
        if tot[i] > 0:
            ax.text(i, tot[i], f'{travel[i] / tot[i] * 100:.0f}% trv',
                    ha='center', va='bottom', fontsize=6)
    xlabels = [_stitle(by_key[k]) for k in keys]
    ax.set_xticks(idx)
    ax.set_xticklabels(xlabels, rotation=90, fontsize=6)
    ax.set_ylabel(f'Σ picker time ({unit}, steady-state sample)',
                  fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    # deepen the reserved bottom margin so the rotated category labels are not
    # clipped (chartkit cannot see tick-label extents)
    need = 0.62 + 0.05 * max((len(t) for t in xlabels), default=0)
    ax.get_subplotspec().get_gridspec().update(
        bottom=min(0.55, min(2.8, need) / ch.fig.get_figheight()))
    ch.legend()
    ch.title('Picker time: travel vs handling',
             'WITHIN a task, from picker events — not the same split as the '
             'picking-vs-traveling share')
    ch.save(os.path.join(out, 'absolute_task_time_breakdown.png'), view='absolute')


def _split_figure(ctx, out):
    S = ctx.series()
    avail = [s for s in ctx.strategies if S.get(s['key'])]
    if not avail:
        return
    ypos = np.arange(len(avail))
    pk = [S[s['key']]['picking_pct'] for s in avail]
    tv = [S[s['key']]['traveling_pct'] for s in avail]

    ch = chartkit.make(panels=1, panel_w=6.4,
                       panel_h=chartkit.height_for_categories(len(avail)),
                       legend='gutter', legend_labels=['picking %', 'traveling %'])
    ax = ch.ax
    ax.barh(ypos, pk, color=_WORK_COL, label='picking %')
    ax.barh(ypos, tv, left=pk, color=_MOVE_COL, label='traveling %')
    ylabels = [_stitle(s) for s in avail]
    ax.set_yticks(ypos)
    ax.set_yticklabels(ylabels, fontsize=6)
    ax.margins(y=max(0.03, 0.5 / max(1, len(avail))))
    ax.invert_yaxis()
    ax.set_xlabel('% of aggregate picker time', fontsize=8)
    ax.grid(axis='x', alpha=0.3)
    ax.grid(axis='y', alpha=0.0)
    # widen the reserved left margin for the category labels (chartkit cannot
    # see tick-label extents)
    need = 0.35 + 0.05 * max((len(t) for t in ylabels), default=0)
    ax.get_subplotspec().get_gridspec().update(
        left=min(2.6, need) / ch.fig.get_figwidth())
    ch.legend()
    ch.title('Picking vs traveling share',
             'share of aggregate picker time — a different cut from the travel-vs-'
             'handling split, which decomposes time inside a task')
    ch.save(os.path.join(out, 'absolute_pick_vs_travel.png'), view='absolute')


@evaluation(key='task_time.breakdown', label='Picker-time decomposition (two cuts)',
            scope='config', needs=('breakdown', 'series'),
            family='task_time', shape='composite')
def render(ctx, params):
    out = io.out_dir(ctx)
    _breakdown_figure(ctx, out)
    _split_figure(ctx, out)
