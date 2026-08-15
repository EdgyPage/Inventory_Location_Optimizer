"""compare.labor_trend — labor cost of the scheduled tasks, batch by batch, and how it moves.
Under compare/breakdown.

WHAT "LABOR" IS HERE.  The per-batch sum of task durations, which is exactly batch_stats.
task_makespan (verified equal to float noise) — the serial makespan a lone picker would incur, i.e.
total work, independent of how many pickers ran in parallel.  That is the quantity the assignment
function actually moves; the scheduler does not change it.  Converted to hours with the same
MS_PER_HOUR the what-if layer uses so figures reconcile across the suite.

WHY IT IS PLOTTED AGAINST BATCH NUMBER.  Labor per batch is a demand-driven quantity: a batch with
more items costs more, whatever the policy.  Plotting it over batch order shows whether a policy's
advantage HOLDS as the warehouse fills and reorders churn, or decays.  The right panel removes the
demand signal entirely by expressing each batch as a percentage against FIFO on that same batch, so
what remains is the policy effect alone.

TWO THINGS NOT TO READ INTO IT.  (1) There is no warm-up transient: batch 0 measured 70% of median
on a real run, well inside the family, so the opening batches are not a ramp and are not excluded.
(2) One batch in the store channel carries ~4 items and shows as a spike in the % panel and a notch
in the raw panel; it is identical in all 34 arms because the batch sequence is shared across arms,
so it is a property of the demand sample and biases no comparison between policies.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot'), win (steady-state tail).
"""
import os

import numpy as np
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.style import (
    _stitle, _LINESTYLES, _TOP_DIMS, _WIN, legend_right)
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.comparison import top_tag

MS_PER_HOUR = 3.6e6
HOURS_NOTE = 'modeled sim pick-time hours (Σ task duration / 3.6e6) — not wall-clock'


def labor_by_batch(task_df):
    """(batch_ids, labor_hours) — Σ task duration per batch, in hours, ordered by batch."""
    if task_df is None or task_df.empty:
        return np.array([]), np.array([])
    g = task_df.groupby('batch_id')['duration'].sum().sort_index()
    return g.index.to_numpy(dtype=float), g.to_numpy(dtype=float) / MS_PER_HOUR


def trend_per_100(batches, hours):
    """Least-squares slope in labor-hours per 100 batches — the trend as a NUMBER.

    Reported so "labor goes down over time" is quantified rather than eyeballed off a line.
    """
    if batches.size < 3:
        return float('nan')
    return float(np.polyfit(batches, hours, 1)[0] * 100.0)


def _smooth(y, win=5):
    """Centred moving average, the same 5-batch window the over-time trajectories use."""
    if y.size < win:
        return y
    k = np.ones(win) / win
    return np.convolve(y, k, mode='same')


