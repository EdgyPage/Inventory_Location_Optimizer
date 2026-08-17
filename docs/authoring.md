# Authoring guide

How to write up a results run and publish it to the site. Keep this page for reference.

---

## The short version

1. Generate plots from a completed run.
2. Copy the plots into the experiment's image tree, `docs/experiments/<experiment>/images/`
   (the schema-driven loop in [`site-usage.md`](site-usage.md) automates this via
   `docs/experiments/ingest.py`).
3. Copy `example-run.md` to a new page and write it up.
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

Copy the plots you actually want to show into the experiment's image folder:

```
docs/experiments/<experiment>/images/
```

!!! warning "Don't commit the raw run data"
    The SQLite run DBs (`sim_*.db`, `warehouse.db`, `*.keyframes.db`) are large and are
    ignored by `.gitignore` (`*.db`, `comparison_*/`). Only commit the **PNG plots** you
    reference on a page.

## 3. Write the page

Copy the template and rename it:

```bash
cp docs/example-run.md docs/experiments/<experiment>/2026-06-run.md
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
snapshot and render it. A **rendered** value cannot drift from the run.

A value quoted in **prose** can, and does: Experiment 6's volume-curve narrative cites
`whatif_volume.csv`, which no macro reads and which is not committed, so a code change that moves
those numbers breaks no test and no build. **As of Experiment 7 that gap is closed for the what-if
numbers**: ingest stages `whatif_volume.json` and `whatif_labor.json` into the experiment's
`data/` beside `whatif_delta.json`, so every quoted percentage has a committed, diffable source —
cite the `data/` file in a `<small>` line under the number. If you must quote a figure from an
*uncommitted* output, say which file it came from, and call
`{{ '{{' }} run_commit() {{ '}}' }}` beside the run id so a reader can see
which simulator commit produced it (`ingest.py` carries it from the run's `run_spec.json` into
`experiment.yml`). Prefer adding a macro over adding a hand-typed number.

1. **Commit the snapshot.** Copy the run's `config.json` next to its images
   (`docs/experiments/<experiment>/images/<run>/<inv>/<cfg>/config.json`) and, for a new
   inventory, its `inventory/params.json` to `docs/experiments/<experiment>/data/<inv>/params.json`.
   These are tiny; the large `*.db` run files stay off the repo.

   !!! warning "The `comparison_*` gitignore trap"
       Run folders are named `comparison_*`, which `.gitignore` excludes. The docs image
       subtree is re-included by an explicit negation in `.gitignore`
       (`!/docs/experiments/*/images/comparison_*/**`). Confirm new images are tracked with
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

Open `mkdocs.yml` and add your page under the experiment's `nav:` block:

```yaml
nav:
  - Home: index.md
  - Experiment 1:
      - Overview: experiments/experiment-1/index.md
      - '2026-06 run': experiments/experiment-1/2026-06-run.md   # <- new entry (newest first)
