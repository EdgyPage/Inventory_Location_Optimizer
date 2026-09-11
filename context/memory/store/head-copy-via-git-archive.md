---
name: head-copy-via-git-archive
description: "`git worktree add` fails on this repo (generated filenames too long for Windows), so a pristine HEAD copy for golden-digest computation comes from `git archive HEAD | tar -x`; and the working copy mixes CRLF and LF, so multi-line exact-string patches must normalize newlines"
metadata: 
  node_type: memory
  type: project
  originSessionId: 48201b20-3ad3-4571-ba31-3d33d30eeb7c
  modified: 2026-09-11T04:29:40.076Z
---

Two machine facts hit on 2026-09-10 while pinning the golden drain-by-drain digests in
`Tests/unit/test_supplier_lead_queue.py` (the archive comparison for inbound-optimization 27):

- **`git worktree add <dir> HEAD` fails part-way** with "Filename too long" on the generated
  `docs/architecture/nodes/*.html` and `docs/experiments/**` paths, leaving a partial checkout and a
  registered worktree (`git worktree prune` afterwards). A pristine HEAD copy that only needs the
  Python packages comes from `mkdir -p <dir> && git archive HEAD | tar -x -C <dir>`, run from the
  repo root, then `sys.path[:0] = [<dir>, <dir>/Tests/unit]` in the computing script.
- **The working copy is mixed CRLF/LF** (autocrlf; e.g. `Inbound/transit.py` is CRLF, `era_coverage.py`
  and the `.scratch/` tracker files are LF). A patch script matching a multi-line exact string
  silently finds zero hits on a CRLF file while single-line anchors still match. Read bytes,
  normalize `\r\n` to `\n`, patch, and write back in the file's original ending.

**Why:** both fail quietly in the middle of a build (the worktree leaves a half-copied tree that
still imports; the patch reports "0 hits" only if you assert the count), and neither is visible
from the code.

**How to apply:** for any "compare against HEAD's code" proof (golden digests, lockstep against the
archive), reach for `git archive` first; for scripted edits, use the `_read`/`_write` normalization
pair or the Edit tool. Related: [[no-triple-single-quotes-in-bash-heredocs]],
[[lockstep-tests-compare-aggregates-only]].
