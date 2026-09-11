"""test_evaluation_scope.py — an evaluation's scope is a DECLARED value, refused when unknown.

Two seams, one silence (site-dock 11):

  * `registry.evaluation(scope=...)` documented `per_strategy | config | aggregate | run` in a
    COMMENT and checked no value, so a typo registered fine;
  * `requests.resolve_needs` read `ev.scope if ev.scope in ('aggregate', 'run') else 'config'`,
    a FALLBACK rather than a map — so that typo was then silently namespaced as `config`,
    resolved against the leaf's requests and given no output directory of its own.

Validating the string at declaration is worthless while a consumer quietly reinterprets it,
so both are pinned here.  `site` is the fifth scope (site-dock 03); nothing declares it yet,
which is exactly why the mapping has to be total before something does.

Run:  python -m pytest Tests/unit/test_evaluation_scope.py -q
"""
from __future__ import annotations

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.core import requests
from Optimization.Performance_Evaluations.core.registry import (
    EVALUATIONS, SCOPES, evaluation)


def test_every_registered_evaluation_declares_a_known_scope():
    assert EVALUATIONS, 'registry did not populate — the rest of this file proves nothing'
    unknown = sorted({ev.key: ev.scope for ev in EVALUATIONS if ev.scope not in SCOPES}.items())
    assert unknown == []


def test_an_unknown_scope_raises_at_decoration_time():
    """Not at render time, and not as a wrong tree: the decorator is where the string is a
    literal a reviewer can see, and a ValueError there costs one import."""
    try:
        @evaluation(key='test.bogus_scope', label='x', scope='per-strategy', out_subdir='')
        def _render(ctx, params):
            raise AssertionError('never called')
    except ValueError as exc:
        assert 'unknown scope' in str(exc) and 'per-strategy' in str(exc)
    else:
        raise AssertionError('a bogus scope registered without complaint')


def test_the_request_namespace_map_is_total_over_the_scopes():
    """A scope with no namespace is the fallback bug restated: `.get(...)` would return None
    and the old expression returned 'config'."""
    assert set(requests.REQUEST_SCOPE) == set(SCOPES)


def test_the_two_leaf_scopes_share_one_namespace_and_the_rest_do_not():
    """`per_strategy` and `config` are two evaluation scopes over the SAME context kind, which
    is why the fallback looked harmless; every other scope is its own namespace."""
    m = requests.REQUEST_SCOPE
    assert m['per_strategy'] == m['config'] == 'config'
    assert m['aggregate'] == 'aggregate' and m['run'] == 'run' and m['site'] == 'site'


def test_resolve_needs_refuses_a_scope_with_no_namespace():
    """The belt to the decorator's braces — a hand-built Evaluation, or a scope added to
    SCOPES and forgotten in the map, must not resolve against the leaf's requests."""
    class _Ev:
        key, scope, needs, quantities = 'test.hand_built', 'nowhere', (), ()

    class _Ctx:
        pass

    try:
        requests.resolve_needs(_Ctx(), _Ev())
    except ValueError as exc:
        assert 'no request namespace' in str(exc)
    else:
        raise AssertionError("resolve_needs served an evaluation whose scope it does not know")
