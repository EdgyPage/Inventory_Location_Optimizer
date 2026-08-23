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
  UNITS     durations render in a NAMED time unit — `to_time` picks hours/minutes/seconds
            from the data's own magnitude (a picker task is seconds, a batch of labor is
            hours) and returns the label with it; the phrase "sim units" is banned.
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
from Optimization.Performance_Evaluations.common import units as _units

# ── geometry constants (inches) ─────────────────────────────────────────────────
_PANEL_W, _PANEL_H = 4.8, 3.6      # default data-panel size
_TITLE_BAND  = 0.55                # suptitle strip
_PANEL_TITLE = 0.30                # extra strip when the TOP row carries ax.set_title
_FOOTER_BAND = 0.32                # provenance strip — reserved, nothing else draws here
_ROW_GAP     = 0.42 * _PANEL_H     # inches between stacked panel rows (see _hspace)
_GUTTER_MIN, _GUTTER_MAX = 1.1, 3.4
_CHAR_W_IN   = 0.062               # approx glyph advance at legend fontsize (8pt)

# ── the single unit policy ──────────────────────────────────────────────────────
# The policy itself now lives in `common/units.py`, which is stdlib-only so the quantity
# table can import it without dragging matplotlib into a test or a schema tool.  These
# names are re-exported because ~20 call sites reach for `chartkit.time_units`, and
# moving the import is churn with no reader benefit.
HOURS = 'hours'

_TIME_UNITS = _units.TIME_UNITS
time_units = _units.time_units


def to_hours(sim_ms):
    """Duration sim values are milliseconds; render an HOURS axis.

    Use this only where hours is the right unit on its face — a run's elapsed time, a
    batch's labor total.  For anything whose magnitude depends on the data, use
    `to_time`: durations in this model span five orders of magnitude (one picker task is
    seconds, a batch of labor is hours), and a fixed unit turns half the suite into
    columns of `0.0008`.
    """
    return np.asarray(sim_ms, dtype=float) / 3.6e6


def to_time(sim_ms):
    """(converted_values, unit_label) for a set of sim-millisecond durations.

    Pool everything that shares an axis into ONE call so every series on it lands in the
    same unit.
    """
    div, label = time_units(sim_ms)
    return np.asarray(sim_ms, dtype=float) / div, label


# ── the single sign convention ──────────────────────────────────────────────────
def improvement_pct(value, baseline, *, lower_is_better):
    """Percent improvement vs baseline, POSITIVE = BETTER — the only sign convention
    any chart in this package may use.

    Returns NaN when the comparison is UNDEFINED (a zero, missing or non-finite
    baseline), never 0.0.  A zero would read as "no change", and a batch where the
    baseline did no work at all is not a batch where the two arms tied: fabricating
    ties there drags a mean toward zero and tightens the interval around it, exactly
    on the observations where the contrast is largest.  Callers that aggregate must
    use `improvement_pct_series`, which drops the undefined pairs; callers that draw
    one value get a point matplotlib skips.
    """
    try:
        b = float(baseline)
    except (TypeError, ValueError):
        return float('nan')
    if not np.isfinite(b) or b == 0.0:
        return float('nan')
    v = float(value) if value is not None else float('nan')
    raw = (v - b) / abs(b) * 100.0
    return -raw if lower_is_better else raw


def improvement_pct_series(values, baselines, *, lower_is_better):
    """Paired per-element improvement percentages with the UNDEFINED pairs dropped.

    THE way to build a sample of paired improvements: every statistic downstream (a
    mean, a median, a confidence interval, a test) is only honest over the pairs where
    the comparison exists.  Returns a float array that may be shorter than its inputs —
    and empty when no pair is comparable.
    """
    out = [improvement_pct(v, b, lower_is_better=lower_is_better)
           for v, b in zip(values, baselines)]
    arr = np.asarray(out, dtype=float)
    return arr[np.isfinite(arr)]


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


