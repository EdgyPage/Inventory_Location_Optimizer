---
name: case-only-rename-deletes-its-own-page
description: renaming a symbol so its arch page filename differs only in case makes render_html --build write the page and then delete it on Windows; a second --build fixes it
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T17:48:38.901Z
---

Renaming a symbol so that its generated node page differs from the old one **only in
case** — `SEED_WORLD` → `seed_world`, `N_BATCHES` → `n_batches` — makes
`context/arch/render_html.py --build` emit the new page and then **delete it**. The
stale-cleanup step resolves the retired `...__SEED_WORLD.html` to the same file as the
just-written `...__seed_world.html` on a case-insensitive filesystem, so the delete lands
on the new page.

**Why:** the result is a site that is missing a page it believes it wrote —
`site_manifest.json` lists the new name, the tree does not have it. It is not silent:
`python context/arch/verify_site.py --fast` reports `tree/manifest file set differs
(missing: [...])`. But the message names the *symptom*, and nothing points at the rename
as the cause, so the obvious readings (a render failure, a stale manifest, a bad
sanitizer) all send you the wrong way. Observed 2026-08-24 retiring five import-time
scalars for call-time accessors; the reported "missing" list was also shorter than the
real one, which made it look like a partial failure rather than a systematic one.

**How to apply:** just run `render_html.py --build` a **second** time. The previous
manifest no longer carries the old-cased names, so nothing is cleaned up and the pages
survive. Then `verify_site.py --fast` passes. Worth doing unprompted for any commit that
renames an `UPPER_CASE` module symbol to `lower_case`, which the arch resync chain in
CLAUDE.md does not mention. Related: [[no-machine-local-paths]].
