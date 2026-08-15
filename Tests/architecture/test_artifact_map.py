"""test_artifact_map.py — the output map stays derived, honest, and equal to history.

core/artifact_map.py derives WHERE each evaluation writes from two declarations (`out_subdir=`
on the registration, `evaluation:` attributions in the run-tree contract) instead of retyped
literals.  These tests are the teeth: the two declarations may never contradict each other,
the derived prepare-list may never drift from the directories the driver has always prepared,
and ingest's explicit figure-pick preference may never fall out of step with the registry's
subdir vocabulary it encodes.

Run:  python -m pytest Tests/architecture/test_artifact_map.py -q
"""
from __future__ import annotations

import importlib.util
import os

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.core import artifact_map
from Optimization.Performance_Evaluations.core.registry import EVALUATIONS

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


# ── declarations vs contract ─────────────────────────────────────────────────────

def test_out_subdir_declarations_agree_with_the_contract():
    """Every `evaluation:`-attributed artifact must land under its evaluation's declared
    out_subdir (or the leaf root).  A finding here means one of the two declarations lies."""
    assert artifact_map.findings() == []


def test_every_attributed_evaluation_is_registered():
    keys = {ev.key for ev in EVALUATIONS}
    assert set(artifact_map.owned_artifacts()) <= keys


# ── the derived prepare-list vs the historical literals ──────────────────────────

def test_config_dirs_derive_exactly_the_historical_literals():
    """prepare_config_dirs wiped per_strategy/ + compare/ and created the four compare
    subdirs since the registry existed.  The shared-top rule must reproduce that set exactly —
    a new value here means an output moved, which is an adoption decision, not a side effect."""
    tops, nested = artifact_map.config_dirs()
    assert set(tops) == {'compare', 'per_strategy'}
    assert set(nested) == {'compare/breakdown', 'compare/faceted',
                           'compare/overlay', 'compare/top'}


def test_single_owner_subdirs_stay_out_of_the_prepare_list():
    """stats/ and stats_by_initial/ have exactly one config-stage owner each, which wipes its
    own leaf (_fresh_dir in its render).  The parent pre-wiping them too would be a behavior
    change the derivation must not smuggle in."""
    tops, _ = artifact_map.config_dirs()
    assert 'stats' not in tops and 'stats_by_initial' not in tops


# ── save-time bounds (the _save_close warning's decision function) ───────────────

def test_save_in_bounds_matches_subdir_segments_not_substrings():
    assert artifact_map.save_in_bounds('compare.faceted',
                                       os.path.join('C:', 'r', 'compare', 'faceted'))
    assert not artifact_map.save_in_bounds('compare.faceted',
                                           os.path.join('C:', 'r', 'compare', 'top'))
    # segment-wise, not substring: .../notcompare/faceted must NOT satisfy compare/faceted
    assert not artifact_map.save_in_bounds('compare.faceted',
                                           os.path.join('C:', 'r', 'notcompare', 'faceted'))


def test_tuple_declaration_bounds_each_member_and_nothing_else():
    """agg.cross_profile declares FOUR dirs (the aggregate mirror of the compare tree) — a
    save inside any member is in bounds, anywhere else is not.  Before the tuple form this
    eval declared '' and its 17 figures escaped the check entirely."""
    sub = artifact_map.figure_subdir('agg.cross_profile')
    assert sub == ('breakdown', 'faceted', 'overlay', 'top')
    for member in sub:
        assert artifact_map.save_in_bounds('agg.cross_profile',
                                           os.path.join('C:', 'agg', 'store', member))
    assert not artifact_map.save_in_bounds('agg.cross_profile',
                                           os.path.join('C:', 'agg', 'store', 'elsewhere'))
    assert not artifact_map.save_in_bounds('agg.cross_profile',
                                           os.path.join('C:', 'agg', 'store'))


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
    children) — the order whose alphabetical luck it exists to make explicit.  Aggregate-scope
    declarations (including tuple members like cross_profile's faceted/overlay/top/breakdown)
    are aggregate-TREE dirs and must never enter the config-leaf preference."""
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
