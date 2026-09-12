---
name: catalog-merge-seeds-a-docstring-fragment
description: "`extract.py --catalog-merge` fills a new file's `purpose` with a truncated docstring fragment instead of `purpose: TODO`, so it escapes the fill step and reads like a description while saying nothing — and a multi-line hand fill breaks test_merge_is_idempotent"
metadata: 
  node_type: memory
  type: project
  originSessionId: 68469de0-3988-452f-9eb3-bc8fb5e0c145
  modified: 2026-09-12T02:52:11.458Z
---

`python context/arch/extract.py --catalog-merge` is supposed to land a new file in
`context/files.yml` as `purpose: TODO`, which CLAUDE.md section 1's regeneration chain tells you
to fill by hand before the next step. **It does not.** It seeds the `purpose` with the first
clause of the module docstring — sometimes truncated mid-sentence — so the entry reads like a
description, says nothing, and **the fill step never sees it** because there is no `TODO` to grep
for.

Hit on site-dock tickets 11, 18, 19, and again on 20/22/23 (five files at once, one of them cut
off mid-sentence). Four separate sessions found it independently.

**And the hand fill has a constraint nothing states:** a **multi-line** YAML `purpose` breaks
`Tests/architecture/test_files_catalog_sync.py::test_merge_is_idempotent`, because the merger
re-emits it on one line and the round trip no longer matches. Write it as one long line, however
long.

**Why:** the catalogue is the thing a later reader greps to find out what a file is for. An entry
that was auto-seeded looks filled, so nobody refills it, and the rot is invisible — the verifier
is happy either way.

**How to apply:** after `--catalog-merge`, always `git diff context/files.yml` and rewrite every
new `purpose:` by hand, on ONE line. Do it before `--write-nodes`, because the node detail and the
HTML site are both generated from it. Related: [[arch-tier-is-red-on-head]],
[[case-only-rename-deletes-its-own-page]].
