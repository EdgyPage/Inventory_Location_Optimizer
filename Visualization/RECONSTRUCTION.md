# Reconstructing a run from the DBs

Everything needed to replay a finished run is persisted by `run_simulation.py` — no live sim and no
warehouse re-plan. This documents **where each piece lives, what is exact, and what is not**.

Read §1 before writing any query against `bin_inventory`. The obvious reading of that table is
wrong, and it is wrong by ~59% of the warehouse.

The DB-backed web viewer that consumes these is `server.py` (Flask over the versioned readers in
`readers/`) + `static/` (canvas views). Run it with:

```
pip install -r Visualization/requirements.txt
python Visualization/server.py "<run_root>"     # the dir holding run_layout.json
# → http://localhost:5000
```

Pass the **run root**, not a cell directory: the viewer resolves the tree through
`Optimization/runschema` using the run's own `schema_id`, and spans every cell. With no argument it
falls back to `$COMPARISON_OUTPUT_DIR`.

---

## 1. The reconstruction contract — keyframes are canonical

### `bin_inventory` is a pure DEPLETION log. It never records a restock.

`Optimization/simdriver/strategy_runner.py` calls `check_reorders()` **before**
`build_pre_snapshot(mgr)`, so a bin restocked this batch is already in `pre_snap` at its
post-restock quantity. `snapshot_bin_inventory` (`Optimization/metrics/Simulation_Analytics.py`)
then skips any bin whose `post_qty == pre_qty` — which is exactly a restocked-but-not-picked bin.
The `manager._unavailable` branch that was meant to catch these never fires, because the bins are
already in `pre_snap`.

Measured on a 100-batch, 396,500-bin production run (`opt_fifo_norsl`, fulfillment):

| Check | Result |
|---|---|
| `bin_inventory` rows with `post_qty > pre_qty` | **0** |
| `batch_stats.reorder_placements` per batch | 20,662 – 42,832 |
| `sum(pre_qty - post_qty)` vs `sum(picks.quantity)` for a batch | **equal** (86,224 = 86,224) |

So the delta stream can only ever *decrease* occupancy. Rolling it forward from a keyframe drains
the warehouse:

| At batch 5 | Occupied bins |
|---|---|
| `bin_keyframe[5]` (truth) | 165,519 |
| keyframe[0] + `post_qty` deltas over `[0,5)` | 68,271 |
| — of which wrong | **97,248 bins missing (59%)**, 0 ghosts, 0 qty/sku disagreements |

The bins that survive the roll are all correct; the ones that were restocked simply vanish.

### `bin_keyframe[B]` IS exact

It is the post-restock, pre-pick state at the start of batch `B`. Verified against the delta stream:
all 44,936 `bin_inventory` rows at batch 5 agree with `bin_keyframe[5]` on **both** `sku` and
`pre_qty` — 0 mismatches, 0 bins absent.

### Therefore

```
state_at(B, t=None)   B % K == 0   ->  bin_keyframe[B]                          EXACT
state_at(B, t)        B % K == 0   ->  bin_keyframe[B] - picks(B, sim_time<=t)  EXACT
state_at(B, ...)      B % K != 0   ->  nearest keyframe below, minus picks
                                       for the batches in between              DEPLETION-EXACT,
                                                                               MISSING the restocks
                                                                               of those batches
```

`K = simulation_runs.keyframe_interval` (default 5; `0` means no keyframe DB was written at all).
A reader **must** return `exact: false` plus the number of pending restock batches for the third
case, and the UI must label it. A 100-batch run at `K=5` therefore has **20 exact spatial frames**,
and any animation of restock/convergence must step those, not `range(n_batches)`.

Empty bins have no row anywhere; they are implicitly qty 0 and drawn from `aisle_layout` geometry.

### Follow-up (not yet done)

The writer fix is to record a row whenever a bin's `sku` differs from its previously recorded sku,
rather than relying on `manager._unavailable`. That would make the delta stream complete and every
batch exactly reconstructible. It only helps **future** runs, so nothing here depends on it; the
tests in `Tests/integration/test_visualization_data.py` assert the *current* behaviour so the change
is caught rather than silently absorbed.

This supersedes the older "reslot-source drift" note, which described a narrower version of the same
class of bug (an evicted source bin getting no `post_qty=0` row) and understated it by an order of
magnitude.

---

## 2. Files per simulation output

