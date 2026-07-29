"""extract.py — derive the repo's call/import graph from source (the ground truth).

Walks the product source roots, parses every module with the stdlib `ast`, and emits a
deterministic, byte-identical graph to context/arch/graph.json:

  nodes: modules / classes / functions / consts, each with a {name, file, kind} anchor
         (the same triple context/verify_context.py already checks) plus a stable id
         `"<posix_relpath>::<qualname>"` — NO line numbers, so moving a def within a file
         changes nothing.
  edges: kind ∈ {imports, calls, ref, dispatch}
         - imports  : module -> module (in-repo `import`/`from ... import`)
         - calls    : caller -> callee for statically resolvable calls (local defs,
                      imported functions, imported-module attributes, and `self.method`
                      resolved across the class + its in-repo mixin bases — MRO pass)
         - ref      : node -> function used AS A VALUE (dict/list item, call argument,
                      kwarg) — captures string-keyed registries (RELOADERS, STRATEGIES)
                      and the ProcessPool `submit(_run_strategy_worker, ...)` spawn edge
         - dispatch : explicit edges from context/arch/resolver_hints.yml — the one
                      dynamic layer AST can't see (Placement.place_one/place_wave closures)

The graph is DERIVED, so it never rots: re-run `--write` to regenerate it.  `--check`
re-extracts in memory and byte-diffs against the committed graph.json (exit 1 on drift).

Usage:
  python context/arch/extract.py --write     # regenerate context/arch/graph.json
  python context/arch/extract.py --check     # assert graph.json matches the code (default)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # context/arch
_ROOT = os.path.dirname(os.path.dirname(_HERE))             # repo root
_GRAPH_PATH = os.path.join(_HERE, 'graph.json')
_NODES_PATH = os.path.join(_HERE, 'nodes.json')
_HINTS_PATH = os.path.join(_HERE, 'resolver_hints.yml')

# Product source roots for the call/import graph.  Tests/ is intentionally excluded from
# the graph (it is in the file catalog's scope, not the call graph's).
GRAPH_ROOTS = ('Warehouse', 'Optimization', 'Diagnostics', 'Visualization', 'scripts', 'docs')

# Receivers we can type without full inference (repo-specific, kept tiny + explicit).
# `self` is resolved structurally via the enclosing class MRO; these named receivers map
# to a concrete in-repo class so cross-module `mgr.method()` calls resolve too.
_RECEIVER_TYPES = {
    'mgr':     'Warehouse/inventory/Inventory_Management.py::Inventory_Manager',
    'manager': 'Warehouse/inventory/Inventory_Management.py::Inventory_Manager',
}


def _posix(path: str) -> str:
    return path.replace(os.sep, '/')


def discover_files(roots: tuple[str, ...], include_init: bool = True) -> list[str]:
    """Sorted posix relpaths of every .py under `roots` (optionally skipping __init__.py)."""
    out: list[str] = []
    for root in roots:
        abs_root = os.path.join(_ROOT, root)
        if not os.path.isdir(abs_root):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_root):
            dirnames.sort()
            # never descend into generated / vendored trees
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__', 'site', 'node_modules')]
            for fn in sorted(filenames):
                if not fn.endswith('.py'):
                    continue
                if not include_init and fn == '__init__.py':
                    continue
                out.append(_posix(os.path.relpath(os.path.join(dirpath, fn), _ROOT)))
    return sorted(set(out))


def _module_name(relpath: str) -> str:
    """`Warehouse/catalog/Order.py` -> `Warehouse.catalog.Order`; `pkg/__init__.py` -> `pkg`."""
    stem = relpath[:-3] if relpath.endswith('.py') else relpath
    if stem.endswith('/__init__'):
        stem = stem[: -len('/__init__')]
    return stem.replace('/', '.')


class _ModuleIndex:
    """Everything statically known about one module: its nodes + name resolution tables."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.top_defs: dict[str, str] = {}          # bare name -> node id (top-level def/class/const)
        self.top_kind: dict[str, str] = {}          # bare name -> kind
        self.classes: dict[str, list[str]] = {}     # class name -> [base names]
        self.methods: dict[str, set[str]] = {}      # class name -> {method names}
        self.import_sym: dict[str, tuple[str, str]] = {}   # name -> (target_relpath, target_symbol)
        self.import_mod: dict[str, str] = {}        # alias -> target module relpath


