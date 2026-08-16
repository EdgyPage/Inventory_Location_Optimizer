# Visualization — the spatial run viewer

A browser viewer for **where things are** in a finished run: any bin, any aisle, up to 24 aisles at
once, stepped through a run's batches, with two arms side by side.

It is the spatial counterpart to `Optimization/Performance_Evaluations/`, which owns everything that
is a *chart of run metrics* (matplotlib, registry-driven, emits PNGs). The split is worth keeping:

| You want | It goes in |
|---|---|
| a new chart of run metrics | a new `@evaluation` module in `Performance_Evaluations/` |
| a new interactive spatial/temporal view | a new module in `Visualization/static/views/` |
| a new way to *read* a sim DB | a method on the `SimReader` protocol, implemented per schema version |

`Diagnostics/` is a third, separate tool — the inventory *lifecycle* dashboard (intake → placed →
stuck → picked → reclaimed). It answers "are units being lost?", not "where are they?".

## Quick start

```bash
pip install -r Visualization/requirements.txt

# 1. build the derived cache for the arms you want to open (minutes per arm)
python -m Visualization.precompute "<run_root>" --cell k1_off_rr --config store --channel store \
       --arms uni_fifo_norsl,opt_rank_labor_norsl

# 2. serve
python Visualization/server.py "<run_root>"        # the dir holding run_layout.json
# → http://localhost:5000
```

Pass the **run root**, not a cell directory. With no argument both commands fall back to
`$COMPARISON_OUTPUT_DIR`. Step 1 is optional — the viewer runs without a cache, just slowly.

## Layout

| Path | Owns |
|---|---|
| `server.py` | the Flask app. Routing and argument parsing only; every route delegates to a bound reader. The one legal `sys.path` bootstrap. |
| `db_reader.py` | run **discovery** (`discover_runs`, `RunRef`) and the back-compat facade over `readers/`. |
| `readers/` | **the versioned data layer.** One vetted reader per sim-DB schema; see below. |
| `precompute.py` | builds the derived sidecar cache. Must not import flask. |
| `cache_schema.py` | the sidecar's DDL, in one place, so `context/verify_context.py` can check it. |
| `static/core/` | shared front-end machinery: fetch + cache tiers, selection state, view registry, palette, canvas helpers, aisle packing. |
| `static/views/` | one module per view. Self-registering, no cross-imports. |
| `RECONSTRUCTION.md` | **what is exact and what is not.** Read it before writing a query. |

The seven views: **Warehouse** (384-cell mini-map + a packed band of up to 24 aisles) →
**Aisle** (full bin grid, pickers, time scrub) → **Bin** (occupant, scores, and its whole
occupancy history); plus **Converge** (restock animation over the keyframe grid),
**Compare** (per-bin diff of two arms), **Top SKUs**, and **Tasks**.

The URL hash carries the selection (`#run=…&cmp=…&view=…&batch=…&aisle=…&bin=x,y`), so a view is
shareable and reloadable.

**Does NOT belong here:** metric computation (→ `Optimization/metrics/`), DDL for anything a
*simulation* writes (→ `Optimization/persistence/`), path construction (→ `Optimization/runschema/`),
or static chart generation (→ `Optimization/Performance_Evaluations/`).

`static/schema.json` is **generated** by `Optimization/runschema/preflight.py`. Never hand-edit it;
if the front end needs more fields, change the generator.

## The two rules that keep this maintainable

### 1. Views never touch a database

```
view (JS)  ->  /api/<verb>  ->  route  ->  ReaderBinding  ->  SimReader
```

A view knows JSON verb names and nothing else. That is the entire versioning firewall: when the sim
DB schema changes, a new reader implements the same protocol and **no view changes**. Conversely,
redesigning a view cannot break data access.

### 2. Reading ANY of the four DBs is versioned — through the shared schema pipeline

`readers/` holds one module per vetted sim schema (`sim_2026_07.py` today) whose vetting IS the
family's (`SCHEMA_IDS = SIM_DB_FAMILY.supported_ids()`).  Identity resolves through
`Schema.identity.resolve` — **stamped → pinned → derived**, the family's own stamp reader doing
the reading (new runs carry `simulation_runs.sim_schema_id`; older ones get the id derived once
at precompute time and pinned into the sidecar).  An unrecognised shape raises
`UnsupportedSimSchema` with a structural diff naming the tables and columns that differ — never
a bare hash, and never a silent best-effort read.

