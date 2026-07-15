# Glossary

Definitions for every term the [lifecycle](comparison-overview.md) and result write-ups
cite. Each entry has a stable anchor — link to one with `glossary.md#<id>` (the id is shown
in the heading link). Formula shapes match the code in
[`Warehouse/Assignment_Functions.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/Assignment_Functions.py)
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
sim's measured Σ task time.

### Task makespan { #task-makespan }
**Σ of every task's time in a batch = total labor** — equivalently, the makespan a single picker
would incur doing all tasks serially (Σ per-picker finish times, since pickers never idle). The
optimization target; **parallelism-independent** — a scheduler cannot change it. This is the
quantity the [assignment function](#assignment-function) moves; converted to
[modeled labor hours](#modeled-hours) it is the "total labor hours" the result pages report.

### Batch makespan { #makespan }
**Wall-clock time to clear all of a batch's picks across the pickers** = the last picker to finish
(max done-time). Parallelism-*dependent*: a smarter task→picker [scheduler](#scheduler) lowers it at
unchanged task makespan. This is what throughput actually tracks (throughput ≈ items / batch
makespan), and the quantity the scheduler moves.

### Throughput (two flavours) { #throughput }
Items ÷ a makespan. **Throughput / batch makespan** (`ss_thr` = items / batch makespan) is the
headline productivity rate and the one that responds to scheduling. **Throughput / task makespan**
(`ss_thr_task` = items / Σ task time) is the labor-efficiency rate, flat under scheduling. A
throughput/batch-makespan win at flat task makespan is a genuine **scheduling** win — exactly what
LPT delivers here.

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
The rule that places reorder waves each batch, choosing *which bin* each arriving unit lands in —
so it sets a batch's total [task makespan](#task-makespan) (labor). The grid sweeps 34 arms (2
initial layouts × 17 restock families). The store's biggest **labor-hours** savers are
**Rank_labor / Rank_cartlabor / Map**; **FIFO** (first-in-first-out) is the uniform-random baseline
everything is measured against. See the **[Formula reference](formula-reference.md#the-families)**
for the full catalogue of families and their scoring equations.

## What-if: picker scheduler &amp; labor hours

Terms specific to this experiment — the lever the two-cell A/B sweeps. The assignment functions
above run *inside* every cell and set the labor; these describe how each cell dispatches that fixed
work across pickers.

### Cell { #cell }
One point in the what-if A/B: a fixed choice of picker [scheduler](#scheduler), with layout and
zoning pinned off, named `k1_off_{scheduler}` (`k1_off_rr`, `k1_off_lpt`). Both cells share **one
frozen inventory + one batch stream**, so a cell-to-cell delta isolates the scheduler effect.
`k1_off_rr` is the reference every throughput delta is measured against.

### Scheduler { #scheduler }
The rule that hands a batch's per-aisle [tasks](#task) to the $K$ pickers. It changes only *when*
each task-second is worked, so it moves [batch makespan](#makespan) (throughput) at **unchanged**
[task makespan](#task-makespan) (labor) — the opposite of the [assignment function](#assignment-function),
which moves labor.

- **round-robin (`rr`)** — tasks dealt to pickers in aisle order, one each in turn. Simple, but a
  picker can draw a run of heavy aisles and become the batch's bottleneck. The reference cell.
- **LPT (`lpt`)** — *longest-processing-time* list scheduling: each task goes to the picker who will
  finish earliest, longest tasks first. The classic makespan-minimising heuristic for identical
  machines; here it lifts store throughput ≈ +20% at flat labor.

### Modeled labor hours { #modeled-hours }
A time total converted from the simulation's milliseconds to hours, **ms ÷ 3,600,000**. "Total
labor hours" = Σ [task makespan](#task-makespan) ÷ 3.6 M; "batch-time hours" = Σ [batch
makespan](#makespan) ÷ 3.6 M. **These are modeled sim pick-time**, a comparable *effort* figure
across arms and schedulers — **not** wall-clock elapsed time or a staffing estimate. `throughput ×
3.6 M` gives items/hour.

### Labor hours saved { #labor-saved }
For an arm, the modeled [labor hours](#modeled-hours) **removed vs the FIFO arm** in the same
scheduler / channel / inventory / initial layout: `saved = labor(fifo) − labor(arm)` (**+ = less
labor**), following the same `base − arm` convention as
[`Optimization/run_channel_rollup.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/run_channel_rollup.py).
Because labor is scheduler-independent, an arm's saving is (near-)identical under round-robin and
LPT. Computed by
[`Optimization/run_whatif_labor.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/run_whatif_labor.py)
into `whatif_labor.csv`.

### Δ throughput / Δ labor { #delta-metrics }
The two cross-cell axes. **Δ throughput** = steady-state gain in [throughput / batch
makespan](#throughput) vs `k1_off_rr` (**+ = faster**); **Δ labor** = steady-state [task
makespan](#task-makespan) *saving* vs `k1_off_rr` (**+ = less work**). They do **not** move together
here: the LPT scheduler moves Δ throughput (via batch makespan) while Δ labor stays ≈ 0, because a
scheduler cannot change total task time. The full run also reports Δ batch makespan and Δ throughput
/ task makespan (see `whatif_delta.csv`).

### Frozen inventory { #frozen-inventory }
One planned inventory sampled once and **reused by every cell** (loaded rather than re-sampled), so
the batch stream — whose fingerprint depends on the sampled inventory, not the scheduler — is byte-
identical across cells. This is what makes the A/B a controlled, apples-to-apples scheduler
comparison.
