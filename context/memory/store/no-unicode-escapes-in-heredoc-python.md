---
name: no-unicode-escapes-in-heredoc-python
description: "Writing \\uXXXX inside a bash-heredoc Python script lands as literal text in this repo's YAML and breaks catalog idempotency"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-07-29T01:58:07.780Z
---

When editing `context/files.yml` (or any repo YAML) from a `python - <<'PY'` heredoc, never write
`—` / `’` in a string literal — the sequence survives as six literal characters, and
`Tests/test_files_catalog_sync.py::test_merge_is_idempotent` then fails because
`extract.py --catalog-merge` re-dumps it as `\\u2014`.

**Why:** this has now bitten three separate times in this repo, each costing a failed suite run and
a resync cycle. It is invisible in the heredoc and only surfaces via the idempotency test.

**How to apply:** paste the real character (—, ’) directly, or build it with `chr(8212)`. To detect
existing damage: `grep -c 'u2014' context/files.yml` (17 accumulated before the sweep in `1c5f0ce`).
Prefer the `Edit` tool over heredoc scripts for prose edits — it takes literal text.

Related: [[results-drive-location]]
