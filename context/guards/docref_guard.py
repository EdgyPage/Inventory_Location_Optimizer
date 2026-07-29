"""docref_guard.py — keeps cross-references to a document's SECTIONS from rotting.

Prose across this repo points at numbered sections of Markdown docs: "see CLAUDE.md section 5",
"CLAUDE.md §2 is canonical".  Those are anchors exactly like the `name@file` anchors in
`context/`, but nothing verified them: renumbering or retitling a section silently invalidates
every reference, and the reader is sent to the wrong place with no error anywhere.

That is the same failure mode as a stale memory path anchor -- the pointer still LOOKS right, so
it survives review.  There were 14 such references when this was written, all correct only because
they had just been checked by hand.

Recognised forms (the file may be any tracked .md, not just CLAUDE.md):
    <doc>.md section <N>        <doc>.md §<N>        <doc>.md §<Word>

A numbered reference must resolve to a heading that opens with that number; a word reference must
appear in some heading's text.  Anything else -- a bare mention of a filename, a link, a path --
is not a section reference and is ignored.

Run standalone:
    python context/guards/docref_guard.py --scan          # every tracked file; exit 1 on findings
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))

# "<something>.md" followed by a section marker and either a number or a CapitalisedWord.
# The doc name may be wrapped in backticks; the marker is the section sign or the literal word.
REF_RE = re.compile(r'`?([A-Za-z0-9_.-]+\.md)`?\s*(?:§\s*|[Ss]ection\s+)([0-9]+|[A-Z][A-Za-z-]+)')

# A numbered heading: "## 5. Git, paths, and memory" -> ('5', 'Git, paths, and memory')
HEAD_RE = re.compile(r'^#{1,6}\s+(?:(\d+)\.\s*)?(.+?)\s*$')

_SKIP_DIRS = ('docs/architecture/', 'context/arch/site_assets/', 'site/')
_TEXT_EXT = ('.md', '.py', '.yml', '.yaml', '.txt')


def headings(md_text: str) -> tuple[set[str], list[str]]:
    """(section numbers, heading titles) declared in a Markdown document."""
    numbers, titles = set(), []
    for line in md_text.splitlines():
        m = HEAD_RE.match(line)
        if not m:
            continue
        if m.group(1):
            numbers.add(m.group(1))
        titles.append(m.group(2))
    return numbers, titles


def _docs_by_basename() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in tracked_files():
        if rel.endswith('.md') and not any(rel.startswith(d) for d in _SKIP_DIRS):
            # A basename collision would make resolution ambiguous; prefer the shallowest path,
            # which is what a bare "CLAUDE.md" in prose means.
            base = os.path.basename(rel)
            if base not in out or rel.count('/') < out[base].count('/'):
                out[base] = rel
    return out


def tracked_files() -> list[str]:
    r = subprocess.run(['git', 'ls-files'], cwd=_ROOT, capture_output=True, text=True)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def scan_text(text: str, docs: dict[str, str], cache: dict[str, tuple]) -> list[tuple[int, str]]:
    """Return [(lineno, problem)] for section references that do not resolve."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for doc, ref in REF_RE.findall(line):
            rel = docs.get(doc)
            if rel is None:
                out.append((i, f'{doc} §{ref} — no such document in the repo'))
                continue
            if rel not in cache:
                with open(os.path.join(_ROOT, rel), encoding='utf-8', errors='replace') as fh:
                    cache[rel] = headings(fh.read())
            numbers, titles = cache[rel]
            if ref.isdigit():
                if ref not in numbers:
                    have = ', '.join(sorted(numbers, key=int)) or '(none)'
                    out.append((i, f'{doc} §{ref} does not exist — it has sections {have}'))
            elif not any(ref.lower() in t.lower() for t in titles):
                out.append((i, f'{doc} §{ref} matches no heading in {rel}'))
    return out


def verify(quiet: bool = False) -> int:
    docs, cache = _docs_by_basename(), {}
    findings, refs = [], 0
    for rel in tracked_files():
        if not rel.endswith(_TEXT_EXT) or any(rel.startswith(d) for d in _SKIP_DIRS):
            continue
        try:
            with open(os.path.join(_ROOT, rel), encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError:
            continue
        refs += len(REF_RE.findall(text))
        findings += [(rel, ln, why) for ln, why in scan_text(text, docs, cache)]
    if findings:
        if not quiet:
            print(f'docref DRIFT - {len(findings)} broken section reference(s):')
            for rel, ln, why in findings[:20]:
                print(f'  {rel}:{ln}  {why}')
            print('A section was renumbered or retitled; update the references or restore the '
                  'heading.')
        return 1
    if not quiet:
        print(f'docref OK - {refs} section reference(s) across '
              f'{len(docs)} document(s) all resolve.')
    return 0


def main(argv: list[str]) -> int:
    return verify(quiet='--quiet' in argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
