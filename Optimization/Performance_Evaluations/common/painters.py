"""Shared figure painters — the builders more than one evaluation draws with.

Post-redesign this module holds exactly three things:

  * `overtime_metrics` / `top_tag` — the shared filename-stem vocabulary.  Every
    over-time figure basename and every `top{n}[_by_{dim}]` suffix in the published
    site composes from these two functions, so THIS module is where those names are
    minted (and where the figure-registry test looks for the stems).  What each name
    MEANS — its unit, axis label and direction — comes from `core/quantities.py`;
    `overtime_metrics` is a projection of that table, not a second copy of it.
  * `paint_overtime` — the one over-time painter, drawn through chartkit for both its
    views: 'absolute' (honest units — a data-chosen time unit, items/hour, f·D) and
    'percent' (improvement vs the baseline strategy, positive = better).  The config-stage
    trajectories family and the aggregate stage both render with it, which is what
    keeps their charts visually identical.
  * unit helpers binding the metric vocabulary to chartkit's unit policy.

Everything else the old painters carried (facet grids, top-N line picks, breakdown and
delta bars) either died in the redesign or moved into the single family module that owns
it now.
"""
import numpy as np

from Optimization.Performance_Evaluations.common import chartkit, present
from Optimization.Performance_Evaluations.common.style import _TOP_DIMS
from Optimization.Performance_Evaluations.core import quantities as _quantities


def overtime_metrics():
    """The over-time metric specs, DERIVED from the quantity table.

    Every over-time figure basename in the published site composes from `f` here, so this
    stays the place those names are minted (and where the figure-registry test looks for
    the stems) — but the names themselves, and the units, labels and directions beside
    them, now come from `core/quantities.py`.  They were a literal here, a literal in each
    of the two significance `_PRESENT` tables, and a literal in the headline module's
    `_METRIC_GROUPS`, and the four had drifted: the same quantity was `Σ task time per
    batch` on one axis and `total task time per batch` on another.

    Each spec carries its `Quantity` under `q`.  Prefer reading that over the flattened
    keys, which exist so `paint_overtime` and its two callers did not have to change in
    the same commit:

      x/y/blo/bhi  series columns (`Source.series`)
      f            figure-basename stem (`Quantity.stem`)
      t            panel title (`Quantity.series_title`)
      time         True for a duration, whose unit is resolved from the data
      conv         raw -> presentation multiplier, or None for a metric needing none
      yl           the axis label WITHOUT the resolved time unit — `paint_overtime`
                   appends it for a time metric, because the divisor is chosen from every
                   arm's values pooled, which only the painter has
    """
    out = []
    for key in _quantities.SERIES_ORDER:
        q = _quantities.BY_KEY[key]
        x, y, blo, bhi = q.source.series
        is_time = q.unit.kind == 'duration_ms'
        conv, label = present.for_metric(q.key)
        # A time metric's converter is DELIBERATELY dropped: its divisor is chosen from
        # every arm's values pooled, which only `_time_axis` can see, and a spec-level
        # converter resolved against no samples would silently claim seconds.
        out.append(dict(q=q, x=x, y=y, blo=blo, bhi=bhi, f=q.stem, t=q.series_title,
                        time=is_time, conv=None if is_time else conv,
                        yl=q.axis_stem if is_time else label,
                        lower_is_better=q.lower_is_better))
    return out


def top_tag(top_n, top_by):
    return f"top{top_n}" + (f"_by_{top_by}" if top_by in _TOP_DIMS else "")


def _conv(m, vals, div=None):
    """Raw series values -> presentation units.  `div` is the shared time divisor a
    time metric resolved for its axis; a fixed-unit metric uses its own `conv`."""
    if div is not None:
        return np.asarray(vals, dtype=float) / div
    return m['conv'](vals) if m.get('conv') else np.asarray(vals, dtype=float)


def _time_axis(m, S, strategies, baseline):
    """(divisor, unit_label) for a time metric — ONE unit for its whole y-axis.

    Every value that will share the axis is pooled into a single `chartkit.time_units`
    call: each arm's series, the baseline's, and the IQR band edges.  Fixing the unit at
    hours instead would print a column of 0.0008 for a metric whose median is seconds."""
    pool = []
    for s in list(strategies) + ([baseline] if baseline else []):
        d = S.get(s['key'])
        if d is None:
            continue
        for field in (m['y'], m.get('blo'), m.get('bhi')):
            if field and d.get(field) is not None:
                pool.append(np.asarray(d[field], dtype=float).ravel())
    return chartkit.time_units(np.concatenate(pool) if pool else [0.0])


#: above this many arms the absolute view stops drawing one line per arm
_SPAGHETTI = 8
_LEVEL_NOTE = ('band = the middle half of all arms; only the extremes are drawn '
               'individually — the percent view separates the rest')


