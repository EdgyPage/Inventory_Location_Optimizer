# Inventory Location Optimizer

A warehouse simulation study of **where to put stock, what the building looks like, and who picks
what next** — and how much each of those is actually worth.

The project began with one question: when restock arrives, does the choice of slot change how far
pickers walk? It does, but less than expected. Chasing that answer turned up two further levers
that matter more, and a distinction that most warehouse reporting quietly elides:

> **Three levers, two outcomes.** *Placement* (which slot restock goes into), *layout* (aisle
> length, zoning), and *scheduling* (which picker takes which job next) are three independent
> choices. They move two different quantities: **labor** — the hands-on hours you pay for — and
> **throughput** — how fast the day's orders clear the building. Either can improve without
> touching the other, and a report that blends them into one "efficiency" number hides which
> lever actually did the work.

Each experiment on this site isolates one lever, holds everything else frozen, and measures both
outcomes.

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
swapping out a full cart for an empty one. That cost model is the simulation's core, and it is
published in full on each experiment's formula reference.

**Stock depletes, hits a reorder point, and restock arrives — and this is the experiment.** When
new units land, an *assignment function* — the software rule deciding which empty slot each unit
goes into — makes the one choice the experiments compare. One rule places units wherever a slot is
free (first-in-first-out — the do-nothing baseline). Others try to minimise travel, to balance
picker labor, to cluster co-picked items together, or deliberately to do the worst possible thing,
to bound how much the lever is worth at all.

**Repeat, then compare.** A hundred batches later, every rule is scored against the FIFO baseline
on labor and on throughput, with significance tests. Two independent simulated warehouses run this
loop side by side over one shared catalogue: a **store** channel — replenishment-style work,
big carts, long sweeps down each aisle, the shape of a retail backroom or distribution center —
and a **fulfillment** channel — e-commerce piece-picking, small totes, short frequent trips. A
real network usually operates both kinds of building; each finding on this site names which kind
it applies to, because a rule that wins one does not necessarily win the other.

**Then the run is replayed.** Freeze the inventory and the batch stream, change one structural
thing — cut the aisles in half, switch on velocity zoning (reserving prime zones for
fast-movers), swap the picker scheduler — and run the
whole sweep again. Each frozen variant is a **cell**, and because everything else is held
byte-identical, the difference between cells is attributable to the one thing that changed. This
is the same-day-run-twice A/B test no real building can perform, and it is the reason a
simulation is used at all.

## What we know so far

- **The three levers move independently.** Placement moves labor and barely touches
  throughput; layout and scheduling move throughput and barely touch labor. Gains from different
  levers add up rather than trade off.
- **Placement is real but bounded** — single-digit percentages, and the size of the prize depends
  on how expensive a pick is. Where picks are cheap, there is little for a placement rule to
  recover.
- **Most "optimizations" lose.** Across the full 17-family suite only a handful beat FIFO. An
  optimization aimed at the wrong objective makes total picking time *worse*, not merely flat.
- **The initial layout barely matters** (under 0.1 percentage points between a random start and a
  pre-optimized one). The restock rule drives the result — which is convenient, because restock is
  the decision a real warehouse actually makes every day.
- **The channel changes the winner.** The same catalogue, split into a store warehouse and a
  fulfillment warehouse, produces two different champions.
- **Throughput has more headroom than placement ever did.** Shortening aisles, and handing pickers
  the longest jobs first so the crew finishes together, each beat the best placement rule — and
  neither costs any labor.
- **Every hour on this site is modeled sim pick-time** — the cost model's own output, not
  wall-clock and not a staffing estimate. It is a comparable measure of effort between arms, and
  nothing more.

## The current experiment

Each experiment is self-contained — its own definitions, inventory, strategy catalogue, results,
and glossary — so a later sweep can change the setup without disturbing earlier ones.

