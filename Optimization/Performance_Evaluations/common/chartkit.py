"""chartkit — the one rendering contract every figure in the suite draws through.

The retired ad-hoc era had 18 distinct figsize formulas, three legend idioms, four sign
conventions, a provenance footer that collided with tick labels in a third of the chart
types, and axes anchored at zero that compressed 2–3% treatment effects into the top tenth
of the canvas.  This module replaces all of that with ONE set of policies:

  SIZING    computed from content (panel count, category count, legend label extents),
            never a per-module literal.
  LEGEND    lives in a RESERVED right gutter (or below, for wide categorical charts) that
            is part of the figure geometry — it cannot intersect the data region, and the
            save path no longer needs bbox_inches='tight' rescue tricks.
  FOOTER    the provenance line gets its own reserved band at the bottom of the figure;
            nothing else may draw there.
  TITLE     short (one clause, <= 60 chars); run keys and context belong to the footer.
  AXES      y-limits hug the data (`data_ylim`) with an explicit truncation marker when
            zero is excluded; panels either share a scale (`shared_ylim`) or say out loud
            that they don't (`annotate_unshared`).
  SIGN      `improvement_pct` — positive = better than baseline, everywhere.  No chart may
            invent its own convention.
  UNITS     durations render in HOURS via `to_hours`; the phrase "sim units" is banned.
  COLOR     one palette contract: color = assignment function (stable across every chart
            of a run), dash = initial family, and the baseline is always drawn in
            BASELINE_STYLE so FIFO is recognisable at a glance.
  EFFECT    `effect_label` prints the effect size first and the significance stars second —
            with n=75 paired batches everything is `***`, so the stars alone discriminate
            nothing.

A render builds a `Chart` via `make(...)`, draws on `chart.axes`, then `chart.save(path,
view=...)`.  `view` names which member of the family grammar (core/families.py) the figure
is — the save asserts the filename carries the matching `<view>_` prefix so the folder
listing itself documents what each file shows.  Non-registry callers (the what-if writers,
notebooks) pass view=None and skip the grammar check.
"""
from __future__ import annotations

import math

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.common import io as _io

# ── geometry constants (inches) ─────────────────────────────────────────────────
_PANEL_W, _PANEL_H = 4.8, 3.6      # default data-panel size
_TITLE_BAND  = 0.55                # suptitle strip
_FOOTER_BAND = 0.32                # provenance strip — reserved, nothing else draws here
_GUTTER_MIN, _GUTTER_MAX = 1.1, 3.4
_CHAR_W_IN   = 0.062               # approx glyph advance at legend fontsize (8pt)

# ── the single unit policy ──────────────────────────────────────────────────────
HOURS = 'hours'

def to_hours(sim_ms):
    """Duration sim values are milliseconds; every duration axis renders in hours."""
    return np.asarray(sim_ms, dtype=float) / 3.6e6


# ── the single sign convention ──────────────────────────────────────────────────
def improvement_pct(value, baseline, *, lower_is_better):
    """Percent improvement vs baseline, POSITIVE = BETTER — the only sign convention
    any chart in this package may use.  0 when the baseline is 0/absent."""
    if not baseline:
        return 0.0
    raw = (value - baseline) / abs(baseline) * 100.0
    return -raw if lower_is_better else raw


# ── the single palette contract ─────────────────────────────────────────────────
BASELINE_STYLE = dict(color='#222222', lw=2.2, ls='-', zorder=10)

_DASHES = ['-', '--', '-.', ':']


def strategy_color(s, strategies=None):
    """Stable color for a strategy dict: its upstream-assigned hex when present (the
    per-run summary publishes the same value, so charts and tables agree), else a
    deterministic tab20 slot keyed by sorted assignment name within `strategies`."""
    if s.get('color'):
        return s['color']
    asn = sorted({t['assignment'] for t in (strategies or [s])})
    return plt.cm.tab20(asn.index(s['assignment']) % 20)


def strategy_dash(s):
    """Linestyle by initial family — uni solid, opt dashed, anything else dash-dot.
    This is the ONLY meaning a dash pattern carries in the suite."""
    ini = str(s.get('initial', '')).lower()
    return '-' if ini.startswith('uni') else '--' if ini.startswith('opt') else '-.'


def mark_baseline(ax, x, y, label='FIFO baseline'):
    """Draw the baseline series so it reads as the reference in every chart."""
    return ax.plot(x, y, label=label, **BASELINE_STYLE)[0]


def baseline_handle(label='FIFO baseline'):
    return Line2D([], [], label=label, **BASELINE_STYLE)