def annotate_points(ax, points, *, fontsize=6.5, dx=6.0, min_gap_frac=0.045,
                    colors=None):
    """Label scattered points without letting the labels overlap each other.

    `points` is [(x, y, text), ...].  Labels are placed to the right of their point and
    pushed apart vertically to at least `min_gap_frac` of the axes height, with a leader
    line back to the point whenever one had to move.  Series that end up close together
    are exactly the ones a reader most needs told apart — on this suite the arms that
    finish within a few tenths of a percent of each other are the podium — and a pile of
    overlapping labels there is worse than none, because it looks like information.
    """
    pts = [(float(x), float(y), str(t)) for x, y, t in points
           if np.isfinite(x) and np.isfinite(y)]
    if not pts:
        return
    y0, y1 = ax.get_ylim()
    gap = abs(y1 - y0) * min_gap_frac
    order = sorted(range(len(pts)), key=lambda i: pts[i][1])
    placed = {}
    last = None
    for i in order:                       # sweep upward, pushing each label clear
        _x, y, _t = pts[i]
        ty = y if last is None else max(y, last + gap)
        placed[i] = ty
        last = ty
    for i, (x, y, text) in enumerate(pts):
        ty = placed[i]
        col = (colors[i] if colors is not None else '#333333')
        ax.annotate(text, xy=(x, y), xytext=(dx, 0), textcoords='offset points',
                    fontsize=fontsize, color=col, va='center', ha='left',
                    annotation_clip=False) if abs(ty - y) < gap * 0.25 else \
            ax.annotate(text, xy=(x, y), xytext=(x, ty), fontsize=fontsize, color=col,
                        va='center', ha='left', annotation_clip=False,
                        arrowprops=dict(arrowstyle='-', lw=0.5, color=col, alpha=0.6,
                                        shrinkA=0, shrinkB=2))


def draw_ci(ax, x, lo, hi, *, color='#555555', alpha=0.18, band=True, **kw):
    """Draw the confidence interval the stats layer computes (and, until now, nothing
    ever rendered).  band=True fills between series; band=False draws error whiskers."""
    if band:
        return ax.fill_between(x, lo, hi, color=color, alpha=alpha, lw=0, **kw)
    return ax.errorbar(x, (np.asarray(lo) + np.asarray(hi)) / 2.0,
                       yerr=(np.asarray(hi) - np.asarray(lo)) / 2.0,
                       fmt='none', ecolor=color, elinewidth=1.0, capsize=2.5, **kw)


# ── figure construction ─────────────────────────────────────────────────────────
_LEGEND_ROW_H = 0.165        # inches per legend row at fontsize 8
_LEGEND_HEAD_H = 0.34        # the legend's own title + frame padding
_GUTTER_MAX_COLS = 3


def _gutter_width(labels, title, ncol=1):
    texts = [str(t) for t in (*labels, title or '')]
    chars = max((len(t) for t in texts), default=0)
    one = min(_GUTTER_MAX, max(_GUTTER_MIN, 0.45 + chars * _CHAR_W_IN))
    return one * ncol


def _gutter_layout(labels, title, content_h):
    """(ncol, width, height) for a gutter legend that must FIT beside the panels.

    A reserved gutter only guarantees the legend cannot overlap the data if the legend
    also fits vertically.  With one entry per arm a 34-arm overlay needs ~5.9 in of
    column — taller than the panels — and a single column simply runs off the canvas and
    through the footer band.  So: add columns until it fits (up to a limit), then let the
    caller grow the figure for whatever is still too tall.
    """
    n = len(labels)
    if not n:
        return 1, 0.0, 0.0
    for ncol in range(1, _GUTTER_MAX_COLS + 1):
        rows = math.ceil(n / ncol)
        h = _LEGEND_HEAD_H + rows * _LEGEND_ROW_H
        if h <= content_h or ncol == _GUTTER_MAX_COLS:
            return ncol, _gutter_width(labels, title, ncol), h
    return 1, _gutter_width(labels, title), _LEGEND_HEAD_H + n * _LEGEND_ROW_H


def _hspace(panel_h: float) -> float:
    """Row gap as a CONSTANT physical distance, expressed in matplotlib's fraction units.

    `hspace` is a fraction of the mean axes height, so a tall panel gets a proportionally
    taller gap: a 17-category ladder at ~6 in per panel opened a 2.5-inch band of white
    between rows, which reads as two unrelated figures stacked in one file.  The gap has
    to clear one x-label plus the next row's title, and that is a fixed number of inches
    no matter how tall the panels are.

    Capped at the historical 0.42 so nothing SHORTER than the default panel moves — this
    only ever shrinks an oversized gap.
    """
    return min(0.42, _ROW_GAP / max(panel_h, 0.1))


