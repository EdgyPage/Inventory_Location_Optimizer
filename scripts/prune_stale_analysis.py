"""prune_stale_analysis.py — delete analysis output the CURRENT contract cannot have written.

Re-analysing an existing run with newer code writes the new layout beside the old one: nothing
in the pipeline removes a folder the current evaluations no longer produce, because the parent
pre-pass only wipes the tops it is told about.  A tree re-analysed across a layout change
therefore carries both — the live outputs and an orphaned copy of the previous suite, which
costs disk, confuses a reader browsing the tree, and shows up as a wall of `undeclared`
findings the next time anyone validates the run.

The rule this applies, in two steps so a tree LEVEL is never mistaken for an orphan:

  PROTECT   every directory whose name a HEAD template spells literally (figures, tables, and
            each family folder), plus all of its ancestors — an ancestor leads to live output
            even though its own name is run-chosen and unknowable from the contract.
  PRUNE     a directory that is not protected, whose parent is, AND which has at least one
            protected SIBLING — so an orphan is removed only where its replacement is
            demonstrably sitting beside it.

That sibling clause is the load-bearing one.  Without it, a stage that has not run yet (an
interrupted analysis, or a cell caught between the config and aggregate stages) presents its
previous output with no replacement in sight, and the tool would happily delete the only copy.

HEAD, deliberately, not the run's own stamped contract — the question here is "could the
analysis code that just ran have written this?", and the run's stamped contract predates that
code (it is the contract the SIMULATION was written with, which is why validating a re-analysed
tree against it reports the NEW files as undeclared).

Files are never touched, only whole directories.  The tool refuses to act on a tree that holds
no current-layout output at all, because there every directory would look stale — that state
means "the analysis has not been re-run yet", not "delete everything".  Dry-run is the default;
`--apply` is required to delete.

    python scripts/prune_stale_analysis.py <run_or_cell_dir>            # report only
    python scripts/prune_stale_analysis.py <run_or_cell_dir> --apply    # delete
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:                      # entry-script bootstrap (CLAUDE.md section 2)
    sys.path.insert(0, _ROOT)

from Optimization.runschema import contract    # noqa: E402

_PLACEHOLDER = re.compile(r'\{\w+\??\}')
#: Reserved trees this tool never walks into: they are declared wholesale and are not analysis
#: output (the viewer cache, the frozen shared assets, the runtime metrics sidecar).
_SKIP = {'_viz', '_frozen', '_runtime', '__pycache__'}


def declared_dir_names(doc: dict) -> set[str]:
    """Every literal directory-segment name any HEAD template can render.

    A segment that is entirely a placeholder renders to a run-chosen name (a cell, a pair) and
    tells us nothing; a segment carrying literal text is a name the contract owns.
    """
    names: set[str] = set()
    for spec in doc['artifacts'].values():
        path = spec.get('path')
        if not path:
            continue
        for seg in path.split('/')[:-1]:                  # directory segments only
            if seg in ('*', '**') or not seg:
                continue
            if _PLACEHOLDER.fullmatch(seg):
                continue                                   # a tree level, not a fixed name
            names.add(_PLACEHOLDER.sub('', seg) or seg)
    return names


def _protected(base: str, known: set[str]) -> set[str]:
    """Every directory that IS, or leads to, output the current contract declares."""
    keep: set[str] = set()
    for dirpath, dirs, _files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        if os.path.basename(dirpath) in known:
            p = os.path.abspath(dirpath)
            stop = os.path.abspath(base)
            while True:
                keep.add(p)
                if p == stop or os.path.dirname(p) == p:
                    break
                p = os.path.dirname(p)
    return keep


def find_stale(base: str) -> list[str]:
    """Stale subtree ROOTS under `base`, or [] — raising when the tree has no live output."""
    doc = contract.load(contract.head()) or contract.build()
    known = declared_dir_names(doc)
    keep = _protected(base, known)
    if not keep:
        raise RuntimeError(
            'no directory here matches the current contract, so nothing can be judged stale — '
            're-run the analysis on this tree first (this guard is what stops a not-yet-'
            'analysed tree from looking entirely obsolete)')
    stale, unreplaced = [], []
    for dirpath, dirs, _files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        if os.path.abspath(dirpath) not in keep:
            continue                                   # only descend through live output
        orphans = [d for d in sorted(dirs) if os.path.abspath(os.path.join(dirpath, d))
                   not in keep]
        replaced = len(orphans) < len(dirs)             # a protected sibling is present
        for name in orphans:
            (stale if replaced else unreplaced).append(os.path.join(dirpath, name))
    for p in unreplaced:
        print(f'  SKIP    {os.path.relpath(p, base)} — nothing current beside it; the stage '
              f'that would replace it has not run on this tree')
    return stale


def _size_mb(path: str) -> float:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total / 1e6


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base', help='a run root or a single cell directory')
    ap.add_argument('--apply', action='store_true',
                    help='delete (default is a dry run that only reports)')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.base):
        print(f'not a directory: {args.base}')
        return 2
    stale = find_stale(args.base)
    if not stale:
        print('nothing stale — every analysis directory is one the current contract declares')
        return 0

    total = 0.0
    for p in stale:
        mb = _size_mb(p)
        total += mb
        print(f'  {"DELETE" if args.apply else "stale "}  {os.path.relpath(p, args.base)}'
              f'  ({mb:,.1f} MB)')
        if args.apply:
            shutil.rmtree(p, ignore_errors=True)
    verb = 'deleted' if args.apply else 'would delete'
    print(f'{verb} {len(stale)} directory(ies), {total:,.1f} MB'
          + ('' if args.apply else '  — re-run with --apply'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