def _node_id(relpath: str, qualname: str) -> str:
    return f'{relpath}::{qualname}'


class _Collector(ast.NodeVisitor):
    """First pass: collect nodes (modules/classes/functions/consts) + import tables."""

    def __init__(self, relpath: str, dotted2relpath: dict[str, str]) -> None:
        self.relpath = relpath
        self.dotted2relpath = dotted2relpath
        self.idx = _ModuleIndex(relpath)
        self.nodes: list[dict] = []
        self._scope: list[str] = []            # qualname components
        self._class_stack: list[str] = []

    # -- node emission ----------------------------------------------------------------
    def _emit(self, name: str, kind: str) -> str:
        qual = '.'.join(self._scope + [name]) if self._scope else name
        nid = _node_id(self.relpath, qual)
        self.nodes.append({'id': nid, 'name': name, 'file': self.relpath, 'kind': kind})
        return nid

    def visit_Module(self, node: ast.Module) -> None:
        self.nodes.append({'id': self.relpath, 'name': _module_name(self.relpath),
                           'file': self.relpath, 'kind': 'module'})
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        nid = self._emit(node.name, 'class')
        top_level = not self._scope
        if top_level:
            self.idx.top_defs[node.name] = nid
            self.idx.top_kind[node.name] = 'class'
            self.idx.classes[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]
            self.idx.methods[node.name] = {
                b.name for b in node.body
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        self._scope.append(node.name)
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()
        self._scope.pop()

    def _visit_func(self, node) -> None:
        nid = self._emit(node.name, 'function')
        if not self._scope:
            self.idx.top_defs[node.name] = nid
            self.idx.top_kind[node.name] = 'function'
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Assign(self, node: ast.Assign) -> None:
        # module-level simple `NAME = ...` -> const node (matches kind:const anchors)
        if not self._scope:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    nid = self._emit(tgt.id, 'const')
                    self.idx.top_defs.setdefault(tgt.id, nid)
                    self.idx.top_kind.setdefault(tgt.id, 'const')
        self.generic_visit(node)

    # -- imports ----------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = self.dotted2relpath.get(alias.name)
            if target:
                self.idx.import_mod[alias.asname or alias.name.split('.')[0]] = target
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self._resolve_from_module(node)
        if module is None:
            self.generic_visit(node)
            return
        for alias in node.names:
            # `from pkg import submodule`?
            sub = f'{module}.{alias.name}'
            sub_rel = self.dotted2relpath.get(sub)
            mod_rel = self.dotted2relpath.get(module)
            if sub_rel:
                self.idx.import_mod[alias.asname or alias.name] = sub_rel
            elif mod_rel:
                self.idx.import_sym[alias.asname or alias.name] = (mod_rel, alias.name)
        self.generic_visit(node)

    def _resolve_from_module(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        # relative import: climb `level` packages from the current module's package
        pkg = _module_name(self.relpath).split('.')
        base = pkg[: len(pkg) - node.level] if not self.relpath.endswith('__init__.py') \
            else pkg[: len(pkg) - node.level + 1]
        parts = base + ([node.module] if node.module else [])
        return '.'.join(p for p in parts if p) or None


def _build_indexes(files: list[str]) -> tuple[dict[str, _ModuleIndex], list[dict], dict[str, ast.AST]]:
    dotted2relpath = {_module_name(f): f for f in files}
    indexes: dict[str, _ModuleIndex] = {}
    nodes: list[dict] = []
    trees: dict[str, ast.AST] = {}
    for relpath in files:
        with open(os.path.join(_ROOT, relpath), encoding='utf-8') as fh:
            src = fh.read()
        tree = ast.parse(src, filename=relpath)
        trees[relpath] = tree
        col = _Collector(relpath, dotted2relpath)
        col.visit(tree)
        indexes[relpath] = col.idx
        nodes.extend(col.nodes)
    return indexes, nodes, trees


class _EdgeFinder(ast.NodeVisitor):
    """Second pass: resolve calls / refs / imports into edges using the global indexes."""

    def __init__(self, relpath: str, indexes: dict[str, _ModuleIndex],
                 node_ids: set[str]) -> None:
        self.relpath = relpath
        self.idx = indexes[relpath]
        self.indexes = indexes
        self.node_ids = node_ids
        self.edges: set[tuple[str, str, str]] = set()
        self._owner: list[str] = [relpath]      # enclosing node id (module by default)
        self._class_stack: list[str] = []
        self._qual_stack: list[str] = []        # qualname components of the current owner

    def _add(self, dst: str | None, kind: str) -> None:
        src = self._owner[-1]
        if dst and dst in self.node_ids and src in self.node_ids and src != dst:
            self.edges.add((src, dst, kind))

    # -- scope tracking ---------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # class names participate in the qualname (mirrors the collector's _scope) so a
        # method's owner id is `file::Class.method`, matching its emitted node.
        self._class_stack.append(node.name)
        self._qual_stack.append(node.name)
        self.generic_visit(node)
        self._qual_stack.pop()
        self._class_stack.pop()

    def _visit_func(self, node) -> None:
        # the owner id must mirror the qualname the collector emitted
        owner_qual = self._current_qual(node.name)
        self._owner.append(_node_id(self.relpath, owner_qual))
        self._qual_stack.append(node.name)
        self.generic_visit(node)
        self._qual_stack.pop()
        self._owner.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Assign(self, node: ast.Assign) -> None:
        # module-level `NAME = <registry literal>` -> owner is the NAME const/def node, so
        # `RELOADERS = {'k': fn}` / `STRATEGIES = [...]` attribute their fn refs to the
        # registry itself (registry membership), not to the whole module.
        if not self._qual_stack and not self._class_stack and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            cand = _node_id(self.relpath, node.targets[0].id)
            if cand in self.node_ids:
                self._owner.append(cand)
                self.generic_visit(node)
                self._owner.pop()
                return
        self.generic_visit(node)

    def _current_qual(self, name: str) -> str:
        return '.'.join(self._qual_stack + [name]) if self._qual_stack else name

    # -- name resolution --------------------------------------------------------------
    def _resolve_name(self, name: str) -> str | None:
        """A bare Name used as a callee/value -> node id of an in-repo def, if known."""
        if name in self.idx.top_defs:
            return self.idx.top_defs[name]
        if name in self.idx.import_sym:
            tgt_rel, tgt_sym = self.idx.import_sym[name]
            return self.indexes.get(tgt_rel, _ModuleIndex(tgt_rel)).top_defs.get(tgt_sym)
        return None

    def _resolve_attr(self, node: ast.Attribute) -> str | None:
        """`x.attr` -> node id, for imported-module attrs, self.method, mgr.method."""
        value, attr = node.value, node.attr
        if isinstance(value, ast.Name):
            # imported module alias -> that module's top-level symbol
            if value.id in self.idx.import_mod:
                tgt_rel = self.idx.import_mod[value.id]
                return self.indexes.get(tgt_rel, _ModuleIndex(tgt_rel)).top_defs.get(attr)
            # self.method -> resolve via the enclosing class MRO
            if value.id == 'self' and self._class_stack:
                return self._resolve_method(self._class_stack[-1], self.relpath, attr)
            # typed receiver (mgr/manager) -> configured class MRO
            if value.id in _RECEIVER_TYPES:
                owner_rel, cls = _RECEIVER_TYPES[value.id].split('::')
                return self._resolve_method(cls, owner_rel, attr)
            # ClassName.method(...) — ClassName an in-repo class (local or imported)
            cls_target = self._resolve_class(value.id)
            if cls_target:
                return self._resolve_method(cls_target[1], cls_target[0], attr)
        return None

    def _resolve_class(self, name: str) -> tuple[str, str] | None:
        if self.idx.top_kind.get(name) == 'class':
            return (self.relpath, name)
        if name in self.idx.import_sym:
            rel, sym = self.idx.import_sym[name]
            tgt = self.indexes.get(rel)
            if tgt and tgt.top_kind.get(sym) == 'class':
                return (rel, sym)
        return None

    def _resolve_method(self, cls: str, cls_rel: str, attr: str) -> str | None:
        """Find `attr` on `cls` or its (in-repo) bases — MRO pass; only if unambiguous."""
        cidx = self.indexes.get(cls_rel)
        if cidx is None or cls not in cidx.classes:
            return None
        if attr in cidx.methods.get(cls, set()):
            return _node_id(cls_rel, f'{cls}.{attr}')
        hits: list[str] = []
        for base in cidx.classes[cls]:
            # base may be local or imported into cls's module
            b_rel, b_name = cls_rel, base
            if base in cidx.import_sym:
                b_rel, b_name = cidx.import_sym[base]
            bidx = self.indexes.get(b_rel)
            if bidx and b_name in bidx.methods and attr in bidx.methods[b_name]:
                hits.append(_node_id(b_rel, f'{b_name}.{attr}'))
        return hits[0] if len(hits) == 1 else None      # ambiguous ⇒ no edge

    # -- edges ------------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self._add(self._resolve_name(func.id), 'calls')
        elif isinstance(func, ast.Attribute):
            self._add(self._resolve_attr(func), 'calls')
        # arguments passed as VALUES (function references) -> ref edges
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            self._ref_value(arg)
        self.generic_visit(node)

    def _ref_value(self, expr: ast.AST) -> None:
        if isinstance(expr, ast.Name):
            dst = self._resolve_name(expr.id)
            if dst and _kind_of(dst) == 'function':
                self._add(dst, 'ref')
        elif isinstance(expr, ast.Attribute):
            dst = self._resolve_attr_value(expr)
            if dst:
                self._add(dst, 'ref')

    def _resolve_attr_value(self, node: ast.Attribute) -> str | None:
        if isinstance(node.value, ast.Name) and node.value.id in self.idx.import_mod:
            tgt_rel = self.idx.import_mod[node.value.id]
            return self.indexes.get(tgt_rel, _ModuleIndex(tgt_rel)).top_defs.get(node.attr)
        return None

    def _visit_container(self, node) -> None:
        elts = list(getattr(node, 'elts', []))
        if isinstance(node, ast.Dict):
            elts = list(node.values)
        for e in elts:
            self._ref_value(e)
        self.generic_visit(node)

    visit_List = _visit_container
    visit_Tuple = _visit_container
    visit_Set = _visit_container
    visit_Dict = _visit_container


_NODE_KIND: dict[str, str] = {}


def _kind_of(node_id: str) -> str:
    return _NODE_KIND.get(node_id, 'function')


def _import_edges(relpath: str, idx: _ModuleIndex, node_ids: set[str]) -> set[tuple[str, str, str]]:
    edges = set()
    targets = set(idx.import_mod.values()) | {r for r, _ in idx.import_sym.values()}
    for tgt in targets:
        if tgt != relpath and tgt in node_ids:
            edges.add((relpath, tgt, 'imports'))
    return edges


def _load_hint_edges(node_ids: set[str], name_file_index: dict[tuple[str, str], list[str]]) -> \
        tuple[set[tuple[str, str, str]], list[str]]:
    """Explicit dispatch edges from resolver_hints.yml; validated against real nodes."""
    problems: list[str] = []
    edges: set[tuple[str, str, str]] = set()
    if not os.path.isfile(_HINTS_PATH):
        return edges, problems
    try:
        import yaml
    except ImportError:                                    # pragma: no cover
        return edges, ['resolver_hints.yml present but pyyaml missing']
    with open(_HINTS_PATH, encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}

    def _resolve(anchor: dict) -> list[str]:
        if 'id' in anchor:
            return [anchor['id']] if anchor['id'] in node_ids else []
        return name_file_index.get((anchor.get('name'), anchor.get('file')), [])

    for edge in (data.get('dispatch') or []):
        srcs = _resolve(edge.get('src', {}))
        dsts = _resolve(edge.get('dst', {}))
        if not srcs:
            problems.append(f"hint src not found: {edge.get('src')}")
        if not dsts:
            problems.append(f"hint dst not found: {edge.get('dst')}")
        for s in srcs:
            for d in dsts:
                edges.add((s, d, 'dispatch'))
    return edges, problems


def build_graph() -> dict:
    files = discover_files(GRAPH_ROOTS, include_init=True)
    indexes, nodes, trees = _build_indexes(files)
    node_ids = {n['id'] for n in nodes}
    _NODE_KIND.clear()
    _NODE_KIND.update({n['id']: n['kind'] for n in nodes})
    name_file_index: dict[tuple[str, str], list[str]] = {}
    for n in nodes:
        name_file_index.setdefault((n['name'], n['file']), []).append(n['id'])

    edges: set[tuple[str, str, str]] = set()
    for relpath in files:
        edges |= _import_edges(relpath, indexes[relpath], node_ids)
        finder = _EdgeFinder(relpath, indexes, node_ids)
        finder.visit(trees[relpath])
        edges |= finder.edges

    hint_edges, problems = _load_hint_edges(node_ids, name_file_index)
    edges |= hint_edges

    # attach module + public flag onto nodes for the renderer / boundary layer mapping
    for n in nodes:
        n['module'] = n['file']
        n['public'] = not n['name'].startswith('_')

    graph = {
        'version': 1,
        'roots': list(GRAPH_ROOTS),
        'nodes': sorted(nodes, key=lambda n: n['id']),
        'edges': sorted(({'src': s, 'dst': d, 'kind': k} for (s, d, k) in edges),
                        key=lambda e: (e['src'], e['dst'], e['kind'])),
    }
    if problems:
        graph['_hint_problems'] = sorted(problems)
    return graph


def dumps(graph: dict) -> str:
    return json.dumps(graph, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


# =========================================================================================
# Node detail (context/arch/nodes.json) — signatures + docstrings for the HTML pages.
#
# A SIBLING artifact, never a widening of graph.json (three tests byte-pin graph.json).
# Keys are exactly the graph node ids (same qualname/scope logic as _Collector), so the
# HTML generator can left-join detail onto graph nodes.  ast.unparse gives deterministic,
# normalized signature text (note: formatting can differ across Python versions — regen
# with the repo's pinned interpreter).
# =========================================================================================

def _clip(text: str, n: int = 200) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= n else text[:n - 3] + '...'


class _DetailCollector(ast.NodeVisitor):
    """Second detail pass mirroring _Collector's emit points → {id: {sig, doc, decorators}}."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.detail: dict[str, dict] = {}
        self._scope: list[str] = []

    def _put(self, name: str, entry: dict) -> None:
        qual = '.'.join(self._scope + [name]) if self._scope else name
        self.detail.setdefault(_node_id(self.relpath, qual), entry)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parts = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        self._put(node.name, {
            'kind': 'class',
            'signature': ('(' + ', '.join(parts) + ')') if parts else '',
            'doc': ast.get_docstring(node),
            'decorators': [ast.unparse(d) for d in node.decorator_list],
        })
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_func(self, node) -> None:
        sig = '(' + ast.unparse(node.args) + ')'
        if node.returns is not None:
            sig += ' -> ' + ast.unparse(node.returns)
        self._put(node.name, {
            'kind': 'function',
            'signature': sig,
            'doc': ast.get_docstring(node),
            'decorators': [ast.unparse(d) for d in node.decorator_list],
            'is_async': isinstance(node, ast.AsyncFunctionDef),
        })
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._scope:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._put(tgt.id, {
                        'kind': 'const',
                        'signature': None,
                        'doc': None,
                        'decorators': [],
                        'value_preview': _clip(ast.unparse(node.value)),
                    })
        self.generic_visit(node)


def build_nodes_detail() -> dict:
    """Detail payload for every graph node id — reuses the ASTs retained by _build_indexes."""
    files = discover_files(GRAPH_ROOTS, include_init=True)
    _indexes, _nodes, trees = _build_indexes(files)
    detail: dict[str, dict] = {}
    for relpath in files:
        tree = trees[relpath]
        col = _DetailCollector(relpath)
        col.visit(tree)
        col.detail.setdefault(relpath, {
            'kind': 'module', 'signature': None,
            'doc': ast.get_docstring(tree), 'decorators': [],
        })
        for nid, d in col.detail.items():
            detail.setdefault(nid, d)
    return {'version': 1, 'nodes': detail}


def dumps_nodes(detail: dict) -> str:
    return json.dumps(detail, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


# =========================================================================================
# File catalog (context/files.yml) — a human+agent index of every source + test file.
# =========================================================================================

# Everything runnable: product source + Tests/ (per user decision). __init__.py excluded.
CATALOG_ROOTS = GRAPH_ROOTS + ('Tests',)
_ARCH_PATH = os.path.join(_ROOT, 'context', 'architecture.yml')
_FILES_PATH = os.path.join(_ROOT, 'context', 'files.yml')


def layer_of(relpath: str, layers: list[dict]) -> str | None:
    """Map a file to its layer: explicit `members` win, else longest `match` (minus `except`)."""
    for lyr in layers:
        if relpath in (lyr.get('members') or []):
            return lyr['name']
    best, best_len = None, -1
    for lyr in layers:
        m = lyr.get('match')
        if not m or not relpath.startswith(m):
            continue
        if any(relpath.startswith(ex) for ex in (lyr.get('except') or [])):
            continue
        if len(m) > best_len:
            best, best_len = lyr['name'], len(m)
    return best


def _load_layers() -> list[dict]:
    import yaml
    if not os.path.isfile(_ARCH_PATH):
        return []
    data = yaml.safe_load(open(_ARCH_PATH, encoding='utf-8')) or {}
    return data.get('layers') or []


def _public_symbols(tree: ast.AST, relpath: str) -> list[dict]:
    """Curated key symbols: top-level public defs/classes + UPPER_CASE module consts."""
    out: list[dict] = []
    for stmt in getattr(tree, 'body', []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and not stmt.name.startswith('_'):
            out.append({'name': stmt.name, 'file': relpath, 'kind': 'function'})
        elif isinstance(stmt, ast.ClassDef) and not stmt.name.startswith('_'):
            out.append({'name': stmt.name, 'file': relpath, 'kind': 'class'})
        elif isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper() and not tgt.id.startswith('_'):
                    out.append({'name': tgt.id, 'file': relpath, 'kind': 'const'})
    # dedupe, keep first occurrence order stable
    seen, uniq = set(), []
    for s in out:
        k = (s['name'], s['kind'])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def _docstring_purpose(tree: ast.AST) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        return 'TODO'
    first = doc.strip().splitlines()[0].strip()
    # module docstrings here start "filename.py — one-line ..." — keep the prose after the dash
    for sep in (' — ', ' -- ', ' - '):
        if sep in first:
            first = first.split(sep, 1)[1].strip()
            break
    return first or 'TODO'


def build_catalog(existing: dict | None = None) -> dict:
    """Note-preserving catalog build: ADD new files (purpose seeded from docstring),
    DROP deleted files, REWRITE mechanical fields (layer, key_symbols); NEVER touch the
    human-owned purpose/notes of an entry that already exists."""
    existing_files = (existing or {}).get('files', {}) if isinstance(existing, dict) else {}
    layers = _load_layers()
    files = discover_files(CATALOG_ROOTS, include_init=False)
    out: dict[str, dict] = {}
    for relpath in files:
        with open(os.path.join(_ROOT, relpath), encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), filename=relpath)
        prior = existing_files.get(relpath)
        entry = {
            'purpose': prior['purpose'] if prior and 'purpose' in prior else _docstring_purpose(tree),
            'layer': layer_of(relpath, layers) or 'unclassified',
            'key_symbols': _public_symbols(tree, relpath),
            'notes': prior.get('notes', '') if prior else '',
        }
        out[relpath] = entry
    return {'version': 1, 'files': out}


def _scalar(s: str) -> str:
    # JSON strings are valid YAML double-quoted scalars: safe, deterministic, ascii-only.
    return json.dumps('' if s is None else str(s), ensure_ascii=True)


def dump_catalog(cat: dict) -> str:
    """Deterministic YAML for context/files.yml (files sorted by relpath)."""
    lines = ['# files.yml -- the repo file catalog (GENERATED skeleton; purpose/notes are',
             '# HUMAN-OWNED -- edit them here).  layer/key_symbols are refreshed from the code by',
             '# `python context/arch/extract.py --catalog-merge`, which never touches purpose/notes.',
             '# Rendered into the HTML file map (docs/architecture/catalog.html + per-file pages).',
             '# Completeness is enforced by verify_architecture.',
             'version: 1', 'files:']
    for relpath in sorted(cat.get('files', {})):
        e = cat['files'][relpath]
        lines.append(f'  {_scalar(relpath)}:')
        lines.append(f'    purpose: {_scalar(e.get("purpose", "TODO"))}')
        lines.append(f'    layer: {_scalar(e.get("layer", "unclassified"))}')
        ks = e.get('key_symbols') or []
        if ks:
            lines.append('    key_symbols:')
            for s in ks:
                lines.append(f'      - {{name: {_scalar(s["name"])}, file: {_scalar(s["file"])}, '
                             f'kind: {_scalar(s["kind"])}}}')
        else:
            lines.append('    key_symbols: []')
        lines.append(f'    notes: {_scalar(e.get("notes", ""))}')
    return '\n'.join(lines) + '\n'


def write_catalog(merge: bool = True) -> dict:
    import yaml
    existing = None
    if merge and os.path.isfile(_FILES_PATH):
        existing = yaml.safe_load(open(_FILES_PATH, encoding='utf-8'))
    cat = build_catalog(existing)
    with open(_FILES_PATH, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(dump_catalog(cat))
    return cat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true', help='regenerate context/arch/graph.json')
    ap.add_argument('--check', action='store_true', help='assert graph.json matches code (default)')
    ap.add_argument('--catalog-stub', action='store_true',
                    help='seed/refresh context/files.yml (preserves existing purpose/notes)')
    ap.add_argument('--catalog-merge', action='store_true',
                    help='refresh context/files.yml note-preservingly (add new, drop deleted)')
    ap.add_argument('--write-nodes', action='store_true',
                    help='regenerate context/arch/nodes.json (signatures + docstrings)')
    ap.add_argument('--check-nodes', action='store_true',
                    help='assert nodes.json matches code (exit 1 on drift)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    if args.catalog_stub or args.catalog_merge:
        cat = write_catalog(merge=True)
        if not args.quiet:
            print(f'files.yml written — {len(cat["files"])} entries.')
        return 0

    if args.write_nodes or args.check_nodes:
        detail = build_nodes_detail()
        text = dumps_nodes(detail)
        if args.write_nodes:
            with open(_NODES_PATH, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
            if not args.quiet:
                print(f'nodes.json written — {len(detail["nodes"])} node details.')
            return 0
        if not os.path.isfile(_NODES_PATH):
            print('nodes.json missing — run: python context/arch/extract.py --write-nodes')
            return 1
        committed = open(_NODES_PATH, encoding='utf-8').read()
        if committed != text:
            print('nodes.json DRIFT — signatures/docstrings no longer match the code.')
            print('  regenerate with: python context/arch/extract.py --write-nodes')
            return 1
        if not args.quiet:
            print(f'nodes.json OK — {len(detail["nodes"])} node details.')
        return 0

    graph = build_graph()
    text = dumps(graph)

    if args.write:
        with open(_GRAPH_PATH, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        if not args.quiet:
            print(f'graph.json written — {len(graph["nodes"])} nodes, {len(graph["edges"])} edges.')
        if graph.get('_hint_problems'):
            print('  WARNING unresolved resolver hints:')
            for p in graph['_hint_problems']:
                print('   -', p)
        return 0

    # default: --check
    if not os.path.isfile(_GRAPH_PATH):
        print('graph.json missing — run: python context/arch/extract.py --write')
        return 1
    with open(_GRAPH_PATH, encoding='utf-8') as fh:
        committed = fh.read()
    if committed != text:
        print('graph.json DRIFT — the call/import graph no longer matches the code.')
        print('  regenerate with: python context/arch/extract.py --write')
        return 1
    if not args.quiet:
        print(f'graph.json OK — {len(graph["nodes"])} nodes, {len(graph["edges"])} edges.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
