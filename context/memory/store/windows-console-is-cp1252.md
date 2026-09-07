---
name: windows-console-is-cp1252
description: "a Python patch script that prints a non-ASCII character (λ, ·, box-drawing) to this machine's console dies with UnicodeEncodeError under cp1252 AFTER writing its first edits -- export PYTHONIOENCODING=utf-8 before running any script that prints repo text"
metadata: 
  node_type: memory
  type: project
  originSessionId: 47aa5709-16a2-4dbd-9bc6-203bbedefe04
  modified: 2026-09-07T00:43:47.523Z
---

This machine's Python console encoding is cp1252, not UTF-8. A script that echoes a matched
line from the repo (the docstrings and comments are full of `λ`, `·`, `—` and box-drawing
banners) raises `UnicodeEncodeError` at the `print`, not at the file write, so a multi-step
patch script half-applies: the edits before the offending print are on disk, the rest are not,
and a naive rerun then fails on "expected 1 match, found 0" for the ones already made.

**Why:** hit 2026-09-06 while routing the line law through `Demand.line` — the second
replacement's echo contained `λ`; the first two edits landed, eight did not.

**How to apply:** `export PYTHONIOENCODING=utf-8` in the same shell line before `python
patch.py`, and make replacement helpers idempotent (skip when `new` is already present) so a
rerun is safe. Related: [[no-triple-single-quotes-in-bash-heredocs]] (write patch scripts to
a file rather than a heredoc), [[no-unicode-escapes-in-heredoc-python]].
