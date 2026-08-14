# Diagnostics — simulation lifecycle visual tool

A browser dashboard for watching the inventory lifecycle of a simulation —
**intake → placed → stuck-in-queue → picked → emptied → reclaimed** — plus a
warehouse fill heatmap, fill/queue time-series, and a per-function execution trace.

Built to spot bugs *before* a multi-hour run: e.g. warehouses that drift toward
~70% fill while others hold the specified ~85%, by making visible exactly where
units are lost (which batch, which bucket, which stage).

No source files in `Warehouse/` or `Optimization/` are modified — the tracer wraps
the manager's lifecycle methods on the instance at runtime.

## Two ways to get data

### 1. Instrumented small run (live) — `trace_lifecycle.py`
Runs a small sim with hooks and records the full lifecycle + function timings.

```bash
# default: contrasts a uniform-stocked vs a policy-stocked strategy
python Diagnostics/trace_lifecycle.py

python Diagnostics/trace_lifecycle.py --strategies uni_fifo_norsl,opt_map_norsl
python Diagnostics/trace_lifecycle.py --skus 3000 --batches 60 --pickers 10
python Diagnostics/trace_lifecycle.py --list        # available strategy keys
```

Writes `Diagnostics/out/trace_<strategy>.json` (one per strategy) + `manifest.json`.

### 2. Replay a real run's DBs — `replay_run.py`
Reads the persisted SQLite output of a finished `run_simulation.py` run (copy the
run dir over from the other machine / external drive) and exports per-batch fill.
`<run_base_dir>` is a run directory under the `COMPARISON_OUTPUT_DIR` from `.env` — read the
key, never paste a machine-local path.

```bash
python Diagnostics/replay_run.py <run_base_dir>
python Diagnostics/replay_run.py <run_base_dir>/<pair>/<config>/sim_uni_fifo_norsl.db
python Diagnostics/replay_run.py <run_base_dir> --max 200
```

Replays merge into the same `manifest.json`, so traces and real runs share the dashboard's run
switcher. (Replays have no per-unit stage/function detail — that isn't persisted — so those
panels hide.) The `sim_X.keyframes.db` sidecars are skipped: they hold only `bin_keyframe`.

#### Where the occupancy curve comes from — and what it is worth

Three sources, tried best-first. Which one was used is no longer implicit: the CLI tags every arm
line `[EXACT]` or `[APPROXIMATE, <phase>]` and prints the full reason once per run, the export
records `meta.occupancy_source` / `occupancy_exact` / `occupancy_phase` / `occupancy_note`, and an
approximate replay shows as `[approx]` in the dashboard's run switcher.

| Source | Used when | Worth |
|---|---|---|
| `bin_placement` + `bin_eviction` + `picks` | the DB carries the bin-mutation log | **Exact at every batch.** Those are every mutation of `Aisle.Bin.storage` the simulation can make, so folding them (EVICT → PLACE → PICK, per batch) reproduces its own bin state. Reference fold `Tests/bench/bin_log_harness.py::fold`; proof `Tests/integration/test_bin_log_replay.py`. |
| `aisle_metrics.n_bins` | no log, and the strategy maintains aisle state (the `opt_*` affinity arms) | Approximate. The manager's own counter, sampled *after* restock and *before* the batch's picks — a different instant than the other two — and it lags a bin emptied by a pick. |
| `bin_inventory` deltas | no log and no aisle state (the `uni_*` arms — `aisle_metrics` is empty there) | Approximate and biased downward. The table records picks and **never** restocks, so rolling it forward can only ever decay. |

Why that last row matters, measured on one production arm (`uni_fifo_norsl`) that carries both the
log and the legacy delta stream — occupied bins per batch:

| batch | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| log fold (exact) | 8,440 | 7,675 | 8,066 | 7,376 | 7,865 | 7,031 |
| `bin_inventory` | 8,440 | 6,878 | 5,995 | 5,025 | 4,416 | 3,623 |

The delta stream draws a warehouse that steadily drains — 48% low by batch 5, and worse the longer
the run. Runs that predate the log (the whole archive) still replay; they are now labelled instead
of believed. Re-run an arm to get an exact curve.

## View the dashboard

The dashboard fetches the JSON, so it needs a local web server:

```bash
cd Diagnostics
python -m http.server 8009
# open http://localhost:8009/static/
```

Use the **Run** dropdown (top-right) to switch strategies, the **batch slider**
(bottom) to scrub through batches, and **▶** to animate. The "Over batches" charts
overlay the other runs faintly so you can compare e.g. `uni_*` vs `opt_*` fill.

## What to look for

- **Leak flag** (Lifecycle panel): turns amber when units were enqueued but not
  placed (`stuck > 0` / `placed < intake`).
- **Queue depth chart**: a steadily rising queue = reorders the placement policy
  can't place — the warehouse saturates and units pile up unplaced.
- **Fill heatmap**: red/amber aisles or whole buckets sitting below the others
  localise *where* the shortfall is (a specific handling/category/size/unit bucket).

## Files
```
trace_lifecycle.py     instrumented live tracer
replay_run.py          real-run DB exporter
static/                index.html · app.js · style.css  (the dashboard)
out/                   generated JSON (git-ignored; regenerate any time)
```
