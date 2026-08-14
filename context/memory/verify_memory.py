"""verify_memory.py — the memory layer's contract, in the shape of verify_architecture.py.

Checks, in order.  The first two are MACHINE-LOCAL (they compare the live store to the mirror) and
are skipped by --repo-only, which is what a pytest gate must use: the live store does not exist in
another clone or in CI, so asserting on it would fail everywhere but one machine.

  1. LOCATED    the live store exists for this repo path — or is ORPHANED (the repo moved)
  2. PARITY     mirror == live, byte for byte
  3. INDEX      MEMORY.md pointers and the memory files are in exact 1:1 correspondence
  4. SHAPE      frontmatter has name (== filename stem), description, and a known metadata.type
  5. LINKS      every [[wiki-link]] resolves to a memory that exists
  6. PATHS      no machine-local paths (delegated to context/guards/path_guard.py)
  7. ANCHORS    every repo path cited in a memory body still exists in the tree

Check 7 is the one that rots on its own: a refactor that moves files silently invalidates memory
citations, and nothing else in the repo would ever notice.

Run:  python context/memory/verify_memory.py [--repo-only] [--quiet]
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))

VALID_TYPES = ('user', 'feedback', 'project', 'reference')
# Only these frontmatter keys are REQUIRED.  node_type / originSessionId / modified are written by
# some sessions and not others -- the store on disk is already inconsistent about them, and a
# verifier that demanded them would fail on day one against real, correct memories.
REQUIRED_KEYS = ('name', 'description')

_TOP_DIRS = ('Warehouse', 'Optimization', 'Tests', 'Visualization', 'Diagnostics',
             'Schema', 'context', 'docs', 'scripts', 'notebooks')
_EXT = r'py|md|json|yml|yaml|db|ipynb|csv|txt|cfg|toml'


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sync = _load(os.path.join(_HERE, 'sync.py'), 'memory_sync')
_guard = _load(os.path.join(_ROOT, 'context', 'guards', 'path_guard.py'), 'path_guard')


def _frontmatter(text: str) -> dict:
    """Hand-parse the leading YAML block.

    Deliberately NOT `import yaml`: every Tests/architecture file does
    `pytest.importorskip('yaml')`, so on a machine without pyyaml all eight drift gates skip and
    the suite is green while the docs rot.  Adding a ninth instance of that trap to the memory
    layer would defeat the point of the layer.  Two levels of key: value is all a memory uses.
    """
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end < 0:
        return {}
    out: dict = {}
    section = None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indented = line[:1] in (' ', '\t')
        if ':' not in line:
            continue
        k, _, v = line.partition(':')
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if indented and section:
            out.setdefault(section, {})[k] = v
        elif v:
            out[k] = v
        else:
            section = k
            out.setdefault(k, {})
    return out


def _cited_paths(body: str) -> list[str]:
    """Repo-relative path anchors cited in backticks.

    Two filters remove the false positives that dominate otherwise:
      - a token containing a space is a COMMAND, not a path (`run_analysis.py <dir> --workers N`);
      - a token with no '/' whose first segment is not a real top-level directory is a run-output
        name (`series.json`, `sim_meta.json`), which lives outside the repo by design.
    """
    out = []
    for tok in re.findall(r'`([^`\n]+)`', body):
        tok = tok.strip()
        if ' ' in tok or not re.search(r'\.(?:' + _EXT + r')(?:::|$)', tok):
            continue
        head = tok.split('/')[0].split('::')[0]
        if '/' not in tok or head not in _TOP_DIRS:
            continue
        out.append(tok)
    return out


def _git_basenames() -> dict[str, list[str]]:
    r = subprocess.run(['git', 'ls-files'], cwd=_ROOT, capture_output=True, text=True)
    idx: dict[str, list[str]] = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            idx.setdefault(os.path.basename(line), []).append(line)
    return idx


def verify(repo_only: bool = False, quiet: bool = False) -> int:
    errors: list[str] = []
    notes: list[str] = []
    st = _sync.status()

    if not repo_only:
        if st['orphaned']:
            errors.append('ORPHANED: the live store is missing/empty but the mirror holds '
                          f'{len(st["mirror_files"])} memor(ies) — the repo moved. '
                          'Restore: python context/memory/sync.py --restore')
            for other in st['others'][:3]:
                notes.append('  a store exists at ' + os.path.basename(os.path.dirname(other)))
        elif st['live'] is None:
            errors.append('no live memory store found for this repo path.')
        else:
            drift = len(st['added']) + len(st['changed']) + len(st['removed'])
            if drift:
                errors.append(f'mirror out of date: +{len(st["added"])} ~{len(st["changed"])} '
                              f'-{len(st["removed"])} — run: python context/memory/sync.py --push')

    # Everything below reads the MIRROR, so it works in any clone.
    files = dict(st['mirror_files'])
    index = files.pop('MEMORY.md', None)
    if index is None:
        if files or not repo_only:
            errors.append('context/memory/store/MEMORY.md is missing (the index).')
        return _report(errors, notes, 0, quiet)

    texts = {n: b.decode('utf-8', 'replace') for n, b in files.items()}
    index_text = index.decode('utf-8', 'replace')

    # 3. INDEX
    pointed = set(re.findall(r'\]\(([^)]+\.md)\)', index_text))
    for p in sorted(pointed - set(texts)):
        errors.append(f'MEMORY.md points at {p}, which does not exist.')
    for f in sorted(set(texts) - pointed):
        errors.append(f'{f} has no pointer line in MEMORY.md.')

    names = set()
    for name, text in sorted(texts.items()):
        fm = _frontmatter(text)
        stem = name[:-3]
        # 4. SHAPE
        for key in REQUIRED_KEYS:
            if not fm.get(key):
                errors.append(f'{name}: frontmatter is missing `{key}`.')
        if fm.get('name') and fm['name'] != stem:
            errors.append(f'{name}: frontmatter name `{fm["name"]}` != filename stem `{stem}`.')
        mtype = (fm.get('metadata') or {}).get('type')
        if mtype not in VALID_TYPES:
            errors.append(f'{name}: metadata.type is {mtype!r}, expected one of {VALID_TYPES}.')
        names.add(fm.get('name') or stem)

    for name, text in sorted(texts.items()):
        # 5. LINKS — a dangling [[link]] is a NOTE, not an error.  The memory convention allows a
        # forward reference: it marks something worth writing later.  Failing on it would push
        # against linking liberally, which is what makes the store navigable.
        for link in re.findall(r'\[\[([^\]]+)\]\]', text):
            if link not in names:
                notes.append(f'  note: {name} links [[{link}]], not written yet')
        # 6. PATHS
        for ln, cat, ex in _guard.scan_text(text):
            errors.append(f'{name}:{ln}: machine-local path [{cat}] {ex}')

    # 7. ANCHORS
    stale = 0
    idx = None
    for name, text in sorted(texts.items()):
        for cited in _cited_paths(text):
            rel = cited.split('::')[0]
            if os.path.exists(os.path.join(_ROOT, rel)):
                continue
            stale += 1
            if idx is None:
                idx = _git_basenames()
            cand = idx.get(os.path.basename(rel)) or []
            hint = f'  ->  {cand[0]} ?' if len(cand) == 1 else (
                '  ->  ' + ', '.join(cand[:3]) + ' ?' if cand else '  (no file of that name)')
            errors.append(f'{name}: stale anchor `{cited}`{hint}')

    return _report(errors, notes, len(texts), quiet, stale)


def _report(errors, notes, n, quiet, stale=0) -> int:
    if errors:
        if not quiet:
            print(f'memory DRIFT - {len(errors)} problem(s):')
            for e in errors:
                print('  - ' + e)
            for note in notes:
                print(note)
        return 1
    if not quiet:
        print(f'memory OK - {n} memor(ies): index, shape, links, paths and anchors verified.')
        for note in notes:
            print(note)
    return 0


def main(argv: list[str]) -> int:
    return verify(repo_only='--repo-only' in argv, quiet='--quiet' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
