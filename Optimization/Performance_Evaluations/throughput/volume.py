"""throughput.volume — cumulative pick volume against elapsed time, in two views.

WHY A CURVE AND NOT A BAR.  Throughput as a single number hides *when* the work
happened.  Plotting running items against running elapsed hours makes the rate itself
the visible thing: the SLOPE of the curve IS throughput, so a steeper line that
terminates further left finished the same demand sooner.  That is the whole "better
throughput at equal labor" story in one picture — the arms do not do less work, they do
it in less elapsed time.

THE TIME AXIS IS SYNTHESIZED, AND HAS TO BE.  There is no global clock in the data:
every batch's sim clock restarts at zero, so elapsed time is the running sum of batch
makespans — exactly the quantity run_whatif_labor calls `batch_hours`, converted with
the same `chartkit.to_hours`, so numbers reconcile across the suite.  These are modeled
pick-time hours, not wall-clock; the axis label carries the qualifier.  HOURS IS FIXED
on this x-axis, not chosen from the data like the duration axes elsewhere in the suite:
elapsed run time is the one quantity the whole comparison is denominated in — the
what-if layer, the finish annotations and the site all say "hours" — and a cumulative
axis that renamed its unit per run would stop reconciling with them.

THE PRIMARY VIEW IS THE PERCENT LEAD.  The cumulative curves are near-identical straight
lines (measured shape index ~1.0007 on a real 100-batch arm), so overlaying them shows
almost nothing, and the retired companion panel plotted the lead in absolute items —
±150k reads as huge while being a few percent.  The percent view interpolates each arm
and FIFO onto a shared hour grid spanning only the hours BOTH actually cover (never
extrapolating past the shorter run) and plots the lead as a % of FIFO's cumulative at
that same moment — FIFO is the zero line, and each arm's finish dot is annotated with
its finish hour, because the finish point is where arms genuinely differ.  The absolute
view keeps the raw curves as the honest-units companion, decluttered: names in the
legend, the per-arm rate/finish numbers in one compact subtitle block, and x-limits
identical across both files so the views superimpose mentally.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot').
"""
import os

import numpy as np
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.common.style import _stitle, _TOP_DIMS

_HOURS_LABEL = 'elapsed pick-time hours (modeled, not wall-clock)'


# ── data extraction (ported verbatim, units via chartkit) ───────────────────────
def cumulative_curve(df):
    """(hours, items) running totals over batches, ordered by batch_id.

    hours[i] is elapsed time at the END of batch i, so the pair is the point "this much
    work had been finished by this much elapsed time"."""
    if df is None or df.empty:
        return np.array([]), np.array([])
    d = df.sort_values('batch_id')
    hrs   = np.cumsum(chartkit.to_hours(d['duration'].to_numpy(dtype=float)))
    items = np.cumsum(d['total_items'].to_numpy(dtype=float))
    return hrs, items


def lead_pct_curve(hrs, items, base_hrs, base_items, npts=200):
    """(grid_hours, lead_pct) — this arm's lead over FIFO at matched elapsed time, as a
    % of FIFO's cumulative at that time.

    Both curves are interpolated onto a shared grid spanning only the hours BOTH
    actually cover, so the comparison never extrapolates past the shorter run.  Grid
    points where FIFO has finished nothing yet are dropped (a ratio needs a base)."""
    if hrs.size == 0 or base_hrs.size == 0:
        return np.array([]), np.array([])
    hi = min(float(hrs[-1]), float(base_hrs[-1]))
    if hi <= 0:
        return np.array([]), np.array([])
    grid = np.linspace(0.0, hi, npts)
    a = np.interp(grid, np.concatenate(([0.0], hrs)), np.concatenate(([0.0], items)))
    b = np.interp(grid, np.concatenate(([0.0], base_hrs)),
                  np.concatenate(([0.0], base_items)))
    mask = b > 0
    pct = np.array([chartkit.improvement_pct(av, bv, lower_is_better=False)
                    for av, bv in zip(a[mask], b[mask])])
    return grid[mask], pct


def _sub_note(top_n, top_by):
    return f'top {top_n} per {top_by}' if top_by in _TOP_DIMS else f'top {top_n}'


