"""headline.throughput_vs_labor — the four success metrics, and the adoption question.

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
the subtitle quantifies it.

A fourth figure, the PERCENT view, answers the question the three scatter panels set up
but do not close: which arm to adopt.  Labor gain against throughput gain, both against
the baseline, so the corner where an arm beats FIFO on both is a place on the chart
rather than a comparison the reader has to carry between two other figures.

UNITS.  Throughput is items/hour on its face.  The two makespan axes are not: a makespan
is whatever the run makes it (a small cell's batch finishes in seconds, a full run's in
hours), so both resolve ONE shared time unit from their pooled magnitudes and name it in
the axis label — panels A and B measure the same kind of quantity and must never be read
in different units.  The ρ triplet is rank-based, hence unit-invariant.
"""
import os

import numpy as np
from matplotlib.patches import Rectangle
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
            family='headline', views=('absolute', 'percent'))
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    bd = S.get(base['key']) if base else None

    xs_prod, ys_thr, xs_dur, xs_thr_task, colors, labels = [], [], [], [], [], []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if not d:
            continue
        prod = _f(d.get('ss_prod_hours'))       # raw sim milliseconds
        thr = _f(d.get('ss_thr')) * _PER_HOUR
        dur = _f(d.get('ss_dur'))               # raw sim milliseconds
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

    # ONE unit for both makespan axes: every arm's task makespan and batch makespan,
    # plus the baseline's, pooled into a single decision so panels A and B are read in
    # the same named unit whatever scale the run turned out to be.
    base_prod = _f(bd.get('ss_prod_hours')) if bd else float('nan')
    base_dur = _f(bd.get('ss_dur')) if bd else float('nan')
    div, unit = chartkit.time_units(np.concatenate(
        [np.asarray(xs_prod, float), np.asarray(xs_dur, float),
         np.asarray([base_prod, base_dur], float)]))
    xs_prod = list(np.asarray(xs_prod, float) / div)
    xs_dur = list(np.asarray(xs_dur, float) / div)

    rho_pl = _rho(xs_prod, ys_thr)        # task makespan (labor) vs throughput — expected ~0
    rho_dl = _rho(xs_dur, ys_thr)         # batch makespan vs throughput — expected strongly negative
    rho_tt = _rho(xs_thr_task, ys_thr)    # thr/task vs thr/batch — how coupled the two are

    base_a = ((base_prod / div, _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)
    base_b = ((base_dur / div, _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)
    base_c = ((_f(bd.get('ss_thr_task')) * _PER_HOUR,
               _f(bd.get('ss_thr')) * _PER_HOUR) if bd else None)

    ch = chartkit.make(panels=3, ncols=3, panel_w=5.2, panel_h=4.2,
                       legend='gutter', legend_labels=['FIFO baseline'],
                       sharey=True)
    a1, a2, a3 = ch.axes
    _panel(a1, xs_prod, ys_thr, colors, labels, base_a,
           f'task makespan ({unit}, ← better)',
           'A: throughput vs task makespan (labor)', sizes=_sizes(xs_dur))
    _panel(a2, xs_dur, ys_thr, colors, labels, base_b,
           f'batch makespan ({unit}, ← better)',
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
    _both_levers(ctx, S, base, bd)


def _both_levers(ctx, S, base, bd):
    """percent view — each arm's labor gain against its throughput gain, vs the baseline.

    The question two independent reviewers asked and no artifact answered: WHICH ONE DO
    WE ADOPT.  Every other headline figure ranks on one quantity, and the podium is not
    the same on both — an arm can save labor by spreading work to the least-loaded aisle
    and finish the day later for it.  Here the baseline is the origin, the shaded corner
    is "better on both", and an arm in an off-diagonal quadrant is trading one for the
    other in the direction its position names.
    """
    if bd is None:
        return
    b_prod, b_thr = _f(bd.get('ss_prod_hours')), _f(bd.get('ss_thr'))
    pts = []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if not d or s['key'] == base['key']:
            continue
        lab = chartkit.improvement_pct(_f(d.get('ss_prod_hours')), b_prod,
                                       lower_is_better=True)
        thr = chartkit.improvement_pct(_f(d.get('ss_thr')), b_thr,
                                       lower_is_better=False)
        if np.isfinite(lab) and np.isfinite(thr):
            pts.append((s, lab, thr))
    if len(pts) < 2:
        return

    ch = chartkit.make(panels=1, panel_w=6.6, panel_h=5.4, legend='gutter',
                       legend_labels=['better on both', 'FIFO baseline'])
    ax = ch.ax
    labs = [p[1] for p in pts]
    thrs = [p[2] for p in pts]
    hi_x, hi_y = max(labs + [0.0]), max(thrs + [0.0])
    x0, x1 = min(labs + [0.0]) * 1.2 - 0.2, hi_x * 1.25 + 0.3
    y0, y1 = min(thrs + [0.0]) * 1.2 - 0.2, hi_y * 1.25 + 0.3
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    # The winning quadrant in DATA coordinates: axhspan's xmin is an AXES fraction, and
    # zero sits at the axes midpoint only by coincidence — shaded that way the region
    # reached left of the zero line and called losing arms winners.
    ax.add_patch(Rectangle((0, 0), x1, y1, facecolor='#e8f4ea', zorder=0,
                           label='better on both'))
    chartkit.reference_line(ax, 0.0, orient='y')
    chartkit.reference_line(ax, 0.0, orient='x')
    for s, lab, thr in pts:
        ax.scatter([lab], [thr], s=46, zorder=3, edgecolors='black', linewidths=0.5,
                   color=chartkit.strategy_color(s, ctx.strategies))
    # Name only the arms a reader would act on: the ones that win on both, ranked by the
    # smaller of the two gains, so a labelled arm is one with no weak side.  The winners
    # cluster tightly by construction, so the labels are stacked at a fixed pitch and
    # leader-lined back to their point instead of written on top of one another.
    winners = sorted([p for p in pts if p[1] > 0 and p[2] > 0],
                     key=lambda p: -min(p[1], p[2]))[:4]
    chartkit.annotate_points(ax, [(lab, thr, _stitle(s)) for s, lab, thr in winners],
                             fontsize=7,
                             colors=[chartkit.strategy_color(s, ctx.strategies)
                                     for s, _l, _t in winners])
    chartkit.pct_axis(ax, better='up', axis='x')
    chartkit.pct_axis(ax, better='up', axis='y')
    ax.set_xlabel('labor saved vs FIFO (↑ better)')
    ax.set_ylabel('throughput vs FIFO (↑ better)')
    n_both = sum(1 for _s, a, b in pts if a > 0 and b > 0)
    ch.legend(title=None)
    ch.title('Which arm wins on BOTH levers',
             f'{n_both} of {len(pts)} arms improve labor and throughput together; '
             f'the rest trade one for the other')
    ch.save(os.path.join(io.out_dir(ctx), 'percent_both_levers.png'), view='percent')
