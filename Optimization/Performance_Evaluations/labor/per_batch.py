"""labor.per_batch — labor cost of the scheduled tasks, batch by batch, in two views.

WHAT "LABOR" IS HERE.  The per-batch sum of task durations — the serial makespan a lone
picker would incur, i.e. total work, independent of how many pickers ran in parallel.
That is the quantity the assignment function actually moves; the scheduler does not
change it.  Durations are sim milliseconds and render in hours via `chartkit.to_hours`,
the same conversion the what-if layer uses, so figures reconcile across the suite.
HOURS IS FIXED HERE, not chosen from the data like the duration axes elsewhere in the
suite: "labor hours per batch" is the reader-facing quantity itself — the staffing
number the what-if layer, the tables and the site all quote — so the axis names the unit
the question is asked in, and a run whose batches happen to be small simply reads small.

WHY IT IS PLOTTED AGAINST BATCH NUMBER.  Labor per batch is demand-driven: a batch with
more items costs more, whatever the policy.  Plotting over batch order shows whether a
policy's advantage HOLDS as the warehouse fills and reorders churn, or decays.  The
percent view removes the demand signal entirely by expressing each batch against FIFO on
that same batch, so what remains is the policy effect alone.

Both views draw the raw line faint UNDER the smoothed line, in the strategy's own color
(the retired chart drew raw lines in one unmapped tan, so they could not be attributed):
hiding the raw spread would overstate how cleanly separated the arms are.  Legends carry
strategy names only — the retired chart printed six near-identical stat rows there; the
compact steady-state h/batch listing now lives in the absolute view's subtitle instead.

TWO THINGS NOT TO READ INTO IT.  (1) There is no warm-up transient: batch 0 measured 70%
of median on a real run, well inside the family, so the opening batches are not excluded.
(2) One batch in the store channel carries ~4 items; its FIFO ratio divides two tiny
numbers, so the percent view is scaled by percentile to the real signal and the offending
batches are marked with triangles at the axis bottom edge — identified, never removed.
The batch sequence is shared across arms, so it biases no comparison.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot'),
win (steady-state tail for the subtitle means).
"""
import os

import numpy as np
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.common.style import _stitle, _TOP_DIMS, _WIN, _SMOOTH


# ── data extraction (ported verbatim, units via chartkit) ───────────────────────
def labor_by_batch(task_df):
    """(batch_ids, labor_hours) — Σ task duration per batch, in hours, by batch order."""
    if task_df is None or task_df.empty:
        return np.array([]), np.array([])
    g = task_df.groupby('batch_id')['duration'].sum().sort_index()
    return g.index.to_numpy(dtype=float), chartkit.to_hours(g.to_numpy(dtype=float))


def _smooth(y, win=_SMOOTH):
    """Centred moving average, the same small window the over-time trajectories use."""
    if y.size < win:
        return y
    k = np.ones(win) / win
    return np.convolve(y, k, mode='same')


def _sub_note(top_n, top_by):
    return f'top {top_n} per {top_by}' if top_by in _TOP_DIMS else f'top {top_n}'


# ── the two views ───────────────────────────────────────────────────────────────
def _absolute(ctx, arms, curves, base_curve, win, top_n, top_by, out):
    labels = [_stitle(s) for s in arms] + ['FIFO baseline']
    ch = chartkit.make(panel_w=7.4, panel_h=4.2, legend='gutter', legend_labels=labels)
    ax = ch.ax
    all_vals, ss_bits, drew = [], [], False
    for s in arms:
        b, h = curves[s['key']]
        if b.size == 0:
            continue
        col = chartkit.strategy_color(s, ctx.strategies)
        ax.plot(b, h, color=col, lw=0.6, alpha=0.30)
        ax.plot(b, _smooth(h), color=col, ls=chartkit.strategy_dash(s), lw=1.9,
                label=_stitle(s))
        all_vals.append(h)
        ss = h[b >= (b.max() - win)]
        ss_bits.append(f'{_stitle(s)} {ss.mean():.2f}')
        drew = True
    b_b, b_h = base_curve
    if b_b.size:
        ax.plot(b_b, b_h, color=chartkit.BASELINE_STYLE['color'], lw=0.6, alpha=0.22)
        chartkit.mark_baseline(ax, b_b, _smooth(b_h))
        all_vals.append(b_h)
        ss_b = b_h[b_b >= (b_b.max() - win)]
        ss_bits.append(f'FIFO {ss_b.mean():.2f}')
        drew = True
    if not drew:
        plt.close(ch.fig)
        return
    chartkit.data_ylim(ax, all_vals)
    ax.set_xlabel('batch')
    ax.set_ylabel(f'labor {chartkit.HOURS} per batch (↓ better)')
    ch.legend(title='strategy')
    listing = (f'{_sub_note(top_n, top_by)} — last-{win} mean h/batch: '
               + ' · '.join(ss_bits))
    ch.title('Labor per batch',
             subtitle=listing if len(listing) <= 100 else _sub_note(top_n, top_by))
    ch.save(os.path.join(out, 'absolute_labor_per_batch.png'), view='absolute')


