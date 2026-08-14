# Inventory Location Optimizer

A warehouse simulation study of **where to put stock, what the building looks like, and who picks
what next** — and how much each of those is actually worth.

The project began with one question: when restock arrives, does the choice of slot change how far
pickers walk? It does, but less than expected. Chasing that answer turned up two further levers
that matter more, and a distinction that most warehouse reporting quietly elides:

> **Three levers, two outcomes.** *Placement* (which bin restock goes into), *layout* (aisle
> length, velocity zoning), and *scheduling* (which picker takes which task) are three independent
> choices. They move two different quantities: **labor** — how much work exists — and
> **throughput** — how fast that work clears. Either can improve without touching the other.
> Conflating them is the usual reporting error.

Placement turns out to be a labor lever. Layout and scheduling turn out to be throughput levers.
Each experiment on this site isolates one of them, holds everything else frozen, and measures both
outcomes. A recurring finding: not every "optimization" wins; some make total picking time
*worse*.

## The story of a run

Every experiment is the same loop, run many times with one thing changed.

**A catalogue is generated.** Tens of thousands of synthetic SKUs, each with a size, a weight, a
product category, and a demand frequency drawn from a fitted distribution. Nothing is hand-picked
— the catalogue is a seeded random draw, so it can be regenerated exactly.

**A warehouse is planned around it and stocked.** Aisles and bins are sized to the catalogue's
actual cube, then filled to a target occupancy. Initial stock is placed either uniformly at random
or through the strategy's own placement rule — and it turns out this barely matters, which is
itself one of the findings.

**Orders arrive in batches and pickers walk.** Each batch samples a slice of the catalogue,
groups the picks into per-aisle tasks, and hands those tasks to a pool of pickers. Every pick costs
time: walking to the aisle, reaching the shelf height, handling the item's weight and volume,
swapping a full cart. That cost model is the simulation's core, and it is published in full on
each experiment's formula reference.

**Stock depletes, hits a reorder point, and restock arrives — and this is the experiment.** When
new units land, an *assignment function* chooses which empty bin each one goes into. One rule
places them wherever a slot is free (first-in-first-out — the do-nothing baseline). Others try to
minimise travel, to balance picker labor, to cluster co-picked items together, or deliberately to
do the worst possible thing, to bound how much the lever is worth at all.

**Repeat, then compare.** A hundred batches later, every rule is scored against the FIFO baseline
on labor and on throughput, with significance tests. Two independent warehouses run this loop side
by side over one shared catalogue — a **store** replenishment channel with big carts and long
trips, and a **fulfillment** channel with small carts and constant cart swaps — because a rule that
wins one does not necessarily win the other.

**Then the run is replayed.** Freeze the inventory and the batch stream, change one structural
thing — cut the aisles in half, switch on velocity zoning, swap the picker scheduler — and run the
whole sweep again. Each frozen variant is a **cell**, and because everything else is held
byte-identical, the difference between cells is attributable to the one thing that changed.

## What we know so far

- **The three levers are close to orthogonal.** Placement moves labor and barely touches
  throughput; layout and scheduling move throughput and barely touch labor. Gains appear to
  compose rather than trade off.
- **Placement is real but bounded** — single-digit percentages, and the size of the prize depends
  on the cost model. Where picks are cheap, there is little for a placement rule to recover.
- **Most "optimizations" lose.** Across the full 17-family suite only a handful beat FIFO. An
  optimization aimed at the wrong proxy makes total task time *worse*, not merely flat.
- **The initial layout barely matters** (under 0.1 pp between the two). The restock rule drives
  the result — which is convenient, because restock is the decision a real warehouse actually
  makes every day.
- **The channel changes the winner.** The same catalogue, split into a store warehouse and a
  fulfillment warehouse, produces two different champions.
- **Throughput has more headroom than placement ever did.** Shortening aisles and load-balancing
  the picker queue each beat the best placement rule, and neither costs any labor.
- **Every hour on this site is modeled sim pick-time** — the cost model's own output, not
  wall-clock and not a staffing estimate. It is a comparable measure of effort between arms, and
  nothing more.

## The current experiment

Each experiment is self-contained — its own definitions, inventory, strategy catalogue, results,
and glossary — so a later sweep can change the setup without disturbing earlier ones.

