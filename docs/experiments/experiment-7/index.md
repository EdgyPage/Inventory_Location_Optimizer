# {{ experiment().title }}

!!! info "Superseded — kept for reference"
    [**Experiment 8**](../experiment-8/index.md) is the current sweep: the same two-lever design
    at full production scale on the upgraded (v2) demand stream. This page's numbers belong to
    the previous demand stream and are not comparable with Experiment 8's — read this experiment
    as the design's second replication, not as current results.

**Two levers, two outcomes, one run.** Where restock gets put away (**placement**) decides how much
work a day contains. Who picks what next (**scheduling**) decides how fast that work clears. This
experiment moves each lever separately, on a fresh catalogue, and measures both outcomes — so the
two never get conflated.

Both levers are rules inside dispatch software, not construction projects. The three that matter
on this page, in plain terms:

- **FIFO** — restock goes into the first free slot. The do-nothing baseline everything is
  measured against.
- **`Rank_labor`** — restock goes where it least burdens the busiest aisle, so no single aisle
  becomes the slow one everyone waits behind.
- **LPT** (longest-processing-time) — hand pickers the biggest jobs first, so the crew finishes
  together instead of everyone waiting on whoever drew the heaviest aisle last.

!!! success "The finding"
    - **Placement is a store lever.** `Rank_labor` cuts store hands-on pick-hours by
      **2.7–3.4 %** against FIFO, in every inventory tested. In the fulfillment warehouse the
      same rules move hours by **under 0.3 %** — effectively nothing.
    - **Scheduling speeds up both warehouses.** LPT clears the same day's work in about
      **two-thirds of the elapsed time** in the store (**+45 %** throughput) and **+5 %** in
      fulfillment — while the amount of hands-on work changes by less than **±0.06 %** in every
      one of the 136 comparisons. The speed-up costs nothing: the work itself did not change.
    - **The two gains are independent, and they stack.** One removes work; the other removes
      waiting. Adopt either without the other; together they compound.

**Two warehouses, not one.** The experiment runs two independent simulated buildings over the
same catalogue, and every finding names which one it belongs to:

| | the work looks like | this experiment's lever |
|---|---|---|
| **store** channel | replenishment: big carts, long sweeps down each aisle — a retail backroom or DC | placement **and** scheduling |
| **fulfillment** channel | e-commerce piece-picking: small totes, short frequent trips | scheduling only |

A real network usually operates both kinds of building — apply each finding to the buildings
whose work looks like its channel. Every other term is one click away in the
[glossary](glossary.md).

## The quick read

One inventory, one warehouse, four ways to run it. Hands-on hours are the work; elapsed hours are
when the day's work was finished:

| | do-nothing placement + naive schedule | best placement + LPT schedule | change |
|---|---:|---:|---:|
| hands-on pick-hours | 230.6 | 224.3 | **−2.7 %** (placement) |
| elapsed hours to clear the run | 14.96 | 10.10 | **−32 %** (mostly scheduling) |
| items picked | 4,523,364 | 4,525,287 | same work |

<small>Store channel, `bell_lt0` inventory: `uni_fifo` under round-robin vs `opt_rank_labor` under
LPT. The second inventory reads the same — the ranges on the [labor page](full-results.md) span
both. Arm names read `<starting layout>_<placement rule>`: `uni` = random start, `opt` = the
rule's own ideal start ([glossary](glossary.md#initial-layout)). All numbers from the committed
[`data/whatif_volume.json`](data/whatif_volume.json).</small>

The two rows move for different reasons, and that is the whole experiment. Placement made the day
*smaller*; scheduling made it *denser*.

!!! question "How can the day end 32 % sooner if the work barely shrank?"
    Because pickers spend part of every batch **waiting**. Under a naive round-robin hand-out, the
    25 store pickers are hands-on for only **62 %** of the elapsed day — whoever draws the heaviest
    aisle finishes last while everyone else stands idle. LPT hands the longest tasks out first, so
    the crew finishes together: hands-on share rises to **89 %**. No work was removed — the same
    230 hours of picking happened — it was packed into fewer elapsed hours. That is why labor and
    throughput are different columns everywhere on this site.