# ── axes policy ─────────────────────────────────────────────────────────────────
def data_ylim(ax, values, *, pad=0.08, include=()):
    """Clip y to the data range (+pad fraction) instead of anchoring at zero.  When the
    resulting window excludes zero the axis is visibly TRUNCATED: a marker glyph on the
    y-axis spine plus a small corner note, so nobody mistakes a zoomed axis for the
    full range."""
    vals = np.asarray([v for v in np.ravel(values) if np.isfinite(v)], dtype=float)
    if vals.size == 0:
        return
    lo, hi = float(vals.min()), float(vals.max())
    for inc in include:
        lo, hi = min(lo, inc), max(hi, inc)
    span = (hi - lo) or (abs(hi) or 1.0)
    lo, hi = lo - span * pad, hi + span * pad
    ax.set_ylim(lo, hi)
    if lo > 0 or hi < 0:
        ax.plot([0, 0], [0.02, 0.045], transform=ax.transAxes, clip_on=False,
                color='#555555', lw=1.0)
        ax.plot([-0.012, 0.012], [0.0325, 0.0325], transform=ax.transAxes,
                clip_on=False, color='#555555', lw=1.0)
        ax.text(0.005, 0.008, 'y-axis truncated', transform=ax.transAxes,
                fontsize=6, color='#777777', va='bottom')


def shared_ylim(axes, values_per_ax, *, pad=0.08, include=()):
    """Force one honest scale across panels — the small-multiples rule."""
    allv = np.concatenate([np.ravel(np.asarray(v, dtype=float))
                           for v in values_per_ax if len(np.ravel(v))])
    for ax in axes:
        data_ylim(ax, allv, pad=pad, include=include)


def annotate_unshared(ax, axis='x'):
    """When panels legitimately cannot share a scale, they must SAY so."""
    ax.text(0.99, 1.01, f'independent {axis} scale', transform=ax.transAxes,
            fontsize=6.5, color='#8a4a00', ha='right', va='bottom', style='italic')


def pct_axis(ax, *, better='up', axis='y'):
    """Format an improvement axis: % ticks + the direction cue in the label position."""
    from matplotlib.ticker import FuncFormatter
    fmt = FuncFormatter(lambda v, _p: f'{v:+.1f}%')
    (ax.yaxis if axis == 'y' else ax.xaxis).set_major_formatter(fmt)
    arrow = '↑' if better == 'up' else '→'
    return f'({arrow} better)'


# ── significance / effect annotation ────────────────────────────────────────────
def stars(p):
    """Significance stars — thresholds 1e-3 / 1e-2 / 5e-2 (moved here from painters)."""
    if p is None or not np.isfinite(p):
        return ''
    return '***' if p < 1e-3 else '**' if p < 1e-2 else '*' if p < 5e-2 else 'ns'


def effect_label(p, effect, *, kind='g', pct=None):
    """Effect first, stars second: '−4.1% (g=0.62) ***'.  The paired n=75 design makes
    nearly every p three-star, so magnitude is what the label must lead with."""
    bits = []
    if pct is not None and np.isfinite(pct):
        bits.append(f'{pct:+.1f}%')
    if effect is not None and np.isfinite(effect):
        bits.append(f'({kind}={effect:.2f})')
    s = stars(p)
    if s:
        bits.append(s)
    return ' '.join(bits)


def draw_ci(ax, x, lo, hi, *, color='#555555', alpha=0.18, band=True, **kw):
    """Draw the confidence interval the stats layer computes (and, until now, nothing
    ever rendered).  band=True fills between series; band=False draws error whiskers."""
    if band:
        return ax.fill_between(x, lo, hi, color=color, alpha=alpha, lw=0, **kw)
    return ax.errorbar(x, (np.asarray(lo) + np.asarray(hi)) / 2.0,
                       yerr=(np.asarray(hi) - np.asarray(lo)) / 2.0,
                       fmt='none', ecolor=color, elinewidth=1.0, capsize=2.5, **kw)


# ── figure construction ─────────────────────────────────────────────────────────
def _gutter_width(labels, title):
    texts = [str(t) for t in (*labels, title or '')]
    chars = max((len(t) for t in texts), default=0)
    return min(_GUTTER_MAX, max(_GUTTER_MIN, 0.45 + chars * _CHAR_W_IN))