def make(*, panels=1, ncols=None, panel_w=_PANEL_W, panel_h=_PANEL_H,
         legend='gutter', legend_labels=(), legend_title=None,
         sharex=False, sharey=False, footer=True, panel_titles=False):
    """Build a Chart whose geometry is computed from its content.

    panels     total data panels; laid out on `ncols` columns (default: all in one row
               up to 3, then near-square).
    panel_w/h  per-panel inches; category-heavy charts pass the `for_categories` helpers.
    legend     'gutter' reserves a right-hand strip sized to `legend_labels`;
               'bottom' reserves a strip below the panels (wide categorical charts);
               'none' reserves nothing.
    panel_titles  the caller will `ax.set_title` each panel — matplotlib draws those
               ABOVE the axes, i.e. into the suptitle band, so the band grows to hold
               them.  Without this a faceted chart's first-row titles overprint the
               subtitle, which is legible enough in a thumbnail to ship and illegible
               at full size.
    The suptitle band and the provenance footer band are always part of the geometry, so
    neither can ever collide with data, ticks, or the legend.
    """
    if ncols is None:
        ncols = panels if panels <= 3 else math.ceil(math.sqrt(panels))
    nrows = math.ceil(panels / ncols)
    gut_cols, gut, gut_h = (_gutter_layout(legend_labels, legend_title, nrows * panel_h)
                            if legend == 'gutter' else (1, 0.0, 0.0))
    leg_rows = (math.ceil(len(legend_labels) / max(1, ncols * 2)) if legend == 'bottom'
                else 0)
    bot_leg = 0.28 * max(1, leg_rows) if legend == 'bottom' else 0.0
    fw = ncols * panel_w + gut + 0.9            # 0.9 ≈ y-labels + left margin
    # The panels stretch to whatever the gutter needs: a legend taller than the data area
    # would otherwise run off the bottom of the canvas and through the footer band.
    content_h = max(nrows * panel_h, gut_h)
    top_band = _TITLE_BAND + (_PANEL_TITLE if panel_titles else 0.0)
    fh = content_h + top_band + bot_leg + (_FOOTER_BAND if footer else 0.12)
    fig = plt.figure(figsize=(fw, fh))
    # Margins are tracked in INCHES (not fractions) because `fit` may grow the canvas and
    # must move the panels by an exact distance; a fraction of the old size would drift.
    m_left = 0.75
    m_right = fw - (gut + 0.15)
    m_top = fh - top_band
    m_bottom = (_FOOTER_BAND if footer else 0.12) + bot_leg + 0.42
    gs = fig.add_gridspec(nrows, ncols, left=m_left / fw, right=m_right / fw,
                          top=m_top / fh, bottom=m_bottom / fh,
                          hspace=_hspace(panel_h), wspace=0.28)
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
    fig._footer_band = _FOOTER_BAND if footer else 0.06
    fig._footer_y = (0.5 * _FOOTER_BAND / fh) if footer else 0.004
    return Chart(fig, axes, gutter_frac=(1.0 - (gut + 0.05) / fw) if gut else None,
                 legend_mode=legend, legend_title=legend_title, footer=footer,
                 gutter_cols=gut_cols, gs=gs,
                 margins=(m_left, m_right, m_bottom, m_top))


