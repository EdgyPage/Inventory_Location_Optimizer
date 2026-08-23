"""marks — draw a quantity in a view, from RAW values, so a stance cannot be mislabelled.

Two shapes cover 25 of the suite's 31 figures: the RANKED CATEGORY (one bar or dot per
arm, sorted) and the SERIAL (one line per arm over batches).  Before this module they had
ten and seven independent implementations respectively, and the implementations disagreed
about the things a reader actually reads — which way "best" points, whether the baseline
is a competitor or a datum, what an undefined value sorts as, how wide the zero line is.

But the reason this module exists is narrower and sharper than de-duplication.

## A caller cannot hand in a percentage

Every entry point takes values **in the quantity's own raw units** and a `view`.  There is
no parameter through which a pre-computed percentage, difference or ratio can be passed.
The renderer derives:

    absolute   raw, scaled into presentation units by the quantity's own `Unit`
    percent    `chartkit.improvement_pct_series(raw, baseline_raw, ...)` — the one sign
               convention, oriented by the quantity's own direction
    delta      a signed difference against the baseline, in presentation units

so the `<view>_` prefix on the filename, the axis label, and the arithmetic that produced
the numbers all come from one decision.  `absolute_compute_cost.png` shipped for a day
plotting a delta and passed all five of the save-time checks, because every one of them
compared a declaration against another declaration and none of them ever saw a number.
`delta_travel_vs_baseline.png` shipped for longer plotting a percent, for the same reason
and with an additional push: its family's allowed-view list had no `percent` in it, so the
author had nowhere honest to put the figure.

## Intervals convert with their values

An interval is supplied RAW and converted by the same conversion as the values it belongs
to.  Two of the retired implementations converted the point and left the whiskers in sim
units, which draws an interval of the wrong width around the right dot.

## What is NOT here

Cumulative curves, scatter, facet grids, heatmaps and rendered tables stay bespoke — their
marks are not interchangeable and forcing them through one signature would produce a
function with a mode switch per caller.  They are declared in `BESPOKE` with a reason, so
"bespoke" stays a decision with a ceiling rather than a habit.
"""
from __future__ import annotations

import numpy as np

from Optimization.Performance_Evaluations.common import chartkit, present
from Optimization.Performance_Evaluations.common.style import _stitle
from Optimization.Performance_Evaluations.core import quantities as _q

#: `eval_key:view` -> why this figure is not drawn through a mark.  Every entry must name
#: a MARK the library does not have, not an inconvenience; `Tests/unit/test_marks.py`
#: checks that each key resolves to a registered evaluation, that each reason is
#: substantive, and that the count stays under the ceiling below.
BESPOKE: dict = {
    'throughput.volume:absolute': 'cumulative pick-volume curves — the mark is a growing '
                                  'area per arm, whose instances are prefixes rather than '
                                  'paired points',
    'throughput.volume:percent':  'the same cumulative curve, expressed against the '
                                  'baseline curve rather than against a scalar',
    'headline.throughput_vs_labor:absolute': 'a two-axis scatter: each arm is one POINT in '
                                             'a labor x throughput plane, and neither axis '
                                             'is the category axis a ranked mark needs',
    'headline.throughput_vs_labor:percent': 'the same scatter with both axes expressed vs '
                                            'the baseline point',
    'headline.top_vs_baseline:table': 'a rendered table, not a mark — the cell values are '
                                      'the figure',
    'diagnostics.metric_grids:absolute': 'a facet grid of raw per-arm read-outs; the '
                                         'diagnostics family is exempt from comparison '
                                         'work by charter',
    'diagnostics.scorecards:absolute': 'a scorecard of text cells, with no data mark at all',
    'labor.delta_grid:delta': 'small multiples — one panel per arm, which is a facet layout '
                              'around the serial mark rather than a mark of its own',
    'sig.suite:effect': 'the distribution+effect panel is its own composed mark (box, '
                        'ladder, Holm-p row labels) and lives in significance/panels.py',
    'sig.by_initial:effect': 'the heatmap and the forest plot, both in significance/panels.py',
    'agg.sig:effect': 'the cross-profile mirror of the same two panels',
    'cost.compute:absolute': 'a stacked horizontal bar of wall-clock SECTIONS per rule; the '
                             'stack is the point, and a ranked mark draws one value per row',
    'cost.compute:delta': 'the same stack, expressed as seconds more or less than the '
                          'do-nothing rule, section by section',
    'cost.compute:percent': 'a multiple of the do-nothing floor — a ratio stance, not the '
                            'improvement-percent every other percent view in the suite '
                            'uses; merging the two would be the same assert-do-not-derive '
                            'failure one level up',
}

#: A ceiling, so `BESPOKE` cannot quietly become the default.  Raising it is a decision
#: someone has to make on purpose, in a diff, with a reason beside it.
BESPOKE_CEILING = 18


