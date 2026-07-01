# Glossary

Definitions for every term the [lifecycle](comparison-overview.md) and result write-ups
cite. Each entry has a stable anchor — link to one with `glossary.md#<id>` (the id is shown
in the heading link). Formula shapes match the code in
[`Warehouse/Assignment_Functions.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/Assignment_Functions.py)
and [`Warehouse/ASSIGNMENT_SCORING.md`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/ASSIGNMENT_SCORING.md).

!!! info "Render check"
    This text comes from `docs/results/glossary.md`. **Sentinel:** `GLOSSARY-PAGE-LIVE-v1`.

## Layout &amp; geometry

### Aisle { #aisle }
A row of the warehouse the picker sweeps end-to-end. Placement mostly decides **which aisle**
a SKU lands in; the number of aisles a batch visits is the dominant cost.

### Bin { #bin }
One storage slot inside an aisle, at physical offsets `x_phys` (along the aisle) and `y_phys`
(shelf height). Holds the stock of a single SKU unit.

### BinKey { #binkey }
`(handling, category, storage_size, unit_type)` — the bucket a unit must be stored in. Every
assignment function chooses a bin *within* the unit's BinKey pool, so legality never has to be
re-checked.

### D — travel cost { #d }
Entrance-relative travel time to a bin: `D_b = x_pace·x_phys + y_pace·y_phys`, where
`x_pace = sec_per_inch(x_speed)` and `y_pace = sec_per_inch(y_speed)`. Low `D` = **front bay**
(shallow, cheap); high `D` = back of the aisle.

### x_speed / y_speed { #speeds }
Picker travel speeds (ft·s⁻¹): `x_speed` cross-aisle, `y_speed` along-aisle. Reported per run
in the setup table.

### Height bracket M(y) { #height-bracket }
An ergonomic multiplier on the whole at-location pick, keyed to shelf height `y_phys`
(the golden zone is cheapest). The `high_height` calibrations steepen it.

## Demand &amp; workload

### f_s — pick frequency { #f-s }
How often SKU *s* is picked (its `demand_frequency`). Drives every travel-weighted score:
hot SKUs are the ones worth putting up front.

### q_s — pick quantity { #q-s }
Units taken per pick of SKU *s* (`demand_qty_rate`). `f_s·q_s` is the SKU's demand mass.

### expected_batch_demand { #expected-demand }
A SKU's expected units picked per batch, `≈ f_s·q_s`. Feeds the equilibrium and reorder model.

### Batch { #batch }
One picking wave: `N(mean_fraction·N, std_fraction·N)` SKUs sampled by demand and affinity.
A run simulates many batches back-to-back.

### W — aisle workload { #workload }
The realised time to clear one aisle, `W = D + P + C`. A **measurement** recorded per task —
not what any scorer optimizes.

- **P — pick time**: `Σ_stops (intercept + weight_coef·ln(w)·q + volume_coef·ln(v)·q)`.
- **C — cart penalty**: `cart_swap_coef · max(0, carts − 1)`.

### Σf·D — layout depth { #sigma-fd }
Demand-weighted within-aisle travel summed over occupied bins. The long-run lever that *feeds*
`W`; the `opt` layout is its rearrangement-inequality optimum.

### Productivity hours (ΣW) { #productivity-hours }
Total within-aisle picker work per batch. The metric that actually tracks makespan (r ≈ 0.95).

### Churn { #churn }
Fraction of bins that turn over per batch (~11% here) — the reorder waves the assignment
function has to place well to hold the layout.

## Inventory control

### q_eq — equilibrium quantity { #q-eq }
Target steady-state stock, `q_eq = round(coverage × d̄)` for expected per-batch demand *d̄*.

### ROP — reorder point { #rop }
Threshold that triggers replenishment: `ROP = round(d̄ × (lead + safety))`.

### coverage / safety { #coverage-safety }
`coverage` = batches of demand held at equilibrium (10 here); `safety` = extra batches of
buffer folded into the ROP (2 here).

### Lead time { #lead-time }
Batches between ordering and arrival. `lt0` = immediate (0); `ltrand0-5` = uniform 0–5.

### Supply CV { #supply-cv }
Coefficient of variation on the received reorder quantity — how noisy a replenishment is.

### Position { #position }
Inventory position = on-hand + queued + in-transit. Compared against ROP to decide reordering,
so an order fires at most once while stock is in transit.

### Lead queue { #lead-queue }
Orders in transit, each `[sku, qty, remaining_lead]`; decremented each batch until it arrives
and is placed.

## Placement

### Initial layout — uni / opt { #initial-layout }
How the warehouse is stocked once before batch 1: **uni** = uniform-random (~62% efficient
start); **opt** = hottest SKU → lowest-`D` bin (~100% start, then decays). Sets the *starting
point*; the assignment function sets the *attractor*.

### Pick-effort priority { #priority }
The order the ranked families place a wave in:
`priority = f_i·(pick_intercept + pick_weight_coef·ln(w) + pick_volume_coef·ln(v)) + β·co_occur`.
Highest-priority unit claims its extremal bin first.

### handle_var (v) { #handle-var }
A unit's per-pick handling term (weight/volume effort), the *v* inside the per-bin labor
primitive `ℓ(b) = M(y_b)·(t₀ + v) + D_b`.

### lift { #lift }
Probabilistic co-occurrence strength between two SKUs (from the affinity matrix). `lift > 1`
means they are co-picked more than chance.

### co_occur { #co-occur }
Demand-weighted affinity of a SKU to an aisle's current members:
`co_occur = Σ_{partner p in aisle} (lift(s,p) − 1)·f_p`. The cohesion objective; also a small
subsidy inside the travel score.

### β — affinity weight { #beta }
Weight (default `1.0`) converting `lift·freq` into the score's units — how much co-location is
rewarded against travel.

### Assignment function (restock family) { #assignment-function }
The rule that places reorder waves each batch. The grid sweeps sixteen (2 initial layouts ×
8 restock families), including:

- **FIFO** — baseline; uniform-random bin, no ordering. Everything else is measured against it.
- **Rank_labor / Map / Map_rank** — the sweep winners; equations on the
  [lifecycle page](comparison-overview.md#top-3-assignment-functions).
- **TripMin / TripMax** — minimise / maximise `f_s·D − β·co_occur` (travel bracket).
- **MaxClu / MinClu** — maximise / minimise cohesion `Σ lift(s,p)·f_p` (affinity bracket).
- **Compact / Expand** — minimise / maximise within-aisle span to partners (co-demand bracket).