def _paint_levels(ax, strategies, S, m, baseline, div):
    """The absolute view's body.  Returns how many series were drawn.

    Up to `_SPAGHETTI` arms, every arm is a line.  Beyond that they are not: 34 arms of
    the same warehouse trace the same demand curve within a few percent, so the overlay
    becomes one opaque ribbon in which no arm can be followed — an SME reading this very
    chart at 34 arms attributed a 6-12% gap to two arms whose sim databases are in fact
    byte-identical.  So the large case draws the SHAPE honestly: the cross-arm median,
    the interquartile band, and only the extreme arms named.  Which arm is which at this
    scale is the percent view's question, and it answers it against a baseline.
    """
    avail = [s for s in strategies
             if S.get(s['key']) is not None
             and not (baseline and s['key'] == baseline['key'])]
    if not avail:
        return 0
    if len(avail) <= _SPAGHETTI:
        for s in avail:
            d = S[s['key']]
            ax.plot(d[m['x']], _conv(m, d[m['y']], div),
                    color=chartkit.strategy_color(s, strategies),
                    ls=chartkit.strategy_dash(s), lw=1.4, label=_label(s))
            if m['blo'] and len(avail) <= 3 and d.get(m['blo']) is not None:
                chartkit.draw_ci(ax, d[m['x']], _conv(m, d[m['blo']], div),
                                 _conv(m, d[m['bhi']], div),
                                 color=chartkit.strategy_color(s, strategies))
        return len(avail)

    # pool onto the shortest shared x so the quantiles are taken across arms at the
    # same batch rather than across ragged tails
    n = min(len(S[s['key']][m['x']]) for s in avail)
    xs = np.asarray(S[avail[0]['key']][m['x']][:n], dtype=float)
    M = np.vstack([_conv(m, S[s['key']][m['y']], div)[:n] for s in avail])
    med = np.nanmedian(M, axis=0)
    lo, hi = np.nanpercentile(M, 25, axis=0), np.nanpercentile(M, 75, axis=0)
    chartkit.draw_ci(ax, xs, lo, hi, color='#4c72b0', alpha=0.20,
                     label='middle half of arms')
    ax.plot(xs, med, color='#1a4d7a', lw=2.0, label='median arm')
    # name the extremes by their run-long mean, the two a reader will ask about
    order = np.argsort(np.nanmean(M, axis=1))
    for idx, tag in ((order[0], 'lowest'), (order[-1], 'highest')):
        s = avail[int(idx)]
        ax.plot(xs, M[int(idx)], color=chartkit.strategy_color(s, strategies),
                ls=chartkit.strategy_dash(s), lw=1.3,
                label=f'{tag}: {_label(s)}')
    return len(avail)


def paint_overtime(strategies, S, m, baseline, out_dir_path, *, view, agg=False):
    """Render one over-time metric in one view and return the saved path (or None
    when nothing could be drawn).

    view='absolute': every strategy in presentation units, baseline in BASELINE_STYLE.
    view='percent' : per-batch % improvement vs the baseline strategy, aligned on the
                     metric's own x — the baseline IS the zero line, so it is drawn as
                     a reference line, not a series.
    agg=True labels the aggregate stage (values are already ×-baseline normalized
    upstream; the absolute view is skipped there by the caller).
    """
    import os
    # Size the gutter for what will actually be legended: the large-N absolute view
    # summarises instead of listing every arm, so reserving 34 rows there would leave a
    # column of empty gutter beside a four-entry legend.
    if view == 'absolute' and len(strategies) > _SPAGHETTI:
        labels = ['middle half of arms', 'median arm', 'lowest: an arm name here',
                  'highest: an arm name here', 'FIFO baseline']
    else:
        labels = [_label(s) for s in strategies] + ['FIFO baseline']
    ch = chartkit.make(panels=1, panel_w=6.4, panel_h=4.2,
                       legend='gutter', legend_labels=labels)
    ax = ch.ax
    db = S.get(baseline['key']) if baseline else None
    drawn = 0
    if view == 'absolute':
        # a time metric resolves ONE divisor+unit for the whole axis before anything
        # is drawn, so every arm and the baseline land in the same named unit
        div, unit = (_time_axis(m, S, strategies, baseline) if m.get('time')
                     else (None, ''))
        if db is not None:
            chartkit.mark_baseline(ax, db[m['x']], _conv(m, db[m['y']], div))
        drawn = _paint_levels(ax, strategies, S, m, baseline, div)
        yl = f"{m['yl']} ({unit})" if m.get('time') and unit else m['yl']
        ax.set_ylabel('× baseline' if agg else yl)
        vals = [_conv(m, S[s['key']][m['y']], div)
                for s in strategies if S.get(s['key'])]
        if vals:
            chartkit.shared_ylim([ax], vals)
        ch.title(m['t'], _LEVEL_NOTE if len(strategies) > _SPAGHETTI else None)
    else:                                              # percent view
        if db is None:
            return ch.abandon()
        bx = {int(b): v for b, v in zip(db[m['x']], np.asarray(db[m['y']], float))
              if v == v and v != 0}
        allv = []
        for s in strategies:
            d = S.get(s['key'])
            if d is None or s['key'] == baseline['key']:
                continue
            xs, ys = [], []
            for b, v in zip(d[m['x']], np.asarray(d[m['y']], float)):
                bv = bx.get(int(b))
                if bv is None or v != v:
                    continue
                xs.append(int(b))
                ys.append(chartkit.improvement_pct(
                    v, bv, lower_is_better=m['lower_is_better']))
            if not xs:
                continue
            ax.plot(xs, ys, color=chartkit.strategy_color(s, strategies),
                    ls=chartkit.strategy_dash(s), lw=1.4, label=_label(s))
            allv.append(ys)
            drawn += 1
        chartkit.reference_line(ax, 0.0, orient='y')
        tag = chartkit.pct_axis(ax, better='up')
        ax.set_ylabel(f'improvement vs FIFO {tag}')
        if allv:
            chartkit.shared_ylim([ax], allv, include=(0.0,))
        ch.title(f"{m['t']} — % vs baseline")
    if not drawn:
        return ch.abandon()
    ax.set_xlabel('batch')
    ch.legend(title='strategy')
    path = os.path.join(out_dir_path, f"{view}_{m['f']}.png")
    return ch.save(path, view=view)


def _label(s):
    parts = [p for p in (s.get('initial', ''), s.get('assignment', ''),
                         s.get('reslot', '')) if p]
    return '|'.join(parts) if parts else s.get('label', s.get('key', ''))
