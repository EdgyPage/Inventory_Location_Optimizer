📊 **Results & analysis site:** <https://edgypage.github.io/Inventory_Location_Optimizer/>

# Inventory Location Optimizer

A discrete-event **warehouse simulator** for measuring three independent levers against two
different outcomes.

> **Three levers, two outcomes.** *Placement* (which bin restock goes into), *layout* (aisle
> length, velocity zoning), and *scheduling* (which picker takes which task) are three separate
> choices. They move two different quantities: **labor** — how much work exists — and
> **throughput** — how fast that work clears. Either can improve without touching the other.

It generates a synthetic inventory, stocks a warehouse, simulates many pick batches under
competing rules, and compares picker travel, batch duration, co-location, and churn against a
do-nothing baseline. The published findings live on the
[results site](https://edgypage.github.io/Inventory_Location_Optimizer/); this file is how you
reproduce them.

> This README is the human-level map. The **granular** parameter semantics, formulae, and design
> trade-offs live in the scripts' inline comments and docstrings (written to be picked up by
> humans and LLMs alike) — read those when you need the exact behaviour of a knob.

---

## ⚠️ Read first: the run data is huge

Simulation output is **enormous** and is **gitignored on purpose**. Think in orders of
magnitude, not exact bytes:

| Artifact | Scale |
|----------|-------|
| `inventory.db` | ~10 MB |
| `affinity.db` | ~250 MB |
| `warehouse.db` | < 1 MB |
| one `sim_<strategy>.db` | **~2 GB** (+ ~200 MB keyframes) |
| one calibration (34 arms, one channel) | **~70 GB** |
| **a full comparison sweep** (2 cells × 2 pairs × 2 configs × 34 arms) | **~500 GB** |

**Do not let a cloud-sync client (OneDrive / Dropbox / Google Drive) or a backup job touch the
run-output directory, and do not upload it.** A single sweep can saturate a month's worth of
upload bandwidth — this has already happened once. Keep run outputs on a **local or external
drive**, well outside any synced folder. Commit only the **curated PNG plots + tiny JSON
snapshots** the docs site references (`docs/experiments/<exp>/images/`, and
`docs/experiments/<exp>/data/<inv>/params.json`).

If the hot drive cannot hold a whole sweep — and at ~500 GB it often cannot —
`python scripts/archive_cells.py --run <dir> --watch` moves each cell to `COLD_DRIVE` as it
finishes and leaves a directory junction, so every downstream reader keeps working unchanged.

<details>
<summary><b>Where these numbers come from</b> (they replace an older, lower estimate)</summary>

The previous table said ~150–600 MB per `sim_<strategy>.db` and 150–200 GB per sweep. Those
figures were never computed. These are extrapolated from a **measured** run, and they are
roughly **3× larger**.

**The measurement.** A deliberately small but structurally complete run — 8,000 SKUs, 6 batches,
both channels, all 34 arms, the 2-cell `scheduler_ab` spec — produced 136 sim DBs totalling
2.1 GB in ~12 minutes. Deleting one table at a time and re-vacuuming gives the per-row cost of
each, which accounts for **99 %** of every DB:

| Table | Bytes/row | Scales with |
|---|---:|---|
| `picker_events` | ~83 | items picked (≈ 2.14 events per pick) |
| `bin_scores` | ~71 | total bins — fixed for the run |
| `bin_inventory` | ~59 | bins × snapshots — **retired; not written any more** |
| `picks` | ~52 | items picked |
| `sku_scores` | ~80 | SKUs in the channel |

These sizes are of the ARCHIVE. Since then `bin_inventory` was dropped (it recorded picks and
never restocks, and `picks` already held every decrement), `bin_placement` / `bin_eviction` were
added, and the keyframe interval moved 5 → 25 — net **~36 % fewer rows per arm**, so the figures
below are now an over-estimate for a fresh run.

**The extrapolation.** Only two of those grow with run length, and at production scale they
dominate — `picker_events` alone is ~69 % of the total. Rather than extrapolate the pick count
(which does not scale cleanly), it is taken from a **published run**: Experiment 6's store arm
records **7,358,481 items** over 100 batches. Feeding that into the measured per-row costs, with
Experiment 6's committed `config.json` geometry (396,500 bins, 130,885 SKUs), gives **~1.9 GB**
per store sim DB and ~500 GB for the sweep.

