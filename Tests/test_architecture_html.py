"""test_architecture_html.py

Locks the generated static HTML code-map suite (docs/architecture/**) to the verified
graph data. Proves, empirically:

  - CURRENCY  : a fresh in-memory build == the committed site_manifest.json.
  - INTEGRITY : the committed docs/architecture/** files hash to that same manifest.
                (currency ∘ integrity  ⇒  committed tree == fresh build, with one rebuild.)
  - COMPLETENESS : every graph node id has a nodes/<slug>.html; every files.yml key has a
                   files/<slug>.html; non-vacuity floor > 1000 node pages.
  - NO DEAD LINKS : every internal href/src in every generated page resolves to a file in
                    the build set.
  - DETERMINISM : building the whole suite twice is byte-identical.
  - SLUG 1:1 : slugs are unique case-insensitively (filesystem-safe).

Run:  python -m pytest Tests/test_architecture_html.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import posixpath
from html.parser import HTMLParser

import pytest

pytest.importorskip('yaml', reason='the generator loads yaml specs (requirements-docs.txt)')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SITE = os.path.join(_ROOT, 'docs', 'architecture')
_MANIFEST = os.path.join(_ROOT, 'context', 'arch', 'site_manifest.json')


def _load_rh():
    path = os.path.join(_ROOT, 'context', 'arch', 'render_html.py')
    spec = importlib.util.spec_from_file_location('render_html', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_once():
    rh = _load_rh()
    return rh, rh.render_all_files(rh._load_index())


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ('href', 'src') and v:
                self.links.append(v)


def _internal_target(link: str) -> str | None:
    """Return the file path a link points at (query/fragment stripped), or None if external
    / same-page / non-file."""
    if link.split(':', 1)[0].lower() in ('http', 'https', 'mailto', 'data', 'javascript'):
        return None
    path = link.split('#', 1)[0].split('?', 1)[0]
    return path or None


def test_currency_manifest_matches_fresh_build():
    rh, files = _build_once()
    fresh = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
    with open(_MANIFEST, encoding='utf-8') as fh:
        committed = json.load(fh)
    assert committed == fresh, 'site stale — run: python context/arch/render_html.py --build'


def test_integrity_committed_tree_matches_manifest():
    with open(_MANIFEST, encoding='utf-8') as fh:
        manifest = json.load(fh)
    on_disk = {}
    for dirpath, _dirs, names in os.walk(_SITE):
        for n in names:
            ap = os.path.join(dirpath, n)
            rel = os.path.relpath(ap, _SITE).replace(os.sep, '/')
            on_disk[rel] = hashlib.sha256(open(ap, 'rb').read()).hexdigest()
    assert set(on_disk) == set(manifest), (
        f'tree/manifest file set differs — extra on disk: {sorted(set(on_disk) - set(manifest))[:5]} ; '
        f'missing: {sorted(set(manifest) - set(on_disk))[:5]}')
    assert on_disk == manifest, 'a committed file does not match its manifest hash'


def test_completeness_every_node_and_file_has_a_page():
    rh, files = _build_once()
    ix = rh._load_index()
    node_pages = {k for k in files if k.startswith('nodes/')}
    assert len(node_pages) == len(ix.by_id), 'node page count != graph node count'
    assert len(node_pages) > 1000, 'implausibly few node pages'
    for nid in ix.by_id:
        assert f'nodes/{ix.slugOf[nid]}.html' in files, f'missing node page for {nid}'
    for relpath in ix.files:
        assert f'files/{rh._mid(relpath)}.html' in files, f'missing file page for {relpath}'


def test_no_dead_internal_links():
    rh, files = _build_once()
    keys = set(files)
    dead = []
    for rel, data in files.items():
        if not rel.endswith('.html'):
            continue
        p = _LinkParser()
        p.feed(data.decode('utf-8'))
        for link in p.links:
            tgt = _internal_target(link)
            if tgt is None:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), tgt))
            if resolved not in keys:
                dead.append((rel, link, resolved))
    assert not dead, 'dead internal links:\n' + '\n'.join(
        f'  {r}: {lk} -> {res}' for r, lk, res in dead[:15])


def test_build_is_deterministic():
    rh = _load_rh()
    a = rh.render_all_files(rh._load_index())
    b = rh.render_all_files(rh._load_index())
    assert a == b, 'HTML build is nondeterministic'


def test_slugs_case_insensitively_unique():
    rh = _load_rh()
    ix = rh._load_index()
    lowered = [s.lower() for s in ix.slugOf.values()]
    assert len(set(lowered)) == len(lowered), 'slug collision (case-insensitive)'


# --- orientation: breadcrumbs on node pages + explorer navigation UI ----------------------

def test_every_node_page_has_a_breadcrumb():
    rh, files = _build_once()
    missing = [k for k in files if k.startswith('nodes/') and b'class="crumbs"' not in files[k]]
    assert not missing, f'{len(missing)} node pages lack a breadcrumb, e.g. {missing[:3]}'


def test_breadcrumb_helper_semantics():
    rh = _load_rh()
    ix = rh._load_index()
    loc = rh.L('nodes')
    # a nested method: layer -> file page -> enclosing class -> name
    nested = rh._breadcrumb(ix, 'Warehouse/Inventory_Management.py::Inventory_Manager._stock_per_unit', loc)
    assert 'href="../layers.html"' in nested or 'layers.html' in nested
    assert 'files/' + rh._mid('Warehouse/Inventory_Management.py') + '.html' in nested
    assert 'Inventory_Manager' in nested and '_stock_per_unit' in nested
    # a top-level function: no class crumb
    top = rh._breadcrumb(ix, 'Optimization/sim_assets.py::build_shared_assets', loc)
    assert 'files/' in top and 'build_shared_assets' in top
    assert 'Inventory_Manager' not in top
    # a module: the file itself is the 'here' crumb
    mod = rh._breadcrumb(ix, 'Optimization/sim_assets.py', loc)
    assert 'class="here"' in mod and 'sim_assets.py' in mod


def test_explorer_has_tree_and_breadcrumb_containers():
    rh, files = _build_once()
    ex = files['explorer.html'].decode('utf-8')
    assert 'id="ego-tree"' in ex, 'explorer missing the code-tree navigator container'
    assert 'id="ego-crumbs"' in ex, 'explorer missing the breadcrumb stripe'
