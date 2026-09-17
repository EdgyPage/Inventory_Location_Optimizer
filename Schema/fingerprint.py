"""fingerprint — the source-hashing half that three content-addressed stores shared by COPY.

`runschema/contract.py`, `Schema/profile_tree.py` and `Schema/store_index.py` each carry a
`source_fingerprint`, and two of the three say in their own docstring that they are a copy of the
first. `Schema/` may not import `Optimization/` (the declared boundary "shape identity must not
depend on the run harness"), so copying was the only way to share it -- and a copy nothing
compares is a copy that drifts.

**It drifted.** `runschema.contract` grew a SECOND input class -- `SHAPE_SOURCE_DIRS`, hashed by
name list and content, so an ADDED file registers in an auto-discovered directory -- and the two
copies did not. From that moment "same algorithm as `runschema.contract.source_fingerprint`" was
false, and `test_source_fingerprint_matches_the_runtree_implementation`, which asserts the copies
hold byte-for-byte, has been failing ever since
(`.scratch/architecture-drift/issues/05`).

## What that ticket calls this, and what it actually is

The ticket calls it "the dangerous one of the seven": two implementations disagreeing on
identical input, so "one caller population thinks a tree is current while another thinks it is
stale, with no error either way". That is not what the disagreement was.

Measured before fixing: with `SHAPE_SOURCE_DIRS` emptied, the two produce the SAME digest on the
same input -- the files half was always byte-identical, and 100% of the difference was the dirs
half that only one of them has. Neither computed a wrong answer for its own inputs; neither reads
the other's index; and on the real tree they hash different source LISTS anyway (nine files
against roughly twenty-five plus a directory), so they were never going to agree. The live hazard
the ticket describes -- a tree one caller thinks is current and another thinks is stale -- needs
two readers of ONE index, and there is no such pair.

So this is a false-equivalence bug, not a live fingerprint hazard: a docstring claiming an
identity that stopped being true, and a test asserting it. Recorded here because the severity is
what decides whether the next person drops everything for it, and the ticket's own answer to that
is wrong.

## The shape of the fix

`update_files` takes the hash object and folds the files into it IN PLACE, rather than returning
a digest the caller then re-hashes. That is deliberate and it is the whole reason this refactor
costs nothing: `contract` folds files and then directories into ONE hash, so a helper that
returned a digest would change its output, invalidate every recorded `source_fingerprint`, and
force a canary re-prove for a pure code move. In place, every one of the three callers emits the
byte-identical value it emitted before.
"""
from __future__ import annotations

import hashlib
import os

__all__ = ['update_files', 'update_dirs', 'of_files']


def update_files(h, rels, repo_root: str) -> None:
    """Fold `rels` (repo-relative paths, in declared order) into the hash `h`.

    Three properties, each of which a caller has been bitten by:

      * **Line endings are NORMALISED to `\\n`.** On Windows a checkout, a stash/pop or an
        `autocrlf` change rewrites CRLF<->LF without touching a statement; hashing raw bytes made
        that look like a structural change and cost a needless canary pair.
      * **A MISSING file contributes its path plus a marker** rather than being skipped, so
        DELETING a source is detected instead of silently matching.
      * **The path is hashed before the content**, so moving a file registers even when its bytes
        do not change.
    """
    for rel in rels:
        h.update(rel.encode('utf-8'))
        p = os.path.join(repo_root, rel.replace('/', os.sep))
        try:
            with open(p, 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))
        except OSError:
            h.update(b'\x00MISSING')


def update_dirs(h, rel_dirs, repo_root: str) -> None:
    """Fold whole DIRECTORIES of `.py` files into the hash `h`, by NAME LIST and then content.

    The input class a path tuple cannot describe: a tuple can only name files someone already
    thought of, and `Optimization/simconfig/configs/` is auto-discovered, so an ADDED pick config
    would register nowhere. Only `runschema.contract` has such an input -- which is the entire
    reason its fingerprint differs from the two file-only stores on shared input
    (`architecture-drift/05`), and why that difference is a DECLARED asymmetry, not drift.

    The NAME LIST is hashed BEFORE any content, so a RENAME registers even when no byte moves --
    renaming a pick config renames a run-tree directory.

    Content is normalised exactly as `update_files` normalises it, and it lives here so that ONE
    place decides what a line ending means. It was a fourth copy of that rule until this module.
    """
    for rel_dir in rel_dirs:
        d = os.path.join(repo_root, rel_dir.replace('/', os.sep))
        h.update(rel_dir.encode('utf-8'))
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith('.py'))
        except OSError:
            h.update(b'\x00MISSING')
            continue
        h.update(repr(names).encode('utf-8'))
        for n in names:
            with open(os.path.join(d, n), 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))


def of_files(rels, repo_root: str) -> str:
    """`update_files` on a fresh hash, as the two file-only stores want it.

    `contract` does NOT use this: it continues the same hash into its directory half, which is
    what keeps its emitted value unchanged across this refactor.
    """
    h = hashlib.sha256()
    update_files(h, rels, repo_root)
    return 'sha256:' + h.hexdigest()
