"""test_out_dir_derivation.py — io.out_dir renders exactly the dirs the renders write into.

The A11 refactor replaced every render's hand-joined output dir with `io.out_dir(ctx)`, which
derives the dir from the registration's `out_subdir` declaration.  These tests pin the derived
path against a HAND-WRITTEN literal table per evaluation key — so the derivation can never
silently relocate an output, and a declaration edit that moves one shows up as a red literal,
which is the intended review point, not a bug in this file.

The clean-slate family redesign re-baselined this table: figure evals live under one folder
per chart family, CSV evals under the tidy-tables folder, and the leaf root keeps only the
series document and the long per-batch CSV.  From here on the same rule applies as before the
redesign: a new/renamed evaluation adds its intended output dir HERE, from the design that
moved it, never by copying the declaration it checks.

Run:  python -m pytest Tests/unit/test_out_dir_derivation.py -q
"""
from __future__ import annotations

import os

import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.core.registry import (
    EVALUATIONS, EVAL_BY_KEY, evaluation)
from Optimization.Performance_Evaluations.common import io


class _Ctx:
    """Duck-typed context: config scope carries run_dir, aggregate scope carries out_dir —
    exactly the attributes EvalContext / AggregateContext expose."""

    def __init__(self, root, scope):
        if scope == 'aggregate':
            self.out_dir = root
        else:
            self.run_dir = root


#: eval key -> the intended output dir (relative segments under the context root).
#: () = the root itself.  A tuple-declaring eval maps {pick: segments}.  HAND-WRITTEN —
#: this table IS the golden record of where every output goes.
_LITERALS = {
    'config.series':                  (),
    'tables.per_run':                 ('tables',),
    'tables.tidy':                    ('tables',),
    'tables.vs_baseline':             ('tables',),
    'tables.stats':                   ('tables',),
    'tables.by_initial':              ('tables',),
    'headline.top_vs_baseline':       ('figures', 'headline'),
    'headline.all_arms':              ('figures', 'headline'),
    'headline.rollup':                ('figures', 'headline'),
    'headline.throughput_vs_labor':   ('figures', 'headline'),
    'trajectories.overtime':          ('figures', 'trajectories'),
    'labor.delta_topn':               ('figures', 'labor'),
    'labor.per_batch':                ('figures', 'labor'),
    'labor.delta_grid':               ('figures', 'labor'),
    'throughput.volume':              ('figures', 'throughput'),
    'task_time.duration':             ('figures', 'task_time'),
    'task_time.breakdown':            ('figures', 'task_time'),
    'layout.churn':                   ('figures', 'layout'),
    'layout.travel':                  ('figures', 'layout'),
    'diagnostics.metric_grids':       ('figures', 'diagnostics'),
    'diagnostics.scorecards':         ('figures', 'diagnostics'),
    'sig.suite':                      ('figures', 'significance'),
    'sig.by_initial':                 ('figures', 'significance'),
    'agg.traj':                       ('figures', 'trajectories'),
    'agg.tables':                     ('tables',),
    'agg.sig':                        ('figures', 'significance'),
}


@pytest.fixture(autouse=True)
def _clear_current_eval():
    yield
    io.set_current_eval(None)


def test_every_registered_evaluation_has_a_pinned_literal():
    assert {ev.key for ev in EVALUATIONS} == set(_LITERALS), (
        'registry and the golden literal table diverged — a new/renamed evaluation must add '
        'its intended output dir here (from the design that moved it, not from out_subdir)')


@pytest.mark.parametrize('key', sorted(_LITERALS), ids=str)
def test_out_dir_renders_the_pinned_literal(tmp_path, key):
    ev = EVAL_BY_KEY.get(key)
    if ev is None:
        pytest.fail(f'{key} is pinned here but not registered')
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


def _throwaway(key, out_subdir):
    @evaluation(key=key, label='probe', scope='aggregate', out_subdir=out_subdir)
    def _probe(ctx, params):  # pragma: no cover — never driven
        pass
    return _probe


def _unregister(key):
    EVAL_BY_KEY.pop(key, None)
    EVALUATIONS[:] = [e for e in EVALUATIONS if e.key != key]


def test_tuple_declaration_requires_a_declared_pick(tmp_path):
    # No production eval declares a tuple since the aggregate mirror was collapsed to
    # single-family evals, but the mechanism stays contract-tested for the next multi-dir
    # owner.
    from Optimization.Performance_Evaluations.core import artifact_map
    _throwaway('zz_probe.tuple', ('alpha', 'beta'))
    artifact_map._MEMO.pop('subdir', None)          # memo may predate the probe registration
    try:
        ctx = _Ctx(str(tmp_path), 'aggregate')
        io.set_current_eval('zz_probe.tuple')
        with pytest.raises(RuntimeError, match='pick'):
            io.out_dir(ctx)                                   # tuple: pick is mandatory
        with pytest.raises(RuntimeError, match='pick'):
            io.out_dir(ctx, pick='elsewhere')                 # and must be a declared member
    finally:
        _unregister('zz_probe.tuple')
        artifact_map._MEMO.pop('subdir', None)      # do not leak the probe to later tests


def test_single_declaration_rejects_pick(tmp_path):
    ctx = _Ctx(str(tmp_path), 'config')
    io.set_current_eval('trajectories.overtime')
    with pytest.raises(RuntimeError, match='single'):
        io.out_dir(ctx, pick='trajectories')


def test_outside_a_render_out_dir_refuses(tmp_path):
    io.set_current_eval(None)
    with pytest.raises(RuntimeError, match='outside a render'):
        io.out_dir(_Ctx(str(tmp_path), 'config'))


def test_fresh_wipes_the_derived_dir(tmp_path):
    # fresh=True is no longer used by any production render (the parent pre-pass owns every
    # wipe) but remains part of io.out_dir's contract.
    root = str(tmp_path)
    ctx = _Ctx(root, 'config')
    io.set_current_eval('tables.tidy')
    stale = os.path.join(root, 'tables', 'stale.bin')
    os.makedirs(os.path.dirname(stale)); open(stale, 'wb').close()
    got = io.out_dir(ctx, fresh=True)
    assert got == os.path.join(root, 'tables')
    assert os.path.isdir(got) and not os.listdir(got)
