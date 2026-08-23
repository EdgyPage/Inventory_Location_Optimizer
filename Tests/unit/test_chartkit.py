"""test_chartkit.py — the shared rendering contract keeps its geometric promises.

chartkit's whole reason to exist is that legibility properties become CHECKABLE: the
legend occupies a reserved gutter and can never intersect a data panel, the footer band
survives the save verbatim (no tight-bbox crop), axis truncation is announced, and the
sign/unit conventions are single functions.  These tests pin exactly those promises.

Run:  python -m pytest Tests/unit/test_chartkit.py -q
"""
from __future__ import annotations

import os
import struct

import numpy as np
import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — Agg backend + registry
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.core import families, registry


_LABELS = ['Opt|Rank_cartlabor|noRSL', 'Opt|Rank_labor|noRSL', 'Uni|Compact|noRSL']


def _bbox_disjoint(a, b):
    return (a.x1 <= b.x0 or b.x1 <= a.x0 or a.y1 <= b.y0 or b.y1 <= a.y0)


# ── legend geometry ─────────────────────────────────────────────────────────────

def test_gutter_legend_never_intersects_any_axes():
    ch = chartkit.make(panels=2, legend='gutter', legend_labels=_LABELS)
    for ax in ch.axes:
        for lab in _LABELS:
            ax.plot(range(50), np.random.default_rng(0).random(50), label=lab)
    leg = ch.legend(title='strategy')
    ch.fig.canvas.draw()
    lbox = leg.get_window_extent()
    for ax in ch.axes:
        assert _bbox_disjoint(lbox, ax.get_window_extent()), (
            'gutter legend overlaps a data panel')


def test_stacked_second_legend_stays_in_the_gutter():
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=_LABELS)
    ch.ax.plot([0, 1], [0, 1], label=_LABELS[0])
    l1 = ch.legend(title='assignment')
    from matplotlib.lines import Line2D
    l2 = ch.legend(handles=[Line2D([], [], ls='--', color='k')], labels=['opt'],
                   title='initial')
    ch.fig.canvas.draw()
    for leg in (l1, l2):
        assert _bbox_disjoint(leg.get_window_extent(), ch.ax.get_window_extent())
    assert _bbox_disjoint(l1.get_window_extent(), l2.get_window_extent()), (
        'stacked gutter legends overlap each other')


def test_a_many_series_legend_fits_inside_the_canvas():
    """A reserved gutter only prevents overlap if the legend also fits VERTICALLY: at
    34 arms a single column is taller than the panels, and it ran off the bottom of the
    figure and through the provenance band."""
    labels = [f'Uni|Strategy_number_{i:02d}|noRSL' for i in range(34)]
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=labels)
    for lab in labels:
        ch.ax.plot([0, 1], [0, 1], label=lab)
    leg = ch.legend(title='strategy')
    ch.fig.canvas.draw()
    fig_box = ch.fig.get_window_extent()
    lbox = leg.get_window_extent()
    assert lbox.y0 >= fig_box.y0 - 1 and lbox.y1 <= fig_box.y1 + 1, (
        'the legend runs off the canvas')
    assert _bbox_disjoint(lbox, ch.ax.get_window_extent())
    footer_top = ch.fig._footer_y * ch.fig.get_figheight() * ch.fig.dpi * 2
    assert lbox.y0 >= footer_top, 'the legend reaches into the reserved footer band'


def test_legend_deduplicates_identical_labels():
    ch = chartkit.make(panels=2, legend='gutter', legend_labels=['same'])
    for ax in ch.axes:
        ax.plot([0, 1], [0, 1], label='same')
        ax.plot([0, 1], [1, 0], label='same')
    leg = ch.legend()
    assert len(leg.get_texts()) == 1


# ── sizing is content-driven and monotone ───────────────────────────────────────

def test_figure_grows_with_panel_count():
    small = chartkit.make(panels=1, legend='none').fig.get_size_inches()
    tall = chartkit.make(panels=6, ncols=2, legend='none').fig.get_size_inches()
    assert tall[1] > small[1]


def test_gutter_width_grows_with_label_length():
    short = chartkit.make(panels=1, legend_labels=['ab']).fig.get_size_inches()
    long_ = chartkit.make(panels=1,
                          legend_labels=['a-very-long-strategy-label-indeed'])
    assert long_.fig.get_size_inches()[0] > short[0]


def test_category_sizing_helpers_are_monotone_and_capped():
    assert chartkit.height_for_categories(34) > chartkit.height_for_categories(5)
    assert chartkit.height_for_categories(500) == chartkit.height_for_categories(1000)


# ── axes policy ─────────────────────────────────────────────────────────────────

def _texts(ax):
    return [t.get_text() for t in ax.texts]


def test_data_ylim_marks_truncation_iff_zero_excluded():
    ch = chartkit.make(panels=2, legend='none')
    chartkit.data_ylim(ch.axes[0], [95.0, 97.5, 96.2])          # far from zero
    chartkit.data_ylim(ch.axes[1], [-1.0, 2.0])                 # spans zero
    assert any('truncated' in t for t in _texts(ch.axes[0]))
    assert not any('truncated' in t for t in _texts(ch.axes[1]))
    lo, hi = ch.axes[0].get_ylim()
    assert lo > 0 and hi < 100, 'limits must hug the data, not anchor at zero'


