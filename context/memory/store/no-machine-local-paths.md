---
name: no-machine-local-paths
description: Never write a machine-local filesystem path into a memory or any git-tracked file
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-07-29T03:58:36.441Z
---

Never write a machine-local filesystem path into a memory or any git-tracked file: no
drive-letter absolutes, no home-directory absolutes, no username, no scratchpad or session-UUID
path. Name the `.env` key (`COMPARISON_OUTPUT_DIR`, `PROFILE_INPUT_DIR`) or use a `~/`-relative
form instead.

**Why:** requested directly on 2026-07-28, when the memory store was about to be mirrored into
git at `context/memory/store/`. A tracked file is public — it goes to GitHub — so a drive letter
or username leaks the shape of one machine and pins the repo to it. It also rots: three tracked
files carried such paths, and `Tests/bench/bench_sections.py` only worked on a machine with those
exact drive letters.

**How to apply:** `context/guards/path_guard.py` enforces it, so this is not advice — a
`PreToolUse` hook blocks the write and `sync.py --push` refuses to mirror a memory that violates
it. The guard derives the username at runtime and never stores it. If a fact genuinely needs a
location, record the `.env` key that holds it, as [[results-drive-location]] does. See
CLAUDE.md §5.

Corollary the guard cannot enforce: this applies to *content*, not to repo-relative paths —
`Warehouse/placement/Assignment_Functions.py` is required, not forbidden. The whole verified
anchor layer is built from repo-relative paths, and stale ones are caught by
`context/memory/verify_memory.py`.
