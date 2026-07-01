# Site usage — tips & tricks (not published)

Working notes for maintaining this MkDocs site. This page is **kept in the repo but excluded
from the built site** (see `exclude_docs` in `mkdocs.yml`), so it never appears publicly.

## Verifying a page actually rendered — the "sentinel" trick

When you push a change you want to confirm the live site really rebuilt and picked it up
(GitHub Pages can serve a stale cache, and a broken build can leave the old page up). A quick
way is a **sentinel**: a visible admonition holding a version string you bump each time.

Paste this at the top of a *draft* page while you're iterating:

```markdown
!!! success "Render check"
    If you can read this, MkDocs built and served this page. **Sentinel:** `PAGE-LIVE-v1`.
```

Then: change `v1` → `v2`, push, and refresh the deployed page. If you see `v2`, the redeploy
landed; if you still see `v1` (or the box is gone/unstyled), the build failed or the cache is
stale. **Remove these boxes before the page is considered done** — they're scaffolding, not
content, and shouldn't ship on public pages. (That's why they live only here now.)

Related quick checks:

- `mkdocs build --strict` locally — fails on broken links, missing images, or a macro error,
  which is what usually breaks a deploy.
- `mkdocs serve` — live preview at <http://127.0.0.1:8000>.

## Where plots live / what to commit

- **Curated plots** are committed under each experiment's image tree, e.g.
  `docs/experiments/experiment-1/images/`. Reference them with page-relative paths
  (`![alt](images/…/plot.png)`), and click any image to zoom (glightbox).
- **Do not commit raw run data.** The SQLite run DBs (`sim_*.db`, `warehouse.db`, etc.) are
  huge and are gitignored (`*.db`, `comparison_*/`). Only the small PNGs + `config.json` /
  `params.json` snapshots are committed — the docs `comparison_*` image subtree is re-included
  by an explicit negation in `.gitignore`. Confirm new images are tracked with
  `git check-ignore <path>` before pushing (CI has no access to the run drive, so anything
  untracked is simply missing from the built site).

## Adding a run write-up

The full generate → analyse → publish workflow is in [`authoring.md`](authoring.md) (also
kept in the repo, off the live site), with `example-run.md` as a page template.
