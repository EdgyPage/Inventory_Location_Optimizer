# Glossary

Definitions for every term the [lifecycle](comparison-overview.md) and result write-ups
cite. Each entry has a stable anchor — link to one with `glossary.md#<id>` (the id is shown
in the heading link). Formula shapes match the code in
[`Warehouse/placement/Assignment_Functions.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/placement/Assignment_Functions.py)
and [`docs/design/ASSIGNMENT_SCORING.md`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/docs/design/ASSIGNMENT_SCORING.md).

## The experiment's own vocabulary

### Arm { #arm }
One complete simulated warehouse run: an [initial layout](#initial-layout) (`uni` or `opt`)
paired with one of the 17 restock families. 2 × 17 = **34 arms** per channel per inventory.
Every arm sees the identical batch sequence, so any gap between arms is the policy's doing.

### Cell { #cell }
One frozen variant of everything *outside* the placement loop. This sweep has two cells that
differ only in the picker scheduler: `k1_off_rr` (round-robin) and `k1_off_lpt` (LPT). The same
34 arms run in each cell, which is what makes the scheduler comparison an apples-to-apples diff.

### Scheduler — round-robin / LPT { #scheduler }
The rule deciding which picker takes which [task](#task). **Round-robin** deals tasks out in
aisle order, like dealing cards. **LPT** (longest processing time first) hands out the longest
tasks first, so no picker is left holding a heavy aisle at the end while others stand idle.

### Modeled hours { #modeled-hours }
Every "hour" on this site is the cost model's own output — pick setup + handling + travel +
cart swaps, summed. It compares effort between arms; it is **not** wall-clock time or a staffing
estimate, and absolute figures are model-scale. Percentages between two arms are the meaningful
quantity.

### Steady-state window { #steady-state }
The last 50 of the run's 75 batches — after the warehouse has churned into its working state.
Medians quoted "steady-state" use this window; "full-run" figures use every batch. The two agree
to within a percentage point here, and any page quoting a number says which window it means.

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
Picker travel speeds (ft·s⁻¹): `x_speed` cross-aisle, `y_speed` along-aisle. Recorded, with the
cart constants, in each leaf's committed `config.json` (staged beside that leaf's figures under
`images/`).

### Height bracket M(y) { #height-bracket }
An ergonomic multiplier on the whole at-location pick, keyed to shelf height `y_phys`
(the golden zone is cheapest). The `high_height` calibrations steepen it.

## Demand &amp; workload

### f_s — relative (pick) frequency { #f-s }
A SKU's pick-selection weight as a **[0,1] relative share** — *not* an absolute pick rate;
stored as `relative_frequency` — the same name in the Python model and the `cartons` DB column. Drives weighted batch sampling,
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
pickers by the cell's [scheduler](#scheduler), and each task's cost is its [labor $W$](#workload).

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
Total within-aisle picker work per batch (Σ of `W` over the batch's tasks). The metric that
actually tracks [makespan](#makespan) (r ≈ 0.95).

### Makespan { #makespan }
Wall-clock time to clear all of a batch's picks across the pickers. What the simulation
ultimately minimises; [productivity hours](#productivity-hours) is its best single-number proxy.

### Churn { #churn }
Fraction of bins that turn over per batch — the reorder waves the assignment function has to
place well to hold the layout.

## Inventory control

### q_eq — equilibrium quantity { #q-eq }
Target steady-state stock per SKU. The planner sizes it as coverage × expected per-batch
demand, but that is not what ends up in the catalogue: the whole inventory is then rescaled
to fit the warehouse, so the stored values are far smaller than the formula asks for. The
realised distribution is published on the
[comparison overview](comparison-overview.md#the-inventory-model) — read that, not a formula.

### ROP — reorder point { #rop }
Threshold that triggers replenishment, derived per SKU from its expected demand and lead
time and then rescaled with `q_eq`. **No closed form reproduces the stored values** — the
closest candidate matches about half of them — which is why the pages publish the
distribution instead of an equation.

### coverage { #coverage-safety }
Batches of demand the planner *aims* to hold at equilibrium (10 here), before the
warehouse-fit rescale. There is no separate *safety* parameter on the builder this run
used: the reorder point is derived from lead time alone. Earlier pages described a
`lead + safety` form, which belongs to a different builder and was never what ran here.

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
sweeps 34 [arms](#arm) (2 initial layouts × 17 restock families). This run's winners are
**Rank_labor / Rank_cartlabor / Rank_minlabor**; **FIFO** (first-in-first-out) is the
uniform-random baseline everything is measured against. See the
**[Formula reference](formula-reference.md#the-families)** for the full catalogue of all 17
families and their scoring equations.
