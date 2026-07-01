📊 **Results & analysis site:** <https://edgypage.github.io/Inventory_Location_Optimizer/>

# Inventory Location Optimizer

A discrete-event **warehouse simulator** for comparing SKU **placement (assignment)
strategies** — where to slot incoming stock so pickers walk less. It generates a synthetic
inventory, stocks a warehouse, then simulates many pick batches under different reorder-placement
rules and measures picker travel, batch duration, co-location, and churn. The published write-ups
and plots live on the [results site](https://edgypage.github.io/Inventory_Location_Optimizer/).

> This README is the human-level map. The **granular** parameter semantics, formulae, and design
> trade-offs live in the scripts' inline comments and docstrings (written to be picked up by
> humans and LLMs alike) — read those when you need the exact behaviour of a knob.

---

## ⚠️ Read first: the run data is huge

Simulation output is **enormous** and is **gitignored on purpose**. Think in orders of
magnitude, not exact bytes:

| Artifact | Scale |
|----------|-------|
| `inventory.db` | ~5–10 MB |
| `affinity.db` | ~150–200 MB |
| `warehouse.db` | < 1 MB |
| one `sim_<strategy>.db` | **~150–600 MB** |
| one calibration (all strategies) | **~20–25 GB** |
| **a full comparison sweep** | **~150–200 GB** |

**Do not let a cloud-sync client (OneDrive / Dropbox / Google Drive) or a backup job touch the
run-output directory, and do not upload it.** A single sweep can saturate a month's worth of
upload bandwidth — this has already happened once. Keep run outputs on a **local or external
drive**, well outside any synced folder. Commit only the **curated PNG plots + tiny JSON
snapshots** the docs site references (`docs/results/images/`, `docs/inventory/data/`).

---

## The workflow

Generate inventory → adjust strategies → adjust simulation → run analysis.

### 1. Generate inventory — `Warehouse/generation/generate_inventory.py`

Builds a synthetic SKU catalogue and its steady-state stock levels.

```bash
python Warehouse/generation/generate_inventory.py --name my_inv --num-skus 100000 --seed 42
```

Useful args (not exhaustive — see the file):

| Arg | Default | Meaning |
|-----|---------|---------|
| `--name` | timestamp | output folder name |
| `--num-skus` | 76500 | SKU count |
| `--seed` | 42 | RNG seed (reproducibility) |
| `--handling-splits` | 0.5 0.5 | conveyable / non-conveyable share |
| `--category-splits` | 1/6 ×6 | food/clothing/electronic/furniture/seasonal/chemical share |
| `--out-dir` | `Warehouse/generated/…` | parent output dir |

Equilibrium knobs (module constants): `EQUILIBRIUM_COVERAGE_BATCHES` (=10, target stock),
`REORDER_SAFETY_BATCHES` (=2), lead-time and supply-CV settings. **Writes:** `inventory.db`
(~5–10 MB), `params.json`, `stats.json`, `plots/`. Run the companion
`generate_affinity.py` to produce `affinity.db` (~150–200 MB) — a runnable **profile** is the
inventory + affinity pair the simulator consumes.

### 2. Adjust strategies — `Optimization/strategies.py`

Which placement families run is a data-driven registry. Edit the lists near the bottom:

- `_INITIALS` — initial layout: `uni` (uniform-random) and `opt` (policy-stocked; the whole
  inventory is placed through the strategy's own assignment function).
- `_RESTOCKS` — the 16 reorder-placement families; comment/uncomment a row to drop/add one.
- `_RESLOTS` — bounded per-batch re-slot variants (commented out by default).

Run-id keys are `{initial}_{restock}_{reslot}`, e.g. `opt_rank_labor_norsl`. The `STRATEGIES`
grid is the cartesian product of the three lists.

### 3. Adjust simulation — `Optimization/run_simulation.py`

```bash
python Optimization/run_simulation.py --workers 15          # parallel run
python Optimization/run_simulation.py --resume <run_dir>    # resume a crashed run
```

Key module constants: `SEED_WORLD=42`, `SEED_BATCHES=1337`, `N_BATCHES=100` (batches),
`K_PICKERS=25` (pickers), `_TARGET_FILL=0.875`, and the `REGRESSION_CONFIGS` pick-time
calibrations (`calibrated`, `calibrated_high_weight`, `calibrated_high_height`,
`calibrated_high_weight_high_height`). Useful args: `--workers`, `--resume`, `--all-profiles`,
`--max-skus` (cap for a smaller/faster warehouse), `--keyframe-interval`. **Writes:**
`sim_<strategy>.db` per arm (**~150–600 MB each**) + `config.json`.

### 4. Run analysis — `Optimization/run_analysis.py`

```bash
python Optimization/run_analysis.py <run_dir>
python Optimization/run_analysis.py <run_dir> --workers 8 --preset BY_INITIAL
```

| Arg | Default | Meaning |
|-----|---------|---------|
| `<run_dir>` | (required) | the comparison output directory |
| `--preset` | `DEFAULT` | which set of graphs |
| `--workers` | 1 | parallel workers |
| `--granularity` | `config` | job unit: `config` or `graph` |
| `--set KEY.PARAM=VALUE` | — | ad-hoc graph override (repeatable) |

**Writes** `compare/`, `per_strategy/`, `stats/`, `_aggregate/` PNGs — the plots the docs site
curates.

---

## Repo layout

| Dir | What |
|-----|------|
| `Warehouse/` | inventory generation, warehouse model, placement/assignment functions, reorder logic |
| `Optimization/` | the run harness (`run_simulation.py`, `run_analysis.py`, `strategies.py`) + reference docs |
| `Simulation/` · `Visualization/` · `Diagnostics/` | pick simulation, replay viewer, diagnostics |
| `Tests/` | test + benchmark suites |
| `docs/` | the MkDocs results site (published via GitHub Pages) |

## Publishing docs

The maintainer workflow for writing up a run is in `docs/authoring.md` (kept in the repo but
not published to the live site). In short: `run_analysis.py`, copy chosen PNGs into
`docs/results/images/`, add a page under `docs/results/`, list it in `mkdocs.yml`, and push —
the **Deploy docs** GitHub Action rebuilds the site. Preview locally with
`pip install -r requirements-docs.txt && mkdocs serve`.
