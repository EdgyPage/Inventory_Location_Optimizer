"""test_view_coverage.py — the derived view set is BINDING, not advisory.

`core/quantities.derive_views(quantity, shape)` says which views a figure must carry.
That is only worth anything if something checks that the modules actually emit them, and
that nothing emits a view the derivation did not ask for.  Three checks, at three
different strengths:

  1. **Static.** Every `<view>_*.png` basename a module can write, found by reading the
     source, is inside its evaluation's derived set.  Cheap, runs everywhere, and catches
     the class that produced `delta_travel_vs_baseline.png` — a percent view saved under a
     delta's name because its family's allow-list had no percent in it.
  2. **Completeness, minus the named debt.** Every derived view is either written by some
     save site in the module or listed in `views_pending` with the reason it is not.
     `views_pending` is capped and is meant to reach zero; it is a DIFFERENT field from
     `views_suppressed`, because "not yet" and "never" are different claims and merging
     them turns a backlog into a design.
  3. **The registry is self-consistent.** No stale exception, every reason substantive,
     every mark known, `BESPOKE` resolves.

What is deliberately NOT here: a fixture render asserting the emitted FILE set equals the
derived one.  At four batches the fixture degenerates — arms come out bit-identical, a
percent view with a zero-variance baseline legitimately emits nothing — so the render
version of this test belongs against a real leaf, where it is `emitted == derived` rather
than the `emitted subset derived` a fixture can honestly support.

Run:  python -m pytest Tests/architecture/test_view_coverage.py -q
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.common import marks
from Optimization.Performance_Evaluations.core import families, quantities
from Optimization.Performance_Evaluations.core.registry import EVALUATIONS, EVAL_BY_KEY

_VIEW_PREFIX = re.compile(r'^(' + '|'.join(families.VIEWS) + r')_')


def _module_of(ev):
    """The module a figure evaluation's render function lives in."""
    return pathlib.Path(ev.render.__code__.co_filename)


