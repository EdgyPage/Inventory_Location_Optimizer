"""test_site_tree.py — the staged docs tree declaration stays tied to both its sides.

docs/experiments/site_tree.py declares the website's staged layout as templates; ingest.py
renders every destination from them.  docs/macros.py deliberately does NOT import the module
(it runs standalone inside mkdocs), so its f-string joins are pinned HERE: each macros literal
must equal the corresponding template character for character.  A template edit that moves a
site path therefore fails this file, the committed snapshots' paths, or both — never neither.

Run:  python -m pytest Tests/architecture/test_site_tree.py -q
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_site_tree():
    spec = importlib.util.spec_from_file_location(
        'docs_site_tree', os.path.join(_ROOT, 'docs', 'experiments', 'site_tree.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _macros_src() -> str:
    with open(os.path.join(_ROOT, 'docs', 'macros.py'), encoding='utf-8') as fh:
        return fh.read()


# ── the templates render today's exact literals ──────────────────────────────────

def test_templates_render_the_committed_snapshot_literals():
    st = _load_site_tree()
    exp = os.path.join('docs', 'experiments', 'experiment-6')
    assert st.path('config_json', exp, run='k1_off_lpt', inv='mix', cfg='calibrated') == \
        os.path.join(exp, 'images', 'k1_off_lpt', 'mix', 'calibrated', 'config.json')
    assert st.path('figure_png', exp, run='k1_off_lpt', inv='mix', cfg='calibrated',
                   figure='top_vs_baseline.png') == \
        os.path.join(exp, 'images', 'k1_off_lpt', 'mix', 'calibrated', 'top_vs_baseline.png')
    assert st.path('pair_params', exp, inv='mix') == \
        os.path.join(exp, 'data', 'mix', 'params.json')
    assert st.path('whatif_data', exp, fname='whatif_delta.json') == \
        os.path.join(exp, 'data', 'whatif_delta.json')
    assert st.path('whatif_delta', exp, fname='whatif_delta.png') == \
        os.path.join(exp, 'images', 'whatif_delta.png')
    assert st.path('catalogue_png', exp, catalogue='catalogue', plot='demand.png') == \
        os.path.join(exp, 'images', 'catalogue', 'demand.png')


def test_missing_part_fails_by_name():
    st = _load_site_tree()
    try:
        st.path('figure_png', run='r', inv='i', cfg='c')          # figure missing
    except KeyError as exc:
        assert 'figure' in str(exc)
    else:                                                          # pragma: no cover
        raise AssertionError('a missing required part must raise KeyError')


# ── macros.py stays character-identical to the declaration ───────────────────────

def test_macros_fstring_joins_equal_the_templates():
    """macros' joins use {fname} where the template names the part {figure}/{plot}; after that
    one declared aliasing the strings must be IDENTICAL.  If this fails, either macros moved a
    read path or site_tree moved a write path — reconcile them, never baseline the diff."""
    st = _load_site_tree()
    src = _macros_src()
    assert st.TEMPLATES['config_json'] == 'images/{run}/{inv}/{cfg}/config.json'
    assert 'images/{run}/{inv}/{cfg}/config.json' in src
    assert st.TEMPLATES['figure_png'] == 'images/{run}/{inv}/{cfg}/{figure}'
    assert 'images/{run}/{inv}/{cfg}/{fname}' in src
    assert st.TEMPLATES['pair_params'] == 'data/{inv}/params.json'
    assert 'data/{inv}/params.json' in src
    assert st.TEMPLATES['whatif_data'] == 'data/{fname}'
    assert 'data/whatif_delta.json' in src                        # the concrete whatif read


# ── ingest routes every destination through the declaration ──────────────────────

def test_ingest_renders_destinations_from_site_tree_only():
    """No hand-joined destination may reappear in ingest: every `exp_dir`-rooted copy target
    goes through site_tree.path.  The raw-join tokens that built destinations before this
    contract existed must stay gone from the staging code."""
    with open(os.path.join(_ROOT, 'docs', 'experiments', 'ingest.py'), encoding='utf-8') as fh:
        src = fh.read()
    assert 'import site_tree' in src
    for token in ('os.path.join(exp_dir, "images"', "os.path.join(exp_dir, 'images'",
                  'os.path.join(exp_dir, "data"', "os.path.join(exp_dir, 'data'"):
        assert token not in src, f'hand-joined destination resurfaced in ingest.py: {token}'