def test_shared_ylim_forces_one_scale():
    ch = chartkit.make(panels=3, legend='none')
    chartkit.shared_ylim(ch.axes, [[1.0, 2.0], [1.5, 8.0], [0.5, 3.0]])
    assert len({ax.get_ylim() for ax in ch.axes}) == 1


def test_annotate_unshared_says_so():
    ch = chartkit.make(panels=1, legend='none')
    chartkit.annotate_unshared(ch.ax, axis='x')
    assert any('independent x scale' in t for t in _texts(ch.ax))


# ── sign, units, effect labels ──────────────────────────────────────────────────

def test_improvement_pct_positive_means_better_both_directions():
    # a duration falling 100 -> 90 is a +10% improvement…
    assert chartkit.improvement_pct(90, 100, lower_is_better=True) == pytest.approx(10.0)
    # …and a throughput rising 100 -> 110 is also +10%
    assert chartkit.improvement_pct(110, 100, lower_is_better=False) == pytest.approx(10.0)
    assert chartkit.improvement_pct(110, 100, lower_is_better=True) == pytest.approx(-10.0)


def test_an_undefined_comparison_is_nan_never_a_fabricated_tie():
    """A zero/missing baseline makes the ratio undefined.  Returning 0.0 there would
    assert "no change" on exactly the observations where the contrast is largest, and
    those fabricated ties then drag a mean toward zero and tighten its interval."""
    for baseline in (0, 0.0, None, float('nan')):
        assert np.isnan(chartkit.improvement_pct(5, baseline, lower_is_better=True))


def test_improvement_pct_series_drops_the_undefined_pairs():
    vals = [90.0, 5.0, 80.0]
    bases = [100.0, 0.0, 100.0]          # the middle pair is undefined
    got = chartkit.improvement_pct_series(vals, bases, lower_is_better=True)
    assert list(got) == pytest.approx([10.0, 20.0])
    assert chartkit.improvement_pct_series([], [], lower_is_better=True).size == 0


def test_to_hours():
    assert chartkit.to_hours(3.6e6) == pytest.approx(1.0)
    assert list(chartkit.to_hours([3.6e6, 7.2e6])) == pytest.approx([1.0, 2.0])


def test_to_time_picks_the_unit_that_keeps_numbers_readable():
    # a picker task of ~3 seconds must not render as 0.0008 hours
    vals, unit = chartkit.to_time([3000.0, 2800.0, 3200.0])
    assert unit == 'seconds' and vals[0] == pytest.approx(3.0)
    vals, unit = chartkit.to_time([5.4e6, 7.2e6])
    assert unit == 'hours' and vals[0] == pytest.approx(1.5)
    vals, unit = chartkit.to_time([1.2e5, 1.8e5])
    assert unit == 'minutes' and vals[0] == pytest.approx(2.0)


def test_time_unit_is_chosen_from_the_median_not_an_outlier():
    # one 4-hour straggler among second-scale tasks must not drag the axis into hours
    _vals, unit = chartkit.to_time([3000.0] * 20 + [1.44e7])
    assert unit == 'seconds'


def test_time_units_degenerate_input_falls_back():
    assert chartkit.time_units([])[1] == 'seconds'
    assert chartkit.time_units([0.0, float('nan')])[1] == 'seconds'


def test_effect_label_leads_with_magnitude():
    lab = chartkit.effect_label(1e-14, 0.62, pct=-4.1)
    assert lab.startswith('-4.1%') and '(g=0.62)' in lab and lab.endswith('***')
    assert chartkit.stars(0.2) == 'ns' and chartkit.stars(None) == ''


def test_strategy_dash_is_the_only_initial_encoding():
    assert chartkit.strategy_dash({'initial': 'Uni'}) == '-'
    assert chartkit.strategy_dash({'initial': 'opt_something'}) == '--'


def test_strategy_color_honours_upstream_hex():
    assert chartkit.strategy_color({'color': '#4c72b0', 'assignment': 'x'}) == '#4c72b0'


# ── save path: exact canvas, view grammar ───────────────────────────────────────

def _png_size(path):
    with open(path, 'rb') as fh:
        head = fh.read(24)
    return struct.unpack('>II', head[16:24])


def test_save_keeps_the_reserved_canvas_exactly(tmp_path):
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=_LABELS, footer=True)
    ch.ax.plot([0, 1], [0, 1], label=_LABELS[0])
    ch.legend()
    io.set_footer('provenance line')
    try:
        out = ch.save(str(tmp_path / 'absolute_x.png'), view='absolute')
    finally:
        io.set_footer(None)
    w_in, h_in = ch.fig.get_size_inches()
    w_px, h_px = _png_size(out)
    assert abs(w_px - w_in * 150) <= 2 and abs(h_px - h_in * 150) <= 2, (
        'tight-bbox cropping is back — the reserved footer/gutter bands were cut off')


