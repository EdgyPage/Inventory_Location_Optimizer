"""compare.throughput_labor — the four success metrics on one figure  (no rerun, no DB touch)

Three scatter panels, one point per strategy arm, built from the per-arm series scalars in
series.json.  Vocabulary: TASK makespan = ss_prod_hours = Σ task time = total labor (the serial
makespan a lone picker would incur); BATCH makespan = ss_dur = last-picker finish (parallel
wall-clock).  The two throughputs are items ÷ each makespan:
  * ss_thr      = items / batch makespan  (metric d)
  * ss_thr_task = items / task makespan   (metric c)

  * Panel A: x = task makespan (ss_prod_hours, lower=better)  vs  y = throughput / batch makespan
             (ss_thr).  Marker size ∝ batch makespan (ss_dur).  Best corner is UP-LEFT.
  * Panel B: x = batch makespan (ss_dur)  vs  y = throughput / batch makespan — the mechanistic link.
  * Panel C: x = throughput / task makespan (ss_thr_task)  vs  y = throughput / batch makespan
             (ss_thr).  Separates the two win types: a pure SCHEDULING win (batch makespan ↓ at flat
             task makespan) moves a point straight UP; a LABOR win (less task time per item) moves it
             RIGHT.  "A throughput win at flat task makespan is still a win" = vertical travel here.

The thesis stays: throughput does NOT track task makespan (total labor) — a placement that lowers Σ
task labor can pile work onto the busiest picker, raising batch makespan and lowering throughput.  So
throughput tracks 1/batch-makespan (Panel B, tight) — not task makespan (Panel A, a scattered cloud).
The Spearman ρ in each title quantifies it.  Writes compare/throughput_vs_labor.png.
"""
import os

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.style import _stitle, legend_right


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


def _panel(ax, xs, ys, colors, labels, base_xy, xlabel, ylabel, title, sizes=None):
    ax.scatter(xs, ys, s=(sizes if sizes is not None else 90), c=colors,
               edgecolors='white', linewidths=0.5, zorder=3)
    for x, y, lab in zip(xs, ys, labels):
        if np.isfinite(x) and np.isfinite(y):
            ax.annotate(lab, (x, y), fontsize=5, alpha=0.7,
                        xytext=(3, 3), textcoords='offset points')
    if base_xy is not None and all(np.isfinite(v) for v in base_xy):
        bx, by = base_xy
        ax.axvline(bx, color='k', lw=0.8, ls='--', alpha=0.6)
        ax.axhline(by, color='k', lw=0.8, ls='--', alpha=0.6)
        ax.scatter([bx], [by], marker='*', s=260, c='k', zorder=4, label='baseline (FIFO)')
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)


@evaluation(key='compare.throughput_labor',
            label='Throughput vs total labor (scatter + Spearman ρ)',
            scope='config', needs=('series',), out_subdir='compare')
def render(ctx, params):
    S = ctx.series()
    base = ctx.base
    bd = S.get(base['key']) if base else None

    xs_prod, ys_thr, xs_dur, xs_thr_task, colors, labels = [], [], [], [], [], []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if not d:
            continue
        prod, thr, dur = _f(d.get('ss_prod_hours')), _f(d.get('ss_thr')), _f(d.get('ss_dur'))
        if not (np.isfinite(prod) and np.isfinite(thr)):
            continue
        xs_prod.append(prod)
        ys_thr.append(thr)
        xs_dur.append(dur)
        xs_thr_task.append(_f(d.get('ss_thr_task')))
        colors.append(s.get('color') or '#4c72b0')
        labels.append(_stitle(s))
    if len(xs_prod) < 2:
        return

    rho_pl = _rho(xs_prod, ys_thr)        # task makespan (labor) vs throughput — expected ~0
    rho_dl = _rho(xs_dur, ys_thr)         # batch makespan vs throughput — expected strongly negative
    rho_tt = _rho(xs_thr_task, ys_thr)    # thr/task vs thr/batch — how coupled the two throughputs are

    base_a = (_f(bd.get('ss_prod_hours')), _f(bd.get('ss_thr'))) if bd else None
    base_b = (_f(bd.get('ss_dur')), _f(bd.get('ss_thr'))) if bd else None
    base_c = (_f(bd.get('ss_thr_task')), _f(bd.get('ss_thr'))) if bd else None

    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(21, 6.2))
    _panel(a1, xs_prod, ys_thr, colors, labels, base_a,
           'Task makespan  ss_prod_hours = Σ task time  (lower = better →)',
           'Throughput / batch makespan  ss_thr  (higher = better)',
           f'Throughput vs task makespan (labor)   (Spearman ρ = '
           f'{rho_pl:.2f})' if np.isfinite(rho_pl) else 'Throughput vs task makespan   (ρ n/a)',
           sizes=_sizes(xs_dur))
    _panel(a2, xs_dur, ys_thr, colors, labels, base_b,
           'Batch makespan  ss_dur  (lower = better →)',
           'Throughput / batch makespan  ss_thr  (higher = better)',
           f'Throughput vs batch makespan   (Spearman ρ = '
           f'{rho_dl:.2f})' if np.isfinite(rho_dl) else 'Throughput vs batch makespan   (ρ n/a)')
    _panel(a3, xs_thr_task, ys_thr, colors, labels, base_c,
           'Throughput / task makespan  ss_thr_task = items / Σ task time  (higher = better →)',
           'Throughput / batch makespan  ss_thr  (higher = better)',
           f'The two throughputs — ↑ = scheduling win, → = labor win   (Spearman ρ = '
           f'{rho_tt:.2f})' if np.isfinite(rho_tt) else 'The two throughputs   (ρ n/a)',
           sizes=_sizes(xs_dur))
    legend_right(a1, fontsize=8)
    fig.suptitle(
        f'Four success metrics: throughput tracks 1/batch-makespan, not task makespan  [{ctx.title}]\n'
        f'A: cloud (task makespan ⊥ throughput).  B: tight inverse (batch makespan drives throughput).  '
        f'C: vertical travel = a throughput win at flat task-makespan throughput (scheduling).  '
        f'Marker size = batch makespan.',
        fontsize=11, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.92))
    out = io.out_dir(ctx)                       # compare/, from the declaration
    _save_close(fig, os.path.join(out, 'throughput_vs_labor.png'))