def make(*, panels=1, ncols=None, panel_w=_PANEL_W, panel_h=_PANEL_H,
         legend='gutter', legend_labels=(), legend_title=None,
         sharex=False, sharey=False, footer=True):
    """Build a Chart whose geometry is computed from its content.

    panels     total data panels; laid out on `ncols` columns (default: all in one row
               up to 3, then near-square).
    panel_w/h  per-panel inches; category-heavy charts pass the `for_categories` helpers.
    legend     'gutter' reserves a right-hand strip sized to `legend_labels`;
               'bottom' reserves a strip below the panels (wide categorical charts);
               'none' reserves nothing.
    The suptitle band and the provenance footer band are always part of the geometry, so
    neither can ever collide with data, ticks, or the legend.
    """
    if ncols is None:
        ncols = panels if panels <= 3 else math.ceil(math.sqrt(panels))
    nrows = math.ceil(panels / ncols)
    gut = _gutter_width(legend_labels, legend_title) if legend == 'gutter' else 0.0
    leg_rows = (math.ceil(len(legend_labels) / max(1, ncols * 2)) if legend == 'bottom'
                else 0)
    bot_leg = 0.28 * max(1, leg_rows) if legend == 'bottom' else 0.0
    fw = ncols * panel_w + gut + 0.9            # 0.9 ≈ y-labels + left margin
    fh = nrows * panel_h + _TITLE_BAND + bot_leg + (_FOOTER_BAND if footer else 0.12)
    fig = plt.figure(figsize=(fw, fh))
    left  = 0.75 / fw
    right = 1.0 - (gut + 0.15) / fw
    top   = 1.0 - _TITLE_BAND / fh
    bottom = ((_FOOTER_BAND if footer else 0.12) + bot_leg + 0.42) / fh
    gs = fig.add_gridspec(nrows, ncols, left=left, right=right, top=top, bottom=bottom,
                          hspace=0.42, wspace=0.28)
    axes = []
    for i in range(panels):
        r, c = divmod(i, ncols)
        kw = {}
        if sharex and axes:
            kw['sharex'] = axes[0]
        if sharey and axes:
            kw['sharey'] = axes[0]
        axes.append(fig.add_subplot(gs[r, c], **kw))
    for ax in axes:
        ax.grid(alpha=0.3)
    fig._chartkit = True                        # io._save_close: no tight-bbox rescue
    fig._footer_y = (0.5 * _FOOTER_BAND / fh) if footer else 0.004
    return Chart(fig, axes, gutter_frac=(1.0 - (gut + 0.05) / fw) if gut else None,
                 legend_mode=legend, legend_title=legend_title, footer=footer)


class Chart:
    """A figure plus the reserved-geometry bookkeeping `make` computed for it."""

    def __init__(self, fig, axes, *, gutter_frac, legend_mode, legend_title, footer):
        self.fig, self.axes = fig, axes
        self._gutter_frac = gutter_frac
        self._legend_mode = legend_mode
        self._legend_title = legend_title
        self._footer = footer
        self._legend_slot = 0

    @property
    def ax(self):
        return self.axes[0]

    def title(self, text, subtitle=None):
        """SHORT title policy: one clause.  Run keys/context belong to the footer —
        anything longer than 60 chars is a caption trying to be a title."""
        self.fig.suptitle(text[:80], fontsize=12, fontweight='bold', y=0.985, va='top')
        if subtitle:
            self.fig.text(0.5, 0.985 - 0.30 / self.fig.get_figheight(), subtitle,
                          ha='center', va='top', fontsize=8.5, color='#555555')

    def legend(self, handles=None, labels=None, *, title=None, ncol=None):
        """Place THE legend in the reserved gutter (or bottom strip).  Deduplicates
        identical (label) rows — six rows reading the same number is a table, not a
        legend.  A second call stacks below the first in the gutter."""
        if self._legend_mode == 'none':
            return None
        if handles is None:
            hs, ls = [], []
            for ax in self.axes:
                h, l = ax.get_legend_handles_labels()
                hs += h
                ls += l
        else:
            hs = list(handles)
            ls = list(labels) if labels is not None else [h.get_label() for h in hs]
        seen, H, L = set(), [], []
        for h, l in zip(hs, ls):
            if l not in seen and not str(l).startswith('_'):
                seen.add(l)
                H.append(h)
                L.append(l)
        if not H:
            return None
        if self._legend_mode == 'bottom':
            return self.fig.legend(H, L, title=title or self._legend_title,
                                   loc='lower center', ncol=ncol or min(len(L), 4),
                                   bbox_to_anchor=(0.5, self.fig._footer_y * 2.2),
                                   fontsize=8, frameon=False)
        y = 0.96 - self._legend_slot * 0.34
        self._legend_slot += 1
        return self.fig.legend(H, L, title=title or self._legend_title,
                               loc='upper left',
                               bbox_to_anchor=(self._gutter_frac or 0.99, y),
                               fontsize=8, frameon=True)

    def save(self, path, *, view=None):
        """Save through io._save_close.  `view` asserts the family-grammar filename
        prefix (`<view>_name.png`) for registry renders; what-if writers and notebooks
        pass view=None and make no grammar claim."""
        if view is not None:
            import os
            base = os.path.basename(path)
            if not base.startswith(view + '_'):
                raise ValueError(f'view {view!r} requires a {view}_* filename, '
                                 f'got {base!r}')
            _assert_view_allowed(view)
        _io._save_close(self.fig, path)
        return path


def _assert_view_allowed(view):
    """Inside a registry render, the view must be one the evaluation's family declares.
    Outside a render (io._CURRENT_EVAL is None) there is no declaration to check."""
    key = _io._CURRENT_EVAL
    if key is None:
        return
    from Optimization.Performance_Evaluations.core import families
    families.check_view(key, view)


# ── sizing helpers for category-heavy charts ────────────────────────────────────
def height_for_categories(n, *, per=0.26, base=1.6, cap=10.0):
    """Panel height for n horizontal category rows — readable at any n."""
    return min(cap, base + per * n)


def width_for_categories(n, *, per=0.5, base=3.0, cap=16.0):
    """Panel width for n vertical category columns."""
    return min(cap, base + per * n)
