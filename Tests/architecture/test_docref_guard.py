"""test_docref_guard.py

Locks context/guards/docref_guard.py: prose that points at a numbered document section
("see CLAUDE.md section 5", "CLAUDE.md §2 is canonical") must still point somewhere real.

These are anchors exactly like the `name@file` anchors in context/, and they fail the same silent
way: renumber or retitle a heading and every reference still LOOKS correct, so it survives review
while sending the reader to the wrong place. 14 such references existed when this was written,
across .py, .md and the memory mirror.

Locked-in invariants:
  1. Every section reference in a tracked file resolves.
  2. The checker is non-vacuous in both directions — it catches a renumbered section and a
     retitled one, and it does NOT fire on a bare filename mention that is not a section
     reference.
  3. CLAUDE.md's own section numbers are unique and contiguous from 1, since the references are
     numeric and a duplicate or gap makes them ambiguous.

Run:  python -m pytest Tests/architecture/test_docref_guard.py -q
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEC = chr(167)          # the section sign, built rather than typed so this file stays ascii-safe


def _guard():
    path = os.path.join(_ROOT, 'context', 'guards', 'docref_guard.py')
    spec = importlib.util.spec_from_file_location('docref_guard_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_section_reference_resolves():
    g = _guard()
    assert g.verify(quiet=True) == 0, (
        'a documented section reference no longer resolves — run '
        'python context/guards/docref_guard.py --scan for the list')


def test_reference_extraction_is_not_vacuous():
    """If the regex matched nothing, invariant 1 would pass trivially."""
    g = _guard()
    found = 0
    for rel in g.tracked_files():
        if not rel.endswith(('.md', '.py')):
            continue
        try:
            with open(os.path.join(_ROOT, rel), encoding='utf-8', errors='replace') as fh:
                found += len(g.REF_RE.findall(fh.read()))
        except OSError:
            continue
    assert found >= 10, (
        f'only {found} section references extracted repo-wide — the pattern is probably broken, '
        'which would make the resolution check vacuous')


def test_catches_a_renumbered_section():
    g = _guard()
    docs = {'FAKE.md': 'FAKE.md'}
    cache = {'FAKE.md': ({'1', '2'}, ['Alpha', 'Beta'])}
    bad = g.scan_text('see FAKE.md section 5 for details', docs, cache)
    assert bad, 'a reference to a section that does not exist was not caught'
    assert '5' in bad[0][1] and '1, 2' in bad[0][1], (
        f'the message should name the missing section and what does exist, got: {bad[0][1]}')


def test_catches_a_retitled_section():
    g = _guard()
    docs = {'FAKE.md': 'FAKE.md'}
    cache = {'FAKE.md': ({'1'}, ['Alpha'])}
    assert g.scan_text('see FAKE.md ' + SEC + 'Conventions', docs, cache), (
        'a word reference to a heading that no longer exists was not caught'
    )
    assert not g.scan_text('see FAKE.md ' + SEC + 'Alpha', docs, cache), (
        'a word reference that DOES match a heading was wrongly flagged')


def test_ignores_non_references():
    """A bare filename is not a section reference; flagging it would be noise."""
    g = _guard()
    docs = {'FAKE.md': 'FAKE.md'}
    cache = {'FAKE.md': ({'1'}, ['Alpha'])}
    for text in ('if the two disagree, FAKE.md wins and this list is stale',
                 'see [the doc](FAKE.md) for more',
                 'FAKE.md is the canonical copy'):
        assert not g.scan_text(text, docs, cache), f'wrongly flagged a non-reference: {text!r}'


def test_claude_md_sections_are_unique_and_contiguous():
    g = _guard()
    with open(os.path.join(_ROOT, 'CLAUDE.md'), encoding='utf-8') as fh:
        numbers, _ = g.headings(fh.read())
    nums = sorted(int(n) for n in numbers)
    assert nums, 'CLAUDE.md declares no numbered sections but is referenced by number'
    assert nums == list(range(1, len(nums) + 1)), (
        f'CLAUDE.md section numbers must be contiguous from 1, got {nums} — numeric references '
        'become ambiguous otherwise')
