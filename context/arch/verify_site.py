"""verify_site.py — assert the generated HTML code-map suite is current + intact.

Full mode (default) proves, in order:
  1. NODES     — context/arch/nodes.json is up to date with the code.
  2. CURRENCY  — a fresh in-memory build == the committed site_manifest.json.
  3. INTEGRITY — the committed docs/architecture/** files hash to that manifest.
  4. LINKS     — every internal href/src in every generated page resolves.

--fast (for the Stop hook) does only INTEGRITY + "nodes.json present" — no rebuild.

Exits 1 with a per-item list on any failure, 0 otherwise.
Usage:  python context/arch/verify_site.py [--fast] [--quiet]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import posixpath
import sys
from html.parser import HTMLParser

_HERE = os.path.dirname(os.path.abspath(__file__))
_CTX = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_CTX)
_SITE = os.path.join(_ROOT, 'docs', 'architecture')
_MANIFEST = os.path.join(_HERE, 'site_manifest.json')
_NODES = os.path.join(_HERE, 'nodes.json')


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ('href', 'src') and v:
                self.links.append(v)


def _internal(link):
    if link.split(':', 1)[0].lower() in ('http', 'https', 'mailto', 'data', 'javascript'):
        return None
    p = link.split('#', 1)[0].split('?', 1)[0]
    return p or None


def _disk_manifest():
    out = {}
    for dirpath, _dirs, names in os.walk(_SITE):
        for n in names:
            ap = os.path.join(dirpath, n)
            rel = os.path.relpath(ap, _SITE).replace(os.sep, '/')
            out[rel] = hashlib.sha256(open(ap, 'rb').read()).hexdigest()
    return out


def verify(fast: bool = False) -> list[str]:
    errors = []
    if not os.path.isfile(_NODES):
        errors.append('nodes.json missing — run: python context/arch/extract.py --write-nodes')
    if not os.path.isfile(_MANIFEST):
        errors.append('site_manifest.json missing — run: python context/arch/render_html.py --build')
        return errors
    committed = json.load(open(_MANIFEST, encoding='utf-8'))

    # INTEGRITY (both modes)
    disk = _disk_manifest()
    if set(disk) != set(committed):
        errors.append(f'tree/manifest file set differs (extra: {sorted(set(disk)-set(committed))[:3]}, '
                      f'missing: {sorted(set(committed)-set(disk))[:3]})')
    elif disk != committed:
        errors.append('a committed docs/architecture file does not match its manifest hash')

    if fast:
        return errors

    # NODES currency
    ex = _load(os.path.join(_HERE, 'extract.py'), 'arch_extract')
    if os.path.isfile(_NODES):
        if open(_NODES, encoding='utf-8').read() != ex.dumps_nodes(ex.build_nodes_detail()):
            errors.append('nodes.json stale — run: python context/arch/extract.py --write-nodes')

    # CURRENCY + LINKS (fresh in-memory build)
    rh = _load(os.path.join(_HERE, 'render_html.py'), 'render_html')
    files = rh.render_all_files(rh._load_index())
    fresh = {rel: hashlib.sha256(b).hexdigest() for rel, b in files.items()}
    if fresh != committed:
        errors.append('site_manifest.json stale — run: python context/arch/render_html.py --build')
    keys = set(files)
    dead = []
    for rel, data in files.items():
        if not rel.endswith('.html'):
            continue
        p = _LinkParser()
        p.feed(data.decode('utf-8'))
        for link in p.links:
            tgt = _internal(link)
            if tgt and posixpath.normpath(posixpath.join(posixpath.dirname(rel), tgt)) not in keys:
                dead.append(f'{rel}: {link}')
    if dead:
        errors.append(f'{len(dead)} dead internal link(s), e.g. {dead[0]}')
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fast', action='store_true', help='integrity + nodes-present only (no rebuild)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    errors = verify(fast=args.fast)
    if errors:
        print(f'site DRIFT — {len(errors)} problem(s):')
        for e in errors:
            print('  -', e)
        return 1
    if not args.quiet:
        mode = 'fast' if args.fast else 'full'
        print(f'site OK ({mode}) — docs/architecture/ matches the code.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