The authoritative, machine-readable layout is `Optimization/schemas/run_tree/<short>.json`, generated
from `Optimization/runschema/schema.py`. Each document is named by the sha256 of its own declared
shape, so a run records exactly which layout produced it. Resolve paths through the resolver
(`runschema.resolver_for(run_root)`) rather than joining strings.

```
<run_root>/run_layout.json                                # descriptor: schema_id + cells
<run_root>/<cell>/<pair>/warehouse.db                     # geometry + sizing (shared by all arms)
<run_root>/<cell>/<pair>/planned_inventory.db             # single-cell runs only (see below)
<run_root>/_frozen/<pair>/planned_inventory.db            # multi-cell runs: frozen, shared
<run_root>/<cell>/<pair>/<config>[/<channel>]/sim_<arm>.db
<run_root>/<cell>/<pair>/<config>[/<channel>]/sim_<arm>.keyframes.db
<run_root>/_viz/<cell>/<pair>/<config>[/<channel>]/<arm>.viz.db     # derived; see §5
```

**Two levels are conditional** — assuming otherwise is what broke the earlier consumers:

| Level | Present | Absent |
|---|---|---|
| `<channel>/` | mixed catalog (store + fulfillment) | store-only run — DBs sit directly under `<config>/` |
| `_frozen/<pair>/` | multi-cell run | single-cell run — `planned_inventory.db` lives at `<cell>/<pair>/` |

Note `<pair>/store/store/` is a real path: the store *config* and the store *channel* share a name,
so levels must be consumed positionally, never matched by directory name.

`_viz/` uses the driver's reserved `_` prefix (`runschema/schema.py:RESERVED_PREFIX`) so every tree
walker skips it. A sidecar named `sim_<arm>.viz.db` beside the sim DB would instead be picked up by
`runlayout._sim_dbs_in` as a 35th arm and crash `Diagnostics/replay_run.py`.

---

## 3. What's stored

| Need | Table / file | Notes |
|---|---|---|
| Warehouse geometry | `warehouse.db` `aisle_layout` | one row/aisle: handling, category, unit_type, storage_size, `bay_x`,`bay_y`. Generate the bin grid (incl. empty bins) from `bay_x × bay_y`; all bins in an aisle share `unit_type`/`storage_size`. |
| Run params | `sim_X.db` `simulation_runs` | num_pickers, x/y speed, pick coefs, k_pickers, n_batches, seed_world, keyframe_interval, and `sim_schema_id` (see §4) |
| Per-batch timing | `sim_X.db` `batch_stats` | duration, `batch_start_time`, `batch_end_time`, avg_concurrent_pickers, and the restock counters (`reorder_placements`, `reload_moves`, `queue_depth`, …) |
| Per-task timing | `sim_X.db` `task_stats` | aisle_id, picker_id, task_start/end_time, duration, `W`, num_bins_visited. A task = one picker's single-aisle ordered pick sequence. |
| Event log (replay) | `sim_X.db` `picker_events` | every event (task_start/arrive/cart_swap/pick/task_end/done) with `time`, picker_id, aisle_id, bayX, bayY, sku, quantity. **Times are batch-relative** — each batch restarts near 0. |
| Bin deltas | `sim_X.db` `bin_inventory` | **depletion only — see §1.** Full snapshot at the run's FIRST batch (`start_i`, not necessarily 0 on a resumed run), changed-bins-only after. |
| Bin keyframes | `sim_X.keyframes.db` `bin_keyframe` | full occupied-bin snapshot every `keyframe_interval` batches. **The canonical spatial timeline.** |
| Per-bin scores | `sim_X.db` `bin_scores` | static, one row/bin: `travel_d`, `height_mult`, `layout_score` (D + height), `map_pref` (NULL unless a `map`/`map_rank` run). |
| Per-SKU scores | `sim_X.db` `sku_scores` | `map_target`, `labor_cost`, `handle_var`, `expected_popularity`/`expected_labor`, `equilibrium_qty`/`reorder_point`/`lead_time_mean`. |
| Per-aisle scores | `sim_X.db` `aisle_metrics` | per batch: `demand_sum`, `lift_sum`, `pick_load_sum`, n_skus, n_bins. **Only written by strategies that maintain aisle state — empty for most arms.** |
| Reorder queues | `sim_X.db` `reorder_queue` | per batch: `kind` (`lead`/`stock`), sku, qty, `remaining_lead`, `unit_type`/`storage_size`. **Also empty for most arms.** |
| Run identity (rename-proof) | `sim_X.db` `simulation_runs` + `warehouse.db` `warehouse_stats` | `strategy_key`, `pair_label`, `config_label`, `warehouse_fingerprint`, `optimal_sigma_fd`/`optimal_work`. The viewer resolves strategy/pair/config from these and matches the warehouse by `warehouse_fingerprint`, so renamed files/folders still load. |

