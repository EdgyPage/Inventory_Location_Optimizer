"""headline.throughput_vs_labor — the four success metrics on one figure.

Three scatter panels, one point per strategy arm, built from the per-arm series
scalars.  Vocabulary: TASK makespan = Σ task time = total labor (the serial makespan
a lone picker would incur); BATCH makespan = last-picker finish (parallel wall-clock).
The two throughputs are items ÷ each makespan.

  * Panel A: x = task makespan  vs  y = throughput / batch makespan.  Marker size ∝
    batch makespan.  Best corner is UP-LEFT.
  * Panel B: x = batch makespan  vs  y = throughput / batch makespan — the
    mechanistic link.
  * Panel C: x = throughput / task makespan  vs  y = throughput / batch makespan.
    Separates the two win types: a pure SCHEDULING win (batch makespan ↓ at flat task
    makespan) moves a point straight UP; a LABOR win (less task time per item) moves
    it RIGHT.

The thesis stays: throughput does NOT track task makespan (total labor) — a placement
that lowers Σ task labor can pile work onto the busiest picker, raising batch
makespan and lowering throughput.  So throughput tracks 1/batch-makespan (Panel B,
tight) — not task makespan (Panel A, a scattered cloud).  The Spearman ρ triplet in
the subtitle quantifies it.  Labor axes render in hours; throughput in items/hour.
"""
import os

import numpy as np
from scipy.stats import spearmanr

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle

_PER_HOUR = 3.6e6            # raw throughput scalars are items per sim-millisecond


def _f(v):
    """Coerce to float, mapping None/missing to NaN."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float('nan')


def _rho(xs, ys):
    """Spearman rank correlation over finite pairs; NaN if too few / degenerate."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3 or np.ptp(x[m]) == 0 or np.ptp(y[m]) == 0:
        return float('nan')
    return float(spearmanr(x[m], y[m]).correlation)


def _sizes(durs):
    """Map makespan → marker area in [40, 300]; flat 90 when all equal / non-finite."""
    d = np.asarray(durs, float)
    fin = d[np.isfinite(d)]
    if fin.size == 0 or fin.max() == fin.min():
        return np.full(d.shape, 90.0)
    lo, hi = fin.min(), fin.max()
    out = 40.0 + 260.0 * (d - lo) / (hi - lo)
    out[~np.isfinite(out)] = 90.0
    return out


def _panel(ax, xs, ys, colors, labels, base_xy, xlabel, title, sizes=None):
    ax.scatter(xs, ys, s=(sizes if sizes is not None else 90), c=colors,
               edgecolors='white', linewidths=0.5, zorder=3)
    for x, y, lab in zip(xs, ys, labels):
        if np.isfinite(x) and np.isfinite(y):
            ax.annotate(lab, (x, y), fontsize=5, alpha=0.7,
                        xytext=(3, 3), textcoords='offset points')
    if base_xy is not None and all(np.isfinite(v) for v in base_xy):
        bx, by = base_xy
        ax.axvline(bx, color=chartkit.BASELINE_STYLE['color'], lw=0.8, ls='--',
                   alpha=0.6)
        ax.axhline(by, color=chartkit.BASELINE_STYLE['color'], lw=0.8, ls='--',
                   alpha=0.6)
        ax.scatter([bx], [by], marker='*', s=260,
                   c=chartkit.BASELINE_STYLE['color'], zorder=4,
                   label='FIFO baseline')
    ax.set_xlabel(xlabel, fontsize=8)
    # panel tag INSIDE the panel: the figure-level subtitle owns the strip above
    ax.text(0.02, 0.975, title, transform=ax.transAxes, va='top', ha='left',
            fontsize=9, fontweight='bold', color='#333333', zorder=5)


@evaluation(key='headline.throughput_vs_labor',
            label='Throughput vs total labor (scatter + Spearman ρ)',
            scope='config', needs=('series',),
            family='headline', views=('absolute',))
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    bd = S.get(base['key']) if base else None

    xs_prod, ys_thr, xs_dur, xs_thr_task, colors, labels = [], [], [], [], [], []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if not d:
            continue
        prod = float(chartkit.to_hours(_f(d.get('ss_prod_hours'))))
        thr = _f(d.get('ss_thr')) * _PER_HOUR
        dur = float(chartkit.to_hours(_f(d.get('ss_dur'))))
        if not (np.isfinite(prod) and np.isfinite(thr)):
            continue
        xs_prod.append(prod)
        ys_thr.append(thr)
        xs_dur.append(dur)
        xs_thr_task.append(_f(d.get('ss_thr_task')) * _PER_HOUR)
        colors.append(chartkit.strategy_color(s, ctx.strategies))
        labels.append(_stitle(s))
    if len(xs_prod) < 2:
        return

    rho_pl = _rho(xs_prod, ys_thr)        # task makespan (labor) vs throughput — expected ~0
    rho_dl = _rho(xs_dur, ys_thr)         # batch makespan vs throughput — expected strongly negative
    rho_tt = _rho(xs_thr_task, ys_thr)    # thr/task vs thr/batch — how coupled the two are

    base_a = ((float(chartkit.to_hours(_f(bd.get('ss_prod_hours')))),
               _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)
    base_b = ((float(chartkit.to_hours(_f(bd.get('ss_dur')))),
               _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)
    base_c = ((_f(bd.get('ss_thr_task')) * _PER_HOUR,
               _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)

    ch = chartkit.make(panels=3, ncols=3, panel_w=5.2, panel_h=4.2,
                       legend='gutter', legend_labels=['FIFO baseline'],
                       sharey=True)
    a1, a2, a3 = ch.axes
    _panel(a1, xs_prod, ys_thr, colors, labels, base_a,
           f'task makespan ({chartkit.HOURS}, ← better)',
           'A: throughput vs task makespan (labor)', sizes=_sizes(xs_dur))
    _panel(a2, xs_dur, ys_thr, colors, labels, base_b,
           f'batch makespan ({chartkit.HOURS}, ← better)',
           'B: throughput vs batch makespan')
    _panel(a3, xs_thr_task, ys_thr, colors, labels, base_c,
           'throughput / task makespan (items / hour, → better)',
           'C: ↑ scheduling win · → labor win', sizes=_sizes(xs_dur))
    a1.set_ylabel('throughput / batch makespan (items / hour, ↑ better)', fontsize=8)
    chartkit.shared_ylim(ch.axes, [ys_thr])

    ch.legend(title=None)
    fmt = lambda r: f'{r:.2f}' if np.isfinite(r) else 'n/a'
    ch.title('Throughput tracks batch makespan, not labor',
             f'Spearman ρ — A (task makespan): {fmt(rho_pl)} · '
             f'B (batch makespan): {fmt(rho_dl)} · '
             f'C (two throughputs): {fmt(rho_tt)} · marker size = batch makespan')
    ch.save(os.path.join(io.out_dir(ctx), 'absolute_throughput_vs_labor.png'),
            view='absolute')
