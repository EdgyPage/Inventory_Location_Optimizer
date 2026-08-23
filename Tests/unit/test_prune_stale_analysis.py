"""test_prune_stale_analysis.py — the stale-output pruner deletes orphans and nothing else.

This tool deletes directories from a run tree, so its two safety properties are the whole
test: a tree LEVEL (whose name the contract cannot know) is never mistaken for an orphan, and
an orphan is only ever removed where its replacement already sits beside it.  The second one
is what stops an interrupted analysis — a stage that has not run yet — from losing its only
copy of the previous output.

Run:  python -m pytest Tests/unit/test_prune_stale_analysis.py -q
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load():
    spec = importlib.util.spec_from_file_location(
        'prune_stale', os.path.join(_ROOT, 'scripts', 'prune_stale_analysis.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prune = _load()


def _mk(*parts):
    os.makedirs(os.path.join(*parts), exist_ok=True)
    return os.path.join(*parts)


def _leaf(base, *, live=True, orphans=('compare', 'per_strategy')):
    """A channel-run leaf: the current family folders plus retired ones beside them."""
    leaf = _mk(base, 'k1', 'pairA', 'store', 'store')
    if live:
        _mk(leaf, 'figures', 'headline')
        _mk(leaf, 'tables')
    for o in orphans:
        _mk(leaf, o, 'faceted')
    return leaf


def test_declared_dir_names_include_the_family_tree_and_exclude_levels():
    from Optimization.runschema import contract
    doc = contract.load(contract.head()) or contract.build()
    names = prune.declared_dir_names(doc)
    assert {'figures', 'tables', 'headline', 'significance'} <= names
    for level in ('cell', 'pair', 'config', 'channel', '{cell}', '{channel?}'):
        assert level not in names, f'{level} is a tree level, not a contract-owned name'


def test_orphans_beside_live_output_are_stale(tmp_path):
    base = str(tmp_path)
    leaf = _leaf(base)
    stale = prune.find_stale(base)
    assert sorted(os.path.basename(p) for p in stale) == ['compare', 'per_strategy']
    # the level directories leading to live output are untouched
    for keep in (leaf, os.path.dirname(leaf), os.path.join(leaf, 'figures'),
                 os.path.join(leaf, 'tables')):
        assert keep not in stale


def test_an_orphan_with_no_live_sibling_is_skipped_not_deleted(tmp_path, capsys):
    """The interrupted-analysis case: this leaf was re-analysed, an aggregate subtree beside
    it was not.  The aggregate orphan has no current replacement, so it must survive."""
    base = str(tmp_path)
    _leaf(base)                                        # gives the tree its live output
    agg = _mk(base, 'k1', '_aggregate', 'store', 'store')
    _mk(agg, 'faceted')                                # only retired content, no figures/
    stale = prune.find_stale(base)
    assert all('_aggregate' not in p for p in stale), 'deleted an unreplaced subtree'
    assert 'SKIP' in capsys.readouterr().out


def test_a_tree_with_no_current_output_refuses(tmp_path):
    """Nothing current anywhere means the analysis has not been re-run — not that the whole
    tree is obsolete."""
    _leaf(str(tmp_path), live=False)
    with pytest.raises(RuntimeError, match='nothing can be judged stale'):
        prune.find_stale(str(tmp_path))


def test_reserved_subtrees_are_never_walked(tmp_path):
    base = str(tmp_path)
    _leaf(base)
    for reserved in ('_viz', '_frozen', '_runtime'):
        _mk(base, 'k1', 'pairA', 'store', 'store', reserved, 'anything')
    stale = prune.find_stale(base)
    assert not [p for p in stale if os.path.basename(p).startswith('_')]


def test_dry_run_deletes_nothing_and_apply_deletes(tmp_path):
    base = str(tmp_path)
    leaf = _leaf(base)
    doomed = os.path.join(leaf, 'compare')
    assert prune.main([base]) == 0
    assert os.path.isdir(doomed), 'dry run must not touch the disk'
    assert prune.main([base, '--apply']) == 0
    assert not os.path.exists(doomed)
    assert os.path.isdir(os.path.join(leaf, 'figures')), 'live output must survive'
    assert prune.find_stale(base) == []                # idempotent: a second pass finds none
