"""The closed-form visualiser (`Optimization/Performance_Evaluations/closed_form/`): its layout is
right, each renderer writes a non-empty file, and the generated docs page is not stale."""
from __future__ import annotations

import os

import pytest

pytest.importorskip('matplotlib')

from Optimization.Performance_Evaluations.closed_form import docs_page, render
from Optimization.simconfig.models import churn, levels


def test_layers_put_inputs_first_and_each_equation_past_what_it_reads():
    d = render.layers(levels.LEVELS)
    assert all(d[n] == 0 for n in levels.LEVELS.inputs)
    for e in levels.LEVELS.equations:
        assert d[e.name] == 1 + max(d[s] for s in e.inputs)
    assert d['S'] > d['Q'] > d['L'] > d['Eq']


def test_every_renderer_writes_a_file(tmp_path):
    paths = [
        render.model_graph(levels.LEVELS, str(tmp_path / 'g.png')),
        render.sweep_chart(churn.FRESH, 'lam', [0.001, 0.01, 0.1], {'H': 40.0, 'ell': 2.77},
                           ['phi', 'served'], str(tmp_path / 's.png')),
        render.predicted_vs_realised(
            [{'label': 'a', 'predicted': 1.0, 'realised': 1.2, 'lo': 0.9, 'hi': 1.5},
             {'label': 'b', 'predicted': -0.5, 'realised': None}],
            str(tmp_path / 'p.png'), title='t'),
    ]
    for p in paths:
        assert os.path.getsize(p) > 1000


def test_the_page_prints_each_models_own_equations(tmp_path):
    p = render.write_page([(churn.FRESH, None), (churn.GAP_CHAIN, None, 'gap')],
                          str(tmp_path / 'x.md'), title='T', intro='hello')
    text = open(p, encoding='utf-8').read()
    assert text.startswith('# T\n\nhello')
    for e in churn.FRESH.equations + churn.GAP_CHAIN.equations:
        assert e.latex() in text
    assert '## gap' in text


def test_the_committed_docs_page_is_fresh():
    """Edit a model and this fails until `docs_page --write` regenerates the page."""
    committed = open(docs_page.PAGE, encoding='utf-8').read()
    assert committed == docs_page.render()
