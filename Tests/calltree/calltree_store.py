"""
calltree_store.py — archival naming + index for measurement artifacts.

Problem this solves: results used to write fixed filenames (growth_deep_skus_s42.json),
so every rerun OVERWROTE the previous evidence — the pre-fix deep ladder survives only in
a conversation transcript. Measurements are evidence; evidence gets archived, not clobbered.

Scheme:
  out/archive/<kind>__<tags>__<UTC-stamp>_<shortsha>.json     (never overwritten)
  out/index.json                                              (append-style registry)

Every entry records kind, tags (knob/seed/tier/...), repo commit + dirty flag, created
stamp, the artifact path, and a small caller-supplied summary (exponents, offenders,
peaks) so results are comparable ACROSS sessions without re-opening every file.

    from calltree_store import archive_path, record, latest, entries
    p = archive_path('growth', ladder='deep', knob='skus', seed=42)
    ... write p ...
    record('growth', p, tags={...}, summary={'t_task_k': 2.42})
    prev = latest('growth', ladder='deep', knob='skus')      # newest matching entry

Artifacts stay gitignored (out/); the INDEX travels with them and is the reference layer.
Not collected by pytest.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
OUT_DIR     = os.path.join(_HERE, 'out')
ARCHIVE_DIR = os.path.join(OUT_DIR, 'archive')
INDEX_PATH  = os.path.join(OUT_DIR, 'index.json')


def repo_commit() -> tuple[str, bool]:
    try:
        sha = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=_REPO_ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '-uno'], cwd=_REPO_ROOT,
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        return sha or 'unknown', dirty
    except Exception:
        return 'unknown', True


def _stamp() -> str:
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())


def archive_path(kind: str, ext: str = 'json', **tags) -> str:
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    sha, dirty = repo_commit()
    tag = '_'.join(f'{k}-{tags[k]}' for k in sorted(tags))
    name = f"{kind}__{tag}__{_stamp()}_{sha}{'+dirty' if dirty else ''}.{ext}"
    return os.path.join(ARCHIVE_DIR, name)


def _load_index() -> list[dict]:
    if not os.path.isfile(INDEX_PATH):
        return []
    try:
        with open(INDEX_PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []


def record(kind: str, path: str, *, tags: dict, summary: dict | None = None) -> None:
    sha, dirty = repo_commit()
    idx = _load_index()
    idx.append({
        'kind': kind,
        'tags': {k: str(v) for k, v in tags.items()},
        'file': os.path.relpath(path, OUT_DIR).replace(os.sep, '/'),
        'commit': sha,
        'dirty': dirty,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'python': sys.version.split()[0],
        'summary': summary or {},
    })
    tmp = INDEX_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(idx, fh, indent=1)
    os.replace(tmp, INDEX_PATH)


def entries(kind: str | None = None, **tags) -> list[dict]:
    out = []
    for e in _load_index():
        if kind is not None and e['kind'] != kind:
            continue
        if any(e['tags'].get(k) != str(v) for k, v in tags.items()):
            continue
        out.append(e)
    return out


def latest(kind: str, **tags) -> dict | None:
    es = entries(kind, **tags)
    return es[-1] if es else None


def migrate_flat_files() -> int:
    """Sweep legacy fixed-name artifacts in out/ into the archive, stamped by mtime.

    Idempotent: already-archived or unknown files are left alone. Returns count moved."""
    moved = 0
    if not os.path.isdir(OUT_DIR):
        return 0
    sha, dirty = repo_commit()
    for fn in sorted(os.listdir(OUT_DIR)):
        src = os.path.join(OUT_DIR, fn)
        if not os.path.isfile(src) or not fn.endswith('.json') or fn == 'index.json':
            continue
        base = fn[:-5]
        if base.startswith('growth_'):
            kind, rest = 'growth', base[len('growth_'):]
        elif base.startswith('memory_'):
            kind, rest = 'memory', base[len('memory_'):]
        else:
            kind, rest = 'capture', base
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(os.path.getmtime(src)))
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        dst = os.path.join(ARCHIVE_DIR, f'{kind}__legacy-{rest}__{stamp}_{sha}.json')
        if os.path.exists(dst):
            continue
        os.replace(src, dst)
        record(kind, dst, tags={'legacy': rest, 'migrated_mtime': stamp},
               summary={'note': 'migrated from flat filename; commit tag is the '
                                'MIGRATION-time commit, not necessarily the producing one'})
        moved += 1
    return moved


if __name__ == '__main__':
    n = migrate_flat_files()
    print(f'migrated {n} legacy artifact(s) into out/archive/')
    for e in _load_index()[-10:]:
        print(f"  {e['kind']:8s} {e['file']}  ({e['created']})")
