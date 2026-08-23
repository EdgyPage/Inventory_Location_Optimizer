"""layout.churn — inventory churn over time, all arms on ONE overlay.

Replaces the retired 34-facet churn grid.  The churn series is ported unchanged:
(reload moves + reorder placements) per batch, as a % of total bins when the bin
count is known, rolled with the steady-state window.  The overlay draws the
cross-arm MEDIAN as one bold line and the cross-arm IQR as a band; an individual
arm is drawn (and labelled) ONLY when its curve leaves the median by more than 2×
the IQR somewhere AND by enough of the plotted range to be findable on the canvas
— everything conforming stays inside the band, and a corner note says how many arms
that is, so 30 indistinguishable facets become one statement plus its exceptions.

A LEGEND ROW IS A PROMISE.  The IQR test alone cannot keep it: churn is identical
across most arms on most runs, so the cross-arm IQR collapses to ~0 and `dev > 2 ×
0` promotes any arm that differs in the last float digit.  Seven such arms were
legended and drawn — at the median's own values, underneath the median's own bold
line, invisible.  Hence the second test (`_VISIBLE_FRAC`) and the raised z-order
with a light halo: whatever the legend names, the reader can point at.
"""
import os

import numpy as np
import pandas as pd
import matplotlib.patheffects as pe

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle, _WIN

_MEDIAN_COL = '#1a4d7a'

#: An arm earns a legend row only when its WORST departure from the median is at least
#: this fraction of the full cross-arm range — i.e. only when the drawn trace separates
#: from the median line by something the eye can resolve.  Measured against the range over
#: EVERY arm (not just the ones kept), so the bar does not move with the selection.
_VISIBLE_FRAC = 0.02


def _churn_series(df, total_bins):
    """Ported: bins moved per batch (reloads + reorder placements), % of bins when
    the total is known, rolling-mean smoothed."""
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
    if total_bins > 0:
        moved = moved / total_bins * 100.0
    return pd.Series(moved.rolling(_WIN, min_periods=1).mean().values,
                     index=d['batch_id'].values)


@evaluation(key='layout.churn', label='Inventory churn overlay (median + IQR + outliers)',
            scope='config', needs=('batch', 'series'),
            family='layout', views=('absolute',))
def render(ctx, params):
    S = ctx.series()
    curves = {}
    for s in ctx.strategies:
        if S.get(s['key']) is None:
            continue
        ser = _churn_series(ctx.batch_df(s['key']), ctx.total_bins)
        if ser is not None and len(ser):
            curves[s['key']] = ser
    if len(curves) < 2:
        return
    by_key = {s['key']: s for s in ctx.strategies}
    M = pd.concat(curves.values(), axis=1, keys=curves.keys()).sort_index()
    med = M.median(axis=1)
    q25 = M.quantile(0.25, axis=1)
    q75 = M.quantile(0.75, axis=1)
    iqr = q75 - q25

    finite = M.values[np.isfinite(M.values)]
    full_span = float(finite.max() - finite.min()) if finite.size else 0.0
    floor = _VISIBLE_FRAC * full_span

    outliers = []
    for k in curves:
        dev = (M[k] - med).abs()
        mask = np.isfinite(dev.values) & np.isfinite(iqr.values)
        if not mask.any():
            continue
        apart = np.any(dev.values[mask] > 2.0 * iqr.values[mask] + 1e-12)
        worst = float(np.max(dev.values[mask]))
        # BOTH: statistically outside the pack, and far enough off the median to draw as
        # a distinguishable trace.  Failing the second test means the arm conforms for
        # every purpose a reader has, whatever a degenerate IQR says.
        if apart and worst > 0.0 and worst >= floor:
            outliers.append(k)
    inside = len(curves) - len(outliers)

    labels = (['median across arms', 'IQR across arms']
              + [_stitle(by_key[k]) for k in outliers])
    ch = chartkit.make(panels=1, panel_w=7.2, panel_h=4.6,
                       legend='gutter', legend_labels=labels)
    ax = ch.ax
    x = med.index.values
    chartkit.draw_ci(ax, x, q25.values, q75.values, band=True,
                     label='IQR across arms')
    ax.plot(x, med.values, color=_MEDIAN_COL, lw=2.4, label='median across arms',
            zorder=4)
    # Above the median (zorder 4) and the band, with a light halo: a deviating arm can
    # still run alongside the median for most of its length, and a 1.2-wide line under a
    # 2.4-wide one is a legend entry the reader cannot find.
    for k in outliers:
        s = by_key[k]
        ax.plot(M[k].index.values, M[k].values,
                color=chartkit.strategy_color(s, ctx.strategies),
                ls=chartkit.strategy_dash(s), lw=1.9, label=_stitle(s), zorder=6,
                path_effects=[pe.Stroke(linewidth=3.6, foreground='white', alpha=0.9),
                              pe.Normal()])
    ax.set_xlabel('batch')
    unit = '% of bins moved / batch' if ctx.total_bins > 0 else 'bins moved / batch'
    ax.set_ylabel(unit, fontsize=8)
    vals = [med.values, q25.values, q75.values] + [M[k].values for k in outliers]
    chartkit.shared_ylim([ax], vals)
    ax.text(0.01, 0.965,
            f'{inside} of {len(curves)} arms track the median — inside 2× the cross-arm '
            f'IQR, or off it by less than {_VISIBLE_FRAC:.0%} of the plotted range',
            transform=ax.transAxes, fontsize=6.5, color='#777777', va='top')
    ch.legend()
    ch.title('Inventory churn across arms',
             'bold = cross-arm median · band = cross-arm IQR · '
             + ('haloed lines = arms leaving the band' if outliers
                else 'no arm leaves the band'))
    ch.save(os.path.join(io.out_dir(ctx), 'absolute_churn_overlay.png'),
            view='absolute')
