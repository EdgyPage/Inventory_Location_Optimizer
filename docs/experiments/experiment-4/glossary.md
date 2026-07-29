# Glossary

Definitions for every term the [lifecycle](comparison-overview.md) and result write-ups
cite. Each entry has a stable anchor — link to one with `glossary.md#<id>` (the id is shown
in the heading link). Formula shapes match the code in
[`Warehouse/placement/Assignment_Functions.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/placement/Assignment_Functions.py)
and [`docs/design/ASSIGNMENT_SCORING.md`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/docs/design/ASSIGNMENT_SCORING.md).

## Layout &amp; geometry

### Aisle { #aisle }
A row of the warehouse the picker sweeps end-to-end. Placement mostly decides **which aisle**
a SKU lands in; the number of aisles a batch visits is the dominant cost.

### Bin { #bin }
One storage slot inside an aisle, at physical offsets `x_phys` (along the aisle) and `y_phys`
(shelf height). Holds one SKU's stock (a *unit* is one order's worth of that SKU).

### BinKey { #binkey }
`(handling, category, storage_size, unit_type)` — the bucket a unit must be stored in. Every
assignment function chooses a bin *within* the unit's BinKey pool, so legality never has to be
re-checked.

### D — travel cost { #d }
Entrance-relative travel time to a bin: $D_b = x_{\text{pace}}\,x_{\text{phys}} +
y_{\text{pace}}\,y_{\text{phys}}$, where $x_{\text{pace}} = \tfrac{1}{12 v_x}$ and
$y_{\text{pace}} = \tfrac{1}{12 v_y}$ (`sec_per_inch` of the travel speeds $v_x,v_y$). Low
$D$ = **front bay** (shallow, cheap); high $D$ = back of the aisle. Realised aisle **travel**
is the **Manhattan** (L1) sweep: $x_{\text{trav}}x_{\text{pace}} + y_{\text{trav}}y_{\text{pace}}$
with $x_{\text{trav}} = \sum_i|x_{i+1}-x_i|$.

### x_speed / y_speed { #speeds }
Picker travel speeds (ft·s⁻¹): `x_speed` cross-aisle, `y_speed` along-aisle. Reported per run
in the setup table.

### Height bracket M(y) { #height-bracket }
An ergonomic multiplier on the whole at-location pick, keyed to shelf height `y_phys`
(the golden zone is cheapest). The `high_height` calibrations steepen it.

## Demand &amp; workload

### f_s — relative (pick) frequency { #f-s }
A SKU's pick-selection weight as a **[0,1] relative share** — *not* an absolute pick rate;
stored as `relative_frequency` (DB column `demand_frequency`). Drives weighted batch sampling,
so it shows up in every travel-weighted score: hot SKUs are the ones worth putting up front.

### q_s — pick quantity { #q-s }
Units taken per pick of SKU *s* (`demand_qty_rate`). `f_s·q_s` is the SKU's demand mass.

### expected_batch_demand { #expected-demand }
A SKU's expected units picked per batch, `≈ f_s·q_s`. Feeds the equilibrium and reorder model.

### Batch { #batch }
One picking wave: $n \sim \mathcal N(0.15\,N,\ 0.05\,N)$ distinct SKUs sampled by demand and
affinity. A run simulates many batches back-to-back.

### Task { #task }
A batch is decomposed into **tasks — one per aisle**: a task is the ordered sweep through the
bins a picker visits in a single aisle (with its SKU→quantity picks). Tasks are handed to the
pickers round-robin by aisle, and each task's cost is its [labor $W$](#workload).

### W — task labor (aisle workload) { #workload }
The realised time to clear one aisle (one [task](#task)), standardised as **handling + travel +
cart**: $W = H + T + C$. A **measurement** recorded per task — not what any scorer optimizes.

- **H — handling**: $\sum_{\text{stops}} M(y)\,(t_0 + q\,h)$ (the per-pick model summed over the
  aisle's stops; $h$ = [handling term](#handle-var), $q$ = quantity). *(code: `P`)*
- **T — travel**: the [Manhattan sweep distance](#d) $x_{\text{trav}}x_{\text{pace}} +
  y_{\text{trav}}y_{\text{pace}}$. *(code: `D`)*
- **C — cart**: $c_{\text{cart}}\cdot\max(0,\ \text{carts} - 1)$.

Full definitions on the [Formula reference](formula-reference.md#task-labor). $W$ is the realised
labor; placement scorers instead rank bins by the cheaper per-bin proxy
$\ell(b)$ — related, but **not** the same calculation.

### Σf·D — layout depth { #sigma-fd }
Demand-weighted within-aisle travel $\sum_{\text{bins}} f_s\,D_b$ over occupied bins. The
long-run lever that *feeds* $W$; its theoretical minimum puts the hottest SKUs in the lowest-$D$
bins (the rearrangement-inequality bound).

### Productivity hours (ΣW) { #productivity-hours }
Total within-aisle picker work per batch (Σ of the **analytical** workload `W` over the batch's
tasks). An a-priori proxy that correlates with [task makespan](#task-makespan) (r ≈ 0.95). Distinct
from the *realized* task makespan below: ΣW is scored from task structure; task makespan is the
sim's measured Σ task time (`ss_prod_hours` = Σ `task_stats.duration`).

### Task makespan { #task-makespan }
**Σ of every task's time in a batch = total labor** — equivalently, the makespan a single picker
would incur doing all tasks serially (Σ per-picker finish times, since pickers never idle). The
optimization target; **parallelism-independent** — a scheduler cannot change it.

### Batch makespan { #makespan }
**Wall-clock time to clear all of a batch's picks across the pickers** = the last picker to finish
(max done-time). Parallelism-*dependent*: a smarter task→picker schedule lowers it at unchanged task
makespan. This is what throughput actually tracks (throughput ≈ items / batch makespan).

### Throughput (two flavours) { #throughput }
Items ÷ a makespan. **Throughput / batch makespan** (`ss_thr` = items / batch makespan) is the
headline productivity rate and the one that responds to scheduling. **Throughput / task makespan**
(`ss_thr_task` = items / Σ task time) is the labor-efficiency rate, flat under scheduling. A
throughput/batch-makespan win at flat task makespan is a genuine (scheduling) win.

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
Inventory position = **on-hand** (units in bins) + **queued** (arrived, awaiting placement) +
**in-transit** (ordered, still in the [lead queue](#lead-queue)). Compared against ROP to
decide reordering, so an order fires at most once while stock is in transit.

### Lead queue { #lead-queue }
The in-transit component of [position](#position): orders on their way, each
`[sku, qty, remaining_lead]`, decremented each batch until `remaining_lead ≤ 0`, then released
to the queued state and placed.

## Placement

### Initial layout — uni / opt { #initial-layout }
How the warehouse is stocked once before batch 1: **uni** = uniform-random fill (a poor
start); **opt** = **policy-stocked** — the whole inventory is placed through the strategy's
*own* assignment function, so it begins at that strategy's ideal layout. The initial layout
sets the *starting point*; the assignment function sets the *attractor*.

### Pick-effort priority { #priority }
The order the ranked families place a wave in: $\text{priority} = f_i\,(t_0 + h) +
\beta\,\text{co-occur}$. Highest-priority unit claims its extremal bin first.

### h — handling term { #handle-var }
A unit's per-pick weight + volume effort, $h = c_w\,w^{e_w} + c_v\,\log_2 V$ (`handle_var` in
code) — **distinct from volume $V$**. It is the term inside the per-bin labor primitive
$\ell(b) = M(y_b)\,(t_0 + h) + D_b$, and inside the pick time $t_{\text{pick}} = M(y)\,(t_0 +
q\,h) + c_{\text{cart}}\,\mathbb{1}[\text{swap}]$.

### lift { #lift }
Probabilistic co-occurrence strength between two SKUs (from the affinity matrix). `lift > 1`
means they are co-picked more than chance.

### co_occur { #co-occur }
Demand-weighted affinity of a SKU to an aisle's current members:
$\text{co-occur} = \sum_{p\,\in\,\text{aisle}} \bigl(\text{lift}(s,p) - 1\bigr) f_p$. The
cohesion objective; also a small subsidy inside the travel score.

### β — affinity weight { #beta }
Weight (default `1.0`) converting `lift·freq` into the score's units — how much co-location is
rewarded against travel.

### Assignment function (restock family) { #assignment-function }
The rule that places reorder waves each batch — the one thing strategies differ on. The grid
sweeps 34 arms (2 initial layouts × 17 restock families). The three winners are **Rank_labor /
Map / Map_rank**; **FIFO** (first-in-first-out) is the uniform-random baseline everything is
measured against. See the **[Formula reference](formula-reference.md#the-families)** for the full
catalogue of families and their scoring equations.

## What-if: aisle-split &amp; velocity zoning

Terms specific to this experiment — the levers the 9-cell matrix sweeps. The assignment functions
above run *inside* every cell; these describe how each cell reshapes the warehouse itself.

### Cell { #cell }
One point in the what-if matrix: a fixed choice of aisle-split `k`, capacity loss, and velocity
zoning, named `k{k}[_l{loss%}]_{zoning}` (e.g. `k2_l10_abc3`). All cells share **one frozen
inventory + one batch stream**, so a cell-to-cell delta isolates the layout/zoning effect. The
un-split, un-zoned cell **`k1_off`** is the reference every other cell is measured against.

### Aisle-split (k) { #aisle-split }
Cut each aisle into **`k` shorter segments** (`k = 1` = un-split). Because the one-way lane model
makes per-visit x-travel scale with aisle length, halving the aisle (`k = 2`) roughly halves the
dominant travel term on every visit — a purely **geometric** throughput gain no assignment function
can reproduce. The headline lever of this experiment.

### Capacity loss { #capacity-loss }
The fraction of bins **genuinely dropped at each cut** when an aisle is split (`k > 1` only) — the
physical cost of adding cross-aisle gaps, with **no** compensating aisles added (the warehouse
footprint is fixed). `k2_l10_*` cells drop 10% of bins per cut; `k2_l0_*` split with none.

### Velocity zoning (ABC) { #velocity-zoning }
"Like-with-like" placement: SKUs are banded by demand velocity and restricted to a matching zone of
aisles (hot SKUs → a small near zone), so batches can skip the cold aisles. `2-band` = hot/cold;
`3-band` = A/B/C. In this experiment it **reduces** throughput — the band restriction removes
placement freedom and lengthens the per-visit sweep in the hot band. See
[`Warehouse/inventory/Inventory_Management.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/inventory/Inventory_Management.py)
(`configure_zoning`).