```

(`scripts/new_experiment.py` prints a ready-made nav block when scaffolding a new experiment.)

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

## The cited-resources pipeline

Why a reader can trust a number on this site: every figure and every quoted percentage traces
back through a chain of committed, verified files to the run that produced it. This section is
the maintainer's map of that chain — what each link owns, and where schema versions are brokered
when a link changes shape.

### The chain

```text
run tree on the drive                        the repo                              the built site
─────────────────────                        ────────                              ──────────────
run_layout.json ── schema_id ─┐
  (runschema contract,        │   ingest.py ──► images/…  data/…  ──►  experiment.yml ─┐
   Optimization/schemas/      │   (staging via     (curated PNGs,        (manifest:     │
   run_tree/<short>.json)     │    site_tree.py     config/params/        title, run,   ├─► macros.py ─► mkdocs build --strict
analysis outputs  ────────────┘    templates)       whatif JSON)          winners,      │    (renders manifests,          │
  (analyze_run, run_whatif_*,                                             figures,      │     verifies schema_id,         │
   run_channel_rollup)             figures.yml ─────────────────────────  schema_id) ───┘     errors on missing JSON)     │
                                   (ONE registry: name, owning eval,                                                      ▼
                                    section, default, caption)                                            Tests/architecture/*
                                                                                                  (site_tree, figure_registry,
                                                                                                   docref_guard, path_guard)
```

Each link, and the failure it guards against:

| Link | Owns | Guards against |
|---|---|---|
| `Optimization/runschema` contract | where every run artifact lives; resolved by **feature negotiation**, never by joining path strings | a moved artifact silently vanishing from ingest |
| `docs/experiments/ingest.py` | copying the *curated selection* out of the run tree (and the catalogue, resolved **by name** via `pair_bindings`) | hand-copy drift; dead absolute paths after a drive move |
| `docs/experiments/site_tree.py` | the staged-path templates, one declaration | ingest and macros disagreeing about where a figure sits |
| `docs/experiments/figures.yml` | the **single registry** of curated figure names, owning eval, section, width, caption | a renamed figure silently staging nothing (the `param_frequency` lesson) |
| `experiment.yml` | one experiment's identity: title, cells, winners, `schema_id`, `commit` | pages hard-coding run ids; unverifiable provenance |
| `docs/macros.py` | rendering manifests into tables/figures; `_verify_manifest_schema` | prose drifting from the run's own snapshots |
| `mkdocs build --strict` + `Tests/architecture/*` | the drift gates CI runs | everything above rotting quietly |

### Where schema versions are brokered

Three version stamps travel with the data, and each has one broker:

- **`schema_id`** (run-tree shape) — stamped into `run_layout.json` by the run, copied into
  `experiment.yml` by `ingest --gen-manifest`, checked by `docs/macros.py` at build time.
  The contract documents live in `Optimization/schemas/run_tree/`; `runschema.resolver_for`
  negotiates by *feature*, so an older run resolves under its own recorded contract, not the
  current head. A run is validated against **its own** contract (`verify_tree`) — fixing a
  contract never rescues a finished run.
- **`commit` / `dirty`** (simulator behaviour) — `run_spec.json:repo_commit` → `experiment.yml`
  → `run_commit()` beside the run id on the page. Answers "which code produced this number"
  when a fix changes a value without changing any path or column.
- **Profiles tree** (the catalogue side) — `run_layout.json:pair_bindings` names the
  `profile_run/profile`, and `Schema.profile_resolver.ProfileTree` resolves it **by name**
  under `PROFILE_INPUT_DIR`. This is what keeps catalogue plots resolvable after the data
  moves drives. Positive evidence it ran: ingest logs one
  `resolve <inv>: catalogue by NAME via pair_bindings` line per pair per cell — absence of
  those lines against a v2 run means the resolution silently fell back, and that is a bug to
  chase, not a shrug.

All three ride the same template engine, `Schema.pathtpl.render` — the run tree, the profiles
tree, and the docs site tree share it, so a path-shape change is one edit and one review.

### Adding a new resource type end-to-end

When a page needs a figure/CSV/table the analysis suite doesn't produce yet:

1. **Paint it.** New `@evaluation` in `Optimization/Performance_Evaluations/` (its output dir
   derives from the declaration), or a new whatif-style script at the run root if it is
   cross-cell.
2. **Declare it.** If it's a new run-tree artifact, add it to `Optimization/runschema/schema.py`
   **through the contract pipeline** (`--sync` before the edit, `--accept` after) — never a
   consumer-side path join. The `schema-maintainer` agent owns this step.
3. **Register it.** Add the figure to `docs/experiments/figures.yml` (name, eval, section,
   default flag, caption). `Tests/architecture/test_figure_registry.py` fails on drift.
4. **Stage and cite it.** Re-run the analysis step, re-run `ingest.py`, reference the figure or
   `data/` file from the page. `mkdocs build --strict` errors if a referenced snapshot is
   missing.

!!! warning "The manifest override trap"
    `experiment.yml`'s `figures:` / `inventory_plots:` keys **override** the registry defaults.
    Omit them unless the experiment genuinely curates a different set — a stale name in an
    override list stages nothing, logs only `MISSING`, and the page silently loses the figure.
    (This is exactly how the `param_frequency.png` → `param_relative_frequency.png` rename went
    unnoticed for months before the registry existed.)

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