**Vintage of that item count.** The published run predates `753d01e`, which made bin selection
deterministic across processes and lifts absolute throughput ~1.3–1.4 % (labor and makespan are
unchanged; the throughput *percentages* the docs quote are ratios in which the shift largely
cancels). The count above is left exactly as that run produced it — a ~1.4 % move is well inside
the one-significant-digit precision this model claims, so the ~1.9 GB and ~500 GB figures do not
change.

**Two independent checks.** Re-running the same model against the tiny run reproduces its measured
size to within 5 %. And `Tests/bench/smoketest.py`'s `full` profile — production sizing at 10
batches — declares a 250 GB free-space floor; the model predicts ~59 GB of actual output there,
a sane margin for a precondition check.

**What is assumed, and where it may be wrong.** Per-row costs are treated as scale-invariant;
they are not exactly — `ix_picks_run_sku` indexes a 16× larger SKU cardinality at production, so
its B-tree is deeper and this model likely **under**-estimates. The events-per-pick ratio (2.14)
and the snapshot scaling of `bin_inventory` are both inferred from one configuration. Treat every
figure as one significant digit.

**To redo it:** point `COMPARISON_OUTPUT_DIR` and `PROFILE_INPUT_DIR` at scratch space, then
`python Tests/bench/smoketest.py --profile tiny`, and measure the resulting tree.

</details>

---

## Setup

**Python 3.11.** Two dependency sets — the simulator, and the docs site plus the architecture
tooling:

```bash
pip install -r requirements.txt          # numpy, scipy, pandas, matplotlib, pyyaml, flask, pytest
pip install -r requirements-docs.txt     # mkdocs stack + networkx/coverage for context/arch
```

**A fresh clone needs one extra step.** `nbstripout` is a git filter whose command lives in
uncommitted `.git/config`, so notebook checkout fails until you run it:

```bash
pip install nbstripout && nbstripout --install
```

### Configure `.env`

Copy `.env.example` to `.env` at the repo root and fill it in. **This is not optional** — the
storage warning above is unactionable without it.

| Key | Controls | If unset |
|-----|----------|----------|
| `COMPARISON_OUTPUT_DIR` | parent dir for `comparison_<ts>/` run trees | **falls back inside the repo** — a run writes hundreds of GB into the source tree, with no error |
| `PROFILE_INPUT_DIR` | root dir for inventory+affinity profile pairs | falls back to `Warehouse/generated/profiles/` |
| `COLD_DRIVE` | bulk storage for `scripts/archive_cells.py` | the archiver refuses to run |
| `ILO_SKIP_PREFLIGHT` | set to `1` to skip the run-tree schema preflight | preflight runs (the correct default) |

Four behaviours of the loader that each fail silently if you assume otherwise:

- **It is not python-dotenv** — it is a small hand-rolled reader in
  `Optimization/config/sim_config.py`.
- **No expansion.** `~`, `%USERPROFILE%` and `$HOME` are **not** expanded; a value of `~/runs`
  creates a directory literally named `~`. Write a full absolute path.
- **A shell variable always wins** over `.env`. That is the supported way to redirect one run
  without editing the file.
- Values may be raw-prefixed (`KEY=r"..."`); the `r"` and quotes are stripped for you.

Verify the setup before committing to a long run:

```bash
python -m Optimization.runschema.preflight --check    # "current." = the run tree contract is fresh
python Tests/bench/smoketest.py --stages preconditions   # ~2s: .env resolves, disk has room
```

---

## The workflow

Generate a profile → choose the arms and cells → run → analyse → publish.

### 1. Generate a profile — `Warehouse/generation/generate_mixed_profile.py`

A **profile** is the inventory + affinity pair the simulator consumes. Build it with
`generate_mixed_profile.py`:

```bash
# always dry-run first — prints the creation plan and the expected affinity size
python Warehouse/generation/generate_mixed_profile.py --num-skus 150000 --seed 42 \
    --fulfillment-fraction 0.4 --freq-profile bell --lead-times 0 random --estimate

# then for real (drop --estimate)
python Warehouse/generation/generate_mixed_profile.py --num-skus 150000 --seed 42 \
    --fulfillment-fraction 0.4 --freq-profile bell --lead-times 0 random
```