**[Experiment 6 — Results](experiments/experiment-6/index.md)** is the current sweep and the place
to start. Throughput is measured as a *rate* rather than an end-of-run total: swapping round-robin
for **LPT** load-balancing lifts throughput by a median **+13.7 % (store)** and
**+5.6 % (fulfillment)** while total labor moves by at most **±0.07 %**. The gain is removed picker
*idle* time, not reduced work. On the same run the best placement rule cuts labor by **5.5 %**
against the FIFO baseline and converts most of that into **+8.5 %** throughput. Two levers, two
outcomes, one run — and they stack.

Every percentage on this page, and on Experiments 4 and 5, comes from a run made before commit
`753d01e` made bin selection deterministic: that fix lifts *absolute* throughput ~1.4 % and leaves
labor unchanged, and these are ratios in which the shift largely cancels — see the
[throughput page](experiments/experiment-6/comparison.md) for the absolute figures it does move.

## Earlier experiments (reference)

These are **superseded** by Experiment 6 and kept for reference. Each was a different run with
different settings, so their headline numbers are not directly comparable with Experiment 6's or
with each other — but each one is the only place a particular lever was measured.

- **[Experiment 5](experiments/experiment-5/index.md)** — the scheduler is a throughput lever at
  **zero labor cost**; the assignment function is the labor lever; and **they stack**. The
  deliberately adversarial arm confirms the bound by going the other way.
- **[Experiment 4](experiments/experiment-4/index.md)** — warehouse geometry. Halving aisle length
  lifts throughput **≈ +11 % (store)** and **≈ +6 % (fulfillment)** for **100 % of arms** — a
  geometric win, not a strategy-specific one. **Velocity zoning consistently hurts.** The practical
  read: shorten aisles, don't zone.
- **[Experiment 3](experiments/experiment-3/index.md)** — demand shape. A bell-shaped demand
  mixture sharpens the store win (**≈ −4.9 %**) and shrinks the fulfillment win (**≈ −1.2 %**).
- **[Experiment 2](experiments/experiment-2/index.md)** — two **independent warehouses** from one
  shared catalogue. Two different champions emerge: **Rank_labor** for stores (**≈ −3.7 %**),
  **Compact** for fulfillment (**≈ −3.4 %**) — and `Compact` wins on labor while *losing* on
  makespan and throughput, the first genuine objective trade-off on the site.
- **[Experiment 1](experiments/experiment-1/index.md)** — the first sweep: one generic warehouse,
  four pick-time calibrations × two replenishment lead-time variants. Establishes that placement
  beats FIFO, that the margin scales with how expensive a pick is (**−1.7 %** to **−9.5 %**), and
  that the restock rule matters far more than the initial layout.

## What comes next

**[Future experiment discussion](future-experiments.md)** — what has been answered, and the levers
still worth a sweep.

## What's inside an experiment

Start from **Results** and go as deep as you want — the pages are ordered shallowest first, and
each one is self-contained:

| Page | What it gives you |
|---|---|
| **Results** | the findings and the three figures they rest on. Read this and stop, if you like |
| **Throughput** | how fast the work clears, and how the scheduler moves it |
| **Labor** | how much work there is, and how placement moves it — every arm, including the losers |
| **How a run works** | the lifecycle end-to-end, and what the sweep holds constant vs. varies |
| **Formula reference** | the pick-time cost model, the labor decomposition, and every assignment function's scoring objective |
| **Inventory distributions** | the catalogue this experiment actually used — sizes, weights, demand, and the reorder model |
| **Glossary** | terms and symbols |

Experiments 1–5 predate this ordering and still use the older page names (*Overview*,
*Comparison*, *Full results*).

Setup parameters and the cross-cell matrix on these pages are rendered from the run's own committed
JSON rather than typed by hand. The prose around them is not — a handful of figures are quoted by
hand from run outputs that are not committed — which is why each experiment page names the commit
its run was produced with, and why a change to the simulator gets a dated note rather than a silent
edit.

The **[code map](code-graph.md)** is a separate, offline-capable browser for the simulator's source
— every module, class, function, and constant, with callers, callees, and layer boundaries. To run
the simulator yourself, start from the
[README in the repository](https://github.com/EdgyPage/Inventory_Location_Optimizer).