def _presented(quantity, raw, samples):
    """Raw values -> presentation values, with the quantity's own conversion."""
    conv, label = present.for_metric(quantity.key, samples)
    arr = np.asarray(raw, dtype=float)
    return (conv(arr) if conv is not None else arr), label


def _view_values(quantity, raw, baseline_raw, view, samples):
    """(values, axis_label) for one view — the ONLY place a stance is computed.

    `raw` and `baseline_raw` are in the quantity's own units.  There is deliberately no
    path by which a caller supplies the answer.
    """
    raw = np.asarray(raw, dtype=float)
    if view == 'absolute':
        return _presented(quantity, raw, samples)
    base = np.asarray(baseline_raw, dtype=float)
    if view == 'percent':
        vals = np.array([chartkit.improvement_pct(
            v, b, lower_is_better=quantity.lower_is_better) for v, b in zip(raw, base)])
        return vals, f'{quantity.label} vs baseline (%, higher = better)'
    if view == 'delta':
        conv, label = present.for_metric(quantity.key, samples)
        diff = raw - base
        return (conv(diff) if conv is not None else diff), f'{label}, difference vs baseline'
    raise ValueError(f'marks cannot draw view {view!r}')


# ── the ranked-category mark ─────────────────────────────────────────────────────

def ranked(ch, entries, *, quantity, view, baseline, strategies, intervals=None,
           annotate=None, ax=None):
    """One bar (absolute) or dot+interval (percent/delta) per arm, best first.

    `entries`   [(strategy_dict, raw_value)] in the quantity's own units.
    `intervals` optional [(lo, hi)] in the SAME raw units, converted with the values.
    `baseline`  the baseline strategy dict; its raw value must be in `entries`.

    Returns the number of arms drawn — 0 when nothing could be, so the caller's exit is
    `if not marks.ranked(...): return ch.abandon()`.
    """
    ax = ax if ax is not None else ch.ax
    pairs = [(s, float(v)) for s, v in entries
             if v is not None and np.isfinite(float(v))]
    if not pairs:
        return 0
    by_key = {s['key']: v for s, v in pairs}
    base_v = by_key.get(baseline['key']) if baseline else None
    if view in ('percent', 'delta'):
        if base_v is None:
            return 0
        pairs = [(s, v) for s, v in pairs if s['key'] != baseline['key']]
        if not pairs:
            return 0

    raw = [v for _s, v in pairs]
    samples = list(by_key.values())
    vals, axis_label = _view_values(quantity, raw,
                                    [base_v] * len(raw) if base_v is not None else [],
                                    view, samples)

    # Best first, ALWAYS — and for a comparison view "best" is the largest improvement,
    # not the largest number in the quantity's own direction.
    lower = quantity.lower_is_better if view == 'absolute' else False
    order = chartkit.rank(vals, lower_is_better=lower)
    pairs = [pairs[i] for i in order]
    vals = np.asarray(vals, dtype=float)[order]
    ivals = None
    if intervals is not None:
        iv = {s['key']: lh for (s, _v), lh in zip(entries, intervals)}
        ivals = [iv.get(s['key']) for s, _v in pairs]

    labels = [_stitle(s) for s, _v in pairs]
    pos = chartkit.category_axis(ax, labels)
    if view == 'absolute':
        for p, (s, _v), val in zip(pos, pairs, vals):
            is_base = bool(baseline) and s['key'] == baseline['key']
            ax.barh([p], [val], height=0.72, zorder=2,
                    color=(chartkit.BASELINE_STYLE['color'] if is_base
                           else chartkit.strategy_color(s, strategies)))
            if is_base:
                chartkit.mark_baseline_row(ax, p)
        # Bars are anchored at zero on purpose: a truncated value axis on a BAR chart
        # misstates the ratio between bars, which is the one thing bars are read for.
    else:
        if ivals is not None:
            lo, hi = _interval_arrays(quantity, ivals, base_v, view, samples)
            chartkit.draw_ci(ax, pos, lo, hi, orient='h', color='#666666')
        ax.scatter(vals, pos, s=38, zorder=3, edgecolors='black', linewidths=0.5,
                   color=[chartkit.strategy_color(s, strategies) for s, _v in pairs])
        chartkit.reference_line(ax, 0.0, orient='x')
    ax.set_xlabel(axis_label)
    if annotate:
        chartkit.annotate_bars(ax, pos, vals, fmt=annotate)
    _hug_x(ax, vals, include_zero=(view != 'absolute'))
    return len(pairs)


