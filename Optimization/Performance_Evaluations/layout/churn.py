"""layout.churn — inventory churn over time, all arms on ONE overlay.

Replaces the retired 34-facet churn grid.  The churn series is ported unchanged:
(reload moves + reorder placements) per batch, as a % of total bins when the bin
count is known, rolled with the steady-state window.  The overlay draws the
cross-arm MEDIAN as one bold line and the cross-arm IQR as a band; an individual
arm is drawn (and labelled) ONLY when its curve leaves the median by more than 2×
the IQR somewhere — everything conforming stays inside the band, and a corner note
says how many arms that is, so 30 indistinguishable facets become one statement
plus its exceptions.
"""
import os

import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle, _WIN

_MEDIAN_COL = '#1a4d7a'


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

    outliers = []
    for k in curves:
        dev = (M[k] - med).abs()
        mask = np.isfinite(dev.values) & np.isfinite(iqr.values)
        if mask.any() and np.any(dev.values[mask] > 2.0 * iqr.values[mask] + 1e-12):
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
    for k in outliers:
        s = by_key[k]
        ax.plot(M[k].index.values, M[k].values,
                color=chartkit.strategy_color(s, ctx.strategies),
                ls=chartkit.strategy_dash(s), lw=1.2, label=_stitle(s), zorder=3)
    ax.set_xlabel('batch')
    unit = '% of bins moved / batch' if ctx.total_bins > 0 else 'bins moved / batch'
    ax.set_ylabel(unit, fontsize=8)
    vals = [med.values, q25.values, q75.values] + [M[k].values for k in outliers]
    chartkit.shared_ylim([ax], vals)
    ax.text(0.01, 0.965,
            f'{inside} of {len(curves)} arms stay within 2× the IQR of the median',
            transform=ax.transAxes, fontsize=6.5, color='#777777', va='top')
    ch.legend()
    ch.title('Inventory churn across arms',
             'bold = cross-arm median · band = cross-arm IQR · '
             'lines = arms leaving the band')
    ch.save(os.path.join(io.out_dir(ctx), 'absolute_churn_overlay.png'),
            view='absolute')