The SQL itself is versioned publisher-side: every regular read executes a **named query**
registered beside its family's DDL (`Picking_Data` for sim + keyframes, `cache_schema` for the
sidecar, `Warehouse_Data` for geometry — where the pre-fingerprint vintage is served by a
`dataset.override`), composed once per (family, query, vintage) via `dataset.sql_for`.  A future
schema change lands as a publisher-side override and no reader method moves.  The raw sites that
legitimately remain (the algorithmic state folds, the streaming precompute passes, the
shape-following `SELECT *` reads) are an allowlist WITH reasons in
`Tests/architecture/test_viewer_broker_boundary.py` — producer-broker-consumer is enforced,
not conventional.

Runs older than the vetted schema are **unsupported by design**. Failing loudly beats a viewer that
draws a plausible-looking warehouse from columns it guessed at.

## Colour, and why it is not one hue per aisle

384 distinguishable hues is perceptually impossible — categorical colour tops out near 8–12. So an
aisle's identity is not what the palette encodes. Ranked by the questions actually being asked:

1. *Is this item where it ends up?* — **binary** → chroma (saturated = home, muted = misplaced).
   A chroma difference survives every colour-vision deficiency.
2. *Which region does it belong to?* — 13 classes → hue, anchored on the aisle **family**
   `(handling_type, unit_type, storage_size)`. Measured on the production warehouse that is
   exactly 13 families across 384 aisles. Adding `category` — i.e. the full BinKey — gives **63**,
   far past where hue stays readable, so category instead orders aisles *within* a family and
   same-category aisles land adjacent in its band.
3. *How high up?* — ordinal → lightness, dark at the bottom to light at the top.
4. *Exactly which aisle?* — a **label**, on hover. Not a colour.

Within a family, aisles fan across an 18° hue band, so "wrong aisle, right family" *looks* nearly
correct — which is the truth being shown. Lightness divides by the aisle's **own** `bay_y`, so a
4-high and a 40-high aisle both span the full ramp and "top shelf" reads the same everywhere.

**The largest family holds 88 of 384 aisles, so its members sit ~0.2° apart — deliberately
indistinguishable.** That is not a bug to fix. At that density the distinguishing signal is stable
screen position plus the label; widening the band would destroy the family read, which is the one
that matters. Colours are OKLCH (perceptually uniform lightness — HSL would make the shelf ramp read
differently per hue).

### "Home" is an aisle SET, not a bin

The colour authority is the **comparison** run's final state, applied to both panes, so the two are
directly comparable. But a SKU rarely lands in one bin: the warehouse is sized `bins ≥ n_skus × 1.1`,
and measured at the last keyframe **64.3% of SKUs hold several bins — and only 9.7% of those keep
every replica in one aisle** (median span 2 aisles, max 8). So `final_home` carries `home_aisles`,
and "is this item home?" tests membership of that set. Scoring against a single designated bin would
paint the majority of correctly-placed replicas as misplaced. Hue and lightness still come from one
*primary* home (largest qty, ties broken by lowest coordinate, so it is deterministic).

Colouring is gated on `warehouse_fingerprint`: two arms from cells with different aisle layouts are
refused rather than drawn with aisle ids that silently mean different things.

## Cost, and where the numbers come from

Sized against a production run — 384 aisles, 396,500 bins, 130,885 SKUs, 100 batches,
`keyframe_interval=5`, ~1.0 GB sim DB + ~240 MB keyframes per arm.  Those figures are from the
ARCHIVE; a run at today's defaults writes no `bin_inventory` and keyframes every 25 batches, so
both files are smaller (the keyframe sidecar by ~5x) while every frame is exact rather than only
the keyframe ones:

| Operation | Live | With a sidecar |
|---|---|---|
| whole-warehouse state at a keyframe batch | ~0.55 s | — (exact, still read live) |
| per-aisle occupancy for one batch | ~0.7 s | **0.001 s** |
| top-N SKUs by volume (`GROUP BY sku`, full scan) | **24.4 s** | **0.000 s** |
| one bin's history (`bin_keyframe` PK starts `run_id, batch_id`) | **5–9 s** | **0.001 s** |

Building one arm's sidecar takes ~75–80 s and ~80 MB against a ~1 GB sim DB. That is why
`precompute.py` takes filters rather than defaulting to the whole run: a full sweep is 272 arms
(~5 hours and ~22 GB), and you almost never want all of it.

The viewer runs fine without a cache — the run picker marks uncached arms — it is just slow on the
three rows above.

## Zero JS dependencies, on purpose

Native ES modules, Canvas 2D, no bundler, no `node_modules`, no CDN — matching `Diagnostics/static/`.
The viewer is debuggable in a browser with no toolchain, which matters more here than a component
framework would: the hard part of every view is the canvas draw, not the DOM.
