"""render_html.py — generate the static HTML code-map suite from the verified data.

Consumes context/arch/graph.json (structure), context/arch/nodes.json (signatures +
docstrings), context/architecture.yml (layers/boundaries/backbone/hotpaths) and
context/files.yml (per-file catalog), and emits a self-contained static site:

  index.html · explorer.html · layers.html · catalog.html · inefficiency.html
  nodes/<slug>.html   (one per graph node — function/class/module/const)
  files/<slug>.html   (one per catalogued file)
  assets/graph.js     (window.GRAPH — adjacency, inlined so it loads at file://)
  assets/{arch.css,explorer.js,ego_svg.js,cytoscape.min.js}   (copied verbatim)

Everything works offline (file://) and hosted: RELATIVE links only, data inlined as JS
(never fetch), rendered client-side. Deterministic: sorted iteration, '\n' newlines, no
timestamps. slug = extract-style _mid(id), asserted 1:1 with the id set at build time.

Usage:
  python context/arch/render_html.py --build     # write docs/architecture/ + site_manifest.json
  python context/arch/render_html.py --check      # assert committed manifest == fresh build
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import sys
from collections import Counter
from urllib.parse import quote

_HERE = os.path.dirname(os.path.abspath(__file__))          # context/arch
_CTX = os.path.dirname(_HERE)                               # context
_ROOT = os.path.dirname(_CTX)                               # repo root
_SITE_DIR = os.path.join(_ROOT, 'docs', 'architecture')
_ASSET_SRC = os.path.join(_HERE, 'site_assets')
_NODES_PATH = os.path.join(_HERE, 'nodes.json')
_MANIFEST_PATH = os.path.join(_HERE, 'site_manifest.json')
_ASSET_FILES = ('arch.css', 'explorer.js', 'ego_svg.js', 'cytoscape.min.js')
_REPO_BLOB = 'https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/'
_TOP_N = 15
esc = html.escape


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


render = _load_module(os.path.join(_HERE, 'render.py'), 'arch_render')   # reuse _load, _mid
extract = _load_module(os.path.join(_HERE, 'extract.py'), 'arch_extract')  # reuse layer_of
_mid = render._mid


# --- relative-link helper (locations: 'root' | 'nodes' | 'files') ------------------------
class L:
    def __init__(self, loc: str) -> None:
        self.loc = loc
        self.up = '' if loc == 'root' else '../'

    def node(self, slug): return f'{slug}.html' if self.loc == 'nodes' else f'{self.up}nodes/{slug}.html'
    def file(self, slug): return f'{slug}.html' if self.loc == 'files' else f'{self.up}files/{slug}.html'
    def page(self, name): return f'{self.up}{name}'
    def asset(self, a):   return f'{self.up}assets/{a}'


def _slugs(ids: list) -> dict:
    """id -> filesystem-safe slug, unique CASE-INSENSITIVELY (Windows/macOS filesystems are
    case-insensitive, so two ids whose _mid differs only by case would clobber one file).
    Clean `_mid(id)` for the ~all-unique majority; a deterministic hash suffix for collisions."""
    base = {i: _mid(i) for i in ids}
    groups: dict[str, list] = {}
    for i, s in base.items():
        groups.setdefault(s.lower(), []).append(i)
    out = {}
    for members in groups.values():
        if len(members) == 1:
            out[members[0]] = base[members[0]]
        else:
            for i in members:
                out[i] = base[i] + '_' + hashlib.sha1(i.encode('utf-8')).hexdigest()[:8]
    assert len({v.lower() for v in out.values()}) == len(out), 'slug collision after disambiguation'
    return out


# --- shared index (built once; node pages and the explorer both derive from it) ----------
class Index:
    pass


def _load_index() -> Index:
    graph, arch, files = render._load()
    detail = json.load(open(_NODES_PATH, encoding='utf-8'))['nodes'] if os.path.isfile(_NODES_PATH) else {}
    layers = arch.get('layers', [])

    ix = Index()
    ix.graph, ix.arch, ix.files, ix.detail, ix.layers = graph, arch, files, detail, layers
    ix.by_id = {n['id']: n for n in graph['nodes']}
    ix.edges = graph['edges']
    ix.slugOf = _slugs(list(ix.by_id))

    # layer per file (cached)
    _lc: dict[str, str] = {}
    def layer_of(relpath):
        if relpath not in _lc:
            _lc[relpath] = extract.layer_of(relpath, layers) or 'unclassified'
        return _lc[relpath]
    ix.layer_of = layer_of

    # adjacency
    call_kinds = {'calls', 'ref', 'dispatch'}
    adj = {nid: {'callers': [], 'callees': [], 'imports': [], 'importedBy': []} for nid in ix.by_id}
    for e in ix.edges:
        s, d, k = e['src'], e['dst'], e['kind']
        if s not in adj or d not in adj:
            continue
        if k == 'imports':
            adj[s]['imports'].append(d)
            adj[d]['importedBy'].append(s)
        elif k in call_kinds:
            adj[s]['callees'].append(d)
            adj[d]['callers'].append(s)
    for a in adj.values():
        for key in a:
            a[key] = sorted(set(a[key]))
    ix.adj = adj

    # nodes grouped by file
    ix.file_nodes: dict[str, list[str]] = {}
    for nid, n in ix.by_id.items():
        if n['kind'] != 'module':
            ix.file_nodes.setdefault(n['file'], []).append(nid)

    # name@file -> ids (map bare backbone/hotpath anchors onto real ids)
    name_file: dict[tuple, list[str]] = {}
    for nid, n in ix.by_id.items():
        name_file.setdefault((n['name'], n['file']), []).append(nid)

    def anchor_ids(anchor):
        return name_file.get((anchor.get('name'), anchor.get('file')), [])

    # inefficiency-derived signals
    fan_out = Counter(e['src'] for e in ix.edges if e['kind'] in call_kinds)
    fan_in = Counter(e['dst'] for e in ix.edges if e['kind'] in call_kinds)
    ix.fan_out, ix.fan_in = fan_out, fan_in
    top_fanout = {nid for nid, _ in fan_out.most_common(_TOP_N)}
    top_fanin = {nid for nid, _ in fan_in.most_common(_TOP_N)}
    ix.cycles = _import_cycles(ix)
    cycle_files = {f for cyc in ix.cycles for f in cyc}
    crosslayer = set()
    ix.xlayer = Counter()
    for e in ix.edges:
        if e['kind'] not in call_kinds:
            continue
        sl, dl = layer_of(ix.by_id[e['src']]['file']), layer_of(ix.by_id[e['dst']]['file'])
        if sl != dl:
            crosslayer.add(e['src']); crosslayer.add(e['dst'])
            ix.xlayer[(sl, dl)] += 1
    backbone_ids, hotpath_ids = set(), set()
    for bb in arch.get('backbone', []):
        backbone_ids.update(anchor_ids(bb.get('src', {})))
        backbone_ids.update(anchor_ids(bb.get('dst', {})))
    for hp in arch.get('hotpaths', []):
        hotpath_ids.update(anchor_ids(hp))
    ix.orphans = sorted(nid for nid, n in ix.by_id.items()
                        if n['kind'] == 'function' and n.get('public')
                        and nid not in fan_in and n['name'] != 'main')
    orphan_set = set(ix.orphans)
    ix.entrypoints = sorted(nid for nid, n in ix.by_id.items()
                            if n['kind'] == 'function' and n['name'] == 'main')
    ix.hotpath_ids, ix.backbone_ids = hotpath_ids, backbone_ids

    # badges per node
    ix.flags: dict[str, list[str]] = {}
    for nid, n in ix.by_id.items():
        f = []
        if nid in backbone_ids: f.append('backbone')
        if nid in hotpath_ids:  f.append('hotpath')
        if nid in orphan_set:   f.append('orphan')
        if nid in top_fanout:   f.append('fanout')
        if nid in top_fanin:    f.append('fanin')
        if nid in crosslayer:   f.append('crosslayer')
        if n['file'] in cycle_files: f.append('cycle')
        ix.flags[nid] = f
    return ix


def _import_cycles(ix):
    try:
        import networkx as nx
    except ImportError:
        return []
    g = nx.DiGraph()
    for e in ix.edges:
        if e['kind'] == 'imports':
            g.add_edge(ix.by_id[e['src']]['file'], ix.by_id[e['dst']]['file'])
    cycles = []
    for c in nx.simple_cycles(g):
        # canonicalize rotation (start at the min node) so output is stable regardless of
        # which starting node networkx picks (varies with PYTHONHASHSEED across processes).
        k = c.index(min(c))
        rot = c[k:] + c[:k]
        cycles.append(rot + [rot[0]])
    return sorted(cycles, key=lambda c: (len(c), c))[:20]


# --- page shell --------------------------------------------------------------------------
def _page(title, body, loc: L) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head>\n'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{esc(title)} · ILO code map</title>\n'
        f'<link rel="stylesheet" href="{loc.asset("arch.css")}">\n'
        '</head><body>\n'
        '<header class="topbar">\n'
        f'  <a class="title" href="{loc.page("index.html")}">ILO code map</a>\n'
        f'  <nav><a href="{loc.page("explorer.html")}">Explorer</a> · '
        f'<a href="{loc.page("layers.html")}">Layers</a> · '
        f'<a href="{loc.page("catalog.html")}">Files</a> · '
        f'<a href="{loc.page("inefficiency.html")}">Inefficiency</a></nav>\n'
        '</header>\n'
        f'<main>\n{body}\n</main>\n'
        '</body></html>\n'
    )


def _badges(flags) -> str:
    if not flags:
        return ''
    label = {'backbone': 'backbone', 'hotpath': 'hot path', 'orphan': 'orphan',
             'fanout': 'high fan-out', 'fanin': 'high fan-in', 'crosslayer': 'cross-layer',
             'cycle': 'in import cycle'}
    return '<div class="badges">' + ''.join(
        f'<span class="badge {f}">{label[f]}</span>' for f in flags) + '</div>'


def _node_link(ix, loc, nid) -> str:
    n = ix.by_id[nid]
    return (f'<a href="{loc.node(ix.slugOf[nid])}"><code>{esc(n["name"])}</code></a>'
            f' <span class="dim">{esc(n["file"])}</span>')


def _rel_section(ix, loc, title, ids) -> str:
    if not ids:
        return f'<section class="rel"><h2>{title} <span class="count">(0)</span></h2></section>'
    items = ''.join(f'<li>{_node_link(ix, loc, nid)}</li>' for nid in ids)
    return (f'<section class="rel"><h2>{title} <span class="count">({len(ids)})</span></h2>'
            f'<ul>{items}</ul></section>')


def _source_href(node) -> str:
    kind, name, file = node['kind'], node['name'], node['file']
    if kind == 'module':
        return _REPO_BLOB + file
    kw = {'function': 'def ', 'class': 'class '}.get(kind, '')
    return _REPO_BLOB + file + '#:~:text=' + quote(kw + name)


# --- per-node page -----------------------------------------------------------------------
def render_node_page(ix, nid) -> str:
    loc = L('nodes')
    n = ix.by_id[nid]
    d = ix.detail.get(nid, {})
    kind, name, file = n['kind'], n['name'], n['file']
    layer = ix.layer_of(file)
    file_slug = _mid(file)

    head = (f'<h1><span class="kind kind-{kind}">{kind}</span> <code>{esc(name)}</code></h1>\n'
            f'<p class="meta">layer <span class="chip">{esc(layer)}</span> · file '
            f'<a href="{loc.file(file_slug)}"><code>{esc(file)}</code></a></p>\n'
            f'{_badges(ix.flags.get(nid, []))}')

    decos = ''.join(f'@{esc(x)}\n' for x in d.get('decorators') or [])
    if kind == 'const':
        sig_body = f'{esc(name)} = {esc(d.get("value_preview") or "..."):s}'
    else:
        prefix = {'function': ('async def ' if d.get('is_async') else 'def '),
                  'class': 'class ', 'module': ''}.get(kind, '')
        sig_body = f'{esc(prefix)}{esc(name)}{esc(d.get("signature") or "")}'
    sig = f'<pre class="sig"><code>{esc(decos)}{sig_body}</code></pre>'

    doc = d.get('doc')
    doc_html = (f'<section class="doc">{esc(doc)}</section>' if doc
                else '<section class="doc empty">No docstring.</section>')

    rels = ''
    if kind == 'module':
        rels += _rel_section(ix, loc, 'Imports', ix.adj[nid]['imports'])
        rels += _rel_section(ix, loc, 'Imported by', ix.adj[nid]['importedBy'])
        members = sorted(ix.file_nodes.get(file, []), key=lambda i: ix.by_id[i]['name'])
        rels += _rel_section(ix, loc, 'Defines', members)
    else:
        rels += _rel_section(ix, loc, 'Callees', ix.adj[nid]['callees'])
        rels += _rel_section(ix, loc, 'Callers', ix.adj[nid]['callers'])

    actions = (f'<p class="actions">'
               f'<a class="btn" href="{loc.page("explorer.html")}?focus={ix.slugOf[nid]}">Open in explorer ▸</a>'
               f'<a class="btn" rel="external" href="{_source_href(n)}">Source ↗</a></p>')

    body = f'<article class="node" id="{ix.slugOf[nid]}" data-id="{esc(nid)}">\n{head}\n{sig}\n{doc_html}\n{rels}\n{actions}\n</article>'
    return _page(name, body, loc)


# --- per-file page -----------------------------------------------------------------------
def render_file_page(ix, relpath) -> str:
    loc = L('files')
    entry = ix.files.get(relpath, {}) or {}
    layer = entry.get('layer') or ix.layer_of(relpath)
    purpose = entry.get('purpose') or ''
    notes = entry.get('notes') or ''
    module_id = relpath if relpath in ix.by_id else None

    head = (f'<h1><code>{esc(relpath)}</code></h1>\n'
            f'<p class="meta">layer <span class="chip">{esc(layer)}</span></p>\n'
            f'<p class="purpose">{esc(purpose)}</p>')
    if notes:
        head += f'<div class="doc">{esc(notes)}</div>'

    # key symbols
    ks_rows = ''
    for s in entry.get('key_symbols') or []:
        ids = [nid for nid in ix.file_nodes.get(relpath, [])
               if ix.by_id[nid]['name'] == s.get('name')]
        if ids:
            ks_rows += f'<li>{_node_link(ix, loc, ids[0])}</li>'
        else:
            ks_rows += f'<li><code>{esc(s.get("name",""))}</code> <span class="dim">{esc(s.get("kind",""))}</span></li>'
    ks_rows = ks_rows or "<li class='dim'>none</li>"
    ks = f'<section><h2>Key symbols</h2><ul class="rel">{ks_rows}</ul></section>'

    # all nodes in this file
    rows = ''
    for nid in sorted(ix.file_nodes.get(relpath, []),
                      key=lambda i: (ix.by_id[i]['kind'], ix.by_id[i]['name'])):
        n = ix.by_id[nid]
        rows += (f'<tr><td>{n["kind"]}</td>'
                 f'<td><a href="{loc.node(ix.slugOf[nid])}"><code>{esc(n["name"])}</code></a></td>'
                 f'<td class="num">{ix.fan_in.get(nid,0)}</td>'
                 f'<td class="num">{ix.fan_out.get(nid,0)}</td></tr>')
    rows = rows or "<tr><td class='dim' colspan='4'>no graph nodes (test/asset file)</td></tr>"
    table = (f'<section><h2>Nodes in this file <span class="count">({len(ix.file_nodes.get(relpath, []))})</span></h2>'
             f'<table class="tbl"><tr><th>kind</th><th>name</th><th class="num">fan-in</th>'
             f'<th class="num">fan-out</th></tr>{rows}</table></section>')

    imports = ''
    if module_id:
        imports = _rel_section(ix, loc, 'Module imports', ix.adj[module_id]['imports'])

    body = f'<article class="file">\n{head}\n{ks}\n{table}\n{imports}\n</article>'
    return _page(relpath, body, loc)


# --- graph.js (window.GRAPH — inlined so it loads at file://) -----------------------------
def render_graph_js(ix) -> str:
    nodes = {}
    for nid, n in ix.by_id.items():
        nodes[nid] = {'name': n['name'], 'file': n['file'], 'kind': n['kind'],
                      'layer': ix.layer_of(n['file']), 'public': n.get('public', False),
                      'slug': ix.slugOf[nid], 'flags': ix.flags.get(nid, [])}
    payload = {'version': 1, 'nodes': nodes, 'adj': ix.adj}
    return 'window.GRAPH = ' + json.dumps(payload, sort_keys=True, ensure_ascii=True) + ';\n'


# --- hub pages ---------------------------------------------------------------------------
def render_index(ix) -> str:
    loc = L('root')
    kinds = Counter(n['kind'] for n in ix.by_id.values())
    stats = ''.join(f'<div class="stat"><div class="n">{v}</div><div class="l">{k}s</div></div>'
                    for k, v in sorted(kinds.items()))
    stats = (f'<div class="stats">{stats}'
             f'<div class="stat"><div class="n">{len(ix.edges)}</div><div class="l">edges</div></div>'
             f'<div class="stat"><div class="n">{len(ix.files)}</div><div class="l">files</div></div></div>')
    hub = ('<div class="hub">'
           f'<a class="card" href="{loc.page("explorer.html")}"><h3>Explorer ▸</h3>'
           '<p class="dim">Interactive ego-graph of the call structure.</p></a>'
           f'<a class="card" href="{loc.page("layers.html")}"><h3>Layers</h3>'
           '<p class="dim">Modules grouped by architectural layer + coupling.</p></a>'
           f'<a class="card" href="{loc.page("catalog.html")}"><h3>Files</h3>'
           '<p class="dim">Every source + test file, purpose, key symbols.</p></a>'
           f'<a class="card" href="{loc.page("inefficiency.html")}"><h3>Inefficiency</h3>'
           '<p class="dim">Cycles, fan-in/out, coupling, orphans.</p></a></div>')
    ep = ''.join(f'<li>{_node_link(ix, loc, nid)}</li>' for nid in ix.entrypoints)
    hp = ''.join(f'<li>{_node_link(ix, loc, nid)}</li>' for nid in sorted(ix.hotpath_ids, key=lambda i: ix.by_id[i]['name']))
    search = ('<section><h2>Find a symbol</h2>'
              '<input id="q" class="filter" style="min-width:280px" placeholder="type a name…" autocomplete="off">'
              '<ul id="q-results" class="rel"></ul></section>'
              f'<script src="{loc.asset("graph.js")}"></script>'
              '<script>(function(){var q=document.getElementById("q"),r=document.getElementById("q-results");'
              'var ns=Object.keys(window.GRAPH.nodes).map(function(id){return [id,window.GRAPH.nodes[id]];});'
              'function render(){var v=q.value.toLowerCase().trim();r.innerHTML="";if(!v)return;'
              'var hits=ns.filter(function(p){return p[1].name.toLowerCase().indexOf(v)>=0;}).slice(0,50);'
              'hits.forEach(function(p){var li=document.createElement("li");'
              'li.innerHTML="<a href=\\"nodes/"+p[1].slug+".html\\"><code>"+p[1].name+"</code></a> <span class=\\"dim\\">"+p[1].file+"</span>";'
              'r.appendChild(li);});}q.addEventListener("input",render);})();</script>')
    body = (f'<h1>ILO code map</h1>\n'
            '<p class="meta">A verified, offline-capable map of every module, class, function, '
            'and constant — signatures, callers/callees, layer boundaries, and inefficiency signals. '
            'Generated from the call graph; kept in sync by the architecture-maintainer.</p>\n'
            f'{stats}\n{hub}\n'
            f'<section class="rel"><h2>Entry points <span class="count">({len(ix.entrypoints)})</span></h2><ul>{ep}</ul></section>\n'
            f'<section class="rel"><h2>Hot paths <span class="count">({len(ix.hotpath_ids)})</span></h2><ul>{hp}</ul></section>\n'
            f'{search}')
    return _page('Home', body, loc)


def render_layers(ix) -> str:
    loc = L('root')
    files_by_layer: dict[str, list[str]] = {}
    for relpath in ix.files:
        files_by_layer.setdefault(ix.files[relpath].get('layer') or ix.layer_of(relpath), []).append(relpath)
    order = [l['name'] for l in ix.layers]
    sections = ''
    for layer in order + sorted(set(files_by_layer) - set(order)):
        rows = sorted(files_by_layer.get(layer, []))
        if not rows:
            continue
        items = ''.join(
            f'<tr><td><a href="{loc.file(_mid(r))}"><code>{esc(r)}</code></a></td>'
            f'<td class="num">{len(ix.file_nodes.get(r, []))}</td></tr>' for r in rows)
        sections += (f'<section><h2>{esc(layer)} <span class="count">({len(rows)} files)</span></h2>'
                     f'<table class="tbl"><tr><th>file</th><th class="num">nodes</th></tr>{items}</table></section>')
    xl = ''.join(f'<tr><td>{esc(a)}</td><td>{esc(b)}</td><td class="num">{c}</td></tr>'
                 for (a, b), c in sorted(ix.xlayer.items(), key=lambda kv: (-kv[1], kv[0])))
    coupling = (f'<section><h2>Cross-layer call coupling</h2><table class="tbl">'
                f'<tr><th>from</th><th>to</th><th class="num">edges</th></tr>{xl}</table></section>')
    return _page('Layers', f'<h1>Layers</h1>\n{coupling}\n{sections}', loc)


def render_catalog(ix) -> str:
    loc = L('root')
    by_layer: dict[str, list[str]] = {}
    for relpath, e in ix.files.items():
        by_layer.setdefault(e.get('layer') or ix.layer_of(relpath), []).append(relpath)
    order = [l['name'] for l in ix.layers]
    sections = ''
    for layer in order + sorted(set(by_layer) - set(order)):
        rows = sorted(by_layer.get(layer, []))
        if not rows:
            continue
        items = ''
        for r in rows:
            e = ix.files[r]
            items += (f'<tr><td><a href="{loc.file(_mid(r))}"><code>{esc(r)}</code></a></td>'
                      f'<td>{esc(e.get("purpose") or "")}</td></tr>')
        sections += (f'<section><h2>{esc(layer)} <span class="count">({len(rows)})</span></h2>'
                     f'<table class="tbl"><tr><th>file</th><th>purpose</th></tr>{items}</table></section>')
    return _page('Files', f'<h1>File catalog <span class="count">({len(ix.files)} files)</span></h1>\n{sections}', loc)


def render_inefficiency(ix) -> str:
    loc = L('root')
    cyc = ('<ul class="rel">' + ''.join('<li>' + ' → '.join(f'<code>{esc(f)}</code>' for f in c) + '</li>'
                                        for c in ix.cycles) + '</ul>') if ix.cycles else '<p>None. Acyclic. ✅</p>'
    def deg_table(counter, title):
        rows = ''.join(f'<tr><td>{_node_link(ix, loc, nid)}</td><td class="num">{deg}</td></tr>'
                       for nid, deg in counter.most_common(_TOP_N))
        return (f'<section><h2>{title}</h2><table class="tbl"><tr><th>function</th>'
                f'<th class="num">degree</th></tr>{rows}</table></section>')
    xl = ''.join(f'<tr><td>{esc(a)}</td><td>{esc(b)}</td><td class="num">{c}</td></tr>'
                 for (a, b), c in sorted(ix.xlayer.items(), key=lambda kv: (-kv[1], kv[0])))
    orph = ''.join(f'<li>{_node_link(ix, loc, nid)}</li>' for nid in ix.orphans[:60])
    body = (f'<h1>Inefficiency signals</h1>\n'
            f'<section><h2>Module import cycles <span class="count">({len(ix.cycles)})</span></h2>{cyc}</section>\n'
            f'{deg_table(ix.fan_out, "Highest fan-out (calls many)")}\n'
            f'{deg_table(ix.fan_in, "Highest fan-in (called by many)")}\n'
            f'<section><h2>Cross-layer call coupling</h2><table class="tbl">'
            f'<tr><th>from</th><th>to</th><th class="num">edges</th></tr>{xl}</table></section>\n'
            f'<section class="rel"><h2>Orphan public functions <span class="count">({len(ix.orphans)})</span></h2>'
            f'<ul>{orph}</ul></section>')
    return _page('Inefficiency', body, loc)


def render_explorer(ix) -> str:
    loc = L('root')
    body = ('<div class="explorer">\n'
            '  <div id="ego" class="ego-stage" data-focus=""></div>\n'
            '  <aside class="ego-side">\n'
            '    <input id="ego-search" placeholder="search node…" autocomplete="off">\n'
            '    <ul id="ego-results"></ul>\n'
            '    <div id="ego-detail"><p class="ego-hint">Search or open a node to explore its '
            'callers &amp; callees. Click a ring node to re-center; double-click to open its page.</p></div>\n'
            '  </aside>\n'
            '</div>\n'
            f'<script src="{loc.asset("graph.js")}"></script>\n'
            f'<script src="{loc.asset("ego_svg.js")}"></script>\n'
            '<script>/* cytoscape is optional; loaded only if vendored */</script>\n'
            + (f'<script src="{loc.asset("cytoscape.min.js")}"></script>\n'
               if os.path.exists(os.path.join(_ASSET_SRC, 'cytoscape.min.js')) else '')
            + f'<script src="{loc.asset("explorer.js")}"></script>\n')
    return _page('Explorer', body, loc)


# --- assembly ----------------------------------------------------------------------------
def render_all_files(ix) -> dict:
    """Return {posix_relpath: bytes} for the entire suite — pure, no disk writes."""
    out: dict[str, bytes] = {}

    def put(rel, text):
        out[rel] = text.encode('utf-8')

    put('index.html', render_index(ix))
    put('explorer.html', render_explorer(ix))
    put('layers.html', render_layers(ix))
    put('catalog.html', render_catalog(ix))
    put('inefficiency.html', render_inefficiency(ix))
    put('assets/graph.js', render_graph_js(ix))
    for nid in sorted(ix.by_id):
        put(f'nodes/{ix.slugOf[nid]}.html', render_node_page(ix, nid))
    # a page for every catalogued file AND every file that owns a graph node (incl.
    # __init__.py modules, which are graph nodes but excluded from the catalog) — so every
    # node -> file link resolves.
    for relpath in sorted(set(ix.files) | {n['file'] for n in ix.by_id.values()}):
        put(f'files/{_mid(relpath)}.html', render_file_page(ix, relpath))
    for a in _ASSET_FILES:
        src = os.path.join(_ASSET_SRC, a)
        if os.path.exists(src):
            with open(src, 'rb') as fh:
                out[f'assets/{a}'] = fh.read()
    return out


def manifest_of(files: dict) -> dict:
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(files.items())}


def build_site(out_dir: str = _SITE_DIR) -> dict:
    ix = _load_index()
    files = render_all_files(ix)
    wanted = set(files)
    for rel, data in sorted(files.items()):
        path = os.path.join(out_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(data)
    # prune stale files (renamed/deleted nodes or catalog entries leave no orphan pages)
    if os.path.isdir(out_dir):
        for dirpath, _dirs, names in os.walk(out_dir, topdown=False):
            for n in names:
                ap = os.path.join(dirpath, n)
                rel = os.path.relpath(ap, out_dir).replace(os.sep, '/')
                if rel not in wanted:
                    os.remove(ap)
            if not os.listdir(dirpath) and os.path.abspath(dirpath) != os.path.abspath(out_dir):
                os.rmdir(dirpath)
    return manifest_of(files)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', action='store_true', help='write docs/architecture/ + site_manifest.json')
    ap.add_argument('--check', action='store_true', help='assert committed manifest == fresh build')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    if args.build:
        manifest = build_site()
        with open(_MANIFEST_PATH, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        if not args.quiet:
            print(f'site built — {len(manifest)} files under docs/architecture/.')
        return 0

    # default / --check: currency of the committed manifest vs a fresh in-memory build
    fresh = manifest_of(render_all_files(_load_index()))
    if not os.path.isfile(_MANIFEST_PATH):
        print('site_manifest.json missing — run: python context/arch/render_html.py --build')
        return 1
    committed = json.load(open(_MANIFEST_PATH, encoding='utf-8'))
    if committed != fresh:
        changed = sorted(set(committed) ^ set(fresh)) or \
            [r for r in fresh if committed.get(r) != fresh[r]]
        print(f'site DRIFT — {len(changed)} file(s) differ; regenerate: '
              'python context/arch/render_html.py --build')
        for r in changed[:10]:
            print('  -', r)
        return 1
    if not args.quiet:
        print(f'site OK — {len(fresh)} files match the manifest.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
