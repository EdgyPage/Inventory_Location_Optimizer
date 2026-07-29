"""sync.py — mirror the session memory store into the repo so it survives the repo moving.

Claude Code keeps this project's durable memories in a directory OUTSIDE the repo, named after
the repo's absolute path.  That is fine until the repo moves: the slug changes, a fresh store is
created, and the old memories are orphaned with no backup.  This repo has already moved once.

So the live store is mirrored into `context/memory/store/`, which is tracked.  Putting the mirror
in git IS the conflict mechanism — three-way merge, history, `git diff` review, and recovery all
come free, and nothing more elaborate is warranted.

DIRECTION IS LIVE -> MIRROR, ALWAYS.  Claude Code owns the live store; the mirror is a replica.
`--restore` is the manual inverse, for after a move or a fresh clone, and it refuses to overwrite
a non-empty live store without --force.

    python context/memory/sync.py --push [--dry-run]
    python context/memory/sync.py --restore [--force]
    python context/memory/sync.py --status
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
MIRROR = os.path.join(_HERE, 'store')


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_guard = _load(os.path.join(_ROOT, 'context', 'guards', 'path_guard.py'), 'path_guard')


# ── locating the live store ──────────────────────────────────────────────────────────────
def _slug(path: str) -> str:
    """Claude Code's project-directory slug: every non-alphanumeric character becomes '-'."""
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def projects_dir() -> str:
    return os.path.join(os.path.expanduser('~'), '.claude', 'projects')


def live_store() -> tuple[str | None, list[str]]:
    """Return (path_or_None, other_candidates).

    The derived path is checked case-INSENSITIVELY: sibling project directories on this machine
    differ only by the case of the drive letter, so an exact match is not safe to rely on.  When
    the derived directory is absent we glob every project's memory/ so the caller can say "found
    the old store at X" instead of only "missing" — that message is the whole point after a move.
    """
    want = _slug(_ROOT).lower()
    base = projects_dir()
    found, others = None, []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            mem = os.path.join(base, name, 'memory')
            if not os.path.isdir(mem):
                continue
            if name.lower() == want:
                found = mem
            else:
                others.append(mem)
    return found, others


def _memories(d: str) -> dict[str, bytes]:
    out = {}
    if not d or not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if name.endswith('.md'):
            with open(os.path.join(d, name), 'rb') as fh:
                out[name] = fh.read()
    return out


# ── the operations ───────────────────────────────────────────────────────────────────────
def status() -> dict:
    live, others = live_store()
    lv, mr = _memories(live), _memories(MIRROR)
    return {
        'live': live, 'others': others,
        'live_files': lv, 'mirror_files': mr,
        'added':   sorted(set(lv) - set(mr)),
        'removed': sorted(set(mr) - set(lv)),
        'changed': sorted(n for n in set(lv) & set(mr) if lv[n] != mr[n]),
        'orphaned': (not lv) and bool(mr),
    }


def push(dry_run: bool = False, quiet: bool = False) -> int:
    st = status()
    if st['orphaned']:
        print('[memory] REFUSING to push: the live store is empty or missing but the mirror holds '
              f'{len(st["mirror_files"])} memor(ies). This is what a repo move looks like — '
              'restore instead: python context/memory/sync.py --restore')
        if st['others']:
            print('         other project stores on this machine: ' + ', '.join(
                os.path.basename(os.path.dirname(o)) for o in st['others'][:4]))
        return 1

    # The guard runs BEFORE anything is copied: a machine-local path must never reach the mirror,
    # because the mirror is tracked and a leak in git history cannot be taken back.
    dirty = {n: _guard.scan_text(b.decode('utf-8', 'replace')) for n, b in st['live_files'].items()}
    dirty = {n: f for n, f in dirty.items() if f}
    if dirty:
        print(f'[memory] REFUSING to push: {len(dirty)} memor(ies) contain machine-local paths:')
        for n, findings in sorted(dirty.items()):
            for ln, cat, ex in findings[:3]:
                print(f'  {n}:{ln}  [{cat}]  {ex}')
        print('         Name the .env key or use a ~/-relative form. See CLAUDE.md section 5.')
        return 1

    if dry_run:
        print(f'[memory] would push: +{len(st["added"])} ~{len(st["changed"])} -{len(st["removed"])}')
        for n in st['added']:   print('  + ' + n)
        for n in st['changed']: print('  ~ ' + n)
        for n in st['removed']: print('  - ' + n + '   (deleted from the live store)')
        return 0

    os.makedirs(MIRROR, exist_ok=True)
    for name, data in st['live_files'].items():
        with open(os.path.join(MIRROR, name), 'wb') as fh:
            fh.write(data)
    for name in st['removed']:
        os.remove(os.path.join(MIRROR, name))
    if not quiet:
        print(f'[memory] pushed {len(st["live_files"])} memor(ies) to context/memory/store/ '
              f'(+{len(st["added"])} ~{len(st["changed"])} -{len(st["removed"])}). '
              'Review with: git diff context/memory/store/')
    return 0


def restore(force: bool = False) -> int:
    live, others = live_store()
    mr = _memories(MIRROR)
    if not mr:
        print('[memory] nothing to restore: the mirror is empty.')
        return 1
    if live is None:
        live = os.path.join(projects_dir(), _slug(_ROOT), 'memory')
        print('[memory] no live store for this path; creating ' + live.replace(
            os.path.expanduser('~'), '~'))
        os.makedirs(live, exist_ok=True)
    existing = _memories(live)
    if existing and not force:
        print(f'[memory] REFUSING to restore: the live store already holds {len(existing)} '
              'memor(ies) and restoring would overwrite them. These would change:')
        for n in sorted(mr):
            if n in existing and existing[n] != mr[n]:
                print('  ~ ' + n)
            elif n not in existing:
                print('  + ' + n)
        print('         Re-run with --force if the mirror really is the good copy.')
        return 1
    for name, data in mr.items():
        with open(os.path.join(live, name), 'wb') as fh:
            fh.write(data)
    print(f'[memory] restored {len(mr)} memor(ies) into the live store.')
    return 0


def main(argv: list[str]) -> int:
    if '--restore' in argv:
        return restore(force='--force' in argv)
    if '--status' in argv:
        st = status()
        live = (st['live'] or '(none)').replace(os.path.expanduser('~'), '~')
        print(f'live store : {live}  ({len(st["live_files"])} memories)')
        print(f'mirror     : context/memory/store/  ({len(st["mirror_files"])} memories)')
        print(f'divergence : +{len(st["added"])} ~{len(st["changed"])} -{len(st["removed"])}'
              + ('   ORPHANED' if st['orphaned'] else ''))
        return 0
    if '--push' in argv:
        return push(dry_run='--dry-run' in argv, quiet='--quiet' in argv)
    print(__doc__.strip().splitlines()[0])
    print('usage: python context/memory/sync.py [--push [--dry-run] | --restore [--force] | --status]')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
