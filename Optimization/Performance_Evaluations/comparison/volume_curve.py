"""compare.volume_curve — cumulative pick volume against elapsed time: how much work each arm
finishes per unit of time, and how far ahead of FIFO it is at any given hour.  Under compare/top.

WHY A CURVE AND NOT A BAR.  Throughput as a single number hides *when* the work happened.  Plotting
running items against running elapsed hours makes the rate itself the visible thing: the SLOPE of the
curve IS throughput (items per hour), so a steeper line that terminates further left finished the
same demand sooner.  That is the whole "better throughput at equal labor" story in one picture — the
arms do not do less work, they do it in less elapsed time.

THE TIME AXIS IS SYNTHESIZED, AND HAS TO BE.  There is no global clock in the data: every batch's sim
clock restarts at zero (batch_start_time is 0.0 for every row, batch_end_time == duration).  So
elapsed time is the running sum of batch makespans, which is exactly the quantity run_whatif_labor
already calls `batch_hours`.  Same definition, same units, so numbers reconcile across the suite.

ON THE AREA METRIC — read this before quoting it.  The cumulative curve is very nearly a straight
line from the origin (measured shape index 1.0007 on a real 100-batch store arm), so its raw area is
T*I/2 and carries NO information the two endpoints do not already give.  Reporting a bare "AUC" would
therefore be a dressed-up restatement of items x hours.  Three numbers are reported instead, and only
the first two are performance measures:

    mean_thr_items_hr    PRIMARY.  items / elapsed hours = the chord slope.  Higher is better.
                         This is what the area reduces to; saying it directly is the same
                         information without pretending to measure curve shape.
    auc_gain_vs_fifo_pct The area BETWEEN this arm's curve and FIFO's, over FIFO's own area, on a
                         shared hour grid.  Higher is better.  Non-degenerate: two straight lines of
                         different slope have a gap that grows with time, which is precisely
                         "by any given hour, how much more work is finished".
    shape_index          area / (T*I/2).  A STABILITY DIAGNOSTIC, not a score.  ~1.000 means the rate
                         held steady for the whole run; a bend would show as a departure from 1.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot').
"""
import os

import numpy as np
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.style import (
    _stitle, _LINESTYLES, _TOP_DIMS, legend_right)
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.comparison import top_tag

MS_PER_HOUR = 3.6e6            # same conversion run_whatif_labor uses, so hours are comparable
HOURS_NOTE  = ('modeled sim pick-time hours (batch_stats ms / 3.6e6) — not wall-clock')


def cumulative_curve(df):
    """(hours, items) running totals over batches, ordered by batch_id.

    hours[i] is elapsed time at the END of batch i, so the pair (hours, items) is the point
    "this much work had been finished by this much elapsed time".
    """
    if df is None or df.empty:
        return np.array([]), np.array([])
    d = df.sort_values('batch_id')
    hrs = np.cumsum(d['duration'].to_numpy(dtype=float)) / MS_PER_HOUR
    items = np.cumsum(d['total_items'].to_numpy(dtype=float))
    return hrs, items


def curve_metrics(hrs, items):
    """{'items','elapsed_hours','mean_thr_items_hr','auc_item_hours','shape_index'}.

    Trapezoid area from the origin: the curve starts at (0,0) by construction, and a batch is only
    counted once it has completed.
    """
    if hrs.size == 0 or items.size == 0 or hrs[-1] <= 0:
        return None
    x = np.concatenate(([0.0], hrs))
    y = np.concatenate(([0.0], items))
    auc = float(np.trapezoid(y, x)) if hasattr(np, 'trapezoid') else float(np.trapz(y, x))
    T, I = float(hrs[-1]), float(items[-1])
    tri = T * I / 2.0
    return {'items': I, 'elapsed_hours': T,
            'mean_thr_items_hr': I / T,
            'auc_item_hours': auc,
            'shape_index': (auc / tri) if tri > 0 else float('nan')}


def gain_vs_base(hrs, items, base_hrs, base_items):
    """(grid_hours, delta_items, gain_pct) — this arm's lead over the baseline at matched time.

    Both curves are interpolated onto a shared grid spanning only the hours BOTH actually cover, so
    the comparison never extrapolates past the shorter run.  gain_pct is the area between the curves
    over the baseline's own area on that grid, which is the honest "how far ahead, on average".
    """
    if hrs.size == 0 or base_hrs.size == 0:
        return np.array([]), np.array([]), float('nan')
    hi = min(float(hrs[-1]), float(base_hrs[-1]))
    if hi <= 0:
        return np.array([]), np.array([]), float('nan')
    grid = np.linspace(0.0, hi, 200)
    a = np.interp(grid, np.concatenate(([0.0], hrs)), np.concatenate(([0.0], items)))
    b = np.interp(grid, np.concatenate(([0.0], base_hrs)), np.concatenate(([0.0], base_items)))
    delta = a - b
    trapz = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz
    base_area = float(trapz(b, grid))
    gain = (float(trapz(delta, grid)) / base_area * 100.0) if base_area > 0 else float('nan')
    return grid, delta, gain


