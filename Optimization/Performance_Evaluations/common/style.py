"""Styling + layout helpers shared across graphs (verbatim from the retired monoliths).

Holds the rolling-window constants, color/linestyle maps, compact-label helpers, the
KDE overlay, subplot-grid builders, and the focus filter applied once at context build.
"""
import math

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde

_TRAVEL_COL = '#a9a9a9'

# ── rolling-average windows ────────────────────────────────────────────────────
_WIN    = 50   # batches — steady-state scalar window (ss_* / ranking / aggregate)
_SMOOTH = 5    # batches — over-time trajectory smoothing.  Kept small so early dynamics
               # (e.g. an optimal-start layout advantage converging away over the first
               # ~30 batches) stay visible instead of being averaged out by a half-horizon
               # window.  Decoupled from _WIN so plot smoothing never moves the rankings.

_LINESTYLES = ['-', '--', '-.', ':']
_TOP_DIMS = ('initial', 'assignment', 'reslot')


def _kde_plot(ax, data, color, bins):
    ax.hist(data, bins=bins, color=color, alpha=0.65, edgecolor='white')
    if len(data) > 1 and data.max() > data.min():
        kde = gaussian_kde(data, bw_method='silverman')
        xs  = np.linspace(data.min(), data.max(), 400)
        ax.plot(xs, kde(xs) * len(data) * (data.max() - data.min()) / bins, color=color, lw=2)
    ax.axvline(data.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {data.mean():.1f}')
    ax.axvline(np.median(data), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(data):.1f}')


def _row(n, figw=4.8, h=4.5):
    """A row of n subplots; always returns a flat list of axes."""
    fig, axes = plt.subplots(1, n, figsize=(figw * n, h), squeeze=False)
    return fig, list(axes[0])


def _pct_delta(val, base):
    return (val - base) / abs(base) * 100 if base else 0.0


def _grid(n, panel_w=3.0, panel_h=2.3, max_cols=8):
    """A roughly-square subplot grid for n strategies; returns (fig, n flat axes)."""
    cols = min(max_cols, max(1, math.ceil(math.sqrt(n))))
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(panel_w * cols, panel_h * rows), squeeze=False)
    flat = [ax for row in axes for ax in row]
    for ax in flat[n:]:
        ax.axis('off')
    return fig, flat[:n]


def legend_right(ax, handles=None, *, anchor=(1.02, 1.0), **kw):
    """Place a legend just OUTSIDE the axes on the right, never overlapping the data.

    _save_close saves with bbox_inches='tight', so the outside legend is captured in full.
    `anchor` is the axes-fraction bbox_to_anchor (use a lower y to stack a second legend).
    """
    kwargs = dict(loc='upper left', bbox_to_anchor=anchor, borderaxespad=0.0, frameon=True)
    kwargs.update(kw)
    if handles is not None:
        return ax.legend(handles=handles, **kwargs)
    return ax.legend(**kwargs)


def _stitle(s):
    """Compact strategy label: initial|assignment|reslot (falls back to label/key)."""
    parts = [p for p in (s.get('initial', ''), s.get('assignment', ''),
                         s.get('reslot', '')) if p]
    return '|'.join(parts) if parts else s.get('label', s.get('key', ''))


def _assign_color_map(strategies):
    asn = sorted({s['assignment'] for s in strategies})
    cmap = plt.cm.tab10
    return {a: cmap(i % 10) for i, a in enumerate(asn)}


def _ir_style_map(strategies):
    irs = sorted({(s['initial'], s['reslot']) for s in strategies})
    return {ir: _LINESTYLES[i % len(_LINESTYLES)] for i, ir in enumerate(irs)}


def _short(key: str) -> str:
    """Compact axis label: drop the trailing reslot token (_norsl)."""
    return key[:-6] if key.endswith('_norsl') else key


def _focus_filter(strategies, focus):
    """Restrict strategies to one initial family by key prefix ('uni'/'opt'); fall back
    to all if the prefix matches nothing or focus='all'.  opt_* win mainly from their
    initial-stock advantage, so 'uni' isolates the reorder-policy comparison."""
    if focus in ('uni', 'opt'):
        sub = [s for s in strategies if str(s.get('key', '')).startswith(focus + '_')]
        if sub:
            return sub
    return strategies