### mass_thresholds { #mass-thresholds }
The cumulative demand-mass cut points that define the ABC bands. SKUs are sorted hottest-first and a
SKU's band is the number of thresholds its running cumulative mass (`relative_frequency ×
quantity_rate`, normalised) has passed. `[0.6, 0.9]` (3-band) puts the SKUs making the first 60% of
demand in band A, 60–90% in B, the tail in C; `[0.6]` (2-band) splits hot/cold at 60%.

### Δ throughput / Δ labor { #delta-metrics }
The two cross-cell axes. **Δ throughput** = steady-state gain in [throughput / batch
makespan](#throughput) vs `k1_off` (**+ = faster**); **Δ labor** = steady-state [task
makespan](#task-makespan) (total task-hours) *saving* vs `k1_off` (**+ = less work**). They do **not**
move together here: the levers change Δ throughput (via batch makespan) while Δ labor stays within
±1%, because this warehouse's labor is **cart-swap-dominated** (~87% of fulfillment task time is cart
swaps, which no layout change touches). The full run also reports Δ batch makespan and Δ throughput /
task makespan (see `whatif_delta.csv`).

### Frozen inventory { #frozen-inventory }
One planned inventory sampled once and **reused by every cell** (loaded rather than re-sampled), so
the batch stream — whose fingerprint depends on the sampled inventory, not the geometry — is byte-
identical across cells. This is what makes the matrix a controlled, apples-to-apples layout
comparison.
