"""test_rule_catalog.py — a published objective must belong to the code it names.

`Optimization/config/objectives.py` replaced three independently drifting transcriptions
of each rule's objective (the closure, `docs/macros.py::assignment_formulas`, and each
experiment's hand-written `formula-reference.md`) with one registry that is emitted into
every run.  Consolidating them only helps if the surviving copy cannot drift from the
code, and the LaTeX itself is unverifiable by construction — no inspection recovers an
objective from a closure.

So this file verifies everything AROUND the LaTeX:

  1. the registry covers the strategy grid exactly — no orphan, no gap, no stale key;
  2. every named symbol exists in the module the entry names;
  3. every builder ACTUALLY CALLS the symbol its entry claims (the tie that catches a
     re-pointed builder, which is how a formula becomes a lie without anyone editing it);
  4. every builder in the grid is reachable from exactly one entry;
  5. the field vocabulary is closed, and the bracket controls are exactly the four the
     experiment pages call "designed to lose";
  6. the anchors the generated sections will emit still cover every fragment the docs
     link to — `docref_guard` checks `<doc>.md section N` refs, not `#fragment`s, so
     nothing else would notice a renamed heading.

Run:  python -m pytest Tests/architecture/test_rule_catalog.py -q
"""
from __future__ import annotations

import importlib
import inspect
import os
import re

import pytest

from Optimization.config import objectives as obj
from Optimization.config import strategies as strat

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: the four deliberate worst-case controls, named on the experiment pages as
#: "designed to lose" — a fifth appearing here silently would change that sentence
_CONTROLS = {'tmax', 'cmin', 'expn', 'rank_maxlabor'}


def _grid_keys() -> set:
    return {rk for rk, *_ in strat._RESTOCKS}


def _builder_fn(entry):
    """The `_build_*` callable an entry's `builder` field names."""
    name, _, path = entry.builder.partition('@')
    assert path == 'Optimization/config/strategies.py', \
        f'{entry.rule}: builders live in the strategy grid, got {path!r}'
    fn = getattr(strat, name, None)
    assert fn is not None, f'{entry.rule}: no such builder {name!r}'
    return fn


# ── 1. the registry covers the grid exactly ─────────────────────────────────────

def test_every_restock_rule_has_exactly_one_objective():
    grid, cat = _grid_keys(), set(obj.OBJECTIVES)
    assert cat == grid, (f'registry drifted from the strategy grid — '
                         f'missing {sorted(grid - cat)}, stale {sorted(cat - grid)}')
    assert len(obj._ENTRIES) == len(cat), 'a rule is declared twice'


def test_labels_match_the_grid():
    """The label is what a page prints; a mismatch renames a rule mid-report."""
    for rk, label, *_ in strat._RESTOCKS:
        assert obj.OBJECTIVES[rk].label == label, f'{rk}: label drifted from the grid'


def test_objective_for_resolves_a_strategy_and_a_bare_key():
    s = strat.STRATEGY_BY_KEY['opt_rank_cartlabor_norsl']
    assert obj.objective_for(s).rule == 'rank_cartlabor'
    assert obj.objective_for('map_rank').label == 'Map_rank'


# ── 2-3. the symbol exists, and the builder really calls it ─────────────────────

def test_every_named_symbol_exists_in_the_module_it_names():
    for e in obj._ENTRIES:
        assert os.path.isfile(os.path.join(_ROOT, e.module)), \
            f'{e.rule}: no such file {e.module}'
        mod = importlib.import_module(e.module[:-3].replace('/', '.'))
        assert hasattr(mod, e.symbol), f'{e.rule}: {e.module} has no {e.symbol!r}'


def test_every_builder_calls_the_symbol_its_entry_claims():
    """The load-bearing tie: re-point a builder and the published formula goes stale."""
    for e in obj._ENTRIES:
        src = inspect.getsource(_builder_fn(e))
        assert e.symbol in src, (f'{e.rule}: {e.builder} does not mention {e.symbol!r} — '
                                 f'either the builder moved or the catalog is now wrong')


