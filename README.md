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

### 2. Adjust strategies — `Optimization/config/strategies.py`

Which placement families run is a data-driven registry. Edit the lists near the bottom:

- `_INITIALS` — initial layout: `uni` (uniform-random) and `opt` (policy-stocked; the whole
  inventory is placed through the strategy's own assignment function).
- `_RESTOCKS` — the 17 reorder-placement families; comment/uncomment a row to drop/add one.
- `_RESLOTS` — bounded per-batch re-slot variants (commented out by default).

Run-id keys are `{initial}_{restock}_{reslot}`, e.g. `opt_rank_labor_norsl`. The `STRATEGIES`
grid is the cartesian product of the three lists.

### 3. Adjust simulation — `Optimization/run_simulation.py`

```bash
python Optimization/run_simulation.py --workers 15          # parallel run
python Optimization/run_simulation.py --resume <run_dir>    # resume a crashed run
python -m Optimization.run_simulation --workers 15          # equivalent module form
```

Everything tunable lives in the **`CONFIG` dict** (`Optimization/config/sim_config.py`): a
`global` section (`seed_world=42`, `seed_batches=1337`, `n_batches=100`, workers, checkpointing)
and a per-channel `channels` section — `store` and `fulfillment` are tuned independently, each
with its own pick-config sweep (currently `STORE_CONFIGS`: `store`; `FULFILLMENT_CONFIGS`:
`ful_calibrated` — the full menu, including disabled variants, lives as self-registering modules
under `Optimization/simconfig/configs/`), picker pool, cart, restock subset, batch shape, fill
headroom, and warehouse sizing. Useful args: `--workers`,
`--resume`, `--all-profiles`, `--max-skus` (cap for a smaller/faster warehouse),
`--keyframe-interval`, `--n-batches`. **Writes:** `sim_<strategy>.db` per arm
(**~150–600 MB each**) + `config.json` per config (+ per-channel subdirs on mixed catalogs).

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

Each subpackage has a **README stating what belongs in it and what does not** — read that before
adding a file, since it is what keeps these directories from sprawling again.

**`Warehouse/` — the domain engine.** `physical.py` stays at the root as the dependency-free leaf.

| Package | What |
|---|---|
| `inventory/` | the `Inventory_Manager` placement engine + its planning/optimal/reorder mixins |
| `placement/` | the assignment functions themselves — the research subject |
| `layout/` | physical geometry: storage units, aisles, bins, the warehouse builder |
| `catalog/` | SKUs and demand: `Order`, the affinity matrix, the catalogue builder |
| `picking/` | the pick simulations and the batch/task workload they consume |
| `kernel/` | zero-dependency value objects (pick-cost primitives, store/fulfillment regime) |
| `generation/` | the data-generation CLIs that build inventory/affinity/profile DBs |

**`Optimization/` — the run harness.** The seven `run_*.py` / `analyze_run.py` entry points stay at
the package root: what you RUN is at the top, everything else is organised beneath.

| Package | What |
|---|---|
| `config/` | `CONFIG` and everything a run is tuned by (+ the `simconfig/` pick-config registry) |
| `simdriver/` | orchestration: cells, work units, the supervisor, the worker, shared assets |
| `runschema/` | the content-addressed run-tree contract + the on-disk layout walkers |
| `persistence/` | SQLite schemas and read/write for a run's DBs |
| `metrics/` | turning simulation events into per-batch / per-task numbers |
| `Performance_Evaluations/` | the registry-driven graph + statistics suite |
| `gpu/` | validated but intentionally dormant — see its README before reviving it |

| Dir | What |
|-----|------|
| `Visualization/` · `Diagnostics/` | replay viewer, diagnostics dashboards |
| `Tests/` | grouped by what a failure means: `unit/`, `integration/`, `e2e/`, `architecture/` |
| `context/` | the verified spec layer — flows, artifacts, architecture, file catalog |
| `notebooks/` | exploratory Jupyter notebooks |
| `docs/` | the MkDocs results site (published via GitHub Pages) + `docs/design/` engineering docs |

## Publishing docs

The maintainer workflow for writing up a run is in `docs/authoring.md` (kept in the repo but
not published to the live site). In short: `run_analysis.py`, copy chosen PNGs into
`docs/results/images/`, add a page under `docs/results/`, list it in `mkdocs.yml`, and push —
the **Deploy docs** GitHub Action rebuilds the site. Preview locally with
`pip install -r requirements-docs.txt && mkdocs serve`.