def _interval_arrays(quantity, ivals, base_v, view, samples):
    """Interval ends put through the SAME conversion as the values they belong to.

    Two retired implementations converted the point and left the whiskers raw, which draws
    an interval of the wrong width around the right dot.
    """
    lo_raw = [(lh[0] if lh else float('nan')) for lh in ivals]
    hi_raw = [(lh[1] if lh else float('nan')) for lh in ivals]
    base = [base_v] * len(lo_raw)
    lo, _l = _view_values(quantity, lo_raw, base, view, samples)
    hi, _h = _view_values(quantity, hi_raw, base, view, samples)
    # A `lower_is_better` percent flips the sign, so the converted lo can exceed the hi.
    return np.minimum(lo, hi), np.maximum(lo, hi)


def _hug_x(ax, vals, *, include_zero):
    finite = [float(v) for v in np.ravel(vals) if np.isfinite(v)]
    if not finite:
        return
    pool = finite + ([0.0] if include_zero else [])
    lo, hi = min(pool), max(pool)
    if not include_zero:
        lo = min(lo, 0.0)               # a bar chart is anchored at zero
    pad = (hi - lo) * 0.12 or 1.0
    ax.set_xlim(lo - pad, hi + pad)


# ── the serial mark ──────────────────────────────────────────────────────────────

def serial(ch, series, *, quantity, view, baseline, strategies, x_key, y_key,
           band=None, smooth=0, ax=None):
    """One line per arm over batches, in one of the three views.

    `series`  {strategy_key: {x_key: [...], y_key: [...], ...}} in raw units.
    `band`    optional (lo_key, hi_key) drawn only in the absolute view, where a band in
              raw units means something; against a baseline it would need its own paired
              interval, which is a different computation and not this one.

    Returns the number of arms drawn.
    """
    ax = ax if ax is not None else ch.ax
    db = series.get(baseline['key']) if baseline else None
    if view in ('percent', 'delta') and db is None:
        return 0

    pool = []
    for s in strategies:
        d = series.get(s['key'])
        if d is not None and d.get(y_key) is not None:
            pool.append(np.asarray(d[y_key], dtype=float).ravel())
    conv, label = present.for_metric(quantity.key, pool)

    base_by_x = {}
    if db is not None:
        base_by_x = {int(b): float(v)
                     for b, v in zip(db[x_key], np.asarray(db[y_key], dtype=float))
                     if np.isfinite(v)}

    drawn = 0
    for s in strategies:
        d = series.get(s['key'])
        if d is None:
            continue
        is_base = bool(baseline) and s['key'] == baseline['key']
        if is_base and view != 'absolute':
            continue                    # the baseline IS the zero line in a comparison
        xs_raw = [int(b) for b in d[x_key]]
        ys_raw = np.asarray(d[y_key], dtype=float)
        if view == 'absolute':
            xs, ys = xs_raw, (conv(ys_raw) if conv is not None else ys_raw)
        else:
            xs, ys = [], []
            for b, v in zip(xs_raw, ys_raw):
                bv = base_by_x.get(b)
                if bv is None or not np.isfinite(v):
                    continue
                xs.append(b)
                ys.append(chartkit.improvement_pct(
                    v, bv, lower_is_better=quantity.lower_is_better) if view == 'percent'
                    else ((conv(v - bv) if conv is not None else v - bv)))
            ys = np.asarray(ys, dtype=float)
        if not len(xs):
            continue
        if smooth:
            ys = chartkit.rolling_mean(ys, smooth)
        if is_base:
            chartkit.mark_baseline(ax, xs, ys)
        else:
            ax.plot(xs, ys, color=chartkit.strategy_color(s, strategies),
                    ls=chartkit.strategy_dash(s), lw=1.4, label=_stitle(s))
        if band and view == 'absolute' and d.get(band[0]) is not None:
            lo = np.asarray(d[band[0]], dtype=float)
            hi = np.asarray(d[band[1]], dtype=float)
            chartkit.draw_ci(ax, xs, conv(lo) if conv is not None else lo,
                             conv(hi) if conv is not None else hi,
                             color=chartkit.strategy_color(s, strategies))
        drawn += 1

    ax.set_xlabel('batch')
    if view == 'absolute':
        ax.set_ylabel(label)
    else:
        chartkit.reference_line(ax, 0.0, orient='y')
        tag = chartkit.pct_axis(ax, better='up') if view == 'percent' else ''
        ax.set_ylabel(f'{quantity.label} vs baseline {tag}'.strip() if view == 'percent'
                      else f'{label}, difference vs baseline')
    return drawn


def views_for(eval_key: str, quantity_keys, shape) -> frozenset:
    """The views one evaluation must emit — the union over the quantities it draws.

    Thin, but it is the seam: an evaluation asks what it must draw rather than declaring
    it, which is what stops `layout.churn` from shipping absolute-only beside
    `layout.travel`'s two views because two different authors made two different calls.
    """
    out: set = set()
    for key in quantity_keys:
        out |= _q.derive_views(_q.BY_KEY[key], shape)
    return frozenset(out)
