"""test_artifact_map.py — the output map stays derived, honest, and equal to the design.

core/artifact_map.py derives WHERE each evaluation writes from two declarations (`out_subdir=`
on the registration, `evaluation:` attributions in the run-tree contract) instead of retyped
literals.  These tests are the teeth: the two declarations may never contradict each other,
the derived prepare-list may never drift from the family-tree layout the redesign adopted,
and ingest's explicit figure-pick preference may never fall out of step with the registry's
subdir vocabulary it encodes.

Run:  python -m pytest Tests/architecture/test_artifact_map.py -q
"""
from __future__ import annotations

import importlib.util
import os

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.core import artifact_map
from Optimization.Performance_Evaluations.core.registry import (
    EVALUATIONS, EVAL_BY_KEY, evaluation)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


# ── declarations vs contract ─────────────────────────────────────────────────────

def test_out_subdir_declarations_agree_with_the_contract():
    """Every `evaluation:`-attributed artifact must land under its evaluation's declared
    out_subdir (or the leaf root).  A finding here means one of the two declarations lies."""
    assert artifact_map.findings() == []


def test_every_attributed_evaluation_is_registered():
    keys = {ev.key for ev in EVALUATIONS}
    assert set(artifact_map.owned_artifacts()) <= keys


# ── the derived prepare-list vs the adopted layout ───────────────────────────────

def test_config_dirs_derive_exactly_the_family_tree():
    """The clean-slate layout: two shared tops, one nested folder per chart family.  The
    shared-top rule must reproduce this set exactly — a new value here means an output
    moved, which is an adoption decision, not a side effect."""
    tops, nested = artifact_map.config_dirs()
    assert set(tops) == {'figures', 'tables'}
    assert set(nested) == {'figures/diagnostics', 'figures/headline', 'figures/labor',
                           'figures/layout', 'figures/significance', 'figures/task_time',
                           'figures/throughput', 'figures/trajectories'}


def test_no_config_eval_wipes_its_own_leaf():
    """The single-owner self-wipe pattern is retired: the parent pre-pass owns every wipe.
    Every config-stage output dir must therefore sit under a shared, pre-wiped top."""
    tops, _ = artifact_map.config_dirs()
    for ev in EVALUATIONS:
        if ev.scope in ('per_strategy', 'config') and ev.out_subdir:
            subs = (ev.out_subdir,) if isinstance(ev.out_subdir, str) else ev.out_subdir
            for sub in subs:
                assert sub.split('/')[0] in tops, (
                    f'{ev.key} writes to {sub!r}, outside the pre-wiped tops {tops} — '
                    f'either add a second owner or move it under figures/ or tables/')


# ── save-time bounds (the _save_close warning's decision function) ───────────────

def test_save_in_bounds_matches_subdir_segments_not_substrings():
    assert artifact_map.save_in_bounds('trajectories.overtime',
                                       os.path.join('C:', 'r', 'figures', 'trajectories'))
    assert not artifact_map.save_in_bounds('trajectories.overtime',
                                           os.path.join('C:', 'r', 'figures', 'labor'))
    # segment-wise, not substring: .../notfigures/trajectories must NOT satisfy the claim
    assert not artifact_map.save_in_bounds('trajectories.overtime',
                                           os.path.join('C:', 'r', 'notfigures',
                                                        'trajectories'))


def test_tuple_declaration_bounds_each_member_and_nothing_else():
    """No production eval declares a tuple since the aggregate mirror collapsed to
    single-family evals, but the multi-dir mechanism stays contract-tested for the next
    owner: a save inside any member is in bounds, anywhere else is not."""
    @evaluation(key='zz_probe.bounds', label='probe', scope='aggregate',
                out_subdir=('alpha', 'beta'))
    def _probe(ctx, params):  # pragma: no cover — never driven
        pass
    artifact_map._MEMO.pop('subdir', None)          # memo predates the probe registration
    try:
        sub = artifact_map.figure_subdir('zz_probe.bounds')
        assert sub == ('alpha', 'beta')
        for member in sub:
            assert artifact_map.save_in_bounds('zz_probe.bounds',
                                               os.path.join('C:', 'agg', 'store', member))
        assert not artifact_map.save_in_bounds('zz_probe.bounds',
                                               os.path.join('C:', 'agg', 'store', 'else'))
        assert not artifact_map.save_in_bounds('zz_probe.bounds',
                                               os.path.join('C:', 'agg', 'store'))
    finally:
        EVAL_BY_KEY.pop('zz_probe.bounds', None)
        EVALUATIONS[:] = [e for e in EVALUATIONS if e.key != 'zz_probe.bounds']
        artifact_map._MEMO.pop('subdir', None)      # do not leak the probe to later tests


def test_root_declaring_evaluations_make_no_checkable_claim():
    for key in ('config.series',):
        assert artifact_map.figure_subdir(key) == ''
        assert artifact_map.save_in_bounds(key, os.path.join('C:', 'anywhere'))


# ── ingest's explicit figure preference vs the registry vocabulary ───────────────

def _load_ingest():
    spec = importlib.util.spec_from_file_location(
        'docs_ingest', os.path.join(_ROOT, 'docs', 'experiments', 'ingest.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ingest_preference_covers_every_declared_figure_subdir_in_walk_order():
    """ingest._FIGURE_DIR_PREFERENCE is a literal (ingest must not import the matplotlib-heavy
    analysis package), so THIS is the tie: it must contain every non-root out_subdir a
    CONFIG-STAGE evaluation declares, in os.walk order (lexicographic, parents before
    children).  Aggregate-scope declarations are aggregate-TREE dirs and must never enter
    the config-leaf preference."""
    ingest = _load_ingest()
    pref = ingest._FIGURE_DIR_PREFERENCE
    declared = set()
    for ev in EVALUATIONS:
        if ev.scope in ('per_strategy', 'config') and ev.out_subdir:
            declared.update((ev.out_subdir,) if isinstance(ev.out_subdir, str)
                            else ev.out_subdir)
    missing = declared - set(pref)
    assert not missing, f'out_subdir(s) not in ingest._FIGURE_DIR_PREFERENCE: {sorted(missing)}'
    assert list(pref) == sorted(pref), (
        'preference must stay in lexicographic (walk) order so the pick matches every '
        'committed snapshot — reorder only with an ingest dry-run diff proving no figure moves')
