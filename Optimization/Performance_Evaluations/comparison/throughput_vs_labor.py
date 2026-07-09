"""compare.throughput_labor — does throughput follow total labor?  (no rerun, no DB touch)

Two scatter panels, one point per strategy arm, built entirely from the per-arm series
scalars that already live in series.json (ss_prod_hours, ss_thr, ss_dur):

  * Panel A: x = ss_prod_hours (total task labor, the minimized objective, lower=better)
             y = ss_thr        (throughput = items / makespan, higher=better)
             marker size ∝ ss_dur (makespan).  The best corner is UP-LEFT.
  * Panel B: x = ss_dur (makespan)  vs  y = ss_thr — the mechanistic link.

The point is to SHOW that throughput does NOT track total labor: because the picker
scheduler is static round-robin by aisle with no load balancing (Warehouse/Pick.py), a
placement that lowers Σ task labor can pile work onto the busiest picker, raising makespan
and lowering throughput (throughput ≈ items/makespan).  So throughput tracks 1/makespan
(Panel B, tight) — not total labor (Panel A, a scattered cloud).  The Spearman ρ in each
title quantifies it.  Writes compare/throughput_vs_labor.png.
"""
import os

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from Optimization.Performance_Evaluations.core.registry import evaluation
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

    xs_prod, ys_thr, xs_dur, colors, labels = [], [], [], [], []
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
        colors.append(s.get('color') or '#4c72b0')
        labels.append(_stitle(s))
    if len(xs_prod) < 2:
        return

    rho_pl = _rho(xs_prod, ys_thr)   # labor  vs throughput — expected ~0 (non-monotone)
    rho_dl = _rho(xs_dur, ys_thr)    # makespan vs throughput — expected strongly negative

    base_a = (_f(bd.get('ss_prod_hours')), _f(bd.get('ss_thr'))) if bd else None
    base_b = (_f(bd.get('ss_dur')), _f(bd.get('ss_thr'))) if bd else None

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.2))
    _panel(a1, xs_prod, ys_thr, colors, labels, base_a,
           'Total task labor  ss_prod_hours  (lower = better →)',
           'Throughput  ss_thr = items / makespan  (higher = better)',
           f'Throughput vs total labor   (Spearman ρ = '
           f'{rho_pl:.2f})' if np.isfinite(rho_pl) else 'Throughput vs total labor   (ρ n/a)',
           sizes=_sizes(xs_dur))
    _panel(a2, xs_dur, ys_thr, colors, labels, base_b,
           'Makespan  ss_dur  (lower = better →)',
           'Throughput  ss_thr = items / makespan  (higher = better)',
           f'Throughput vs makespan   (Spearman ρ = '
           f'{rho_dl:.2f})' if np.isfinite(rho_dl) else 'Throughput vs makespan   (ρ n/a)')
    legend_right(a1, fontsize=8)
    fig.suptitle(
        f'Does throughput follow total labor?  No — it tracks 1/makespan  [{ctx.title}]\n'
        f'Panel A cloud (labor⊥throughput) vs Panel B tight inverse (makespan drives throughput). '
        f'Marker size = makespan.  Scheduler = static round-robin, no load balancing.',
        fontsize=11, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.93))
    out = os.path.join(ctx.run_dir, 'compare')
    os.makedirs(out, exist_ok=True)
    _save_close(fig, os.path.join(out, 'throughput_vs_labor.png'))
