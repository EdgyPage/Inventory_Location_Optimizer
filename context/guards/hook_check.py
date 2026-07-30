"""hook_check.py — the path guard's hook wiring.

`--pre-write` is THE ONE HOOK IN THIS REPO ALLOWED TO FAIL A CALL.  Every other hook here
(context/arch, Optimization/runschema, context/memory) is advisory and always exits 0.  That
exception is deliberate: "never write a machine-local path into a tracked file" cannot be
satisfied by a message printed after the write has already happened.

Three rules keep the exception from becoming a foot-gun:

  FAIL OPEN.  Any internal error, unreadable payload, or missing dependency exits 0 and permits
    the write.  A crashing guard must never brick every write in a session.  It is a floor, not
    the only line of defence -- --stop and `path_guard.py --scan` catch what it misses.
  SCOPED.  Only files inside the repo that git does not ignore, plus the live memory store.
    Scratchpad, .env, and run-output writes are exempt: session paths belong there.
  CHEAP.  Regex over one payload plus one cached `git check-ignore` call.

Wired via .claude/settings.json:
    PreToolUse (matcher Write|Edit) -> python context/guards/hook_check.py --pre-write
    Stop                            -> python context/guards/hook_check.py --stop

Run standalone:  python context/guards/hook_check.py --stop
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))

BLOCK = 2          # PreToolUse: exit 2 denies the call and returns stderr to the model
ALLOW = 0


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _in_scope(path: str) -> bool:
    """True if a write to `path` may not contain machine-local paths.

    In scope: tracked-or-trackable files in the repo, and the live memory store (which is
    mirrored INTO the repo, so a leak there reaches git one --push later).
    Out of scope: anything git ignores, and anything outside both trees.
    """
    if not path:
        return False
    ap = os.path.abspath(path)
    if os.sep + '.claude' + os.sep + 'projects' + os.sep in ap and ap.endswith('.md'):
        return 'memory' in ap.replace('\\', '/').split('/')
    try:
        rel = os.path.relpath(ap, _ROOT)
    except ValueError:                      # different drive
        return False
    if rel.startswith('..'):
        return False
    r = subprocess.run(['git', 'check-ignore', '-q', rel], cwd=_ROOT,
                       capture_output=True, text=True)
    return r.returncode != 0                # 0 == ignored, 1 == not ignored


def _pre_write() -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ''
    if not raw.strip():
        return ALLOW                        # nothing to inspect -> permit
    payload = json.loads(raw)
    ti = payload.get('tool_input') or {}
    path = ti.get('file_path') or ti.get('notebook_path') or ''
    if not _in_scope(path):
        return ALLOW

    # Whatever this call would put INTO the file.  Edit sends new_string, Write sends content,
    # MultiEdit sends a list of edits.
    chunks = [ti.get('content'), ti.get('new_string'), ti.get('new_source')]
    for e in (ti.get('edits') or []):
        if isinstance(e, dict):
            chunks.append(e.get('new_string'))
    text = '\n'.join(c for c in chunks if isinstance(c, str))
    if not text:
        return ALLOW

    guard = _load(os.path.join(_HERE, 'path_guard.py'), 'path_guard')
    findings = guard.scan_text(text)
    if not findings:
        return ALLOW

    shown = '; '.join(f'[{cat}] {ex}' for _, cat, ex in findings[:3])
    rel = os.path.relpath(os.path.abspath(path), _ROOT).replace('\\', '/')
    if rel.startswith('..'):                # the live memory store — name it, don't echo its path
        rel = 'the memory store (' + os.path.basename(path) + ')'
    sys.stderr.write(
        'BLOCKED by context/guards/path_guard.py: this write puts a machine-local path into '
        + rel + ' -- ' + shown
        + '\nTracked files and memories are public. Name the .env key (COMPARISON_OUTPUT_DIR, '
          'PROFILE_INPUT_DIR) or use a ~/-relative form. See CLAUDE.md section 5.\n')
    return BLOCK


def _stop() -> int:
    """Backstop for writes that never went through PreToolUse — a Bash heredoc, an external
    editor, a `git checkout`.  Scans only what changed in the working tree, so it stays cheap."""
    r = subprocess.run(['git', 'status', '--porcelain', '-uall'], cwd=_ROOT,
                       capture_output=True, text=True)
    changed = [ln[3:].strip().strip('"') for ln in r.stdout.splitlines() if ln[3:].strip()]
    changed = [c.split(' -> ')[-1] for c in changed][:200]
    if not changed:
        return ALLOW
    guard = _load(os.path.join(_HERE, 'path_guard.py'), 'path_guard')
    hits = [(p, f) for p in changed for f in guard.scan_file(p)]
    if hits:
        files = sorted({p for p, _ in hits})
        print(f'[paths] {len(hits)} machine-local path(s) in {len(files)} changed file(s): '
              + ', '.join(files[:3]) + (' ...' if len(files) > 3 else '')
              + ' — check: python context/guards/path_guard.py --scan')

    # Section references only break when a heading is renumbered or retitled, so this runs only
    # when a Markdown file changed -- which is exactly when it can break.
    if any(p.endswith('.md') for p in changed):
        docref = _load(os.path.join(_HERE, 'docref_guard.py'), 'docref_guard')
        if docref.verify(quiet=True) != 0:
            print('[docref] a "<doc>.md section N" reference no longer resolves — a heading was '
                  'renumbered or retitled: python context/guards/docref_guard.py --scan')
    return ALLOW


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else '--stop'
    try:
        if mode == '--pre-write':
            return _pre_write()
        return _stop()
    except SystemExit:
        return ALLOW
    except Exception:
        # FAIL OPEN, always.  A guard that cannot run must not stop the session working.
        return ALLOW


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
