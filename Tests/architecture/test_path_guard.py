"""test_path_guard.py

Locks context/guards/path_guard.py: no machine-local filesystem path may reach a tracked file.
A tracked file is public, and context/memory/store/ (the memory mirror) is tracked with the rest,
so a drive letter or username committed here leaks the shape of one machine into git history --
where it cannot be taken back.

Locked-in invariants:
  1. Every tracked file scans clean.  This is the standing baseline; it was brought to zero in
     082b758 rather than allowlisted, so a new finding is always a real regression.
  2. The detector is NON-VACUOUS in both directions: it catches every machine-local form we have
     actually seen in this repo, and it rejects the specific look-alikes that have caused false
     positives (a JSON-escaped newline in a docstring, a bare originSessionId, slash-separated
     prose).  Invariant 1 would pass trivially against a broken matcher without this.
  3. ALLOW stays small and every entry names a real file.

EVERY fixture below is assembled by concatenation from `D` (a synthetic drive) and `BS`
(a backslash).  Writing a literal example would make THIS FILE a finding of the very scan
asserted in invariant 1 -- the guard's own module and README follow the same rule.

Run:  python -m pytest Tests/architecture/test_path_guard.py -q
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BS = chr(92)
D = 'Z:'          # a drive letter that is not this machine's
S = '/'


def _guard():
    path = os.path.join(_ROOT, 'context', 'guards', 'path_guard.py')
    spec = importlib.util.spec_from_file_location('path_guard_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── invariant 2a: forms the guard MUST catch ─────────────────────────────────────────────
MUST_CATCH = {
    'windows plain':      'root = ' + D + BS + 'Data' + BS + 'runs' + BS + 'x.db',
    'windows escaped':    'root = "' + D + BS + BS + 'Data' + BS + BS + 'runs' + BS + BS + 'x.db"',
    'windows forward':    'root = "' + D + S + 'Data' + S + 'runs' + S + 'x.db"',
    'posix home':         'cd ' + S + 'home' + S + 'example' + S + 'proj',
    'posix users':        'p = "' + S + 'Users' + S + 'example' + S + 'proj"',
    'git-bash drive':     'p = ' + S + S + 'z' + S + 'Users' + S + 'example',
    'unc share':          'p = ' + BS + BS + 'fileserver' + BS + 'share',
    'session path':       'tmp' + S + 'claude' + S + 'proj' + S
                          + '49b1dcd0-98e3-44f0-98bb-1a59de429667' + S + 'scratchpad',
}

# ── invariant 2b: look-alikes the guard MUST NOT catch ───────────────────────────────────
# Each of these is real content from this repo that an earlier, looser pattern flagged.
MUST_IGNORE = {
    'repo-relative path':  'Warehouse' + S + 'placement' + S + 'Assignment_Functions.py',
    'tilde form':          '~' + S + '.claude' + S + 'plans' + S + 'x.md',
    'env key':             'read COMPARISON_OUTPUT_DIR and PROFILE_INPUT_DIR from .env',
    'json escaped newline': '"doc": "...for DB storage.' + BS + 'n' + BS + 'nEvery event is"',
    'bare session id':     '  originSessionId: 4489d670-51cd-451a-b42f-5e7cc534ea9e',
    'slash-separated prose': 'a drive' + S + 'home' + S + 'username path is forbidden',
    'url tail':            'https:' + S + S + 'example.com' + S + 'home' + S + 'user',
    'ratio-ish text':      'the a:b/c ratio held',
}


def test_detector_catches_every_known_machine_local_form():
    g = _guard()
    missed = [label for label, text in MUST_CATCH.items() if not g.scan_text(text)]
    assert not missed, (
        'path_guard failed to catch machine-local forms it has seen in this repo: '
        f'{missed}. A miss here means invariant 1 (the clean scan) is vacuous.')


def test_detector_ignores_the_known_false_positives():
    g = _guard()
    wrong = {label: g.scan_text(text) for label, text in MUST_IGNORE.items() if g.scan_text(text)}
    assert not wrong, (
        'path_guard flagged content that is NOT a machine-local path: '
        f'{sorted(wrong)}. Each of these is real repo content that a looser pattern flagged; '
        'a false positive here BLOCKS writes, so it is worse than a miss.')


def test_every_tracked_file_is_clean():
    g = _guard()
    targets = g.tracked_files()
    assert len(targets) > 500, f'implausibly few tracked files ({len(targets)}) — scan is broken'
    findings = [(p, f) for p in targets for f in g.scan_file(p)]
    shown = '; '.join(f'{p}:{ln} [{cat}] {ex}' for p, (ln, cat, ex) in findings[:8])
    assert not findings, (
        f'{len(findings)} machine-local path(s) in tracked files: {shown}. '
        'Name the .env key or use a ~/-relative form; see CLAUDE.md section 5.')


def test_allowlist_is_small_and_points_at_real_files():
    g = _guard()
    assert len(g.ALLOW) <= 3, (
        f'ALLOW has grown to {len(g.ALLOW)} entries — every one switches the guard off for a '
        'file. Prefer fixing the file.')
    for rel, reason in g.ALLOW.items():
        assert os.path.exists(os.path.join(_ROOT, rel)), f'ALLOW names a missing file: {rel}'
        assert len(reason) > 30, f'ALLOW entry {rel} needs a real reason, not {reason!r}'


def test_username_is_never_hardcoded():
    """The guard derives the username at runtime; writing it down would leak it into git."""
    path = os.path.join(_ROOT, 'context', 'guards', 'path_guard.py')
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    user = os.path.basename(os.path.expanduser('~'))
    assert user.lower() not in src.lower(), (
        'path_guard.py contains the current username literally — it must derive it from '
        'expanduser("~") instead, or the guard becomes the first violation of its own rule.')
