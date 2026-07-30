# Glossary

Definitions for every term the [lifecycle](comparison-overview.md), [Highlights](highlights.md),
and [Everything else](everything-else.md) pages cite. Each entry has a stable anchor — link to one
with `glossary.md#<id>` (the id is shown in the heading link). Formula shapes match the code in
[`Warehouse/placement/Assignment_Functions.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/placement/Assignment_Functions.py)
and [`docs/design/ASSIGNMENT_SCORING.md`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/docs/design/ASSIGNMENT_SCORING.md).

## Channels &amp; carts

### Channel — store / fulfillment { #channel }
Experiment 2's two **independent warehouses**, generated from one shared catalogue but simulated
and analysed separately. **Store** uses a `StoreCart` (large capacity, 25 pickers, cross/along
speeds 3/2 ft·s⁻¹, steep $w^{1.5}$ weight term) and sweeps only the labor restock subset;
**fulfillment** uses a `FulfillmentCart` (small capacity, 20 pickers, speeds 2/4, gentle
$\log w$ weight term) and sweeps the full 17-family suite. Each channel can have a different
winner.

### Cart / cart swap { #cart }
A picker's container. When an aisle's picks exceed the cart capacity, the picker must **swap**
carts, costing $c_{\text{cart}}$ each. The large `StoreCart` swaps rarely (term ≈ 0); the small
`FulfillmentCart` swaps often — the lever [`Rank_cartlabor`](formula-reference.md#rank-cartlabor)
targets.

## Layout &amp; geometry

### Aisle { #aisle }
A row of the warehouse the picker sweeps end-to-end. Placement mostly decides **which aisle** a
SKU lands in; the number of aisles a batch visits is the dominant cost.

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
$y_{\text{pace}} = \tfrac{1}{12 v_y}$ (`sec_per_inch` of the travel speeds $v_x,v_y$). Low $D$ =
**front bay** (shallow, cheap); high $D$ = back of the aisle. Realised aisle **travel** is the
**Manhattan** (L1) sweep: $x_{\text{trav}}x_{\text{pace}} + y_{\text{trav}}y_{\text{pace}}$ with
$x_{\text{trav}} = \sum_i|x_{i+1}-x_i|$.

### x_speed / y_speed { #speeds }
Picker travel speeds (ft·s⁻¹): `x_speed` cross-aisle, `y_speed` along-aisle. Store runs 3/2;
fulfillment runs 2/4; the `fast_walkers` calibration raises cross-aisle speed to 4.

### Height bracket M(y) { #height-bracket }
An ergonomic multiplier on the whole at-location pick, keyed to shelf height `y_phys` (the golden
zone is cheapest).

## Demand &amp; workload

### f_s — relative (pick) frequency { #f-s }
A SKU's pick-selection weight as a **[0,1] relative share** — *not* an absolute pick rate; stored
as `relative_frequency` — the same name in the Python model and the `cartons` DB column. Drives
weighted batch sampling, so it shows
up in every travel-weighted score: hot SKUs are the ones worth putting up front. Its distribution
is on [Inventory baselines](inventory.md#relative-frequency-distributions).

### q_s — pick quantity { #q-s }
Units taken per pick of SKU *s* (`demand_qty_rate`). `f_s·q_s` is the SKU's demand mass.

### expected_batch_demand { #expected-demand }
A SKU's expected units picked per batch, `≈ f_s·q_s`. Feeds the equilibrium and reorder model.

### Batch { #batch }
One picking wave: $n \sim \mathcal N(\text{frac}\cdot N,\ 0.05\,N)$ distinct SKUs sampled by demand
and affinity (store fraction 0.15, fulfillment 0.20). A run simulates 100 batches back-to-back.

### Task { #task }
A batch is decomposed into **tasks — one per aisle**: a task is the ordered sweep through the bins
a picker visits in a single aisle (with its SKU→quantity picks). Tasks are handed to the pickers
round-robin by aisle, and each task's cost is its [labor $W$](#workload).

### W — task labor (aisle workload) { #workload }
The realised time to clear one aisle (one [task](#task)), standardised as **handling + travel +
cart**: $W = H + T + C$. A **measurement** recorded per task — not what any scorer optimizes.

- **H — handling**: $\sum_{\text{stops}} M(y)\,(t_0 + q\,h)$ (the per-pick model summed over the
  aisle's stops; $h$ = [handling term](#handle-var), $q$ = quantity). *(code: `P`)*
- **T — travel**: the [Manhattan sweep distance](#d) $x_{\text{trav}}x_{\text{pace}} +
  y_{\text{trav}}y_{\text{pace}}$. *(code: `D`)*
- **C — cart**: $c_{\text{cart}}\cdot\max(0,\ \text{carts} - 1)$.

Full definitions on the [Formula reference](formula-reference.md#task-labor). $W$ is the realised
labor; placement scorers instead rank bins by the cheaper per-bin proxy $\ell(b)$ — related, but
**not** the same calculation.

### Total task time / production time { #production-time }
The sum of task labor $W$ over a run — the headline metric every strategy is ranked on (lower =
better). The [percent tables](highlights.md) report each strategy's total task time versus FIFO.

### Throughput { #throughput }
Items completed per unit time — a **makespan-based** productivity rate. A strategy can *lower*
total task time yet *lose* throughput if it serialises the work (the Compact
[trade-off](highlights.md#the-compact-trade-off)).

### Makespan { #makespan }
Wall-clock time to clear all of a batch's picks across the pickers. Concentrating stock into tight
columns can raise makespan even while it lowers total labor.

### Σf·D — layout depth { #sigma-fd }
Demand-weighted within-aisle travel $\sum_{\text{bins}} f_s\,D_b$ over occupied bins. The long-run
lever that *feeds* $W$; its theoretical minimum puts the hottest SKUs in the lowest-$D$ bins (the
rearrangement-inequality bound).

### Churn { #churn }
Fraction of bins that turn over per batch — the reorder waves the assignment function has to place
well to hold the layout.

## Inventory control

### q_eq — equilibrium quantity { #q-eq }
Target steady-state stock, `q_eq = round(coverage × d̄)` for expected per-batch demand *d̄*.

### ROP — reorder point { #rop }
Threshold that triggers replenishment: `ROP = round(d̄ × (lead + safety))`.

### coverage / safety { #coverage-safety }
`coverage` = batches of demand held at equilibrium; `safety` = extra batches of buffer folded into
the ROP.

### Lead time { #lead-time }
Batches between ordering and arrival. `lt0` = immediate (0); `ltrand0-5` = uniform 0–5.

### Supply CV { #supply-cv }
Coefficient of variation on the received reorder quantity — how noisy a replenishment is.

### Position { #position }
Inventory position = **on-hand** (units in bins) + **queued** (arrived, awaiting placement) +
**in-transit** (ordered, still in the [lead queue](#lead-queue)). Compared against ROP to decide
reordering, so an order fires at most once while stock is in transit.

### Lead queue { #lead-queue }
The in-transit component of [position](#position): orders on their way, each
`[sku, qty, remaining_lead]`, decremented each batch until `remaining_lead ≤ 0`, then released to
the queued state and placed.

## Placement

### Initial layout — uni / opt { #initial-layout }
How the warehouse is stocked once before batch 1: **uni** = uniform-random fill (a poor start);
**opt** = **policy-stocked** — the whole inventory is placed through the strategy's *own*
assignment function, so it begins at that strategy's ideal layout. The initial layout sets the
*starting point*; the assignment function sets the *attractor*.

### Pick-effort priority { #priority }
The order the ranked families place a wave in: $\text{priority} = f_i\,(t_0 + h) +
\beta\,\text{co-occur}$. Highest-priority unit claims its extremal bin first.

### h — handling term { #handle-var }
A unit's per-pick weight + volume effort, $h = c_w\,g_w(w) + c_v\,g_v(V)$ (`handle_var` in code) —
**distinct from volume $V$**. The forms differ by channel: store uses $c_w w^{e_w} + c_v\log_2 V$,
fulfillment uses $c_w\log w + c_v\log V$. It is the term inside the per-bin labor primitive
$\ell(b) = M(y_b)\,(t_0 + h) + D_b$.

### lift { #lift }
Probabilistic co-occurrence strength between two SKUs (from the affinity matrix). `lift > 1` means
they are co-picked more than chance.

### co_occur { #co-occur }
Demand-weighted affinity of a SKU to an aisle's current members:
$\text{co-occur} = \sum_{p\,\in\,\text{aisle}} \bigl(\text{lift}(s,p) - 1\bigr) f_p$. The cohesion
objective; also a small subsidy inside the travel score.

### β — affinity weight { #beta }
Weight (default `1.0`) converting `lift·freq` into the score's units — how much co-location is
rewarded against travel.

### Assignment function (restock family) { #assignment-function }
The rule that places reorder waves each batch — the one thing strategies differ on. The
fulfillment channel sweeps **34 arms** (2 initial layouts × 17 restock families); the store channel
sweeps a 6-arm labor subset. The channel winners are **Rank_labor** (store) and **Compact**
(fulfillment); **FIFO** (first-in-first-out) is the uniform-random baseline everything is measured
against. See the **[Formula reference](formula-reference.md#the-families)** for the full catalogue
of all 17 families and their scoring equations.