class Chart:
    """A figure plus the reserved-geometry bookkeeping `make` computed for it."""

    def __init__(self, fig, axes, *, gutter_frac, legend_mode, legend_title, footer,
                 gutter_cols=1, gs=None, margins=None):
        self._gs = gs
        self._margins = margins             # (left, right, bottom, top) in inches
        self.fig, self.axes = fig, axes
        self._gutter_frac = gutter_frac
        self._legend_mode = legend_mode
        self._legend_title = legend_title
        self._footer = footer
        self._gutter_cols = gutter_cols
        self._legend_slot = 0
        self._legends = []
        self._header = []                   # (text artist, inches below the top edge)

    @property
    def ax(self):
        return self.axes[0]

    def title(self, text, subtitle=None):
        """SHORT title policy: one clause.  Run keys/context belong to the footer —
        anything longer than 60 chars is a caption trying to be a title.

        Both lines are placed a fixed DISTANCE below the top edge, not at a fixed
        fraction of the height: the title band is a constant number of inches, so on a
        tall figure (a 34-row ladder runs past 16 in) a fractional offset walks the
        subtitle straight down into the axes."""
        h = self.fig.get_figheight()
        cx = self._panel_center_x()
        self._header = [(self.fig.suptitle(text[:80], fontsize=12, fontweight='bold',
                                           x=cx, y=1.0 - 0.13 / h, va='top'), 0.13)]
        if subtitle:
            self._header.append(
                (self.fig.text(cx, 1.0 - 0.36 / h, subtitle, ha='center', va='top',
                               fontsize=8.5, color='#555555'), 0.36))

    def _panel_center_x(self):
        """Figure-fraction centre of the PANEL area, not of the whole canvas.

        A header centred on the canvas drifts right into the legend gutter, and a long
        subtitle runs underneath it — which is how five trajectory figures ended up with
        their explanatory line clipped by the legend box."""
        if not self._margins:
            return 0.5
        m_l, m_r, _b, _t = self._margins
        return (m_l + m_r) / 2.0 / self.fig.get_figwidth()

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
        x = self._gutter_frac or 0.99
        leg = self.fig.legend(H, L, title=title or self._legend_title,
                              loc='upper left', ncol=ncol or self._gutter_cols,
                              bbox_to_anchor=(x, y), fontsize=8, frameon=True)
        # Remember the anchor in INCHES: `fit` may grow the canvas, and a fraction of
        # the old width points somewhere else on the new one.
        w, h = self.fig.get_size_inches()
        self._legends.append((leg, x * w, y * h))
        return leg

    def fit(self):
        """Grow the canvas until nothing is clipped, keeping the footer band clear.

        Called automatically by `save`.  The reserved bands solve the collisions chartkit
        can predict, but not the one it cannot: how wide a caller's own tick labels turn
        out to be.  Seventeen rotated 20-character arm names need more left and bottom
        margin than any fixed constant should promise, and the old answer — crop with a
        tight bbox — is what cut the provenance line off in the first place.  So measure
        after drawing, and EXPAND rather than crop: the panels keep the size they were
        designed at, and the overflow gets canvas of its own.
        """
        fig = self.fig
        band = getattr(fig, '_footer_band', _FOOTER_BAND)
        # Growing moves the axes, which can change how far a rotated label reaches, so
        # this is a fixed point rather than one subtraction.  Two passes settle every
        # real figure; the third is the guard against a pathological one.
        for _ in range(3):
            fig.canvas.draw()
            bb = fig.get_tightbbox(fig.canvas.get_renderer())
            w, h = fig.get_size_inches()
            dl = max(0.0, 0.06 - bb.x0)
            dr = max(0.0, bb.x1 - (w - 0.06))
            db = max(0.0, band - bb.y0)      # the footer band stays empty
            dt = max(0.0, bb.y1 - (h - 0.04))
            if dl + dr + db + dt < 0.02:
                break
            nw, nh = w + dl + dr, h + db + dt
            m_l, m_r, m_b, m_t = self._margins
            m_l, m_r, m_b, m_t = m_l + dl, m_r + dl, m_b + db, m_t + db
            self._margins = (m_l, m_r, m_b, m_t)
            fig.set_size_inches(nw, nh)
            # The gridspec carries its OWN margins; fig.subplots_adjust does not reach
            # axes created from one, which is why the panels used to stay put while the
            # canvas grew around them.
            self._gs.update(left=m_l / nw, right=m_r / nw,
                            bottom=m_b / nh, top=m_t / nh)
            self._legends = [(leg, x_in + dl, y_in + db)
                             for leg, x_in, y_in in self._legends]
            for leg, x_in, y_in in self._legends:
                leg.set_bbox_to_anchor((x_in / nw, y_in / nh))
            # The header lines are placed a fixed distance below the TOP edge; growing
            # the canvas moves that edge, and a stale fraction walks them into the axes.
            cx = self._panel_center_x()
            for artist, inches_down in self._header:
                artist.set_position((cx, 1.0 - inches_down / nh))
            fig._footer_y = 0.5 * band / nh
        return self

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
        self.fit()
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
