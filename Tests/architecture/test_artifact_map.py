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
    """The clean-slate layout: two shared tops, one nested folder per LEAF chart family.

    `tops` stays hand-written because it IS the adopted design — two shared, pre-wiped
    roots — and a change to it means an output moved, which is a decision rather than a
    side effect.  The nested set is derived, because "one folder per leaf family" is a
    restatement of `core/families.py` and restating it here bought nothing except a second
    list to forget: `cost` renders at run scope and must NOT appear, which the derivation
    knows and a literal only remembered.
    """
    from Optimization.Performance_Evaluations.core.families import (
        LEAF_FAMILIES, figures_subdir)
    tops, nested = artifact_map.config_dirs()
    assert set(tops) == {'figures', 'tables'}
    assert set(nested) == {figures_subdir(f) for f in LEAF_FAMILIES}
    # Nine since 2026-08-31: `yard` joined as the tenth family overall and the ninth at
    # leaf scope.  The literal is here to make a family arriving a DECISION rather than a
    # side effect, so it moves in the same commit that adds one — and this one also moved
    # `figures_yard_pngs` into the run-tree contract, which is where the folder becomes
    # declared rather than merely created.
    assert len(nested) == 9, 'the leaf family count changed; that is an adoption decision'
    assert 'figures/cost' not in nested, 'cost renders at run scope, not per leaf'


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
    # THE REVERSE, which the forward check alone never gave: an entry here that no
    # evaluation declares is a folder that will never exist.  It is harmless at walk time —
    # the path simply misses — and that is exactly why it survives a family being retired:
    # nothing fails, the literal just quietly describes a tree that is gone.
    stale = set(pref) - declared
    assert not stale, (
        f'ingest._FIGURE_DIR_PREFERENCE lists {sorted(stale)}, which no config-stage '
        f'evaluation writes to. A retired family must leave this literal too.')
    assert list(pref) == sorted(pref), (
        'preference must stay in lexicographic (walk) order so the pick matches every '
        'committed snapshot — reorder only with an ingest dry-run diff proving no figure moves')


# ── ingest's prune and the guard's finding are ONE rule ──────────────────────────

def test_ingest_prunes_exactly_what_the_guard_would_report():
    """Staging copies and never removed anything, so a RENAMED figure left the old file
    committed, staged and reachable — describing a chart the suite no longer draws.  The
    only thing that noticed was `experiment_guard --scan`, at the END of the publish loop.

    Ingest now applies the guard's own rule before the guard looks.  These two must be the
    SAME rule: one deletes and the other reports, and a file one calls an orphan while the
    other does not is a build that fails after the cleanup already ran.
    """
    import importlib.util
    ingest = _load_ingest()
    spec = importlib.util.spec_from_file_location(
        'zz_experiment_guard', os.path.join(_ROOT, 'context', 'guards',
                                            'experiment_guard.py'))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    assert ingest._declared_png_names() == guard._registry_png_names(), (
        'ingest and the guard disagree about which PNG basenames the registry declares')
    assert ingest.WHATIF_PNG_GLOB == 'whatif_*.png', (
        "the guard exempts whatif_*.png from the ad-hoc-graph finding; ingest's prune "
        'must exempt the same pattern or it deletes declared what-if artifacts')


def test_the_declared_name_set_includes_retired_entries():
    """A `retired:` entry keeps protecting its staged copies: retired means the WRITER is
    gone, not that evidence a published page cites should vanish underneath it."""
    ingest = _load_ingest()
    import yaml
    with open(os.path.join(_ROOT, 'docs', 'experiments', 'figures.yml'),
              encoding='utf-8') as fh:
        figs = yaml.safe_load(fh)['figures']
    retired = {f['name'] for f in figs if f.get('retired')}
    assert retired, 'no retired entries — this test would pass vacuously'
    assert retired <= ingest._declared_png_names()
