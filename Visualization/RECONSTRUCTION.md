# Reconstructing a run from the DBs

Everything needed to replay any `(run, batch, time t)` is persisted by
`run_simulation.py` — no live sim or warehouse re-plan. This documents where each
piece lives and the queries to reconstruct state.

The DB-backed web viewer that consumes these now exists: `server.py` (Flask API over
`db_reader.py`) + `static/` (canvas viewer). Run it with:

```
cd Visualization
pip install -r requirements.txt
python server.py "<comparison_YYYYMMDD_HHMMSS dir>"   # positional, or --base, or $COMPARISON_OUTPUT_DIR
# → http://localhost:5000
```

Add up to 4 runs by name → side-by-side panes. Two view modes:

- **active aisles only** (default): only the aisles a picker is *currently* standing in are
  drawn, each as its own SKU-coloured **bin layout** with the picker dot and Manhattan-routed
  **arrows tracing the pick path** (served by `/api/batch`: active-aisle bins + full timed
  events, indexed client-side so within-batch scrubbing is recompute-free).
- **all aisles**: zoomed-out per-aisle heatmap (fill / pick-activity / layout-score), served
  by the cheap `/api/overview` aggregates.

Click any aisle to drill into its full bin grid (`/api/aisle`), then click a bin to inspect
its status (SKU, qty now vs. start, picked, layout score). The batch stepper + time slider are
synced across panes — a single load token guards against stale fetches so panes never drift to
different batches (each batch restarts at t=0, with a leading reorder phase that shows the
queued restock). The reorder panel needs a run produced after the `reorder_queue` table was
added; older runs replay everything else.

## Files per simulation output

```
<base_dir>/<pair>/warehouse.db              # geometry + sizing (shared by A/B/C)
<base_dir>/<pair>/planned_inventory.db      # the sampled inventory actually stocked
<base_dir>/<pair>/<config>/sim_A.db         # strategy A run DB (B, C alongside)
<base_dir>/<pair>/<config>/sim_A.keyframes.db   # full bin snapshots every K batches
```

## What's stored

| Need | Table / file | Notes |
|---|---|---|
| Warehouse geometry | `warehouse.db` `aisle_layout` | one row/aisle: handling, category, unit_type, storage_size, `bay_x`,`bay_y`. Generate the full bin grid (incl. empty bins) from `bay_x × bay_y`; all bins in an aisle share `unit_type`/`storage_size`. |
| Run params | `sim_X.db` `simulation_runs` | num_pickers, x/y speed, pick coefs, k_pickers, n_batches, seed_world, keyframe_interval |
| Per-batch timing | `sim_X.db` `batch_stats` | duration, `batch_start_time`, `batch_end_time`, avg_concurrent_pickers |
| Per-task timing | `sim_X.db` `task_stats` | aisle_id, picker_id, task_start_time, task_end_time, duration, W_a, num_bins_visited |
| Event log (replay) | `sim_X.db` `picker_events` | every event (task_start/arrive/cart_swap/pick/task_end/done) with `time`, picker_id, aisle_id, bayX, bayY, sku, quantity. **Times are batch-relative** (each batch starts ≈0). |
| Bin deltas | `sim_X.db` `bin_inventory` | full snapshot at batch 0 + changed-bins-only each later batch (pre_qty/post_qty). |
| Bin keyframes | `sim_X.keyframes.db` `bin_keyframe` | full occupied-bin snapshot every `keyframe_interval` (default 5) batches → jump without replaying from 0. |
| Per-bin scores | `sim_X.db` `bin_scores` | static, one row/bin: `travel_d`, `height_mult`, `layout_score` (D + height), and `map_pref` (optimal-map basis; NULL unless a `map`/`map_rank` run). Saved once after build; viewer reads instead of recomputing. |
| Per-SKU scores | `sim_X.db` `sku_scores` | `map_target`, `labor_cost`, `handle_var`, `expected_popularity`/`expected_labor`, `equilibrium_qty`/`reorder_point`/`lead_time_mean`. |
| Per-aisle scores | `sim_X.db` `aisle_metrics` | per batch: `demand_sum`, `lift_sum`, `pick_load_sum`, n_skus, n_bins (only for strategies that maintain aisle state). |
| Reorder queues | `sim_X.db` `reorder_queue` | per batch: `kind` (`lead`/`stock`), sku, qty, `remaining_lead`, and `unit_type`/`storage_size` (stock units only). |
| Run identity (rename-proof) | `sim_X.db` `simulation_runs` + `warehouse.db` `warehouse_stats` | `strategy_key`, `pair_label`, `config_label`, `warehouse_fingerprint`, `optimal_sigma_fd`/`optimal_work`. The viewer resolves strategy/pair/config from these and matches the warehouse by `warehouse_fingerprint`, so renamed files/folders still load. |

The viewer surfaces these: the **layout score** and **map pref** colour modes (per-aisle heatmap),
the bin inspector (layout score, map pref, and the bin's SKU map_target / labor / popularity),
the aisle drill-in header (demand / lift / pick-load), and the **queues** panel (lead +
stock contents with bin tier, beside each run's score summary). `/api/scores` →
`{layout, map_pref, has_map, source}`; `/api/sku_scores` → per-SKU.

## Reconstruct bin quantities — run R, batch B, time t

1. Nearest keyframe `kf = K·floor(B/K)` (K = `keyframe_interval`): occupied-bin qty
   at the start of batch `kf` from `bin_keyframe WHERE run_id=R AND batch_id=kf`.
2. Roll forward `(kf, B]` with `bin_inventory` deltas (latest `pre_qty` per bin
   ≤ B) to get batch-B start state. (Or skip keyframes and roll all of `[0, B]`.)
3. Subtract picks up to `t` within batch B:

```sql
WITH start_state AS (          -- step 1 (+2) result: bin -> qty at batch B start
    SELECT aisle_id, bayX, bayY, sku, qty FROM bin_keyframe
    WHERE run_id = :R AND batch_id = :kf
    -- ⊕ apply bin_inventory deltas for batch_id in (kf, B]
),
picks AS (
    SELECT aisle_id, bayX, bayY, SUM(quantity) AS picked
    FROM   picker_events
    WHERE  run_id = :R AND batch_id = :B AND event_type = 'pick' AND time <= :t
    GROUP  BY aisle_id, bayX, bayY
)
SELECT s.aisle_id, s.bayX, s.bayY, s.sku,
       MAX(0, s.qty - COALESCE(p.picked, 0)) AS qty_at_t
FROM   start_state s LEFT JOIN picks p USING (aisle_id, bayX, bayY);
```

## Reconstruct pickers / concurrency at time t

```sql
SELECT picker_id, event_type, aisle_id, bayX, bayY, items_picked
FROM   picker_events
WHERE  run_id = :R AND batch_id = :B AND time <= :t
-- keep the latest event per picker_id → current position/state;
-- count pickers whose latest event ∈ (task_start..task_end) → concurrency.
```

Empty bins (no `bin_inventory`/keyframe row) are implicitly qty 0 and drawn from
`aisle_layout` geometry.