# ── the two views ───────────────────────────────────────────────────────────────
def _percent(ctx, curves, base_curve, xlim, top_n, top_by, out):
    b_hrs, b_items = base_curve
    if b_hrs.size == 0:
        return
    # NO legend: every curve is labelled at its own finish point, which beats a legend
    # (the name sits next to the line instead of asking the reader to match a hue), and
    # a gutter here would be a box for the labels to disappear behind.  chartkit's fit
    # grows the canvas around the annotations.
    ch = chartkit.make(panel_w=7.2, panel_h=4.4, legend='none')
    ax = ch.ax
    all_pct = []
    finish_labels, finish_colors = [], []
    for s, hrs, items in curves:
        grid, pct = lead_pct_curve(hrs, items, b_hrs, b_items)
        if grid.size == 0:
            continue
        col = chartkit.strategy_color(s, ctx.strategies)
        ax.plot(grid, pct, color=col, ls=chartkit.strategy_dash(s), lw=1.8,
                label=_stitle(s))
        # Finish dot, annotated with the arm's NAME and its own finish hour.  The name is
        # on the curve rather than only in the legend because colour here is the
        # assignment function and neighbouring functions land on neighbouring hues: a
        # site director reading this chart matched a negative curve to the wrong arm and
        # reported the page as self-contradictory, which it was not.  A line that says
        # what it is cannot be mismatched.
        ax.plot([grid[-1]], [pct[-1]], marker='o', ms=5, color=col, zorder=5)
        finish_labels.append((grid[-1], pct[-1], f'{_stitle(s)} · {float(hrs[-1]):.1f} h'))
        finish_colors.append(col)
        all_pct.append(pct)
    if not all_pct:
        return ch.abandon()
    chartkit.reference_line(ax, 0.0, orient='y')
    chartkit.data_ylim(ax, all_pct, include=(0.0,))
    ax.set_xlim(*xlim)
    # after the limits: the collision sweep measures gaps against the axes height
    chartkit.annotate_points(ax, finish_labels, colors=finish_colors)
    ax.set_xlabel(_HOURS_LABEL)
    # % tick labels are wide; shrink them so the rotated ylabel fits the reserved margin.
    ax.tick_params(axis='y', labelsize=9)
    ax.set_ylabel(f'volume lead over FIFO {chartkit.pct_axis(ax, better="up")}',
                  fontsize=9, labelpad=2)
    ch.title('Volume lead over FIFO',
             subtitle=(f'{_sub_note(top_n, top_by)} · % more items done than FIFO '
                       f'by the same hour · dot = finish (labeled)'))
    ch.save(os.path.join(out, 'percent_volume_lead.png'), view='percent')


def _absolute(ctx, curves, base_curve, xlim, top_n, top_by, out):
    labels = [_stitle(s) for s, *_ in curves] + ['FIFO baseline']
    # The per-arm rate/finish rows are longer than the names; size the gutter for them,
    # because that is where they render (a second legend block, never over the curves).
    widest = [f'{l}  99,999/h · 99.9 h' for l in labels]
    ch = chartkit.make(panel_w=7.2, panel_h=4.4, legend='gutter',
                       legend_labels=labels + widest)
    ax = ch.ax
    ann, all_items, drew = [], [], False
    for s, hrs, items in curves:
        if hrs.size == 0 or hrs[-1] <= 0:
            continue
        col = chartkit.strategy_color(s, ctx.strategies)
        ax.plot(hrs, items, color=col, ls=chartkit.strategy_dash(s), lw=1.9,
                label=_stitle(s))
        # Endpoint marker: arms differ in BOTH total volume and elapsed time (measured
        # spread on a real run: 6.8% in items, 69% in hours) — the finish point is the
        # informative part; the curves themselves are near-identical straight lines.
        ax.plot([hrs[-1]], [items[-1]], marker='o', ms=5, color=col, zorder=5)
        ann.append(f'{_stitle(s)}  {items[-1] / hrs[-1]:,.0f}/h · {hrs[-1]:.1f} h')
        all_items.append(items)
        drew = True
    b_hrs, b_items = base_curve
    if b_hrs.size and b_hrs[-1] > 0:
        chartkit.mark_baseline(ax, b_hrs, b_items)
        ax.plot([b_hrs[-1]], [b_items[-1]], marker='o', ms=6,
                color=chartkit.BASELINE_STYLE['color'], zorder=5)
        ann.append(f'FIFO  {b_items[-1] / b_hrs[-1]:,.0f}/h · {b_hrs[-1]:.1f} h')
        all_items.append(b_items)
        drew = True
    if not drew:
        return ch.abandon()
    chartkit.data_ylim(ax, all_items, include=(0.0,))    # curves start at ~0 by nature
    ax.set_xlim(*xlim)
    ax.set_xlabel(_HOURS_LABEL)
    ax.set_ylabel('cumulative items picked')
    ch.legend(title='strategy')
    # The per-arm annotation block: a second legend stacked below the first in the
    # reserved gutter — compact, attributable, and never over the curves.
    ch.legend(handles=[Line2D([], [], ls='none') for _ in ann], labels=ann,
              title='rate · finish')
    ch.title('Cumulative pick volume',
             subtitle=f'{_sub_note(top_n, top_by)} — slope = throughput, dot = finish')
    ch.save(os.path.join(out, 'absolute_volume_curve.png'), view='absolute')


@evaluation(key='throughput.volume',
            label='Cumulative pick volume vs elapsed time + % lead over FIFO',
            scope='config', needs=('batch', 'series'),
            defaults={'top_n': 3, 'top_by': 'initial'},
            family='throughput', views=('percent', 'absolute'))
def render(ctx, params):
    top_n  = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    selected, _gof = _select_top(ctx.strategies, ctx.series(), top_n, top_by)
    base = ctx.base
    arms = [s for s in selected if s['key'] != base['key']]
    if not arms:
        return
    frames = {s['key']: ctx.batch_df(s['key']) for s in arms + [base]}
    curves = [(s, *cumulative_curve(frames[s['key']])) for s in arms]
    curves = [c for c in curves if c[1].size]
    base_curve = cumulative_curve(frames[base['key']])
    if not curves and base_curve[0].size == 0:
        return
    # One x-window for BOTH files, spanning every arm's full run.
    ends = [c[1][-1] for c in curves] + ([base_curve[0][-1]] if base_curve[0].size else [])
    xlim = (0.0, max(ends) * 1.02)
    out = io.out_dir(ctx)                 # figures/throughput, from the family
    _percent(ctx, curves, base_curve, xlim, top_n, top_by, out)
    _absolute(ctx, curves, base_curve, xlim, top_n, top_by, out)
