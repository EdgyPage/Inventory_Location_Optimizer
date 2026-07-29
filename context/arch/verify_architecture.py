"""verify_architecture.py — assert the architecture spec matches the real call graph.

The DERIVED truth is context/arch/graph.json (built from source by extract.py); the CLAIMS
live in context/architecture.yml (+ context/files.yml, the catalog).  This verifier reuses
context/verify_context.py's `check_symbol` regex helper and enforces, in order:

  1. UP-TO-DATE  — a fresh in-memory extract == the committed graph.json (else it is stale).
  2. NON-VACUITY — the spec/graph are not empty (a check that cannot pass trivially).
  3. ANCHORS     — every {name,file,kind} in the spec exists in the code (check_symbol).
  4. SCOPE       — every `backbone` edge (intent) exists in graph.json (reality).
  5. BOUNDARY    — no import edge in graph.json crosses a `forbid` layer pair (policy).
  6. CATALOG     — context/files.yml is complete + consistent (see check_files; skipped if absent).

Exits 1 with a per-item drift list on any failure, 0 otherwise.

Usage:  python context/arch/verify_architecture.py [--quiet]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # context/arch
_CTX = os.path.dirname(_HERE)                               # context
_ROOT = os.path.dirname(_CTX)                               # repo root
_GRAPH_PATH = os.path.join(_HERE, 'graph.json')
_ARCH_PATH = os.path.join(_CTX, 'architecture.yml')
_FILES_PATH = os.path.join(_CTX, 'files.yml')

MIN_BACKBONE = 10
MIN_NODES = 500

try:
    import yaml
except ImportError:                                        # pragma: no cover
    sys.exit('verify_architecture needs pyyaml (pip install -r requirements-docs.txt)')


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vc = _load_module(os.path.join(_CTX, 'verify_context.py'), 'verify_context')
extract = _load_module(os.path.join(_HERE, 'extract.py'), 'arch_extract')

_errors: list[str] = []


def err(msg: str) -> None:
    _errors.append(msg)


# --- layer mapping -----------------------------------------------------------------------
def _layer_of(relpath: str, layers: list[dict]) -> str | None:
    for lyr in layers:                      # explicit members win
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


def layer_prefix_violations(layers: list[dict], relpaths: list[str] | None = None) -> list[str]:
    """Every `match` / `except` path prefix must select at least one real file.

    WHY THIS EXISTS: `_layer_of` is a plain `str.startswith`, and a prefix that matches nothing is
    not an error anywhere else.  So renaming or moving a layer's root directory SILENTLY empties
    that layer — and any `forbid` boundary naming it becomes vacuously true forever, still green,
    while enforcing nothing.  A stale `except` is the same trap in reverse: it keeps carving out a
    subtree that no longer exists, or stops carving out one that moved.

    Only `members:` was previously existence-checked, which covers exactly one path in the file.
    """
    if relpaths is None:
        relpaths = extract.discover_files(extract.CATALOG_ROOTS, include_init=False)
    msgs: list[str] = []
    for lyr in layers:
        name = lyr.get('name', '?')
        m = lyr.get('match')
        if m and not any(p.startswith(m) for p in relpaths):
            msgs.append(f'layer {name}: match prefix {m!r} selects no files '
                        f'(renamed or deleted? any boundary naming this layer is now vacuous)')
        for exc in (lyr.get('except') or []):
            if not any(p.startswith(exc) for p in relpaths):
                msgs.append(f'layer {name}: except prefix {exc!r} carves out no files (stale?)')
    return msgs


def _anchor(a: dict, where: str) -> None:
    vc.check_symbol(a.get('name', ''), a.get('file', ''), a.get('kind', 'function'), where)


def scope_violations(backbone: list[dict], nodes: list[dict], edges: list[dict]) -> list[str]:
    """Pure SCOPE check: which backbone (intent) edges are absent from the graph (reality)."""
    by_id = {n['id']: n for n in nodes}
    edge_index: dict[tuple, set] = {}
    for e in edges:
        s, d = by_id.get(e['src']), by_id.get(e['dst'])
        if s and d:
            edge_index.setdefault(((s['name'], s['file']), (d['name'], d['file'])), set()).add(e['kind'])
    out: list[str] = []
    for i, e in enumerate(backbone):
        src, dst = e.get('src', {}), e.get('dst', {})
        key = ((src.get('name'), src.get('file')), (dst.get('name'), dst.get('file')))
        kinds = edge_index.get(key)
        via = e.get('via')
        if not kinds:
            out.append(f'backbone[{i}] SCOPE: no edge {src.get("name")} -> {dst.get("name")} in graph')
        elif via and via not in kinds:
            out.append(f'backbone[{i}] SCOPE: edge {src.get("name")} -> {dst.get("name")} is '
                       f'{sorted(kinds)}, spec says via={via}')
    return out


def boundary_violations(edges: list[dict], nodes: list[dict], layers: list[dict],
                        forbids: list[tuple]) -> list[str]:
    """Pure BOUNDARY check: which import edges (reality) cross a forbidden layer pair (policy)."""
    by_id = {n['id']: n for n in nodes}
    out: list[str] = []
    for e in edges:
        if e['kind'] != 'imports':
            continue
        sl = _layer_of(by_id[e['src']]['file'], layers)
        dl = _layer_of(by_id[e['dst']]['file'], layers)
        for a, b in forbids:
            if (a == '*' or a == sl) and (b == '*' or b == dl):
                out.append(f'BOUNDARY: forbidden import {sl} -> {dl}  ({by_id[e["src"]]["file"]} '
                           f'imports {by_id[e["dst"]]["file"]})')
    return out


def verify(quiet: bool = False) -> int:
    _errors.clear()
    vc._errors.clear()

    if not os.path.isfile(_GRAPH_PATH):
        err('graph.json missing — run: python context/arch/extract.py --write')
        return _finish(quiet, 0, 0, 0, 0)
    committed = open(_GRAPH_PATH, encoding='utf-8').read()
    graph = json.loads(committed)
    arch = yaml.safe_load(open(_ARCH_PATH, encoding='utf-8')) if os.path.isfile(_ARCH_PATH) else None
    if not isinstance(arch, dict):
        err('context/architecture.yml missing or not a mapping')
        return _finish(quiet, 0, 0, 0, 0)

    layers = arch.get('layers') or []
    backbone = arch.get('backbone') or []

    # 1. UP-TO-DATE
    fresh = extract.dumps(extract.build_graph())
    if fresh != committed:
        err('graph.json is STALE — run: python context/arch/extract.py --write')

    # 2. NON-VACUITY
    if len(backbone) < MIN_BACKBONE:
        err(f'backbone has {len(backbone)} edges (< {MIN_BACKBONE}); spec looks empty')
    if len(graph.get('nodes', [])) < MIN_NODES:
        err(f'graph has {len(graph.get("nodes", []))} nodes (< {MIN_NODES}); extractor looks broken')

    # node indexes
    nodes = graph.get('nodes', [])
    by_namefile: dict[tuple, list[str]] = {}
    for n in nodes:
        by_namefile.setdefault((n['name'], n['file']), []).append(n['id'])
    edges = graph.get('edges', [])

    # 3. ANCHORS (layer members, layer path prefixes, backbone endpoints, hotpaths)
    for lyr in layers:
        for m in (lyr.get('members') or []):
            if vc.src_of(m) is None:
                err(f"layer {lyr.get('name')}: member file missing: {m}")
    for msg in layer_prefix_violations(layers):
        err(msg)
    for i, e in enumerate(backbone):
        _anchor(e.get('src', {}), f'backbone[{i}].src')
        _anchor(e.get('dst', {}), f'backbone[{i}].dst')
    for i, h in enumerate(arch.get('hotpaths') or []):
        _anchor(h, f'hotpaths[{i}]')
    _errors.extend(vc._errors)
    vc._errors.clear()

    # 4. SCOPE — each backbone edge exists in the graph
    for msg in scope_violations(backbone, nodes, edges):
        err(msg)

    # 5. BOUNDARY — no import edge crosses a forbidden layer pair
    forbids = [(f['forbid'][0], f['forbid'][1]) for f in (arch.get('boundaries') or [])]
    for msg in boundary_violations(edges, nodes, layers, forbids):
        err(msg)

    # 6. CATALOG (optional until context/files.yml exists)
    if os.path.isfile(_FILES_PATH):
        for msg in check_files(layers, nodes):
            err(msg)

    return _finish(quiet, len(nodes), len(edges), len(backbone), len(layers))


def _spec_referenced_files() -> set[str]:
    """Every source file named by an anchor in flows/*.yml, artifacts.yml, architecture.yml."""
    out: set[str] = set()
    arch = yaml.safe_load(open(_ARCH_PATH, encoding='utf-8')) if os.path.isfile(_ARCH_PATH) else {}
    for e in (arch.get('backbone') or []):
        out |= {e.get('src', {}).get('file'), e.get('dst', {}).get('file')}
    for h in (arch.get('hotpaths') or []):
        out.add(h.get('file'))
    for lyr in (arch.get('layers') or []):
        out |= set(lyr.get('members') or [])
    flows_dir = os.path.join(_CTX, 'flows')
    for fn in (sorted(os.listdir(flows_dir)) if os.path.isdir(flows_dir) else []):
        if not fn.endswith('.yml'):
            continue
        data = yaml.safe_load(open(os.path.join(flows_dir, fn), encoding='utf-8')) or {}
        out.add(data.get('entry', {}).get('file'))
        for step in data.get('steps', []):
            out |= {f.get('file') for f in step.get('functions', [])}
    art_path = os.path.join(_CTX, 'artifacts.yml')
    if os.path.isfile(art_path):
        arts = (yaml.safe_load(open(art_path, encoding='utf-8')) or {}).get('artifacts', {})
        for art in arts.values():
            out.add(art.get('writer', {}).get('file'))
            out |= {r.get('file') for r in art.get('readers', [])}
    return {f for f in out if f}


def check_files(layers: list[dict], nodes: list[dict]) -> list[str]:
    """CATALOG check: context/files.yml is complete (every in-scope .py catalogued exactly
    once), every layer is known, every key_symbol anchor exists, and every file named by a
    flow/architecture anchor has an entry.  Does NOT re-run the graph."""
    msgs: list[str] = []
    cat = yaml.safe_load(open(_FILES_PATH, encoding='utf-8')) or {}
    entries = cat.get('files', {}) if isinstance(cat, dict) else {}
    layer_names = {lyr['name'] for lyr in layers}

    disk = set(extract.discover_files(extract.CATALOG_ROOTS, include_init=False))
    keys = set(entries)
    for m in sorted(disk - keys):
        msgs.append(f'catalog: file not catalogued (add to context/files.yml): {m}')
    for s in sorted(keys - disk):
        msgs.append(f'catalog: entry for a missing file (remove from context/files.yml): {s}')

    vc._errors.clear()
    for relpath in sorted(keys & disk):
        e = entries[relpath] or {}
        if not str(e.get('purpose', '')).strip():
            msgs.append(f'catalog {relpath}: purpose is empty (use TODO if unknown)')
        if e.get('layer') not in layer_names:
            msgs.append(f'catalog {relpath}: unknown layer {e.get("layer")!r}')
        for s in (e.get('key_symbols') or []):
            vc.check_symbol(s.get('name', ''), s.get('file', ''), s.get('kind', 'function'),
                            f'catalog:{relpath}')
    msgs.extend(vc._errors)
    vc._errors.clear()

    for f in sorted(_spec_referenced_files() - keys):
        msgs.append(f'catalog: file referenced by a flow/architecture anchor is not catalogued: {f}')
    return msgs


def _finish(quiet: bool, n_nodes: int, n_edges: int, n_backbone: int, n_layers: int) -> int:
    if _errors:
        print(f'architecture DRIFT — {len(_errors)} problem(s):')
        for e in _errors:
            print('  -', e)
        return 1
    if not quiet:
        print(f'architecture OK — {n_nodes} nodes, {n_edges} edges, '
              f'{n_backbone} backbone, {n_layers} layers verified.')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--quiet', action='store_true', help='print nothing on success')
    args = ap.parse_args()
    return verify(quiet=args.quiet)


if __name__ == '__main__':
    sys.exit(main())
