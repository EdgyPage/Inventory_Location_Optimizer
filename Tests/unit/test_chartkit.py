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
    assert chartkit.improvement_pct(5, 0, lower_is_better=True) == 0.0


def test_to_hours():
    assert chartkit.to_hours(3.6e6) == pytest.approx(1.0)
    assert list(chartkit.to_hours([3.6e6, 7.2e6])) == pytest.approx([1.0, 2.0])


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