def _saved_views(path: pathlib.Path) -> set:
    """Every view an f-string or literal `<view>_*.png` in this module could produce.

    Reads the source rather than rendering, so it runs in CI with no run tree. An f-string
    whose prefix is a variable (`f'{view}_travel_per_arm.png'`) contributes the sentinel
    `'*'`, meaning "this module writes whatever view it is asked for" — which is the
    correct answer for a module that iterates its derived set, and is what the caller
    below treats as full coverage.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = _VIEW_PREFIX.match(node.value)
            if m and node.value.endswith('.png'):
                out.add(m.group(1))
        elif isinstance(node, ast.JoinedStr):
            parts = node.values
            if (parts and isinstance(parts[0], ast.FormattedValue)
                    and any(isinstance(p, ast.Constant)
                            and str(p.value).endswith('.png') for p in parts)):
                out.add('*')
            else:
                literal = ''.join(p.value for p in parts
                                  if isinstance(p, ast.Constant))
                m = _VIEW_PREFIX.match(literal)
                if m and literal.rstrip('_').endswith(('.png', 'png')) or (
                        m and '.png' in literal):
                    out.add(m.group(1))
    return out


def _figure_evals():
    return [ev for ev in EVALUATIONS if ev.family]


# ── 1. nothing emits a view its derivation did not ask for ───────────────────────

def test_no_module_writes_a_view_outside_its_derived_set():
    """The `delta_travel_vs_baseline.png` class: a stance under the wrong name.

    Note this catches only what a module CAN write. The save-time check in
    `families.check_view` catches the rest, and it runs on every real render.
    """
    bad = []
    for ev in _figure_evals():
        emitted = _saved_views(_module_of(ev)) - {'*'}
        for view in sorted(emitted - set(ev.views)):
            bad.append(f'{ev.key} writes {view}_*.png but its derived views are '
                       f'{tuple(ev.views)}')
    assert not bad, '\n'.join(bad)


# ── 2. every derived view is emitted, or is named debt ───────────────────────────

def test_every_derived_view_is_emitted_or_recorded_as_pending():
    gaps = []
    for ev in _figure_evals():
        emitted = _saved_views(_module_of(ev))
        if '*' in emitted:
            continue                     # iterates its derived set; nothing to check
        # a shared painter writes the file for the trajectory evals
        if ev.key in ('trajectories.overtime', 'agg.traj'):
            continue
        for view in sorted(set(ev.views) - emitted):
            gaps.append(f'{ev.key}: the derivation requires a {view!r} view and no save '
                        f'site writes one. Implement it, or record it in views_pending '
                        f'with the reason.')
    assert not gaps, '\n'.join(gaps)


def test_the_pending_ledger_is_capped_and_every_entry_says_why():
    total = sum(len(ev.views_pending) for ev in _figure_evals())
    assert total <= PENDING_CEILING, (
        f'{total} pending views against a ceiling of {PENDING_CEILING}. This ledger is '
        f'meant to shrink; raising the ceiling is a decision to make on purpose.')
    for ev in _figure_evals():
        for view, reason in ev.views_pending:
            assert view in families.VIEWS, f'{ev.key}: {view!r} is not a view'
            assert len(reason.split()) >= 10, \
                f'{ev.key} defers {view!r} with a reason too short to be one'


#: The backlog the derivation exposed on the day it landed, and the number it must not
#: exceed.  Each one is a view some figure SHOULD carry and does not yet — recorded so the
#: gap is a line in a diff rather than something a reader notices on the published page.
PENDING_CEILING = 7


def test_the_pending_ledger_matches_what_is_actually_missing():
    """A pending entry for a view that IS emitted is a stale exception."""
    stale = []
    for ev in _figure_evals():
        emitted = _saved_views(_module_of(ev))
        if '*' in emitted:
            stale += [f'{ev.key}:{v}' for v, _r in ev.views_pending]
            continue
        stale += [f'{ev.key}:{v}' for v, _r in ev.views_pending if v in emitted]
    assert not stale, f'pending, but already written: {stale}'


# ── 3. the registry is self-consistent ───────────────────────────────────────────

def test_every_figure_evaluation_declares_a_known_mark():
    for ev in _figure_evals():
        assert ev.shape, f'{ev.key} declares no mark'
        for name in ev.shape:
            assert name in quantities.SHAPE_BY_NAME, f'{ev.key}: unknown mark {name!r}'


def test_a_comparison_mark_declares_the_quantities_it_draws():
    for ev in _figure_evals():
        if all(quantities.SHAPE_BY_NAME[n].self_describing for n in ev.shape):
            continue
        assert ev.quantities, f'{ev.key} draws a comparison mark but names no quantity'
        for q in ev.quantities:
            assert q in quantities.BY_KEY, f'{ev.key}: no quantity declares {q!r}'


def test_the_derived_views_are_reproducible_from_the_declaration():
    """The registry stores the answer; recomputing it must give the same one."""
    for ev in _figure_evals():
        base: set = set()
        for name in ev.shape:
            shp = quantities.SHAPE_BY_NAME[name]
            if shp.self_describing:
                base |= shp.fixed
            else:
                for q in ev.quantities:
                    base |= quantities.derive_views(quantities.BY_KEY[q], shp)
        withheld = {v for v, _r in ev.views_suppressed} | {v for v, _r in ev.views_pending}
        assert set(ev.views) == base - withheld, ev.key


def test_no_evaluation_still_hand_writes_its_views():
    assert families.LEGACY_VIEWS == {}, \
        f'still on the retired declaration: {sorted(families.LEGACY_VIEWS)}'


# ── the bespoke ledger ───────────────────────────────────────────────────────────

def test_every_bespoke_key_resolves_to_a_real_evaluation_and_view():
    for key, reason in marks.BESPOKE.items():
        eval_key, _, view = key.partition(':')
        assert eval_key in EVAL_BY_KEY, f'BESPOKE names unknown evaluation {eval_key!r}'
        assert view in families.VIEWS, f'BESPOKE names unknown view {view!r}'
        assert view in EVAL_BY_KEY[eval_key].views, \
            f'BESPOKE[{key}] excuses a view {eval_key} does not carry — stale'
        assert len(reason.split()) >= 8, f'BESPOKE[{key}]: the reason is too short'


def test_the_bespoke_ledger_is_capped():
    assert len(marks.BESPOKE) <= marks.BESPOKE_CEILING, (
        f'{len(marks.BESPOKE)} bespoke figures against a ceiling of '
        f'{marks.BESPOKE_CEILING}; "bespoke" must stay a decision, not a habit')


# ── non-vacuity ──────────────────────────────────────────────────────────────────

def test_the_scan_actually_finds_figure_evaluations_and_their_saves():
    """Every guard above passes trivially over an empty list."""
    evs = _figure_evals()
    assert len(evs) >= 18, f'only {len(evs)} figure evaluations found'
    with_saves = [ev for ev in evs if _saved_views(_module_of(ev))]
    assert len(with_saves) >= 12, \
        f'the source scan found save sites in only {len(with_saves)} modules'
