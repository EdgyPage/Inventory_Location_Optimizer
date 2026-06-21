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

```bash
python Diagnostics/replay_run.py <run_base_dir>
python Diagnostics/replay_run.py <run_base_dir>/<pair>/<config>/sim_uni_fifo_norsl.db
python Diagnostics/replay_run.py <run_base_dir> --max 200
```

Occupied bins come from `aisle_metrics.n_bins` when present, otherwise are
reconstructed from `bin_inventory` deltas. Replays merge into the same `manifest.json`,
so traces and real runs share the dashboard's run switcher. (Replays have no
per-unit stage/function detail — that isn't persisted — so those panels hide.)

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