!!! warning "What kind of evidence this is"
    This is a **controlled A/B experiment inside a simulation**: the same day of orders, replayed
    identically under 34 placement rules × 2 schedulers — 68 runs of a day no real building could
    run twice. That control is what a simulation buys; what it cannot buy is your building's exact
    numbers. Every "hour" is **modeled pick-time** (setup + handling + travel + cart swaps under a
    stated cost model), so absolute *levels* are model-scale — but the **percentage comparisons
    are exact**: the simulator is deterministic, each pair of runs replays the identical day, and
    the difference is a recomputation, not an estimate with error bars. That is why a headline
    can say +45 % while refusing to treat 448,196 items/h as a real-world rate. The model's size
    — 25 pickers, 384 aisles — is likewise arbitrary: the mechanisms (waiting at the end of a
    batch; travel per pick) exist in any building, and it is the *direction and ranking* of the
    results, not the third decimal, that transfers.

!!! note "Dated note — the demand stream changed after this sweep (2026-08-20)"
    This sweep ran on the corrected measurement basis (post-`753d01e`) with no caveat of its own.
    After it was published, the simulator's synthetic **demand stream was upgraded** (the v2
    order-draw engine): [Experiment 8](../experiment-8/index.md) is the first sweep on that
    stream and is the current baseline. These figures remain internally exact but are not
    comparable with v2-era sweeps — the convention, as always, is a dated note rather than
    silently edited numbers.

## Lever 1 — placement: less work to begin with

<figure markdown>
  ![Top runs vs FIFO — labor and throughput](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/top_vs_baseline_table.png){ width=920 }
  <figcaption><strong>Figure 1.</strong> The top three store placement rules from each
  initial-layout family, measured against the do-nothing FIFO baseline on labor and throughput at
  once. The <code>Rank</code> family — which places each arriving unit where it least burdens the
  busiest aisle — sweeps the podium. Source: <code>top_vs_baseline_table.png</code> (store,
  <code>bell_lt0</code>, cell <code>{{ experiment().run }}</code>).</figcaption>
</figure>

Read the labor column as *how much work the rule removed* and the throughput column as *how much
faster the day went*. The same rules were run in the fulfillment warehouse and moved its hours by
less than 0.3 % — the [labor page](full-results.md) treats that null result as a finding, not a
failure: fulfillment's small carts and short trips leave placement little travel to save.

## Lever 2 — the scheduler: the same work, finished sooner

<figure markdown>
  ![Cumulative volume vs elapsed time, round-robin vs LPT](images/{{ experiment().whatif.scatter }}){ width=920 }
  <figcaption><strong>Figure 2.</strong> Items picked so far (y) against elapsed hours (x), one
  panel per channel, for the same placement rule under <strong>both</strong> schedulers. The slope
  is throughput; the dot is the finish. Both lines reach the same height — the same work — but the
  LPT line is steeper and stops sooner. Source: <code>whatif_volume_curves.png</code>.</figcaption>
</figure>

This is the whole argument in one picture. Nothing about the warehouse, the catalogue, or the
placement changed between the two lines — only the order tasks were handed to pickers. One
deliberately cross-lever comparison sizes the prize: a warehouse that changes *only* its scheduler
(keeping do-nothing placement) still clears work faster than one that adopts the *best* placement
rule but keeps the naive schedule — 437,301 vs 312,197 items/h. If only one change is on the
table, change the scheduler.

## Reading the figures together

<figure markdown>
  ![Cumulative volume by placement rule, store](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/top3_by_initial_volume_curve.png){ width=920 }
  <figcaption><strong>Figure 3.</strong> The same cumulative-volume lens, now holding the scheduler
  fixed at LPT and varying the <strong>placement rule</strong>. Right panel: each rule's lead over
  FIFO at matched elapsed time. These are the same six runs as Figure 1's table. Source:
  <code>top3_by_initial_volume_curve.png</code> (store, <code>bell_lt0</code>, cell
  <code>{{ experiment().run }}</code>).</figcaption>
