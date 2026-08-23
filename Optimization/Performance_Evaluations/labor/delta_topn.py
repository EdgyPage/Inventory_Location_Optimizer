"""labor.delta_topn — the task-time delta story vs FIFO, told once, in one figure.

The retired suite told it twice: one chart plotted the cumulative % of baseline task
time saved but anchored its y-axis at zero, so a 2–3% treatment effect occupied the top
tenth of the canvas; a second chart re-plotted the same per-batch deltas as a bar forest.
Merged here into two panels sharing the batch axis:

  TOP     the HONEST cumulative measure — running task-time saved over running baseline
          total (`series._prodtime_delta`'s cum series, not a mean of percents) — with y
          clipped to the data (`chartkit.data_ylim`); the zero reference appears only
          when it is within or near the data window, because forcing it in is exactly
          the canvas-wasting anchor this figure exists to fix.
  BOTTOM  the per-batch % trend: the raw line kept faint so the demand swing stays
          visible, a small rolling mean carrying the policy level, and a rolling ±1 std
          band around it (`chartkit.draw_ci`) so the spread is drawn, not implied.

Arm selection is the shared top-N rule (`series._select_top`).  The FIFO baseline is
excluded from selection — its delta against itself is identically zero — and appears as
the zero reference line instead of a redundant flat series or a duplicate legend row:
the legend carries strategy names only.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot').
"""
import os

import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, painters
from Optimization.Performance_Evaluations.common.series import _select_top, _prodtime_delta
from Optimization.Performance_Evaluations.common.style import _stitle, _SMOOTH, _TOP_DIMS


# ── the zero-reference policy ───────────────────────────────────────────────────
def _ylim_zero_aware(ax, values):
    """Clip y to the data; draw the FIFO zero reference only when zero is already
    within — or near (25% of the data span beyond) — the data window.  A far-away zero
    would re-anchor the axis and compress the effect, which is the old chart's bug."""
    flat = np.concatenate([np.ravel(np.asarray(v, dtype=float)) for v in values])
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return
    lo, hi = float(flat.min()), float(flat.max())
    span = (hi - lo) or (abs(hi) or 1.0)
    near = (lo - 0.25 * span) <= 0.0 <= (hi + 0.25 * span)
    chartkit.data_ylim(ax, flat, include=(0.0,) if near else ())
    if near:
        ax.axhline(0, **{**chartkit.BASELINE_STYLE, 'lw': 1.2})


@evaluation(key='labor.delta_topn',
            label='Task-time saved vs FIFO — cumulative + per-batch (top-N)',
            scope='config', needs=('series',),
            defaults={'top_n': 3, 'top_by': 'initial'},
            family='labor', views=('delta',))
def render(ctx, params):
    S = ctx.series()
    top_n  = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    base   = ctx.base
    selected, _gof = _select_top(ctx.strategies, S, top_n, top_by)
    selected = [s for s in selected if s['key'] != base['key']]

    rows = []                                        # (strategy, batches, pb, cum)
    for s in selected:
        batches, pb, cum = _prodtime_delta(S, s['key'], base['key'])
        if batches.size:
            rows.append((s, batches, pb, cum))
    if not rows:
        return

    labels = [_stitle(s) for s, *_ in rows]
    ch = chartkit.make(panels=2, ncols=1, panel_w=7.2, panel_h=2.9,
                       sharex=True, legend='gutter', legend_labels=labels)
    ax_cum, ax_pb = ch.axes

    cum_vals, pb_vals = [], []
    for s, batches, pb, cum in rows:
        col  = chartkit.strategy_color(s, ctx.strategies)
        dash = chartkit.strategy_dash(s)
        ax_cum.plot(batches, cum, color=col, ls=dash, lw=1.9, label=_stitle(s))
        cum_vals.append(cum)
        # Raw faint + rolling mean bold + rolling ±1 std band: level, swing, and spread.
        ser  = pd.Series(pb, dtype=float)
        mean = ser.rolling(_SMOOTH, min_periods=1).mean().to_numpy()
        sd   = ser.rolling(_SMOOTH, min_periods=2).std().fillna(0.0).to_numpy()
        ax_pb.plot(batches, pb, color=col, lw=0.7, alpha=0.28)
        ax_pb.plot(batches, mean, color=col, ls=dash, lw=1.9)
        chartkit.draw_ci(ax_pb, batches, mean - sd, mean + sd, color=col, alpha=0.12)
        pb_vals.append(np.concatenate([mean - sd, mean + sd]))

    _ylim_zero_aware(ax_cum, cum_vals)
    _ylim_zero_aware(ax_pb, pb_vals)
    ax_cum.tick_params(labelbottom=False)            # sharex — bottom panel owns the ticks
    # % tick labels are wide; shrink them so the rotated ylabel fits the reserved margin.
    for ax in (ax_cum, ax_pb):
        ax.tick_params(axis='y', labelsize=9)
    ax_cum.set_ylabel(f'cumulative % saved {chartkit.pct_axis(ax_cum, better="up")}',
                      fontsize=9, labelpad=2)
    ax_pb.set_ylabel(f'per-batch % {chartkit.pct_axis(ax_pb, better="up")}',
                     fontsize=9, labelpad=2)
    ax_pb.set_xlabel('batch')

    ch.legend(title='strategy')
    sub = f'top {top_n} per {top_by}' if top_by in _TOP_DIMS else f'top {top_n}'
    ch.title('Task time saved vs FIFO',
             subtitle=(f'{sub} · top: running % saved · bottom: per-batch % '
                       f'({_SMOOTH}-batch mean ± 1 std)'))
    ch.save(os.path.join(io.out_dir(ctx),
                         f'delta_prodtime_{painters.top_tag(top_n, top_by)}.png'),
            view='delta')