def _percent(ctx, arms, curves, base_curve, top_n, top_by, out):
    b_b, b_h = base_curve
    base_by = {int(b): v for b, v in zip(b_b, b_h) if v > 0}
    if not base_by:
        return
    labels = [_stitle(s) for s in arms]
    ch = chartkit.make(panel_w=7.4, panel_h=4.2, legend='gutter', legend_labels=labels)
    ax = ch.ax
    pct_all, drew = [], False
    for s in arms:
        b, h = curves[s['key']]
        xs  = np.array([bb for bb in b if int(bb) in base_by])
        pct = np.array([chartkit.improvement_pct(hh, base_by[int(bb)],
                                                 lower_is_better=True)
                        for bb, hh in zip(b, h) if int(bb) in base_by])
        if xs.size == 0:
            continue
        col = chartkit.strategy_color(s, ctx.strategies)
        ax.plot(xs, pct, color=col, lw=0.6, alpha=0.30)
        ax.plot(xs, _smooth(pct), color=col, ls=chartkit.strategy_dash(s), lw=1.9,
                label=_stitle(s))
        pct_all.append(pct)
        drew = True
    if not drew:
        plt.close(ch.fig)
        return
    ax.axhline(0, **{**chartkit.BASELINE_STYLE, 'lw': 1.2})
    # A near-empty batch divides two tiny numbers, so its ratio is wild and would set
    # the y-scale, hiding the real few-percent signal.  Scale to the 2–98 percentile of
    # the signal (the data is NOT clipped — raw excursions simply exit the frame).
    lo, hi = np.percentile(np.concatenate(pct_all), [2, 98])
    chartkit.data_ylim(ax, np.array([lo, hi]), pad=0.20, include=(0.0,))
    # Median, not mean, of the base labor: the same near-empty batch must not move the
    # flagging threshold itself.
    med  = float(np.median(list(base_by.values())))
    tiny = sorted(b for b, v in base_by.items() if v < 0.05 * med)
    if tiny:
        ax.plot(tiny, [0.015] * len(tiny), transform=ax.get_xaxis_transform(),
                marker='^', ls='none', color='#b03030', ms=5, zorder=5, clip_on=False)
    ax.set_xlabel('batch')
    # % tick labels are wide; shrink them so the rotated ylabel fits the reserved margin.
    ax.tick_params(axis='y', labelsize=9)
    ax.set_ylabel(f'% less labor than FIFO {chartkit.pct_axis(ax, better="up")}',
                  fontsize=9, labelpad=2)
    ch.legend(title='strategy')
    caveat = 'triangles = near-empty batches: ratio off-scale; shared demand, no bias'
    ch.title('Labor saved vs FIFO, per batch',
             subtitle=f'{_sub_note(top_n, top_by)} · {caveat}' if tiny
             else _sub_note(top_n, top_by))
    ch.save(os.path.join(out, 'percent_labor_per_batch.png'), view='percent')


@evaluation(key='labor.per_batch',
            label='Labor hours per batch + % saved vs FIFO, over batch order',
            scope='config', needs=('task', 'series'),
            defaults={'top_n': 3, 'top_by': 'initial', 'win': _WIN},
            family='labor', views=('absolute', 'percent'))
def render(ctx, params):
    top_n  = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    win    = int(params.get('win', _WIN) or _WIN)
    selected, _gof = _select_top(ctx.strategies, ctx.series(), top_n, top_by)
    base = ctx.base
    arms = [s for s in selected if s['key'] != base['key']]
    if not arms:
        return
    out = io.out_dir(ctx)                 # figures/labor, from the family
    frames = {s['key']: ctx.task_df(s['key']) for s in arms + [base]}
    curves = {k: labor_by_batch(df) for k, df in frames.items()}
    base_curve = curves[base['key']]
    _absolute(ctx, arms, curves, base_curve, win, top_n, top_by, out)
    _percent(ctx, arms, curves, base_curve, top_n, top_by, out)