| Arg | Default | Meaning |
|-----|---------|---------|
| `--num-skus` | 76500 | SKU count |
| `--seed` | 42 | RNG seed (reproducibility) |
| `--fulfillment-fraction` | 0.0 | share of SKUs that are fulfillment SKUs; **> 0 makes the catalogue mixed**, which is what creates the two-channel run |
| `--freq-profile` | `uniform` | demand shape: `uniform` or `bell` |
| `--lead-times` | `1` | one profile leaf per value; `random` uses `--lead-random-range` |
| `--coverage` | 10.0 | equilibrium coverage in batches (target stock) |
| `--supply-cv-max` | 0.15 | supply variability cap |
| `--top-k` | 20 | affinity partners stored per SKU |
| `--out-dir` | `$PROFILE_INPUT_DIR` | parent output dir |
| `--estimate` | — | print the plan and expected sizes, then exit |

**Writes the exact leaf layout the simulator discovers** — this matters, because anything else is
invisible to it:

```
<PROFILE_INPUT_DIR>/<run>/<profile>/inventory/inventory.db   (+ params.json, stats.json, plots/)
<PROFILE_INPUT_DIR>/<run>/<profile>/affinity/affinity.db     (+ params.json, stats.json, plots/)
```

Discovery is `_profile_pairs` in `Optimization/runschema/runlayout.py`. Running the lower-level
`generate_inventory.py` and `generate_affinity.py` by hand produces DBs in a *different* shape
that `run_simulation` cannot find — use them only when you want a standalone catalogue.
`generate_profile_suite.py` builds a whole suite of carton-shape profiles instead of one.

### 2. Choose the arms and the cells

Two files define an experiment.

**Arms — `Optimization/config/strategies.py`.** Which placement families run is a data-driven
registry. Edit the lists near the bottom:

- `_INITIALS` — initial layout: `uni` (uniform-random) and `opt` (policy-stocked; the whole
  inventory is placed through the strategy's own assignment function).
- `_RESTOCKS` — the 17 reorder-placement families; comment/uncomment a row to drop/add one.
- `_RESLOTS` — bounded per-batch re-slot variants (commented out by default).

Run-id keys are `{initial}_{restock}_{reslot}`, e.g. `opt_rank_labor_norsl`. The `STRATEGIES`
grid is the cartesian product of the three lists — **2 × 17 × 1 = 34 arms** by default.
`CHANNEL_RESTOCKS` narrows the suite per channel.

**Cells — `Optimization/config/whatif_config.py`.** A **cell** is a frozen structural variant:
one choice of aisle-split × velocity-zoning × picker-scheduler, replayed over the same inventory
and the same batch stream so the difference between cells is attributable. Every run is a cell
matrix; a plain run is the single cell `k1_off`. Select one with `--spec`:

```python
'scheduler_ab': {
    'ks':         [1],                           # aisle split; 1 = whole aisle, 2 = halved
    'losses':     [0.0],                         # capacity lost per cut; only applies when k > 1
    'zoning':     [('off', {'enabled': False})], # (name suffix, velocity_zoning spec)
    'schedulers': ['round_robin', 'lpt'],        # >1 appends the _rr / _lpt cell suffix
    'arms':       'all',                         # 'all' | tuple of restock keys | None (as committed)
    'reference':  'k1_off_rr',                   # the cell every other cell is diffed against
}
```

Registered specs: `single` (1 cell), `scheduler_ab` (the committed 2-cell A/B). Cells are named
`k{k}[_l{loss%}]_{zone}[_{rr|lpt}]`. **More than one cell renames the output root**
`comparison_` → `comparison_whatif_` and switches on the cross-cell what-if outputs.

**Everything else — `Optimization/config/sim_config.py`.** The `CONFIG` dict: a `global` section
(`seed_world=42`, `seed_batches=1337`, `n_batches=100`, checkpointing) and a per-channel
`channels` section — `store` and `fulfillment` are tuned independently, each with its own
picker pool, cart, batch shape, fill headroom, and warehouse sizing. Pick-time calibrations are
self-registering modules under `Optimization/simconfig/configs/` (a sibling package of `config/`);
add one by dropping in a file with a `@pick_config` decorator.

### 3. Run the simulation — `Optimization/run_simulation.py`

```bash
python -m Optimization.run_simulation --workers 15                       # single cell
python -m Optimization.run_simulation --spec scheduler_ab --workers 15   # the 2-cell A/B
python -m Optimization.run_simulation --resume <run_dir>                 # zero retyped flags
```

| Arg | Default | Meaning |
|-----|---------|---------|
| `--spec` | `single` | which cell matrix to run |
| `--workers` | 1 | process-pool size (flat; every unit shares one pool) |
| `--analysis-workers` | = `--workers` | pool size for the post-run analysis |
| `--n-batches` | (CONFIG: 100) | batches per run |
| `--max-skus` | — | cap the catalogue for a smaller/faster warehouse |
| `--s-max-bins` / `--ff-max-bins` | — | cap store / fulfillment bin counts (also `--s-min-bins`, `--ff-min-bins`, `--s-max-aisles`, `--ff-max-aisles`) |
| `--keyframe-interval` | (CONFIG: 25) | full bin snapshot every K batches (0 disables). Not the reconstruction mechanism — the bin-mutation log is; a keyframe is its independent audit and a qty anchor |
| `--max-tasks-per-child` | 1 | recycle a pool worker after N jobs |
| `--resume` | — | resume from a run directory; flags are read back from `run_spec.json` |
| `--resume-granularity` | `strategy` | `strategy` restarts a partial arm bit-identically; `batch` continues from checkpoint (faster, not bit-identical) |
| `--no-analyze` | — | skip the automatic in-process analysis |
| `--no-preflight` | — | skip the run-tree schema check |
| `--all-profiles` | — | run every profile pair, not just the newest |

**The output tree.** Two levels are conditional, and both have caused real bugs:

```
comparison[_whatif]_<ts>/
  run_layout.json  run_spec.json  run.log  runtime_metrics.db
  _frozen/<pair>/planned_inventory.db                 # multi-cell runs only
  <cell>/<pair>/warehouse.db
  <cell>/<pair>/<config>/config.json
  <cell>/<pair>/<config>[/<channel>]/sim_<strategy>.db (+ .keyframes.db)
  whatif_{delta,labor,volume}.{csv,json}              # multi-cell runs only
```

**Consume these levels positionally, never by name.** The store *config* and the store *channel*
are both called `store`, so `<cell>/<pair>/store/store/` is a real path; `<channel>/` exists only
on a mixed catalogue and `_frozen/` only on a multi-cell run. Use
`runschema.resolver_for(base_dir)` — never join path strings.

### 4. Analyse

**It already ran.** `run_simulation` performs the whole analysis in-process unless you passed
`--no-analyze`. Re-run or extend it with the hub:

```bash
python -m Optimization.analyze_run <run_dir> --workers 8 --granularity graph
python -m Optimization.analyze_run <run_dir> --reference k1_off_rr --preset BY_INITIAL
```

| CLI | Produces |
|-----|----------|
| `run_analysis.py` | the per-cell graph + statistics suite → `compare/`, `per_strategy/`, `stats/`, `_aggregate/` |
| `run_channel_rollup.py` | store + fulfillment combined into a whole-warehouse view |
| `run_whatif_delta.py` | the cross-cell steady-state delta matrix (multi-cell) |
| `run_whatif_labor.py` | the same runs told in modeled labor-hours |
| `run_whatif_volume.py` | cumulative volume vs elapsed time — throughput as a *rate* |
| `run_runtime_graphs.py` | compute cost per arm, from `runtime_metrics.db` |

Three things worth knowing:

- `--preset` defaults to **`BY_INITIAL`** (keeps `uni_*` and `opt_*`). The older `DEFAULT` preset
  is uniform-only and silently drops every `opt_*` arm.
- `--granularity config` (the default) emits only ~4 jobs per cell, so a large `--workers` idles.
  Pass `--granularity graph` (~88 jobs/cell) when re-analysing with a big pool.
- Every step is wrapped in a handler that logs and continues, so a half-finished analysis still
  exits 0. The real check is `grep 'analysis step failed' run.log`.

### 5. Publish to the results site

```bash
python scripts/new_experiment.py --name experiment-7 --source <run_dir>   # scaffolds + prints the nav block
python docs/experiments/ingest.py --exp experiment-7 --source <run_dir> --gen-manifest
#   edit docs/experiments/experiment-7/experiment.yml   — the single source of truth for the pages
#   paste the nav block into mkdocs.yml
mkdocs build --strict && mkdocs serve
```

**Rendered values cannot drift; prose can.** `experiment.yml` plus the `docs/macros.py` helpers
render every setup table and the cross-cell what-if matrix from the committed `config.json` /
`params.json` / `whatif_delta.json` snapshots, and a missing JSON is a `--strict` build error
rather than a blank. The **narrative around them is not covered by that**: Experiment 6's
volume-curve prose quotes five values from `whatif_volume.csv` by hand — a file no macro reads and
which is not committed — so nothing re-derives them at build time.

That bypass is exactly why `753d01e` could shift absolute throughput ~1.4 % with no test failure,
no build failure and no reader-visible signal. Two things close the gap for now, neither of them
structural: each experiment page carries a dated note when its run is superseded by a code change,
and `docs/macros.py:run_commit()` renders the simulator commit beside the run id (from
`run_spec.json`, via `ingest.py`). The real fix is to route that prose through a macro over a
committed `whatif_volume.json` — until then, "cannot drift" applies to the rendered tables only.

Two traps. Run folders are named `comparison_*`, which `.gitignore` excludes; the docs image
subtree is re-included by an explicit negation, so confirm new images are tracked with
`git check-ignore` before pushing. And the **Deploy docs** action fires on push to `main` only —
day-to-day work is on `develop`, so the site does not move until a milestone merge.

Full detail in [`docs/authoring.md`](docs/authoring.md).

### Optional — look at the warehouse itself

The analysis suite answers *how much*; `Visualization/` answers *where*. It replays a finished run
spatially: any bin → any aisle → up to 24 aisles at once, two arms side by side, with a restock
convergence animation coloured by where each item ends up.

```bash
# 1. build the derived cache for the arms you want (~80 s and ~80 MB per arm)
python -m Visualization.precompute <run_dir> --cell k1_off_rr --config store --channel store \
       --arms uni_fifo_norsl,opt_rank_labor_norsl
#    --pair/--channel/--arms filter; --list shows what would be built; --workers 4; --force

# 2. serve it
python Visualization/server.py <run_dir>          # --port 5000; bare name resolves against
#                                                 #   COMPARISON_OUTPUT_DIR, same as every CLI
```

Both take the **run root** (the directory holding `run_layout.json`), not a cell directory. Step 1
is optional — the viewer works without a cache, it is just slow on the three queries that need a
full table scan. A full sweep is 272 arms, so `precompute` filters rather than defaulting to all.

Two things worth knowing before trusting a frame. Spatial state is **exact at every batch** for a
run carrying the bin-mutation log (`bin_placement` + `bin_eviction` + `picks`), but only at
keyframe batches for an **archived** arm, whose `bin_inventory` never recorded restocks — the
payload says which record it used and the viewer labels any frame that is not exact. And only the
arms whose schema has a vetted reader will open at all: anything else fails by name rather than
guessing. Both are explained in
[`Visualization/RECONSTRUCTION.md`](Visualization/RECONSTRUCTION.md).

---

## Verifying a change

Eight gates guard the spec layer, plus the test suite. **The invocation form is not
interchangeable** — see `CLAUDE.md` for the full list and for what each failure means:

```bash
python context/guards/path_guard.py --scan      # no machine-local paths in tracked files
python context/arch/verify_architecture.py      # call graph + import boundaries + file catalog
python -m pytest Tests/unit -q                  # domain behaviour — fast
```

The full suite exceeds 40 minutes; prefer the narrowest subset that covers the change.

---

## Repo layout

Most subpackages have a **README stating what belongs in them and what does not** — read it before
adding a file, since it is what keeps these directories from sprawling.

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

**`Optimization/` — the run harness.** The eight entry points stay at the package root —
`run_simulation`, `analyze_run`, `run_analysis`, `run_channel_rollup`, `run_whatif_delta`,
`run_whatif_labor`, `run_whatif_volume`, `run_runtime_graphs`: what you RUN is at the top,
everything else is organised beneath.

| Package | What |
|---|---|
| `config/` | `CONFIG`, the strategy registry, and the cell-matrix specs |
| `simconfig/` | the self-registering pick-config (calibration) registry |
| `simdriver/` | orchestration: cells, work units, the supervisor, the worker, shared assets |
| `runschema/` | the content-addressed run-tree contract + the on-disk layout walkers |
| `persistence/` | SQLite schemas and read/write for a run's DBs |
| `metrics/` | turning simulation events into per-batch / per-task numbers |
| `Performance_Evaluations/` | the registry-driven graph + statistics suite |
| `gpu/` | validated but intentionally dormant — see its README before reviving it |

| Dir | What |
|-----|------|
| `scripts/` | maintenance tools: the experiment scaffold, the cold-drive cell archiver |
| `Visualization/` · `Diagnostics/` | the spatial run viewer (see below), diagnostics dashboards |
| `Tests/` | grouped by what a failure means: `unit/`, `integration/`, `e2e/`, `architecture/`, `bench/` |
| `context/` | the verified spec layer — flows, artifacts, architecture, file catalog, guards, memory |
| `notebooks/` | exploratory Jupyter notebooks |
| `docs/` | the MkDocs results site (published via GitHub Pages), the generated code map under `docs/architecture/`, and `docs/design/` engineering docs |