</figure>

| Figure | Lever it isolates | Compared against | Cell | Inventory | Channel |
|---|---|---|---|---|---|
| **1** — takeaway table | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |
| **2** — volume curves, rr vs LPT | scheduler | cell `{{ experiment().whatif.reference }}` | both | `bell_lt0` | store + fulfillment |
| **3** — volume curves by rule | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |

<small>Run <code>{{ experiment().whatif.source_run }}</code> ({{ run_commit() }}):
{{ experiment().whatif.cells }} cells × {{ experiment().whatif.arms }} arms ×
{{ experiment().whatif.n_batches }} batches. Every percentage on this page is quoted from the
committed <code>data/whatif_volume.json</code>, <code>data/whatif_labor.json</code>, or
<code>data/whatif_delta.json</code>; the figures are rendered from the same run's databases by
<code>Optimization/run_analysis.py</code>.</small>

## The ask

Nobody should re-slot a warehouse on a simulation's word — and nobody needs to. Both changes are
dispatch-software settings, pilotable in one building in weeks, with no capital and no
construction. What this experiment contributes is **which two changes, out of 34 tested, are
worth that pilot** — and exactly what to measure:

1. **Stores: place restock with `Rank_labor`** instead of first-free-slot. Modeled saving:
   **2.7–3.4 %** of hands-on pick-hours, steady from the first weeks, needing only data any WMS
   already has (demand rates and slot positions).
2. **Both channels: dispatch the longest tasks first (LPT)** instead of dealing them out in turn.
   Modeled effect: the same workload clears in **~32 % less elapsed time** in store, **~5 %** in
   fulfillment — at zero labor cost, under every one of the 34 placement rules tested.
3. **Measure the pilot the way the experiment measures:** hands-on hours (did the work shrink?)
   and time-to-clear (did the day end sooner?), against the current rule on comparable weeks. The
   model says both effects are large enough to see; the pilot prices them in your building's
   numbers.

Why trust the *selection*, even without trusting the exact percentages: the ranking is not
fragile. The scheduler gain is positive in **all 136 arm-comparisons across two very different
catalogues** (this run and [Experiment 6](../experiment-6/index.md)), and the `Rank_labor` family
leads store labor in **every cell and inventory of both sweeps**. Magnitudes move with the
catalogue; the winners don't.

## Go deeper

Ordered shallowest first — stop wherever the question is answered.

1. **[Throughput — the scheduler lever](comparison.md)** — how the rate was measured, the windows
   it was measured over, and the uplift across every placement rule.
2. **[Labor — the placement lever](full-results.md)** — whether the saving holds over the run, the
   proof the scheduler doesn't touch labor, and every arm including the losers.
3. **[How a run works](comparison-overview.md)** — the simulation lifecycle end-to-end, and what
   the sweep holds constant versus varies.
4. **[Formula reference](formula-reference.md)** — the pick-time cost model, the labor
   decomposition, and every placement rule's scoring objective.
5. **[Inventory distributions](inventory.md)** — the fresh catalogue this experiment used.
6. **[Glossary](glossary.md)** — every term on these pages, each with a stable anchor.

!!! warning "Not comparable with Experiment 6 — but it is the replication"
    [Experiment 6](../experiment-6/index.md) ran the same design on a **different catalogue** (and
    the older measurement basis). Its percentages differ — +13.7 % store scheduler uplift there vs
    +45 % here — because its catalogue loaded the store's aisles more evenly, leaving less idle
    time to recover. Do not read the two sweeps as a trend; read them as **the same finding
    surviving a change of inventory**: same winners, same direction, same zero-labor-cost
    scheduler gain. Current numbers come from this page.
