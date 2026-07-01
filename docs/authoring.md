# Authoring guide

How to write up a results run and publish it to the site. Keep this page for reference.

!!! success "Render check"
    If you can read this green call-out box on the live site, MkDocs is building and
    serving the Markdown in `docs/` correctly. **Sentinel:** `AUTHORING-PAGE-LIVE-v1`.

---

## The short version

1. Generate plots from a completed run.
2. Copy the plots into `docs/results/images/`.
3. Copy `results/example-run.md` to a new page and write it up.
4. Add the new page to `nav:` in `mkdocs.yml`.
5. Preview locally, then push — the site redeploys automatically.

---

## 1. Generate the plots

Run the analysis on a finished simulation run (your run output lives on the external
drive):

```bash
python Optimization/run_analysis.py "<path-to-run-base-dir>"
```

This writes PNGs next to the run (e.g. `compare/`, `per_strategy/`, grid plots like
`grid_batch_duration.png`).

## 2. Add the images

Copy the plots you actually want to show into the site's image folder:

```
docs/results/images/
```

!!! warning "Don't commit the raw run data"
    The SQLite run DBs (`sim_*.db`, `warehouse.db`, `*.keyframes.db`) are large and are
    ignored by `.gitignore` (`*.db`, `comparison_*/`). Only commit the **PNG plots** you
    reference on a page.

## 3. Write the page

Copy the template and rename it:

```bash
cp docs/results/example-run.md docs/results/2026-06-run.md
```

Then edit the Markdown. Common building blocks:

**A captioned, zoomable image** (click to enlarge via the lightbox):

```markdown
<figure markdown>
  ![Batch duration](images/grid_batch_duration.png){ width=820 }
  <figcaption>Mean batch duration by strategy over 100 batches.</figcaption>
</figure>
```

**A plain image** (also zoomable):

```markdown
![Pick vs travel](images/pick_vs_travel.png)
```

**A results table:**

```markdown
| Strategy | Mean duration | vs. FIFO |
|----------|--------------:|---------:|
| FIFO     | 1190 | — |
| TripMin  | 1021 | −14% |
```

**Call-outs** for takeaways / caveats:

```markdown
!!! note
    TripMin cut mean batch duration by 14% vs. FIFO.

!!! warning
    MaxClu raised churn — note the trade-off.
```

## 3b. Pull setup parameters from JSON instead of retyping them

Don't hand-copy run parameters — the [`docs/macros.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/docs/macros.py)
helpers (via `mkdocs-macros-plugin`) read a committed `config.json` / `params.json`
snapshot and render it. So a page never states a value that can drift from the run.

1. **Commit the snapshot.** Copy the run's `config.json` next to its images
   (`docs/results/images/<run>/<inv>/<cfg>/config.json`) and, for a new inventory,
   its `inventory/params.json` to `docs/inventory/data/<variant>/params.json`. These are
   tiny; the large `*.db` run files stay off the repo.

   !!! warning "The `comparison_*` gitignore trap"
       Run folders are named `comparison_*`, which `.gitignore` excludes. The docs image
       subtree is re-included by an explicit negation in `.gitignore`
       (`!/docs/results/images/comparison_*/**`). Confirm new images are tracked with
       `git status` / `git check-ignore <path>` before pushing — CI has no access to the
       run drive, so anything untracked is simply missing from the built site.

2. **Call the macros** in Markdown:

   ```markdown
   {{ '{{' }} setup_table('comparison_20260627_054619', 'mixed_..._lt0', 'calibrated') {{ '}}' }}
   {{ '{{' }} pick_time_formula('comparison_20260627_054619', 'mixed_..._lt0', 'calibrated') {{ '}}' }}
   {{ '{{' }} reorder_formula(...) {{ '}}' }}          # equilibrium / ROP model with this run's averages
   {{ '{{' }} run_section('comparison_20260627_054619', 'mixed_..._lt0') {{ '}}' }}   # collapsible per-config figure blocks
   {{ '{{' }} inv_distribution_table('mixed_realistic_lt0') {{ '}}' }}   # inventory category table
   ```

   A missing JSON raises a build error under `--strict`, so a typo can't silently render blank.

## 4. Add the page to the navigation

Open `mkdocs.yml` and add your page under `nav:` → `Results:`

```yaml
nav:
  - Home: index.md
  - Authoring guide: authoring.md
  - Results:
      - Overview: results/index.md
      - '2026-06 run': results/2026-06-run.md      # <- new entry (newest first)
      - 'Example run (2026-06)': results/example-run.md
```

Also add a row to the table in `docs/results/index.md` so it's linked from the overview.

## 5. Preview, then publish

Preview locally (live-reloads as you edit):

```bash
pip install -r requirements-docs.txt
mkdocs serve            # open http://127.0.0.1:8000
```

Catch broken links/images before pushing (same gate the CI uses):

```bash
mkdocs build --strict
```

Publish:

```bash
git add docs/ mkdocs.yml
git commit -m "Add 2026-06 results write-up"
git push
```

The **Deploy docs** GitHub Action rebuilds and republishes automatically. Watch it under the
repo's **Actions** tab; when it's green the site is updated at
<https://edgypage.github.io/Inventory_Location_Optimizer/>.

---

## One-time setup (already done / to verify)

- **Settings → Pages → Build and deployment → Source = GitHub Actions.**
- Repo must be **public** (or on a plan that allows Pages for private repos).
- The deploy workflow lives at `.github/workflows/deploy-docs.yml`.

## Markdown cheat-sheet

| Want | Syntax |
|------|--------|
| Heading | `## Title` |
| Bold / italic | `**bold**` / `*italic*` |
| Link | `[text](https://...)` |
| Inline code | `` `code` `` |
| Image | `![alt](images/file.png)` |
| Sized image | `![alt](images/file.png){ width=600 }` |
| Note box | `!!! note` then indented text |
| Table | `\| a \| b \|` / `\|---\|---\|` |
