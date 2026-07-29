"""test_architecture_sync.py

Locks context/architecture.yml to the code via context/arch/verify_architecture.py:
every backbone edge (intent) must exist in the derived graph (SCOPE), no import may
cross a forbidden layer pair (BOUNDARY), the committed graph.json must be up to date, and
every {name,file,kind} anchor must exist.  Drift fails the ordinary suite.

Includes POSITIVE CONTROLS — synthetic-input tests proving the SCOPE and BOUNDARY checks
actually report violations, so a green run can never be a vacuous pass.

Run:  python -m pytest Tests/test_architecture_sync.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

pytest.importorskip('yaml', reason='architecture verification needs pyyaml (requirements-docs.txt)')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_verifier():
    path = os.path.join(_ROOT, 'context', 'arch', 'verify_architecture.py')
    spec = importlib.util.spec_from_file_location('verify_architecture', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_architecture_matches_code():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, 'context', 'arch', 'verify_architecture.py'), '--quiet'],
        capture_output=True, text=True, cwd=_ROOT,
    )
    assert r.returncode == 0, 'architecture drift:\n' + r.stdout + r.stderr


# --- positive controls: prove the checks have teeth --------------------------------------

def test_scope_detects_missing_edge():
    va = _load_verifier()
    nodes = [
        {'id': 'a.py::f', 'name': 'f', 'file': 'a.py', 'kind': 'function'},
        {'id': 'b.py::g', 'name': 'g', 'file': 'b.py', 'kind': 'function'},
    ]
    edges = [{'src': 'a.py::f', 'dst': 'b.py::g', 'kind': 'calls'}]
    # a real edge is accepted...
    assert va.scope_violations([{'src': {'name': 'f', 'file': 'a.py'},
                                 'dst': {'name': 'g', 'file': 'b.py'}, 'via': 'calls'}],
                               nodes, edges) == []
    # ...a fabricated edge is REPORTED missing
    bogus = va.scope_violations([{'src': {'name': 'f', 'file': 'a.py'},
                                  'dst': {'name': 'nope', 'file': 'b.py'}}], nodes, edges)
    assert bogus and 'SCOPE' in bogus[0]
    # ...and a wrong `via` (real edge, wrong kind) is also reported
    wrong_via = va.scope_violations([{'src': {'name': 'f', 'file': 'a.py'},
                                      'dst': {'name': 'g', 'file': 'b.py'}, 'via': 'dispatch'}],
                                    nodes, edges)
    assert wrong_via and 'via=dispatch' in wrong_via[0]


def test_boundary_detects_forbidden_edge():
    va = _load_verifier()
    layers = [
        {'name': 'core', 'match': 'Warehouse/'},
        {'name': 'harness', 'match': 'Optimization/'},
    ]
    nodes = [
        {'id': 'Warehouse/x.py', 'name': 'Warehouse.x', 'file': 'Warehouse/x.py', 'kind': 'module'},
        {'id': 'Optimization/y.py', 'name': 'Optimization.y', 'file': 'Optimization/y.py', 'kind': 'module'},
    ]
    # inject a synthetic forbidden import core -> harness
    edges = [{'src': 'Warehouse/x.py', 'dst': 'Optimization/y.py', 'kind': 'imports'}]
    viol = va.boundary_violations(edges, nodes, layers, [('core', 'harness')])
    assert viol and 'BOUNDARY' in viol[0] and 'core -> harness' in viol[0]
    # the reverse direction is allowed by that rule
    ok = va.boundary_violations(edges, nodes, layers, [('harness', 'core')])
    assert ok == []


def test_wildcard_boundary_and_layer_mapping():
    va = _load_verifier()
    layers = [{'name': 'leaf', 'members': ['Warehouse/physical.py']},
              {'name': 'core', 'match': 'Warehouse/', 'except': ['Warehouse/physical.py']}]
    # members override longest-match; except carves the leaf out of core
    assert va._layer_of('Warehouse/physical.py', layers) == 'leaf'
    assert va._layer_of('Warehouse/catalog/Order.py', layers) == 'core'
    # leaf -> * wildcard fires for any out-edge from the leaf
    nodes = [{'id': 'Warehouse/physical.py', 'name': 'p', 'file': 'Warehouse/physical.py', 'kind': 'module'},
             {'id': 'Warehouse/catalog/Order.py', 'name': 'o', 'file': 'Warehouse/catalog/Order.py', 'kind': 'module'}]
    edges = [{'src': 'Warehouse/physical.py', 'dst': 'Warehouse/catalog/Order.py', 'kind': 'imports'}]
    assert va.boundary_violations(edges, nodes, layers, [('leaf', '*')])
