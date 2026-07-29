"""test_archgraph_nodes.py

Locks context/arch/nodes.json (per-node signatures + docstrings, the detail payload the
HTML pages render) to the code:
  - it is NON-EMPTY and DETERMINISTIC (building twice is byte-identical),
  - its keys are EXACTLY the graph node ids (so the HTML generator can left-join it),
  - the committed nodes.json is UP-TO-DATE (== a fresh build),
  - it carries real signature/docstring/value payloads (not vacuous).

nodes.json is a SIBLING of graph.json — this must never perturb graph.json's byte contract.

Run:  python -m pytest Tests/test_archgraph_nodes.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_extract():
    path = os.path.join(_ROOT, 'context', 'arch', 'extract.py')
    spec = importlib.util.spec_from_file_location('arch_extract', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nodes_detail_non_empty_and_deterministic():
    ex = _load_extract()
    d1 = ex.build_nodes_detail()
    d2 = ex.build_nodes_detail()
    assert len(d1['nodes']) > 1000, 'nodes.json implausibly small'
    assert ex.dumps_nodes(d1) == ex.dumps_nodes(d2), 'nodes detail is nondeterministic'


def test_keys_equal_graph_node_ids():
    ex = _load_extract()
    detail_ids = set(ex.build_nodes_detail()['nodes'])
    graph_ids = {n['id'] for n in ex.build_graph()['nodes']}
    assert detail_ids == graph_ids, (
        f'detail/graph id mismatch — missing: {sorted(graph_ids - detail_ids)[:5]} ; '
        f'extra: {sorted(detail_ids - graph_ids)[:5]}')


def test_committed_nodes_is_current():
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'arch', 'nodes.json'), encoding='utf-8') as fh:
        committed = fh.read()
    assert committed == ex.dumps_nodes(ex.build_nodes_detail()), \
        'nodes.json stale — run: python context/arch/extract.py --write-nodes'


def test_check_nodes_subprocess_clean():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, 'context', 'arch', 'extract.py'), '--check-nodes', '--quiet'],
        capture_output=True, text=True, cwd=_ROOT,
    )
    assert r.returncode == 0, 'nodes.json drift:\n' + r.stdout + r.stderr


def test_detail_payloads_are_real():
    ex = _load_extract()
    d = ex.build_nodes_detail()['nodes']
    # a known function: signature + docstring present
    fn = d['Optimization/simdriver/sim_assets.py::build_shared_assets']
    assert fn['kind'] == 'function'
    assert fn['signature'].startswith('(') and 'inventory_db' in fn['signature']
    assert fn['doc'] and len(fn['doc']) > 10
    # a module carries its docstring
    mod = d['Optimization/simdriver/strategy_runner.py']
    assert mod['kind'] == 'module'
    # a const carries a value preview
    const = d['Warehouse/placement/Capacity_Reloader.py::RELOADERS']
    assert const['kind'] == 'const' and const['value_preview']


def test_nodes_json_does_not_perturb_graph_json():
    # sanity: graph.json byte contract is independent of the nodes pass
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'arch', 'graph.json'), encoding='utf-8') as fh:
        committed_graph = fh.read()
    assert committed_graph == ex.dumps(ex.build_graph())