The last two rows matter: an **empty table** and a **missing column** are different facts, and the
reader distinguishes them. `capabilities()` probes for rows, so the UI hides a panel rather than
rendering 0.0 as if it were a measurement.

---

## 4. Which schema am I reading?

A sim DB carries no `PRAGMA user_version` and no version table. Identity is derived from shape and
then pinned, exactly as the run-tree contract derives its `schema_id` from declared shape:

- `readers/fingerprint.py` normalizes `PRAGMA table_info` + `index_list`/`index_info` +
  AUTOINCREMENT/WITHOUT-ROWID into a canonical structure and hashes it to 12 hex.
- The **declared** id is derived by running that same normalizer over a fresh
  `Picking_Data.init_run_db(':memory:')`, so declaration and observation cannot drift apart.
- New runs stamp it into `simulation_runs.sim_schema_id`. Runs written before that column existed
  have it derived once and pinned into the sidecar's `cache_meta`.
- `readers/__init__.py::reader_for` resolves stamped → pinned → derived, and raises
  `UnsupportedSimSchema` with a structural diff for anything not vetted.

`sqlite_sequence` and `sqlite_stat*` are excluded from the hash, so running `ANALYZE` on a sim DB
never mints a new id.

---

## 5. The derived sidecar (`_viz/**/<arm>.viz.db`)

Built by `python -m Visualization.precompute <run_root> [filters]`. It holds nothing that isn't
derivable from the sim DBs — deleting it costs time, never data. It exists because three things are
too slow to compute per request on a production run:

| Table | Replaces |
|---|---|
| `aisle_batch_rollup` | a ~0.7 s whole-warehouse state rebuild per pane per batch step, done only to count occupied bins per aisle |
| `sku_rank` / `sku_series` | a measured 24.4 s `GROUP BY sku` full scan of `picks` |
| `bin_span` | a measured 5.0 s scan for "this bin's history" (`bin_keyframe`'s PK starts `(run_id, batch_id)`, so there is no usable per-aisle index) |
| `final_home` | the per-SKU destination map that drives the convergence colouring |

`bin_span` is built **from the keyframes**, never from `bin_inventory` — that is what makes it exact
per §1. Invalidation compares `st_size` and `st_mtime_ns` of all three source DBs; a stale cache is
bypassed with a loud warning naming the exact rebuild command, never silently served.

---

## 6. Query recipes

Bin quantities for run R, batch B, time t — the exact path:

```sql
-- 1. the frame: B must be a keyframe batch for an exact answer
SELECT aisle_id, bayX, bayY, sku, qty
FROM   bin_keyframe WHERE run_id = :R AND batch_id = :B;

-- 2. subtract picks up to t within batch B
WITH picked AS (
    SELECT aisle_id, bayX, bayY, SUM(quantity) AS n
    FROM   picks
    WHERE  run_id = :R AND batch_id = :B AND sim_time <= :t
    GROUP  BY aisle_id, bayX, bayY
)
SELECT s.aisle_id, s.bayX, s.bayY, s.sku,
       MAX(0, s.qty - COALESCE(p.n, 0)) AS qty_at_t
FROM   start_state s LEFT JOIN picked p USING (aisle_id, bayX, bayY);
```

Pickers / concurrency at time t:

```sql
SELECT picker_id, event_type, aisle_id, bayX, bayY, items_picked
FROM   picker_events
WHERE  run_id = :R AND batch_id = :B AND time <= :t
-- keep the latest event per picker_id -> current position/state;
-- count pickers whose latest event is between task_start and task_end -> concurrency.
```

Two ordering traps when reading `bin_inventory` for any purpose: order by `batch_id, id` (never
`batch_id` alone — the loop is last-write-wins and a bin can receive rows from two branches in one
batch), and never assume batch 0 holds the full snapshot (it is `start_i`, which moves on a resumed
run).

---

## 7. Batches with no tasks write nothing

`strategy_runner.py` `continue`s before appending stats when a batch produces no tasks. Such a batch
has a keyframe and reorder-queue rows but **no** `batch_stats`, `picker_events`, `bin_inventory` or
`aisle_metrics` row. Build the batch list from `SELECT DISTINCT batch_id FROM batch_stats`, never
from `range(n_batches)`.
