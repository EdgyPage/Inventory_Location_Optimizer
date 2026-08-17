"""test_viewer_capability_strings.py — JS capability tokens are wire values; tie them down.

A capability name is what /api/capabilities publishes and what a view's `requires` list is
matched against — a one-character drift silently hides a tab instead of raising.  The Python
side is unified (protocol.py re-exports `Picking_Data.SIM_CAPABILITIES`); the strings
hardcoded in the FRONT END were the surviving copy of the hazard, with no gate.  Two gates:

  1. every token in a view's `requires: [...]` array must be one the reader can actually
     PUBLISH (`ALL_CAPABILITIES`) — a token outside it gates the tab off unconditionally;
  2. every occurrence of a REGISTERED capability name as a JS string literal must appear in
     the pinned site map below — so moving, adding, or removing a genuine capability usage
     forces this map (and therefore a registration check) to move with it.  A pure typo is
     undecidable by any scan; gate 1 catches it where it bites (the requires path).

Run:  python -m pytest Tests/architecture/test_viewer_capability_strings.py -q
"""
from __future__ import annotations

import glob
import os
import re

from Optimization.persistence.Picking_Data import SIM_CAPABILITIES
from Visualization.readers.protocol import ALL_CAPABILITIES

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_STATIC = os.path.join(_ROOT, 'Visualization', 'static')

#: Every place a REGISTERED capability name appears as a string literal in the front end.
#: file (repo-relative, /) -> expected token set.  Update this map WITH the JS edit; the
#: subset assertions below make a stale or typo'd entry fail, not drift.
_PINNED_SITES = {
    'Visualization/static/views/converge.js': {'keyframes'},          # requires-gate
    'Visualization/static/main.js': {'aisle_metrics', 'bin_log'},     # hidden-panel notice,
                                                                      # exactness switch
}

#: The fetch layer's string literals are /api/<verb> endpoint names, which legitimately
#: collide with capability names (e.g. the 'sku_scores' VERB vs the 'sku_scores' capability).
#: Endpoint-name drift is caught by the server itself (404), not by this gate.
_EXCLUDED = {'Visualization/static/core/api.js'}

_REQUIRES = re.compile(r"requires\s*:\s*\[([^\]]*)\]")
_STR = re.compile(r"['\"]([a-z_]+)['\"]")


def _js_files():
    return [p for p in glob.glob(os.path.join(_STATIC, '**', '*.js'), recursive=True)
            if _rel(p) not in _EXCLUDED]


def _rel(path):
    return os.path.relpath(path, _ROOT).replace(os.sep, '/')


def test_requires_tokens_are_publishable():
    publishable = set(ALL_CAPABILITIES)
    found_any = False
    for path in _js_files():
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        for arr in _REQUIRES.findall(src):
            for tok in _STR.findall(arr):
                found_any = True
                assert tok in publishable, (
                    f'{_rel(path)} requires {tok!r}, which the reader never publishes — '
                    f'the tab would be hidden unconditionally')
    assert found_any, 'no requires arrays found — the scan pattern rotted'


def test_registered_capability_literals_match_the_pinned_sites():
    registered = set(SIM_CAPABILITIES)
    actual: dict = {}
    for path in _js_files():
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        toks = {t for t in _STR.findall(src) if t in registered}
        if toks:
            actual[_rel(path)] = toks
    assert actual == _PINNED_SITES, (
        f'capability literals moved without the pin moving.\n  found: {actual}\n'
        f'  pinned: {_PINNED_SITES}\nUpdate _PINNED_SITES with the JS edit — that is the '
        f'registration check.')


def test_the_pin_itself_is_registered():
    for rel, toks in _PINNED_SITES.items():
        assert toks <= set(SIM_CAPABILITIES), f'{rel}: pinned unregistered token(s)'
        assert os.path.isfile(os.path.join(_ROOT, *rel.split('/'))), rel
