---
name: renaming-a-local-needs-ast-positions
description: "A word-boundary regex rename still corrupts English comments and nothing fails; rewrite identifier tokens at AST-reported (lineno, col_offset) instead"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T14:28:43.381Z
---

Renaming a local across a large function — `triggered` to `bstate.triggered`, `mgr` to
`asm.mgr` — looks like a job for `re.sub(r'\bname\b', ...)`. It is not, even when the pattern
is scoped to one function's line range and anchored on word boundaries.

On a 36-site rename in `_build_leaf` (2026-09-17), word boundaries correctly skipped
`released_late`, `day_end=`, `put_base=` and `_note_triggered` — and still rewrote two English
comments: *"hands back what this one triggered"* became *"what this one bstate.triggered"*.
Caught by reading the diff. **No test failed, and none could have**: a comment is not code, and
the code the rename produced was correct.

**Why:** a regex cannot tell an identifier from the same letters in prose or in a string
literal. Word boundaries only remove the adjacent-identifier class of error; the prose class is
untouched, and it scales with the size of the rename.

**How to apply:** for anything beyond a handful of sites, walk the AST, collect the exact
`(lineno, col_offset)` of every `ast.Name` node whose `id` matches, and rewrite those positions
bottom-up so earlier edits do not shift later offsets. Comments and strings are then untouched
by construction, and the formatting survives (which `ast.unparse` would not). Read the diff
either way. Related: [[no-unicode-escapes-in-heredoc-python]],
[[symbol-table-relationship-not-verified-by-symbols]].
