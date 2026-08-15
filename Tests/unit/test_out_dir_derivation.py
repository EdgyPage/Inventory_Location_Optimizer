"""test_out_dir_derivation.py — io.out_dir renders exactly the dirs the renders always joined.

The A11 refactor replaced every render's hand-joined output dir with `io.out_dir(ctx)`, which
derives the dir from the registration's `out_subdir` declaration.  These tests pin the derived
path against the HISTORICAL LITERALS, hand-written here per evaluation key — so the derivation
can never silently relocate an output, and a declaration edit that moves one shows up as a
red literal, which is the intended review point, not a bug in this file.

Run:  python -m pytest Tests/unit/test_out_dir_derivation.py -q
"""
from __future__ import annotations

import os

import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.core.registry import EVALUATIONS
from Optimization.Performance_Evaluations.common import io


class _Ctx:
    """Duck-typed context: config scope carries run_dir, aggregate scope carries out_dir —
    exactly the attributes EvalContext / AggregateContext expose."""

    def __init__(self, root, scope):
        if scope == 'aggregate':
            self.out_dir = root
        else:
            self.run_dir = root


#: eval key -> the pre-refactor literal join (relative segments under the context root).
#: () = the root itself.  A tuple-declaring eval maps {pick: segments}.  HAND-WRITTEN — this
#: table IS the golden record of where every output has always gone; regenerate it only from
#: the git history of the render bodies, never from the declarations it checks.
_LITERALS = {
    'config.series':                  (),
    'config.summary_csv':             ('per_strategy',),
    'compare.faceted':                ('compare', 'faceted'),
    'compare.overlay':                ('compare', 'overlay'),
    'compare.top_metric':             ('compare', 'top'),
    'compare.delta_by_batch':         ('compare', 'top'),
    'compare.delta_over_time':        ('compare', 'top'),
    'compare.volume_curve':           ('compare', 'top'),
    'compare.top_vs_baseline':        ('compare',),
    'compare.throughput_labor':       ('compare',),
    'compare.delta_bars':             ('compare', 'breakdown'),
    'compare.labor_trend':            ('compare', 'breakdown'),
    'compare.pick_vs_travel':         ('compare', 'breakdown'),
    'compare.task_box':               ('compare', 'breakdown'),
    'breakdown.travel_handling':      ('compare', 'breakdown'),
    'per_strategy.delta_by_batch':    ('per_strategy',),
    'per_strategy.delta_over_time':   ('per_strategy',),
    'per_strategy.metric_grids':      ('per_strategy',),
    'per_strategy.report_bars':       ('per_strategy',),
    'per_strategy.scorecards':        ('per_strategy',),
    'per_strategy.summary_bars':      ('per_strategy',),
    'stats.suite':                    ('stats',),
    'stats.by_initial':               ('stats_by_initial',),
    'agg.stats':                      ('stats',),
    'agg.stats_by_initial':           ('stats_by_initial',),
    'agg.cross_profile':              {'breakdown': ('breakdown',),
                                       'faceted': ('faceted',),
                                       'overlay': ('overlay',),
                                       'top': ('top',)},
}


@pytest.fixture(autouse=True)
def _clear_current_eval():
    yield
    io.set_current_eval(None)


def test_every_registered_evaluation_has_a_pinned_literal():
    assert {ev.key for ev in EVALUATIONS} == set(_LITERALS), (
        'registry and the golden literal table diverged — a new/renamed evaluation must add '
        'its historical output dir here (from the render body it replaced, not from '
        'out_subdir)')


@pytest.mark.parametrize('ev', EVALUATIONS, ids=lambda e: e.key)
def test_out_dir_renders_the_historical_literal(tmp_path, ev):
    root = str(tmp_path / 'root')
    os.makedirs(root)
    ctx = _Ctx(root, ev.scope)
    io.set_current_eval(ev.key)
    expected = _LITERALS[ev.key]
    if isinstance(expected, dict):
        for pick, segs in expected.items():
            got = io.out_dir(ctx, pick=pick)
            assert got == os.path.join(root, *segs), (ev.key, pick)
            assert os.path.isdir(got)
    else:
        got = io.out_dir(ctx)
        assert got == (os.path.join(root, *expected) if expected else root), ev.key
        assert os.path.isdir(got)


def test_tuple_declaration_requires_a_declared_pick(tmp_path):
    ctx = _Ctx(str(tmp_path), 'aggregate')
    io.set_current_eval('agg.cross_profile')
    with pytest.raises(RuntimeError, match='pick'):
        io.out_dir(ctx)                                   # tuple: pick is mandatory
    with pytest.raises(RuntimeError, match='pick'):
        io.out_dir(ctx, pick='elsewhere')                 # and must be a declared member


def test_single_declaration_rejects_pick(tmp_path):
    ctx = _Ctx(str(tmp_path), 'config')
    io.set_current_eval('compare.faceted')
    with pytest.raises(RuntimeError, match='single'):
        io.out_dir(ctx, pick='faceted')


def test_outside_a_render_out_dir_refuses(tmp_path):
    io.set_current_eval(None)
    with pytest.raises(RuntimeError, match='outside a render'):
        io.out_dir(_Ctx(str(tmp_path), 'config'))


def test_fresh_wipes_the_derived_dir(tmp_path):
    root = str(tmp_path)
    ctx = _Ctx(root, 'config')
    io.set_current_eval('stats.suite')
    stale = os.path.join(root, 'stats', 'stale.png')
    os.makedirs(os.path.dirname(stale)); open(stale, 'wb').close()
    got = io.out_dir(ctx, fresh=True)
    assert got == os.path.join(root, 'stats')
    assert os.path.isdir(got) and not os.listdir(got)
