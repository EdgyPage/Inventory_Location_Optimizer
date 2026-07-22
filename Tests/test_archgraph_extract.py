"""test_archgraph_extract.py

Locks the architecture call/import extractor (context/arch/extract.py):
  - the graph builds and is NON-EMPTY (never a vacuous pass),
  - the emit is DETERMINISTIC — building twice is byte-identical (no set/dict-order leak),
  - the committed context/arch/graph.json is UP-TO-DATE with the code (== a fresh build),
  - a few backbone edges the rest of the system relies on are actually present.

Run:  python -m pytest Tests/test_archgraph_extract.py -q
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytest.importorskip('yaml', reason='resolver hints (dispatch edges) need pyyaml (requirements-docs.txt)')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_extract():
    path = os.path.join(_ROOT, 'context', 'arch', 'extract.py')
    spec = importlib.util.spec_from_file_location('arch_extract', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_graph_non_empty_and_deterministic():
    ex = _load_extract()
    g1 = ex.build_graph()
    g2 = ex.build_graph()
    # non-vacuity: a real repo has hundreds of nodes/edges
    assert len(g1['nodes']) > 500, 'graph implausibly small — extractor likely broke'
    assert len(g1['edges']) > 500
    assert ex.dumps(g1) == ex.dumps(g2), 'extractor output is nondeterministic'
    assert g1.get('_hint_problems') is None, f"unresolved resolver hints: {g1.get('_hint_problems')}"


def test_committed_graph_is_current():
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'arch', 'graph.json'), encoding='utf-8') as fh:
        committed = fh.read()
    assert committed == ex.dumps(ex.build_graph()), \
        'graph.json stale — run: python context/arch/extract.py --write'


def test_key_backbone_edges_present():
    ex = _load_extract()
    edges = {(e['src'], e['dst'], e['kind']) for e in ex.build_graph()['edges']}

    def has(src_suffix, dst_suffix, kind):
        return any(s.endswith(src_suffix) and d.endswith(dst_suffix) and k == kind
                   for (s, d, k) in edges)

    # the ProcessPool spawn edge (function passed as a value) — resolved as `ref`.
    # The submit lives in _run_pool (the crash-recovery supervisor's inner pool lifetime),
    # now in the extracted Optimization/simdriver/supervisor.py.
    assert has('simdriver/supervisor.py::_run_pool',
               'strategy_runner.py::_run_strategy_worker', 'ref')
    # a mixin method call resolved via the Inventory_Manager MRO / class-qualified call
    assert has('sim_assets.py::build_shared_assets',
               'inventory_planning.py::PlanningMixin.plan_warehouse', 'calls')
    # the placement-closure dispatch supplied by resolver_hints.yml — the closures are
    # nested `def place_one` in the build_* factories, so the qualname ends in `.place_one`
    assert any(k == 'dispatch' and 'Assignment_Functions.py::' in d
               and d.split('::')[-1].endswith('place_one')
               for (s, d, k) in edges)
    # registry membership: RELOADERS references its reloader factories
    assert has('Capacity_Reloader.py::RELOADERS', 'Capacity_Reloader.py::rebalance_reloader', 'ref')