**[Experiment 9 — Results](experiments/experiment-9/index.md)** is the current sweep and the place
to start. It asks the next question after the two pick-side levers: when several trailers are
waiting in the yard, **which one does the dock unload first?** Eleven dock rules — first come
first served, newest first, six "gain" rules that unload the trailer whose stock would land best,
two oracles allowed to read the future, and the no-yard pole — run the same forty site days over
the full catalogue on one **coupled** site (one dock, four doors, one receiving crew serving both
channels), with Experiment 8's winning placement rules putting the stock away. The answer is a
result rather than a ranking: **at this site's demand the unloading order does not move the pick
work** — the placement score shifts by at most **0.12 %** and total labour by at most
**±0.15 %** — and **what separates the rules is the yard bill**, from **19 trailer-days** past
the free threshold for first-come-first-served to **97** for the rules that defer trailers to
place fractionally better. The page states the mechanism in one paragraph (the forty-day script
asks for far fewer lines than the catalogue has SKUs, so a pack put away now is rarely picked
inside the window) and says which kind of site *could* see a difference.

Two housekeeping notes, told plainly. The ranking behind that finding was corrected before
publishing: unpriced demand is now charged at the leaf's own mean line, the tie floor is
**measured** from the run's paired batch-to-batch noise (0.080 %) instead of declared, and the
FIFO restock rider is read as a control rather than a replication. And Experiment 9 is a **new
era** — coupled channels, a standing yard with derived crews, the v3 demand stream — so its
numbers are compared within itself; Experiment 8 stays the reference for placement and
scheduling, which this run held fixed.

## Earlier experiments (reference)

These are **superseded** by Experiment 9 and kept for reference. Each was a different run with
different settings, so their headline numbers are not directly comparable with Experiment 9's or
with each other — but each one is the only place a particular lever was measured.

- **[Experiment 8](experiments/experiment-8/index.md)** — the two-lever design at full production
  scale on the v2 demand stream: LPT **+45 % (store)** / **+5 % (fulfillment)** throughput at
  ±0.07 % labor, and a split placement podium — the `Rank_labor` family (**~2.6 %**) where
  replenishment is predictable, the `Map` family (**~0.8 %**) where lead times are erratic.
  The reference for both pick-side levers, which Experiment 9 holds fixed at these winners.
- **[Experiment 7](experiments/experiment-7/index.md)** — the same two-lever design on the
  previous demand stream at reduced scale: LPT **+44.9 % (store)** / **+5.3 % (fulfillment)** at
  ±0.06 % labor, and the `Rank_labor` family leading store labor in both inventories
  (**2.7–3.4 %**). Experiment 8 replicated the scheduler finding almost exactly and showed the
  placement winner tracks the supply model. The last sweep on the v1 demand stream.
- **[Experiment 6](experiments/experiment-6/index.md)** — throughput measured as a *rate*
  (cumulative volume against elapsed time), the lens Experiment 7 inherits. On its catalogue,
  LPT lifted throughput **+13.7 % (store)** / **+5.6 % (fulfillment)** at **±0.07 %** labor, and
  the best placement rule cut labor **5.5 %**. The last sweep on the pre-`753d01e` measurement
  basis.
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
*Comparison*, *Full results*); Experiments 6–8 share the results-first names above.

Setup parameters and the cross-cell matrix on these pages are rendered from the run's own committed
JSON rather than typed by hand. As of Experiment 7 the quoted what-if numbers (throughput, labor)
are staged into each experiment's `data/` as well, so every percentage in the prose has a
committed, diffable source; earlier experiments quoted a handful of figures by hand from run
outputs that were not committed — which is why each experiment page names the commit its run was
produced with, and why a change to the simulator gets a dated note rather than a silent edit.

The **[code map](code-graph.md)** is a separate, offline-capable browser for the simulator's source
— every module, class, function, and constant, with callers, callees, and layer boundaries. To run
the simulator yourself, start from the
[README in the repository](https://github.com/EdgyPage/Inventory_Location_Optimizer).
