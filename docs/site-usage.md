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

## Adding a new experiment (schema-driven)

Each experiment is a self-contained folder under `docs/experiments/<exp>/` driven by one
**`experiment.yml`** manifest (the single source of truth for its run id, inventory keys→ids,
calibrations, figures, winners). The macros read the manifest, so the pages carry **no
hard-coded run IDs** — you declare params once and the pages "just work". (Experiment 1
predates this and uses the older explicit-ID style; both render fine.)

The loop:

1. **Scaffold** the folder from the template:
   ```bash
   python scripts/new_experiment.py --name experiment-2 --source "D:/…/comparison_YYYYMMDD_HHMMSS"
   ```
   With `--source` it also runs the ingest below and generates a starter `experiment.yml`.
   Without it, you get the template + a manifest stub to fill by hand.

2. **Fill `experiment.yml`** — title, short inventory keys, `winners`, labels (ingest fills
   `run`, ids, and `height_brackets` from the run's `run_manifest.json`).

3. **Ingest** the run's files from the drive (idempotent; `--dry-run` to preview):
   ```bash
   python docs/experiments/ingest.py --exp experiment-2 --source "D:/…/comparison_…"
   ```
   This pulls each inventory × config `config.json` + the curated PNGs, plus `params.json` and
   the catalogue distribution plots — matching the figure schema in the manifest.

4. **Add the nav block** the scaffold printed under `nav:` in `mkdocs.yml`, then
   `mkdocs serve`. Confirm the ingested images aren't gitignored (`git check-ignore …`).

New simulation runs emit `run_manifest.json` (run root) and `height_brackets` inside each
`config.json`, so the ingest is fully automatic. Older runs without those still work — ingest
falls back to the IDs you list in `experiment.yml`, and `height_brackets` can be filled by hand.

The template lives at `docs/experiments/_template/` (excluded from the build); its pages use
manifest-driven macros (short inventory keys, e.g. `{{ '{{' }} setup_table('lt0') {{ '}}' }}`)
and Jinja loops over `experiment().inventories`.

## Adding a run write-up (legacy)

The full generate → analyse → publish workflow is in [`authoring.md`](authoring.md) (also
kept in the repo, off the live site), with `example-run.md` as a page template.
