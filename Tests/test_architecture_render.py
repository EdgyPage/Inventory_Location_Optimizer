"""test_architecture_render.py

Smoke-tests the architecture renderer (context/arch/render.py): the Mermaid call graph,
the inefficiency report, and the file map all produce non-empty, well-formed Markdown, and
rendering is deterministic.  Does not write files (calls the render_* functions directly).

Run:  python -m pytest Tests/test_architecture_render.py -q
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytest.importorskip('yaml', reason='renderer needs pyyaml (requirements-docs.txt)')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_render():
    path = os.path.join(_ROOT, 'context', 'arch', 'render.py')
    spec = importlib.util.spec_from_file_location('arch_render', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_graph_md_has_mermaid_and_backbone():
    r = _load_render()
    graph, arch, _ = r._load()
    md = r.render_graph_md(graph, arch)
    assert '```mermaid' in md and 'graph LR' in md
    assert 'Layer import overview' in md and 'Backbone call structure' in md
    # a real backbone node is rendered
    assert 'build_shared_assets' in md
    assert md == r.render_graph_md(graph, arch)      # deterministic


def test_inefficiency_md_has_sections():
    r = _load_render()
    graph, arch, _ = r._load()
    md = r.render_inefficiency_md(graph, arch)
    for section in ('Module import cycles', 'fan-out', 'fan-in',
                    'Cross-layer call coupling', 'Orphan public functions'):
        assert section in md, f'missing section: {section}'
    # the busiest orchestrator should top fan-out
    assert '_run_strategy_worker' in md
    assert md == r.render_inefficiency_md(graph, arch)


def test_filemap_md_groups_by_layer():
    r = _load_render()
    _, arch, files = r._load()
    if not files:
        pytest.skip('context/files.yml not present')
    md = r.render_filemap_md(arch, files)
    assert '## warehouse_core' in md and '## tests' in md
    assert 'Optimization/strategy_runner.py' in md
    assert md == r.render_filemap_md(arch, files)


def test_committed_render_outputs_exist():
    # the maintainer commits these; ensure they were generated and are non-trivial
    for rel in ('context/arch/GRAPH.md', 'context/arch/INEFFICIENCY.md', 'context/FILEMAP.md'):
        p = os.path.join(_ROOT, rel)
        assert os.path.isfile(p), f'missing generated file: {rel} (run python context/arch/render.py)'
        assert len(open(p, encoding='utf-8').read()) > 200