def test_the_map_family_names_its_precompute_and_nobody_else_does():
    for e in obj._ENTRIES:
        src = inspect.getsource(_builder_fn(e))
        calls_map = 'build_optimal_map' in src
        assert bool(e.precompute) == calls_map, (
            f'{e.rule}: precompute field says {e.precompute!r} but its builder '
            f'{"does" if calls_map else "does not"} call build_optimal_map')
        if e.precompute:
            name, _, path = e.precompute.partition('@')
            assert os.path.isfile(os.path.join(_ROOT, path))
            assert e.stage == 'precomputed_map'


# ── 4. no builder is left undocumented ──────────────────────────────────────────

def test_every_grid_builder_is_reachable_from_exactly_one_entry():
    from collections import Counter
    declared = Counter(e.builder.partition('@')[0] for e in obj._ENTRIES)
    grid = {fn.__name__ for _rk, _lbl, fn, *_ in strat._RESTOCKS}
    assert set(declared) == grid, (f'builder coverage drifted — '
                                   f'missing {sorted(grid - set(declared))}, '
                                   f'stale {sorted(set(declared) - grid)}')
    dupes = {k: n for k, n in declared.items() if n > 1}
    # map/map_rank and comp/expn are distinct builders, so nothing may repeat
    assert not dupes, f'one builder claimed by several rules: {dupes}'


# ── 5. the field vocabulary is closed ───────────────────────────────────────────

def test_field_vocabulary_and_the_designed_to_lose_set():
    for e in obj._ENTRIES:
        assert e.sense in ('min', 'max', 'none'), f'{e.rule}: sense {e.sense!r}'
        assert e.stage in ('per_unit', 'ranked_wave', 'precomputed_map'), \
            f'{e.rule}: stage {e.stage!r}'
        assert e.family in obj.FAMILIES, f'{e.rule}: family {e.family!r}'
        assert e.notes.strip(), f'{e.rule}: every rule owes the reader a mechanism'
        # a control maximises something, or it is not a worst-case bracket
        if e.control:
            assert e.sense in ('max', 'min')
    assert {e.rule for e in obj._ENTRIES if e.control} == _CONTROLS


def test_latex_is_present_where_there_is_an_objective_and_balanced():
    for e in obj._ENTRIES:
        if e.sense == 'none':
            assert e.latex == '', f'{e.rule}: a rule with no objective states no formula'
            continue
        assert e.latex.strip(), f'{e.rule}: sense={e.sense} but no formula'
        assert e.latex.count('$') % 2 == 0, f'{e.rule}: unbalanced $ in the formula'
        assert e.latex.startswith('$') and e.latex.endswith('$'), \
            f'{e.rule}: the formula must be delimited, so a page can inline it'


def test_as_dicts_is_json_ready_and_ordered_like_the_declaration():
    rows = obj.as_dicts()
    assert [r['rule'] for r in rows] == [e.rule for e in obj._ENTRIES]
    for r in rows:
        assert r['anchor'] and r['family_label']
        assert all(isinstance(v, (str, bool)) for v in r.values()), \
            'the catalog must serialise without a custom encoder'


# ── 6. anchors the docs already link to ─────────────────────────────────────────

def test_generated_anchors_cover_every_rule_fragment_the_docs_link_to():
    """A generated heading that renames its anchor is a broken published link.

    mkdocs does not fail on a dead intra-page fragment and `docref_guard` checks a
    different reference form, so this test is the only thing standing between a rename
    and a silently broken site.
    """
    anchors = {e.anchor for e in obj._ENTRIES}
    ref = re.compile(r'formula-reference\.md#([a-z0-9-]+)')
    referenced, docs = set(), os.path.join(_ROOT, 'docs')
    for dirpath, _dirs, files in os.walk(docs):
        if os.path.basename(dirpath) == 'architecture':
            continue
        for fn in files:
            if not fn.endswith('.md'):
                continue
            with open(os.path.join(dirpath, fn), encoding='utf-8') as fh:
                referenced |= set(ref.findall(fh.read()))
    # Only the fragments that name a RULE are this registry's business; the page's own
    # section anchors (notation, pick-time, the statistics section) stay hand-written.
    rule_frags = {f for f in referenced if f in {e.rule.replace('_', '-')
                                                 for e in obj._ENTRIES}}
    assert rule_frags <= anchors, f'docs link to rule anchors we would not emit: ' \
                                  f'{sorted(rule_frags - anchors)}'
