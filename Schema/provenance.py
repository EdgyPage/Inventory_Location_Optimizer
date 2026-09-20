"""provenance.py — which CODE produced an artifact, derived without raising, git binary optional.

MOVED here from ``Optimization/runschema/sim_manifest.py`` (which re-exports for its existing
callers): the profiles-tree descriptor is written by ``Warehouse/generation`` — a layer that must
not import the run harness — and provenance stamping is not a run-tree concern to begin with.
``Schema/`` is the stdlib-only leaf every layer may import, and "what code wrote this file" is a
sibling question to "what shape is this file".

WHY THIS MUST NOT RAISE: it runs at the front of every simulation and every catalogue generation,
and a producer that dies because provenance could not be derived is strictly worse than one that
records ``unknown``.  A shallow clone, a source export with no ``.git``, a machine with no git on
PATH and a wedged index lock all resolve to a recorded value rather than an exception.

THE EXPORT STAMP, and why it is not a nicety.  Every campaign in this repo runs from a
``git archive`` copy, because a spawn-per-job run re-imports the tree for every unit and must not
see the working tree move under it (memory ``detached-runs-import-the-working-tree``).  An archive
carries no ``.git``, so the runs whose provenance matters MOST -- the multi-day ones nobody will
remember the details of -- were exactly the ones recording ``repo_commit: unknown``.  Caught
2026-09-20, on a phase-2 campaign launch.  So an export may carry ``.source_commit`` at its root
and this reads it when git metadata is absent; ``write_source_stamp`` is what puts it there.
"""
from __future__ import annotations

import os
import subprocess

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

#: `git status --porcelain` gets this long to answer before dirtiness is recorded as
#: "not established".  Provenance is best-effort; a wedged git must not stall a producer.
_GIT_TIMEOUT = 10.0


def _git_dir(repo_root: str) -> str | None:
    """The `.git` directory for `repo_root`, or None when there is no git metadata at all.

    `.git` is a DIRECTORY in a normal clone and a FILE holding `gitdir: <path>` in a linked
    worktree or a submodule, which is why this is not a bare isdir() check.
    """
    p = os.path.join(repo_root, '.git')
    if os.path.isdir(p):
        return p
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                head = f.read().strip()
        except OSError:
            return None
        if head.startswith('gitdir:'):
            target = head.split(':', 1)[1].strip()
            target = target if os.path.isabs(target) else os.path.join(repo_root, target)
            return os.path.normpath(target)
    return None


def _head_commit(git_dir: str) -> str | None:
    """HEAD's full sha by reading files only — no subprocess, and no git binary required.

    A source export (a zip, a docker COPY) has no `.git` and returns None from _git_dir before we
    get here; a SHALLOW clone does have one and resolves normally, which is the case the naive
    `git describe` approach gets wrong.  Three shapes are handled: a detached HEAD (the sha
    inline), a loose ref, and a ref that only exists in `packed-refs` (a fresh clone's usual state).
    """
    try:
        with open(os.path.join(git_dir, 'HEAD'), encoding='utf-8') as f:
            head = f.read().strip()
    except OSError:
        return None
    if not head.startswith('ref:'):
        return head or None                                  # detached: HEAD is the sha itself
    ref = head.split(':', 1)[1].strip()
    try:
        with open(os.path.join(git_dir, *ref.split('/')), encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        pass
    try:
        with open(os.path.join(git_dir, 'packed-refs'), encoding='utf-8') as f:
            for line in f:
                if line.startswith(('#', '^')):
                    continue
                sha, _, name = line.strip().partition(' ')
                if name == ref:
                    return sha or None
    except OSError:
        pass
    return None


#: An export's own record of what it was cut from, read when there is no git metadata.
#: One line: the sha, optionally followed by the word `dirty` when the export carries edits on
#: top of that commit (a probe snapshot with files overlaid, which this session made several
#: of).  Plain text rather than JSON so a human reading a snapshot directory can answer "what
#: is this?" with `cat`.
SOURCE_STAMP = '.source_commit'


def _stamped(repo_root: str) -> dict | None:
    """`repo_provenance`'s answer from an export stamp, or None when there is no stamp."""
    try:
        with open(os.path.join(repo_root, SOURCE_STAMP), encoding='utf-8') as f:
            text = f.read().strip()
    except OSError:
        return None
    if not text:
        return None
    parts = text.split()
    # An archive is an export of a COMMIT, so it is clean by construction unless the stamp says
    # otherwise.  That is a stronger answer than `None`, and it is the true one.
    return {'repo_commit': parts[0][:12], 'repo_dirty': 'dirty' in parts[1:]}


def write_source_stamp(dest_root: str, sha: str, *, dirty: bool = False) -> str:
    """Write `dest_root/.source_commit` so an export can say what it came from.

    Called by whatever cuts the export: `git archive` has no hook for this, and an exporter
    that forgets is exactly how a campaign ends up recording `unknown`.
    """
    path = os.path.join(dest_root, SOURCE_STAMP)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'{sha}{" dirty" if dirty else ""}\n')
    return path


def repo_provenance(repo_root: str = _REPO_ROOT) -> dict:
    """{'repo_commit': <short sha> | 'unknown', 'repo_dirty': True | False | None}.

    `repo_dirty` is deliberately THREE-valued: True/False are answers, `None` means "not
    established" — which is what an export or a missing git binary honestly is, and reading it as
    "clean" would be the one wrong inference.
    """
    git_dir = _git_dir(repo_root)
    if git_dir is None:
        # Git metadata WINS when both are present: a clone that also carries a stamp is a
        # clone, and its working tree is the truth about what is running.
        return _stamped(repo_root) or {'repo_commit': 'unknown', 'repo_dirty': None}
    sha = _head_commit(git_dir)
    dirty: bool | None = None
    try:
        r = subprocess.run(['git', 'status', '--porcelain'], cwd=repo_root,
                           capture_output=True, text=True, timeout=_GIT_TIMEOUT)
        if r.returncode == 0:
            dirty = bool(r.stdout.strip())
    except Exception:                        # noqa: BLE001 - provenance is best-effort, always
        dirty = None
    return {'repo_commit': (sha[:12] if sha else 'unknown'), 'repo_dirty': dirty}
