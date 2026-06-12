# Assignment functions — what actually happens

A concrete trace of every placement policy in the current grid: where a unit goes, what
is scored, and which code path runs. A *strategy* = `initial stock × restock (reorder)
rule × re-slot` ([strategies.py](strategies.py)); this doc covers the **8 restock
families** (the "assignment functions") plus the **2 initial-stock modes**.

Builders live in [Warehouse/Assignment_Functions.py](../Warehouse/Assignment_Functions.py);
the drain lives in [Warehouse/Inventory_Management.py](../Warehouse/Inventory_Management.py).
For the scoring objectives at a higher level see
[Warehouse/ASSIGNMENT_SCORING.md](../Warehouse/ASSIGNMENT_SCORING.md).

---

## 0. Placement runs in two phases

1. **Initial stock** — once, before the batch loop, in the worker
   ([strategy_runner.py](strategy_runner.py) `_run_strategy_worker`):
   - `uni` → `mgr.enqueue_all(cartons)` → `_drain()` with the manager's **default**
     placement (`uniform_fifo`). The restock rule's `build()` has **not run yet**, so the
     initial layout is *always uniform-random* for `uni` strategies.
   - `opt` → `mgr.place_optimal(...)` (the restock rule still doesn't touch initial stock).
   Then `strat.build(mgr, ctx)` swaps in the restock policy — which therefore only governs
   **reorders**.
2. **Reorder waves** — every batch, `check_reorders()` queues replenishment units for
   depleted SKUs and calls `_drain()`.

## 1. Shared machinery every function uses

- **`Placement(name, place_one, place_wave=None)`** — the single policy object a strategy
  sets on `mgr.placement`. `place_one(unit, candidates) -> bin` is per-unit (always present);
  `place_wave(units, candidates_fn) -> [(unit, bin)]` is the optional ranked wave.
- **`_drain()` dispatcher** ([Inventory_Management.py:~1187](../Warehouse/Inventory_Management.py)):
  runs the coupling guard, then `if placement.place_wave: _drain_ranked() else: _drain_per_unit()`.
  - `_drain_per_unit`: pops the queue one unit at a time, `bin = placement.place_one(unit, cand)`.
  - `_drain_ranked`: groups the queue by **BinKey**, calls `place_wave` per group, then routes
    repacked stragglers back through `_drain_per_unit`.
- **`_candidates(unit)`** — the legal bin pool: all empty bins matching the unit's
  `BinKey = (handling, category, storage_size, unit_type)`. Every function chooses *within*
  this pool, so it never has to re-validate legality.
- **`D(bin) = x_speed·x_phys + y_speed·y_phys`** — per-bin travel cost from the aisle entrance.
  Low D = "front bay" (shallow); high D = "back". `_aisle_extremal_bins` reduces a candidate
  pool to one representative bin per aisle (min-D or max-D).
- **Pick-effort priority** (ranked drains, `_ranked_assign_impl`):
  `priority = f_i·(pick_intercept + pick_weight_coef·ln(w) + pick_volume_coef·ln(v)) + β·co_occur`,
  units placed **highest-priority first** so they claim the extremal bins before the rest.
- **W** (task workload, recorded per task) is the *measurement* — realized sweep-path travel
  + pick + cart. It is **not** what any scorer optimizes; placement optimizes the proxies below.

---

## 2. Initial-stock modes

| key | what | mechanism |
|---|---|---|
| **`uni`** (Uniform) | random initial layout | `enqueue_all` → `_drain_per_unit` → `_uniform_assignment` = `random.choice(candidates)` |
| **`opt`** (Optimal) | rearrangement-inequality optimum | `place_optimal` → per BinKey class, **hottest SKU → lowest-D bin** (`_optimal_assign`); this is the `Σf·D` yardstick |

---

## 3. The 8 restock (reorder) families

Each block: the policy object, the drain path, and exactly where a queued unit ends up.

### `fifo` — `uniform_fifo` (baseline)
- `Placement('uniform_fifo', _uniform_assignment)`, **no place_wave** → per-unit drain.
- A unit is dropped into a **uniformly random bin** of its BinKey pool. No affinity, no
  demand, no ordering. RNG-order sensitive. `STRATEGIES[0]` baseline.

### `rank` — `ranked_uniform`
- `place_wave = build_ranked_uniform` → **ranked** drain.
- Units are sorted by pick-effort priority, then each is placed in a **uniform-random
  aisle's front (min-D) bin** (`aisle_selector = random.choice`). So: effort ordering +
  front-bay bins, but the *aisle* is random. `place_one` (stragglers) = uniform-aisle + min-D.
- Ablation: isolates "does the trip-min *aisle* choice matter?" vs `tmin`.

### `tmin` — `ranked_min` (TripMin)
- `place_wave = build_ranked_minimizing`, `place_one = build_trip_minimizing`.
- Ranked drain, **aisle = the min-D representative, bin = min-D** → the highest-effort units
  claim the **lowest-D (front) bins** first. Per-unit fallback scores aisles by
  `f_s·D − β·co_occur` (minimized). Drives `Σf·D` down (hot items shallow).

### `tmax` — `ranked_max` (TripMax)
- Mirror of `tmin`: ranked drain, **aisle/bin = max-D** → hot units pushed to the **back**.
  The worst-layout upper-bound control. (Throughput ties `tmin` — see §5.)

### `cmax` — `cohesion_max` (MaxClu)
- `Placement('cohesion_max', build_cluster_maximizing)`, **no place_wave** → per-unit drain.
- For each unit, score every candidate **aisle** by the demand-weighted lift of the SKU to
  that aisle's current members (`_demand_weighted_delta_lift` over `_aisle_idx_sets`); place
  in the aisle with the **highest** lift, **front (min-D) bin**. Membership is committed
  incrementally, so co-picked SKUs snowball into the same aisle. Consumes the pre-sorted
  `_aisle_index` fast path (`uses_aisle_index=True`).

### `cmin` — `cohesion_min` (MinClu)
- Same as `cmax` but picks the **lowest**-lift aisle — anti-affinity control.

### `comp` — `compaction` (Compact)  *(new)*
- `place_wave = co-demand ranked`, `place_one = per-unit co-demand` (`build_co_demand_placement(True, …)`).
- Ranked drain. Per unit: pick the aisle with the **most** demand-weighted co-demand mass;
  *within it*, take the **bin in the COLUMN NEAREST** the lift-weighted centroid of the SKU's
  already-placed partners (`_demand_weighted_partner_centroid` over the new `_aisle_member_pos`).
  Positions are committed **in-wave**, so co-demanded SKUs cluster into adjacent columns →
  shorter sweep path → lower W. No partners yet ⇒ front bin.

### `expn` — `expansion` (Expand)  *(new, counter)*
- Mirror of `comp`: aisle with the **least** co-demand mass; bin in the column **FARTHEST**
  from the partner centroid → scatters co-demanded SKUs → longer sweep path. The upper-bound
  that brackets how much the co-demand placement lever is worth.

---

## 4. Summary

| family | drain | aisle choice | bin choice | reads | one-line |
|---|---|---|---|---|---|
| `fifo` | per-unit | random | random | — | random placement (baseline) |
| `rank` | ranked | random | min-D (front) | lift (sort only) | effort order, random aisle, front bay |
| `tmin` | ranked | min-D | min-D (front) | demand | hot units → front bins |
| `tmax` | ranked | max-D | max-D (back) | demand | hot units → back bins (control) |
| `cmax` | per-unit | max lift | min-D (front) | `_aisle_idx_sets` | co-locate co-picked SKUs in one aisle |
| `cmin` | per-unit | min lift | min-D (front) | `_aisle_idx_sets` | anti-cohesion control |
| `comp` | ranked | max co-demand | column nearest partners | `_aisle_member_pos` | cluster co-demand into adjacent columns |
| `expn` | ranked | min co-demand | column farthest | `_aisle_member_pos` | scatter co-demand (control) |

*Common threads:* every reorder unit is scoped by `_candidates` to its BinKey pool; ranked
families place high-effort units first; cohesion/co-demand families accumulate aisle state
incrementally so clusters build up; the front/back "bin" choice is a `D`-rank pick except for
`comp`/`expn`, which choose the bin by **column position** relative to partners.

---

## 5. What the data showed (so the mechanics connect to outcomes)

- **Makespan is driven by within-aisle work `W`** (`diagnose_makespan.py`, r≈0.95), whose
  travel term is the **column-sweep path length** through a batch's demanded bins. Σf·D
  (layout depth) is *decoupled* (r≈0.16) and load balance is a near-constant overhead (r≈0.03).
- **Coherence, not depth, is the lever the ranked drain captures.** `fifo` scatters a SKU's
  units → long paths → highest W; the ranked families (`rank`/`tmin`/`tmax`) keep a SKU's
  units compact → similar W → **`tmin` and `tmax` tie on throughput** despite opposite Σf·D.
  In the full 12-profile study `rank`/`tmin` beat `fifo` by ~+11–15%.
- **Cluster** (`cmax`/`cmin`) co-locates at *aisle* granularity → only ~+2–6%.
- **Compaction** (`comp`) targets the finer **column-span** lever but, as a *reorder* rule on a
  uniform start, barely moves W (the compact↔expand gap is ~2 pp with near-identical W) —
  because only ~11% of bins reorder per batch, so the uniform initial layout dominates. The
  `comp`/`expn` bracket *measures* that the residual placement lever is small; the round-robin
  dispatcher ([fast_pick.py](../Warehouse/fast_pick.py), `i % n` over aisle-sorted tasks) and
  the initial layout are the larger levers.
