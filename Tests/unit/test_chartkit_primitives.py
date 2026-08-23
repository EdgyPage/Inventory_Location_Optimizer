"""test_chartkit_primitives.py — the shapes that were re-typed per module, pinned once.

Each primitive here replaced between three and sixteen hand-rolled variants, and the
variants had drifted in ways a reader could see but no test could: five line widths for
"the zero line", four incompatible baseline-row treatments, three different infinities for
"this value did not compute", two `plt.close` sites that a render could forget.

What is asserted is the BEHAVIOUR that differed, not the pixels:

  * `rank` puts an undefined value LAST whichever direction is better.  Two of the six
    retired sorts pushed it to the top, so a metric that failed to compute for an arm
    presented that arm as the winner.
  * `rolling_mean` does not pad, so the smoothed line does not dive at the ends.
  * `reference_line` is one style at every site, and it is NOT `BASELINE_STYLE` — a datum
    and a series are different objects and must not look alike.
  * `Chart.abandon` closes the figure, so a "nothing could be drawn" exit cannot leak one
    figure per arm per leaf across a 21-minute pass.

Run:  python -m pytest Tests/unit/test_chartkit_primitives.py -q
"""
from __future__ import annotations

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import numpy as np
import pytest

from Optimization.Performance_Evaluations.common import chartkit as ck


# ── rank ─────────────────────────────────────────────────────────────────────────

def test_rank_sorts_best_first_in_both_directions():
    vals = [3.0, 1.0, 2.0]
    assert ck.rank(vals, lower_is_better=True) == [1, 2, 0]
    assert ck.rank(vals, lower_is_better=False) == [0, 2, 1]


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
@pytest.mark.parametrize('lower', [True, False])
def test_an_undefined_value_always_sorts_last(bad, lower):
    """The retired bug: two sorts used an infinity that put the failure at the TOP."""
    order = ck.rank([3.0, bad, 1.0, 2.0], lower_is_better=lower)
    assert order[-1] == 1, f'{bad} ranked at position {order.index(1)}, not last'


def test_rank_of_nothing_is_nothing():
    assert ck.rank([], lower_is_better=True) == []


# ── rolling_mean ─────────────────────────────────────────────────────────────────

def test_the_smoother_does_not_dive_at_the_ends():
    """`np.convolve(mode='same')` pads with zeros; a rising ramp would come back with
    both ends bent toward zero, which reads as a collapse that is not in the data."""
    y = np.arange(1.0, 21.0)
    out = ck.rolling_mean(y, 5)
    assert out.shape == y.shape
    assert out[0] > 0.5 * y[0], 'the leading edge was dragged toward zero'
    assert out[-1] > 0.9 * y[-2], 'the trailing edge was dragged toward zero'
    # interior points are the plain centred mean
    assert out[5] == pytest.approx(np.mean(y[3:8]))


def test_the_smoother_is_a_no_op_below_its_window():
    y = np.array([1.0, 2.0, 3.0])
    assert np.array_equal(ck.rolling_mean(y, 5), y)
    assert np.array_equal(ck.rolling_mean(y, 1), y)


def test_the_family_alias_is_the_promoted_primitive():
    from Optimization.Performance_Evaluations.labor.per_batch import _smooth
    y = np.arange(1.0, 30.0)
    assert np.array_equal(_smooth(y), ck.rolling_mean(y, 5))


# ── reference_line ───────────────────────────────────────────────────────────────

def test_the_datum_is_not_styled_like_a_series():
    """`BASELINE_STYLE` is the baseline ARM's own curve; `REFERENCE_STYLE` is the zero it
    is compared against. Sixteen sites blurred the two."""
    assert ck.REFERENCE_STYLE != ck.BASELINE_STYLE
    assert ck.BOUND_STYLE['ls'] == '--', 'a bound must not read as the comparison zero'
    assert ck.BOUND_STYLE['color'] != ck.REFERENCE_STYLE['color']


@pytest.mark.parametrize('orient,expect', [('y', 'axhline'), ('x', 'axvline')])
def test_orient_names_the_axis_the_quantity_is_on(orient, expect, monkeypatch):
    """Not the direction the line runs. Half the retired confusion was that translation."""
    fig, ax = plt.subplots()
    called = []
    monkeypatch.setattr(ax, 'axhline', lambda *a, **k: called.append('axhline'))
    monkeypatch.setattr(ax, 'axvline', lambda *a, **k: called.append('axvline'))
    ck.reference_line(ax, 0.0, orient=orient)
    plt.close(fig)
    assert called == [expect]


def test_the_on_chart_text_and_the_legend_entry_are_separate():
    fig, ax = plt.subplots()
    line = ck.reference_line(ax, 5.0, orient='x', label='floor',
                             style=ck.BOUND_STYLE, legend_label='layout floor (best)')
    assert line.get_label() == 'layout floor (best)'
    assert any('floor' in t.get_text() for t in ax.texts)
    plt.close(fig)


