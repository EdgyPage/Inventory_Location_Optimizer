---
name: channel-experiment-independent-warehouses
description: Store/fulfillment channels are two independent warehouses; combine per-channel best plans via run_channel_rollup.py
metadata: 
  node_type: memory
  type: project
  originSessionId: 601be2f3-6d4d-4ded-b010-d6492832dbc8
---

Store and fulfillment channels are treated as **two independent warehouses/sections**, not a combined run. Each is a separate worker/manager with its own queue, batch stream, picker pool, and pick-cost — the ONLY intentional difference between them is the pick-time cost calculation. They share the same inventory source + world seed.

Consequences (decided 2026-07-02):
- The run is **non-combinatorial**: each channel runs its own arms as `store_arms + ff_arms` independent runs, never the cross product. Store fifo paired with any fulfillment fn gives the same store info, so no N×M.
- Per-channel arm selection lives in `strategies.CHANNEL_RESTOCKS` (aliased `sim_config.STORE_RESTOCKS`), consumed via `Channel.restocks` + `strategies.strategies_for` (None ⇒ full suite). **As of 2026-07-08 BOTH channels are `None` → the full 34-arm assignment-function suite** (2 initials × 17 restocks × noRSL); the earlier store 3-restock subset `('fifo','rank_labor','rank_cartlabor')` was lifted for a full-sweep run. To re-restrict a channel, set its `CHANNEL_RESTOCKS` value back to a tuple of restock keys.
- Yardsticks (`optimal_sigma_fd`/`optimal_work`) are computed **per channel** over its own regime-filtered orders + own speeds — a mixed store-speed yardstick would be apples-to-oranges. Fulfillment `optimal_sigma_fd` is legitimately ~0 (short shelves + fast walker → tiny travel), not a bug.
- Downstream, **`Optimization/run_channel_rollup.py`** (run after `run_analysis.py`) combines: it reads each channel's `series.json`, computes each plan's absolute `ss_prod_hours` saving vs that channel's fifo baseline, picks the best per channel, and SUMS them into a cumulative whole-warehouse saving. Absolute savings are additive, so `channel_rollup.csv` doubles as a mix-and-match table.
- Cross-regime placement is guarded in `Inventory_Manager._execute_placement` (fail-fast if a unit lands in a wrong-regime bin). See [[gpu-broker-dormant-not-for-placement]].

Quick smoke run: `run_simulation.py --n-batches N --max-skus S --max-bins B` (the `--n-batches` override was added for this). Note the mixed catalog floors the warehouse at ~70k bins regardless of `--max-bins`, so warehouse *build* dominates job time, not batch count.

**`run_analysis.py` now DEFAULTS to `--preset BY_INITIAL`** (focus='all'), which keeps both `uni_*` and `opt_*` arms and bakes in `top_n=3, top_by='initial'` (top 3 per initial). So the plain `run_analysis.py <dir> --workers N` already gives top-3 uni + top-3 opt — there is NO `--top-n` flag (change N via `--set compare.top_metric.top_n=K`). Pass `--preset DEFAULT` only for the legacy uni-only mode (focus='uni', drops every `opt_*` arm → rollup sees half the suite). `run_channel_rollup.py` warns when it detects dropped arms.

**Fixed bug (was silently producing blank sim DBs):** the aisle-index fast path in `Assignment_Functions._build_aisle_score_fn` (and the two `build_load_*` fns) used the pallet-only `_SIZE_RANKS`/`_SIZES_DESCENDING`; fulfillment units (sizes `ff_*`) never matched an ff BinKey, so `opt_cmax`/`opt_cmin` on fulfillment placed nothing → all batches skipped → blank DB. Fixed by using `tier_ranks_for(unit_type)`. `run_simulation` now runs `_warn_blank_arms` at the end to loudly flag any sim_*.db with zero batches.
