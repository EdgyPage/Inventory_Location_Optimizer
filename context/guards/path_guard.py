"""path_guard.py — keeps machine-local filesystem paths out of tracked files and memories.

A tracked file is public: it goes to GitHub, and the memory mirror (context/memory/store/)
goes with it.  A drive letter, a home directory, or a username in that content leaks the
shape of one machine and pins the repo to it.  This module is the single detector; callers
are the PreToolUse hook (which BLOCKS a write), the Stop backstop, verify_memory.py, and the
`--scan` CLI below.

What is FORBIDDEN (see PATTERNS):
  drive-letter absolutes, home-directory absolutes, UNC shares, the current username, and
  session-scoped scratchpad/temp paths.

What is LEGAL, and deliberately so:
  repo-relative paths (`Warehouse/placement/Assignment_Functions.py`) — the entire verified
  anchor layer is built from them; `context/files.yml` alone is 167; `~/`-relative forms;
  the env-var forms `%USERPROFILE%` / `$HOME`; and .env KEY NAMES (COMPARISON_OUTPUT_DIR,
  PROFILE_INPUT_DIR), which are how a machine path is supposed to be referred to here.

The current username is derived at RUNTIME from the home directory — never written down.
Hardcoding it would make this file the first violation of its own rule.  For the same
reason every example in this module and its tests uses a synthetic value (Z:\\Users\\example).

Run standalone:
    python context/guards/path_guard.py --scan          # every tracked file; exit 1 on findings
    python context/guards/path_guard.py --scan PATH...  # just these
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
assert os.path.isdir(os.path.join(_ROOT, 'Warehouse')), (
    'path_guard._ROOT must be the repo root; this file lives at context/guards/, so it '
    'needs exactly two levels up. See CLAUDE.md section 3 on _REPO_ROOT depth.')

# ── the patterns ─────────────────────────────────────────────────────────────────────────
# Each is (category, compiled regex).  Kept deliberately narrow: a false positive here BLOCKS
# a write, so every pattern must require evidence of a real absolute path, not merely a colon
# or a slash.
PATTERNS = [
    # C:\Users\...  C:/Users/...  C:\\Users\\... (JSON/notebook-escaped)  //c/Users/... (git-bash).
    # Two segments are required, and the first must be >= 2 chars.  Both conditions earn their
    # keep against real content in this repo:
    #   - the separator is [\\/]{1,2} because notebooks and nodes.json store paths escaped, and a
    #     one-backslash-only rule misses `C:\\path\\to\\x.db` entirely — a real leak vector;
    #   - the {2,40} minimum is what still rejects `db:\n\nEvery ...`, a JSON-escaped newline in a
    #     docstring.  A lone escape letter is one char; a directory name is not.  Without it the
    #     guard blocks every write to context/arch/nodes.json forever.
    ('drive-absolute', re.compile(r'(?:^|[^A-Za-z0-9_])[A-Za-z]:[\\/]{1,2}'
                                  r'[A-Za-z0-9_$.~ -]{2,40}[\\/]{1,2}[A-Za-z0-9_$.~-]')),
    ('drive-absolute', re.compile(r'/{1,2}[a-zA-Z]/(?:Users|Data|home)/', re.I)),
    ('home-absolute',  re.compile(r'/(?:home|Users)/[A-Za-z0-9._-]+')),
    ('unc-share',      re.compile(r'\\\\[A-Za-z0-9._-]+\\[A-Za-z0-9._$-]+')),
    # A bare session UUID: how scratchpad/transcript paths identify one run of one machine.
    ('session-id',     re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                                  r'[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)),
]

# The username, resolved at runtime and never stored in this file.  Guarded against absurd
# values ('', 'root', 'user') that would match everywhere and block every write.
_USER = os.path.basename(os.path.expanduser('~')).strip()
if len(_USER) >= 4 and _USER.lower() not in ('root', 'user', 'users', 'home', 'administrator'):
    PATTERNS.append(('username', re.compile(r'\b' + re.escape(_USER) + r'\b', re.I)))

# ── the allowlist ────────────────────────────────────────────────────────────────────────
# Declared, with a reason, exactly as architecture.yml declares intent — never a silent skip.
# Keep this near-empty: every entry is a place the guard has been switched off.
ALLOW = {
    '.claude/settings.json':
        'Permission entries must name real interpreter/site-packages paths to match against; '
        'this file is machine-local config that only exists to be matched literally.',
}

# Binary/generated trees the scan never reads.  Not exemptions — these hold no prose.
_SKIP_DIRS = ('docs/architecture/', 'context/arch/site_assets/', 'site/')
_SKIP_SUFFIX = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.db', '.zip', '.woff', '.woff2')


def scan_text(text: str, *, skip_lines: set[int] | None = None) -> list[tuple[int, str, str]]:
    """Return [(lineno, category, offending_excerpt)] for machine-local paths in `text`.

    lineno is 1-based.  The excerpt is trimmed to 60 chars so a finding can be printed
    without echoing an entire line of someone's home directory into a log.
    """
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if skip_lines and i in skip_lines:
            continue
        for category, rx in PATTERNS:
            m = rx.search(line)
            if m:
                excerpt = m.group(0).strip()
                out.append((i, category, excerpt[:60]))
                break                      # one finding per line is enough to act on
    return out


def scan_file(relpath: str) -> list[tuple[int, str, str]]:
    """Scan one repo-relative file.  Allowlisted or binary files return no findings."""
    if relpath.replace('\\', '/') in ALLOW:
        return []
    rel = relpath.replace('\\', '/')
    if rel.endswith(_SKIP_SUFFIX) or any(rel.startswith(d) for d in _SKIP_DIRS):
        return []
    full = os.path.join(_ROOT, relpath)
    try:
        with open(full, encoding='utf-8', errors='replace') as fh:
            return scan_text(fh.read())
    except (OSError, UnicodeDecodeError):
        return []


def tracked_files() -> list[str]:
    """Every git-tracked file, repo-relative with forward slashes."""
    r = subprocess.run(['git', 'ls-files'], cwd=_ROOT, capture_output=True, text=True)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def verify(paths: list[str] | None = None, quiet: bool = False) -> int:
    """Scan `paths` (default: all tracked files).  Returns 0 clean, 1 on findings."""
    targets = paths if paths else tracked_files()
    findings = [(p, f) for p in targets for f in scan_file(p)]
    if findings:
        if not quiet:
            print(f'path guard FAILED - {len(findings)} machine-local path(s) in '
                  f'{len({p for p, _ in findings})} file(s):')
            for p, (ln, cat, excerpt) in findings[:25]:
                print(f'  {p}:{ln}  [{cat}]  {excerpt}')
            if len(findings) > 25:
                print(f'  ... and {len(findings) - 25} more')
            print('Name the .env key (COMPARISON_OUTPUT_DIR, PROFILE_INPUT_DIR) or use a '
                  '~/-relative form. See CLAUDE.md section 5.')
        return 1
    if not quiet:
        print(f'path guard OK - {len(targets)} file(s) scanned, '
              f'{len(ALLOW)} allowlisted, {len(PATTERNS)} pattern(s).')
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith('--')]
    quiet = '--quiet' in argv
    if '--scan' in argv or args:
        return verify(args or None, quiet=quiet)
    print(__doc__.strip().splitlines()[0])
    print('usage: python context/guards/path_guard.py --scan [PATH ...]')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
