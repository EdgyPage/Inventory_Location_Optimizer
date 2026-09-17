"""test_frame_specs.py — the frame table, and the cache that used to be nine dicts.

`core/requests.py` had nine near-identical bodies, each reaching into a DIFFERENT private
cache dict on `EvalContext` by name from another module.  Adding a kind was six edits across
four files, and forgetting the cache dict raised `AttributeError` instead of denying — which
defeats the broker's one contract, that a missing resource never raises.  `SiteContext`
initialised three of the nine, and its own docstring records that every yard evaluation
raised on the first real coupled run while the `[access]` summary reported a grant.

What is pinned here:

    1. Every declared kind is reachable through the generic accessor, and memoises.
    2. `SiteContext` declares the SAME cache as the base — the six-missing-dicts class is
       gone by construction, not by remembering.
    3. `FRAME_TABLE` and `PAIRED_KINDS` are DERIVED from one record, so they cannot drift
       apart the way the two hand-kept structures could.
    4. Every paired kind declares a source table — the join the old split could break.

    python -m pytest Tests/unit/test_frame_specs.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.Performance_Evaluations.core import requests as R
from Optimization.Performance_Evaluations.core import context as C
from Optimization.Performance_Evaluations.core import quantities as Q


# ── the table ─────────────────────────────────────────────────────────────────

def test_every_kind_declares_a_loader_and_a_builder():
    assert R.FRAME_SPECS, 'no frame kinds declared at all'
    for kind, spec in R.FRAME_SPECS.items():
        assert callable(spec.loader), f'{kind}: loader is not callable'
        assert callable(spec.builder), f'{kind}: builder is not callable'
        assert spec.extra is None or callable(spec.extra), f'{kind}: extra is not callable'


def test_the_nine_named_accessors_cover_the_table_exactly():
    """A kind added to the table with no accessor is unreachable from `EvalContext`;
    an accessor with no table entry raises `KeyError` on first use."""
    named = {n[:-len('_frame')] for n in dir(R)
             if n.endswith('_frame') and not n.startswith('_')
             and n not in ('site_batch_frame',)}
    assert named == set(R.FRAME_SPECS), (
        f'accessors {sorted(named)} != table {sorted(R.FRAME_SPECS)}')


def test_every_kind_has_an_eval_context_method():
    for kind in R.FRAME_SPECS:
        method = 'free_index_df' if kind == 'free_index' else f'{kind}_df'
        assert hasattr(C.EvalContext, method), f'{kind}: EvalContext has no {method}'


# ── the cache ─────────────────────────────────────────────────────────────────

class _FakeCtx:
    """The minimum `frame()` touches: a key map and the one cache."""

    def __init__(self, rows):
        self._frames: dict = {}
        self._by_key = {'a': {'db_path': ':memory:', 'run_id': 1}}
        self._rows = rows


def test_frame_loads_once_and_memoises():
    calls = []

    def loader(db_path, run_id):
        calls.append((db_path, run_id))
        return ['row']

    spec = R._FrameSpec(loader, lambda rows: {'built': list(rows)})
    R.FRAME_SPECS['_probe'] = spec
    try:
        ctx = _FakeCtx(['row'])
        first = R.frame(ctx, '_probe', 'a')
        second = R.frame(ctx, '_probe', 'a')
        assert first == {'built': ['row']}
        assert first is second, 'the second call rebuilt instead of hitting the cache'
        assert calls == [(':memory:', 1)], f'loader ran {len(calls)} times, expected 1'
        assert ctx._frames['_probe']['a'] is first, 'cached under the wrong key'
    finally:
        del R.FRAME_SPECS['_probe']


def test_extra_receives_the_context_the_key_and_the_rows():
    seen = {}

    def extra(ctx, key, rows):
        seen.update(key=key, rows=rows)
        return ('EXTRA',)

    spec = R._FrameSpec(lambda d, r: ['row'], lambda rows, tag: (list(rows), tag), extra)
    R.FRAME_SPECS['_probe2'] = spec
    try:
        got = R.frame(_FakeCtx(['row']), '_probe2', 'a')
        assert got == (['row'], 'EXTRA')
        assert seen == {'key': 'a', 'rows': ['row']}, seen
    finally:
        del R.FRAME_SPECS['_probe2']


def test_site_context_declares_the_same_cache_as_the_base():
    """The whole point.  `SiteContext` used to declare three of nine caches, and the six it
    omitted were an `AttributeError` waiting for a coupled run."""
    base = inspect.getsource(C.EvalContext.__init__)
    site = inspect.getsource(C.SiteContext.__init__)
    assert 'self._frames' in base, 'EvalContext no longer declares the frame cache'
    assert 'self._frames' in site, 'SiteContext does not declare the frame cache'
    for stale in ('_bcache', '_tcache', '_ycache', '_dcache', '_mcache',
                  '_ccache', '_ficache', '_wcache', '_scache'):
        assert stale not in base, f'EvalContext still declares {stale}'
        assert stale not in site, f'SiteContext still declares {stale}'


def test_site_context_overrides_only_the_frame_that_differs():
    """`yard_df` and `drain_df` were overridden identically to the base.  Only `batch_df`
    is a real override — the site's batch record is both leaves' frames concatenated."""
    assert 'batch_df' in SiteOverrides(), 'the one real override went missing'
    for noop in ('yard_df', 'drain_df'):
        assert noop not in SiteOverrides(), f'{noop} is overridden identically to the base'


def SiteOverrides() -> set:
    return {n for n, v in vars(C.SiteContext).items() if callable(v)}


# ── the derived pair ──────────────────────────────────────────────────────────

def test_frame_table_and_paired_kinds_are_derived_from_one_record():
    assert set(Q.FRAME_TABLE) == set(Q.FRAME_KINDS)
    assert set(Q.PAIRED_KINDS) <= set(Q.FRAME_KINDS)
    for kind, spec in Q.FRAME_KINDS.items():
        assert Q.FRAME_TABLE[kind] == spec.table
        assert (kind in Q.PAIRED_KINDS) is spec.paired, (
            f'{kind}: paired={spec.paired} but PAIRED_KINDS membership is '
            f'{kind in Q.PAIRED_KINDS}')


def test_every_paired_kind_declares_a_source_table():
    """The join the old two-structure split could break: a kind handed to the significance
    suite with no table behind it is a quantity nothing can read."""
    for kind in Q.PAIRED_KINDS:
        assert Q.FRAME_TABLE.get(kind), f'{kind!r} is paired but declares no source table'