def _plot(selected, gof, frames, baseline, top_n, top_by, win, title, path):
    b_b, b_h = labor_by_batch(frames.get(baseline['key']))
    base_by = {int(b): v for b, v in zip(b_b, b_h) if v > 0}
    gstyle = {g: _LINESTYLES[i % len(_LINESTYLES)]
              for i, g in enumerate(sorted(set((gof or {}).values())))}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17, 6.2))
    drew = False
    pct_all = []
    for s in selected:
        b, h = labor_by_batch(frames.get(s['key']))
        if b.size == 0:
            continue
        ls = gstyle.get((gof or {}).get(s['key']), '-')
        ss = h[b >= (b.max() - win)]
        slope = trend_per_100(b, h)
        # Raw thin + smoothed heavy: the raw line carries the per-batch demand swing, the smooth
        # line carries the policy level.  Both, because hiding the raw spread would overstate
        # how cleanly separated these arms are.
        ax1.plot(b, h, color=s['color'], lw=0.6, alpha=0.30, ls='-')
        ax1.plot(b, _smooth(h), color=s['color'], lw=1.9, ls=ls,
                 label=(f'{_stitle(s)}  {ss.mean():.2f} h/batch'
                        f'  · {slope:+.2f} h/100b'))
        if base_by:
            pct = np.array([((base_by[int(bb)] - hh) / base_by[int(bb)] * 100.0)
                            for bb, hh in zip(b, h) if int(bb) in base_by])
            xs = np.array([bb for bb in b if int(bb) in base_by])
            if xs.size and s['key'] != baseline['key']:
                # Median, not mean: one near-empty batch in the shared demand sample makes the
                # mean of a ratio unstable, and the legend number must survive that.
                ax2.plot(xs, _smooth(pct), color=s['color'], lw=1.9, ls=ls,
                         label=f'{_stitle(s)}  {np.median(pct):+.1f}%')
                pct_all.append(pct)
        drew = True

    if not drew:
        plt.close(fig)
        return

    if b_b.size:
        ss_b = b_h[b_b >= (b_b.max() - win)]
        ax1.plot(b_b, _smooth(b_h), color='grey', lw=2.4, ls='--', zorder=1,
                 label=f'baseline (FIFO)  {ss_b.mean():.2f} h/batch')
    ax1.set_xlabel('batch')
    ax1.set_ylabel('labor hours per batch  (Σ task duration, ↓ better)')
    ax1.grid(alpha=0.3)
    ax1.set_title(f'Labor per batch — raw + {5}-batch mean; legend shows last-{win} mean and trend',
                  fontsize=10)

    ax2.axhline(0, color='grey', lw=1.0, ls='--', label='baseline (FIFO)')
    ax2.set_xlabel('batch')
    ax2.set_ylabel('% less labor than FIFO on the same batch (↑ better)')
    ax2.grid(alpha=0.3)
    ax2.set_title('Labor saved vs FIFO, per batch — demand signal removed', fontsize=10)

    # A near-empty batch (the shared demand sample contains one) divides two tiny numbers, so its
    # ratio is wild and would otherwise set the y-scale and hide the real few-percent signal.  The
    # data is NOT clipped — the view is scaled to the signal and the offending batches are marked,
    # so the excursion is identified rather than silently removed.
    if base_by:
        med = float(np.median(list(base_by.values())))
        tiny = sorted(b for b, v in base_by.items() if v < 0.05 * med)
        for b in tiny:
            ax2.axvline(b, color='#b03030', lw=1.0, ls=':', alpha=0.8, zorder=0)
        if tiny:
            # Axes-fraction text, not annotate(xy=...): the y-limit below is set from percentiles,
            # so a data-coordinate anchor can land outside the view and silently not draw.
            ax2.text(0.02, 0.03,
                     f'dotted line = near-empty batch {tiny[0]} (~4 items): a ratio of two tiny '
                     f'numbers, off-scale here.\nIdentical in every arm — the batch sequence is '
                     f'shared, so it biases no comparison.',
                     transform=ax2.transAxes, fontsize=7, color='#b03030', va='bottom')
    if pct_all:
        lo, hi = np.percentile(np.concatenate(pct_all), [2, 98])
        pad = max(0.5, (hi - lo) * 0.35)
        ax2.set_ylim(lo - pad, hi + pad)

    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    fig.suptitle(title + sub, fontsize=12, fontweight='bold')
    fig.text(0.5, 0.005, f'labor hours = {HOURS_NOTE}', ha='center', va='bottom',
             fontsize=8, color='#555555')
    legend_right(ax1, fontsize=7)
    legend_right(ax2, fontsize=7)
    plt.tight_layout(rect=(0, 0.035, 1, 0.93))
    _save_close(fig, path)


@evaluation(key='compare.labor_trend',
            label='Labor hours per batch and % saved vs FIFO, over batch order',
            scope='config', needs=('task', 'series'), out_subdir='compare/breakdown',
            defaults={'top_n': 3, 'top_by': 'initial', 'win': _WIN})
def render(ctx, params):
    top_n  = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    win    = int(params.get('win', _WIN) or _WIN)
    selected, gof = _select_top(ctx.strategies, ctx.series(), top_n, top_by)
    if not selected:
        return
    out = io.out_dir(ctx)                       # compare/breakdown, from the declaration
    tag = top_tag(top_n, top_by)
    frames = {s['key']: ctx.task_df(s['key']) for s in list(selected) + [ctx.base]}
    _plot(selected, gof, frames, ctx.base, top_n, top_by, win,
          f'Labor cost of scheduled tasks over batches  [{ctx.title}]',
          os.path.join(out, f'{tag}_labor_per_batch.png'))