def _plot(selected, gof, frames, baseline, top_n, top_by, title, path):
    """Left: cumulative volume vs elapsed hours.  Right: lead over FIFO at matched hours."""
    b_hrs, b_items = cumulative_curve(frames.get(baseline['key']))
    gstyle = {g: _LINESTYLES[i % len(_LINESTYLES)]
              for i, g in enumerate(sorted(set((gof or {}).values())))}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17, 6.2))
    drew = False
    rows = {}
    for s in selected:
        hrs, items = cumulative_curve(frames.get(s['key']))
        m = curve_metrics(hrs, items)
        if m is None:
            continue
        ls = gstyle.get((gof or {}).get(s['key']), '-')
        ax1.plot(hrs, items, color=s['color'], lw=1.9, ls=ls,
                 label=(f"{_stitle(s)}  {m['mean_thr_items_hr']:,.0f} items/h"
                        f"  · {m['elapsed_hours']:.1f} h"))
        # Endpoint marker: arms differ in BOTH total volume and elapsed time (measured spread on a
        # real run: 6.8% in items, 69% in hours), so the finish point is the informative part of
        # this panel — the curves themselves are near-identical straight lines.
        ax1.plot([hrs[-1]], [items[-1]], marker='o', ms=5, color=s['color'], zorder=5)
        if s['key'] != baseline['key']:
            grid, delta, gain = gain_vs_base(hrs, items, b_hrs, b_items)
            if grid.size:
                ax2.plot(grid, delta, color=s['color'], lw=1.9, ls=ls,
                         label=f"{_stitle(s)}  ({gain:+.1f}%)")
                m['auc_gain_vs_fifo_pct'] = gain
        rows[s['key']] = m
        drew = True

    if not drew:
        plt.close(fig)
        return {}

    if b_hrs.size:
        bm = curve_metrics(b_hrs, b_items)
        ax1.plot(b_hrs, b_items, color='grey', lw=2.4, ls='--', zorder=1,
                 label=(f'baseline (FIFO) · {bm["mean_thr_items_hr"]:,.0f} items/h'
                        f'  · {bm["elapsed_hours"]:.1f} h' if bm else 'baseline (FIFO)'))
        if bm:
            ax1.plot([b_hrs[-1]], [b_items[-1]], marker='o', ms=6, color='grey', zorder=5)
    ax1.set_xlabel('elapsed hours')
    ax1.set_ylabel('cumulative items picked')
    ax1.grid(alpha=0.3)
    ax1.set_title('Work finished vs elapsed time — slope = throughput, dot = finish',
                  fontsize=11)

    ax2.axhline(0, color='grey', lw=1.0, ls='--', label='baseline (FIFO)')
    ax2.set_xlabel('elapsed hours')
    ax2.set_ylabel('items ahead of FIFO at the same elapsed time (↑ better)')
    ax2.grid(alpha=0.3)
    ax2.set_title('Lead over FIFO at matched time', fontsize=11)

    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    fig.suptitle(title + sub, fontsize=12, fontweight='bold')
    # The hours caveat appears ONCE, under the figure, rather than on both x-axes: these are
    # modeled effort hours and every downstream claim has to carry that qualifier.
    fig.text(0.5, 0.005, f'elapsed hours = {HOURS_NOTE}', ha='center', va='bottom',
             fontsize=8, color='#555555')
    legend_right(ax1, fontsize=7)
    legend_right(ax2, fontsize=7)
    plt.tight_layout(rect=(0, 0.035, 1, 0.93))
    _save_close(fig, path)
    return rows


@evaluation(key='compare.volume_curve',
            label='Cumulative pick volume vs elapsed time, with rate + lead-over-FIFO metrics',
            scope='config', needs=('batch', 'series'), out_subdir='compare/top',
            defaults={'top_n': 3, 'top_by': 'initial'})
def render(ctx, params):
    top_n  = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    selected, gof = _select_top(ctx.strategies, ctx.series(), top_n, top_by)
    if not selected:
        return
    out = os.path.join(ctx.run_dir, 'compare', 'top')
    os.makedirs(out, exist_ok=True)
    tag = top_tag(top_n, top_by)
    frames = {s['key']: ctx.batch_df(s['key']) for s in list(selected) + [ctx.base]}
    # _plot returns a per-arm metrics dict (mean_thr_items_hr, auc_gain_vs_fifo_pct,
    # shape_index) that is INTENTIONALLY unconsumed here: persisting it would add a run-tree
    # artifact (a hashed contract change), and the cross-cell layer (run_whatif_volume)
    # already derives the published numbers from batch_stats itself.
    _plot(selected, gof, frames, ctx.base, top_n, top_by,
          f'Cumulative pick volume vs elapsed time  [{ctx.title}]',
          os.path.join(out, f'{tag}_volume_curve.png'))