def test_long_tick_labels_grow_the_canvas_instead_of_being_clipped(tmp_path):
    """chartkit cannot predict how wide a caller's own tick labels will be, and the old
    answer — crop with a tight bbox — is what cut the provenance line off.  `fit` grows
    the canvas around them and keeps the footer band clear."""
    names = [f'Opt|Rank_something_long_{i:02d}|noRSL' for i in range(17)]
    ch = chartkit.make(panels=1, legend='none')
    ch.ax.bar(range(len(names)), range(len(names)))
    ch.ax.set_xticks(range(len(names)))
    ch.ax.set_xticklabels(names, rotation=45, ha='right')
    ch.ax.set_yticks(range(0, 17, 4))
    ch.ax.set_yticklabels([f'a-very-long-row-label-{i}' for i in range(0, 17, 4)])
    before = ch.fig.get_size_inches().copy()
    ch.fit()
    after = ch.fig.get_size_inches()
    assert after[0] > before[0] and after[1] > before[1], 'canvas did not grow'
    ch.fig.canvas.draw()
    bb = ch.fig.get_tightbbox(ch.fig.canvas.get_renderer())
    assert bb.x0 >= -0.01 and bb.y0 >= -0.01, 'content still hangs off the canvas'
    assert bb.y0 >= ch.fig._footer_band - 0.02, 'content intrudes on the footer band'


def test_fit_keeps_the_gutter_legend_beside_the_data_after_growing(tmp_path):
    labels = [f'Uni|Arm_{i:02d}|noRSL' for i in range(12)]
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=labels)
    for lab in labels:
        ch.ax.plot([0, 1], [0, 1], label=lab)
    leg = ch.legend(title='strategy')
    ch.ax.set_xticks([0, 1])
    ch.ax.set_xticklabels(['an extremely long tick label here',
                           'another extremely long one'], rotation=45, ha='right')
    ch.fit()
    ch.fig.canvas.draw()
    assert _bbox_disjoint(leg.get_window_extent(), ch.ax.get_window_extent()), (
        'the legend landed on the data after the canvas grew')


@pytest.mark.parametrize('panel_h', [3.6, 16.0])
def test_title_and_subtitle_stay_out_of_the_axes_at_any_height(panel_h):
    """Both header lines sit a fixed distance from the top edge.  Positioned by
    fraction, the subtitle of a tall figure (a 34-row ladder) lands inside the data."""
    ch = chartkit.make(panels=1, panel_h=panel_h, legend='none')
    ch.ax.plot([0, 1], [0, 1])
    ch.title('A title', 'and its subtitle')
    ch.fig.canvas.draw()
    ax_top = ch.ax.get_window_extent().y1
    for txt in ch.fig.texts:
        if txt.get_text() in ('A title', 'and its subtitle'):
            assert txt.get_window_extent().y0 >= ax_top - 1, (
                f'{txt.get_text()!r} overlaps the axes at panel_h={panel_h}')


def test_save_rejects_a_filename_that_contradicts_its_view(tmp_path):
    ch = chartkit.make(panels=1, legend='none')
    with pytest.raises(ValueError, match='view'):
        ch.save(str(tmp_path / 'percent_x.png'), view='absolute')


def test_view_grammar_enforced_inside_a_render(tmp_path):
    @registry.evaluation(key='zz_test.family_probe', label='probe', scope='config',
                         family='labor', views=('delta',))
    def _probe(ctx, params):  # pragma: no cover — never driven
        pass
    try:
        assert registry.EVAL_BY_KEY['zz_test.family_probe'].out_subdir == 'figures/labor'
        io.set_current_eval('zz_test.family_probe')
        ch = chartkit.make(panels=1, legend='none')
        with pytest.raises(ValueError, match='not among its declared'):
            ch.save(str(tmp_path / 'percent_y.png'), view='percent')
        ch2 = chartkit.make(panels=1, legend='none')
        with pytest.raises(ValueError, match='not in family'):
            ch2.save(str(tmp_path / 'table_y.png'), view='table')
    finally:
        io.set_current_eval(None)
        registry.EVAL_BY_KEY.pop('zz_test.family_probe')
        registry.EVALUATIONS[:] = [e for e in registry.EVALUATIONS
                                   if e.key != 'zz_test.family_probe']


def test_family_and_out_subdir_are_mutually_exclusive():
    with pytest.raises(ValueError, match='not both'):
        @registry.evaluation(key='zz_test.both', label='x', scope='config',
                             family='labor', out_subdir='elsewhere')
        def _bad(ctx, params):  # pragma: no cover
            pass


def test_family_grammar_rejects_undeclared_views_at_registration():
    with pytest.raises(ValueError, match='not allowed in family'):
        @registry.evaluation(key='zz_test.badview', label='x', scope='config',
                             family='significance', views=('percent',))
        def _bad(ctx, params):  # pragma: no cover
            pass


def test_every_family_view_set_is_within_the_grammar_vocabulary():
    for fam, spec in families.FAMILIES.items():
        assert set(spec['required']) <= set(spec['views']) <= set(families.VIEWS), fam