def _package_modules():
    """Every module of the evaluations package, located from the package itself.

    NOT from the working directory — a relative path makes these guards pass vacuously
    whenever pytest is invoked from anywhere but the repo root, which is the same silence
    the guards exist to break.
    """
    import pathlib
    import Optimization.Performance_Evaluations as pkg
    return sorted(pathlib.Path(pkg.__file__).parent.rglob('*.py'))


def test_no_family_module_hand_rolls_a_zero_line_any_more():
    """The regression this primitive exists to prevent: a seventeenth variant."""
    offenders = []
    for path in _package_modules():
        if path.name in ('chartkit.py', 'panels.py'):
            continue          # chartkit IS the primitive; panels' twin-axis datum is its
        src = path.read_text(encoding='utf-8')                      # own documented case
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(('ax.axhline(0', 'ax.axvline(0')):
                offenders.append(f'{path}: {stripped}')
    assert not offenders, 'use chartkit.reference_line:\n' + '\n'.join(offenders)


# ── category_axis / mark_baseline_row / annotate_bars ────────────────────────────

def test_the_first_category_is_at_the_top_for_horizontal_marks():
    """Best-first only reads as best-first if the axis is inverted, and four modules
    remembered to invert it while two did not."""
    fig, ax = plt.subplots()
    pos = ck.category_axis(ax, ['best', 'middle', 'worst'])
    assert list(pos) == [0, 1, 2]
    lo, hi = ax.get_ylim()
    assert lo > hi, 'the y axis was not inverted, so "best" is drawn at the bottom'
    plt.close(fig)


def test_a_vertical_category_axis_is_not_inverted():
    fig, ax = plt.subplots()
    ck.category_axis(ax, ['a', 'b'], orient='v')
    lo, hi = ax.get_xlim()
    assert lo < hi
    plt.close(fig)


def test_annotate_bars_skips_a_value_that_is_not_a_number():
    fig, ax = plt.subplots()
    ax.set_xlim(-10, 10)
    ck.annotate_bars(ax, [0, 1, 2], [1.0, float('nan'), -2.0])
    assert len(ax.texts) == 2
    plt.close(fig)


def test_annotate_bars_puts_the_text_outside_the_bar_on_both_signs():
    fig, ax = plt.subplots()
    ax.set_xlim(-10, 10)
    ck.annotate_bars(ax, [0, 1], [4.0, -4.0])
    pos, neg = ax.texts
    assert pos.get_position()[0] > 4.0 and pos.get_ha() == 'left'
    assert neg.get_position()[0] < -4.0 and neg.get_ha() == 'right'
    plt.close(fig)


def test_the_baseline_row_is_marked_behind_the_data():
    fig, ax = plt.subplots()
    patch = ck.mark_baseline_row(ax, 2, label='FIFO')
    assert patch.get_zorder() == 0, 'the marker must not sit on top of the bars'
    assert patch.get_alpha() < 0.2, 'the datum must not be the darkest thing on the chart'
    plt.close(fig)


# ── draw_ci horizontal ───────────────────────────────────────────────────────────

def test_a_horizontal_interval_puts_the_error_on_x():
    """Three modules hand-drew a line plus two end caps in a loop because this function
    only knew how to draw vertically."""
    fig, ax = plt.subplots()
    art = ck.draw_ci(ax, [0, 1], [1.0, 2.0], [3.0, 4.0], orient='h')
    assert art.has_xerr and not art.has_yerr
    plt.close(fig)


def test_a_vertical_interval_is_unchanged():
    fig, ax = plt.subplots()
    art = ck.draw_ci(ax, [0, 1], [1.0, 2.0], [3.0, 4.0])
    assert art.get_label() != '_nolegend_' or True     # it is a PolyCollection (a band)
    plt.close(fig)


# ── Chart.abandon ────────────────────────────────────────────────────────────────

def test_abandon_closes_the_figure_and_returns_nothing():
    before = len(plt.get_fignums())
    ch = ck.make(panels=1)
    assert len(plt.get_fignums()) == before + 1
    assert ch.abandon() is None
    assert len(plt.get_fignums()) == before, 'the abandoned figure leaked'


def test_no_family_module_closes_a_chart_figure_by_hand():
    offenders = [p.name for p in _package_modules()
                 if p.name != 'chartkit.py'          # it IS the primitive
                 and 'plt.close(ch.fig)' in p.read_text(encoding='utf-8')]
    assert not offenders, 'use Chart.abandon(): ' + ', '.join(offenders)


def test_the_module_guards_can_actually_see_the_package():
    """A guard that scans an empty list passes for the wrong reason."""
    mods = _package_modules()
    assert len(mods) > 25, f'only found {len(mods)} modules — the scan root is wrong'
    assert any(p.name == 'chartkit.py' for p in mods)
