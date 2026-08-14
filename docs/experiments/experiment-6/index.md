# {{ experiment().title }}

**Two levers, two outcomes, one run.** Who picks what next (**the scheduler**) decides how fast the
work clears. Where restock gets put away (**placement**) decides how much work there is. This
experiment moves each one separately and measures both outcomes, so the two never get conflated.

!!! success "The finding"
    Switching the picker scheduler from round-robin to **LPT load-balancing** lifts throughput by a
    median **+13.7 % on store** and **+5.6 % on fulfillment** — while total labor moves by at most
    **±0.07 %** across every arm in the sweep. No work was removed; picker *idle* time was.

    On the same run, the best **placement** rule cuts labor by **5.5 %** against the do-nothing
    FIFO baseline, and picks up **+8.5 %** throughput as a side effect.

    The two gains come from different places and **stack**.

## The quick read

<figure markdown>
  ![Top runs vs FIFO — labor and throughput](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/top_vs_baseline_table.png){ width=920 }
  <figcaption><strong>Figure 1.</strong> The top three store placement rules from each initial-layout family
  (<code>Opt</code> and <code>Uni</code>), measured against the FIFO baseline on labor and on
  throughput at once. Both columns are paired over all 100 batches, and every row clears
  significance at p&lt;.001. <code>Rank_cartlabor</code> and <code>Rank_labor</code> lead on both;
  the <code>Map</code> family saves noticeably less labor yet lands within 0.6 pp of them on
  throughput. Source: <code>top_vs_baseline_table.png</code> (store, <code>bell_lt0</code>, cell
  <code>{{ experiment().run }}</code>).</figcaption>
</figure>

Read the labor column as *how much work the rule removed* and the throughput column as *how much
faster the day went*. They are related but not the same number, which is the point of the two
sections below.

!!! warning "Modeled hours, not wall-clock"
    Every "hour" on this site is **modeled sim pick-time** — the cost model's own output. It is a
    comparable measure of *effort* between arms, **not** a wall-clock schedule or a staffing
    estimate. A +13.7 % throughput gain is not a claim that a shift finishes 13.7 % sooner.

## Lever 1 — the scheduler: the same work, finished sooner

<figure markdown>
  ![Cumulative volume vs elapsed time, round-robin vs LPT](images/{{ experiment().whatif.scatter }}){ width=920 }
  <figcaption><strong>Figure 2.</strong> Cumulative items picked (y) against elapsed hours (x),
  one panel per channel, for a single placement rule run under <strong>both</strong> schedulers.
  The <strong>slope is throughput</strong> and the dot is the finish. Both lines reach the same
  height — the same work — but the LPT line is steeper and stops sooner. The store panel is on the
  right. Source: <code>whatif_volume_curves.png</code>.</figcaption>
</figure>

This is the whole argument in one picture. Nothing about the warehouse, the catalogue or the
placement rule changed between the two lines; only the order in which tasks were handed to pickers
did. The store line climbs from **553,477** to **627,725 items/h** and finishes 1.6 hours earlier,
while total labor moves by **+0.002 %**.

That effect is large enough to beat the placement lever outright: FIFO — the *do-nothing* placement
baseline — reaches **578,595 items/h** once it is scheduled with LPT, which is faster than the best
placement rule managed under round-robin (**553,477 items/h**). Scheduling is the bigger prize, and
it is free.

## Lever 2 — placement: less work to begin with

<figure markdown>
  ![Cumulative volume by placement rule, store](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/top3_by_initial_volume_curve.png){ width=920 }
  <figcaption><strong>Figure 3.</strong> The same cumulative-volume view, now holding the scheduler fixed at LPT and varying
  the <strong>placement rule</strong> instead. Left: the six rules against the grey dashed FIFO
  baseline. Right: each rule's lead over FIFO at matched elapsed time, where they separate cleanly.
  These are the same six runs, in the same order, as the table at the top of this page.
  Source: <code>top3_by_initial_volume_curve.png</code> (store, <code>bell_lt0</code>, cell
  <code>{{ experiment().run }}</code>).</figcaption>
</figure>

Here the lines do **not** all reach the same height, because the rules genuinely differ in how much
work they create — that is the labor column of the table, made visible. The right-hand panel
compares at *matched* elapsed time for exactly that reason.

## Reading the three figures together

They are one run seen three ways, not three studies:

- **Figures 1 and 3 are the same six runs.** Both are selected as the top three placement
  rules within each initial-layout family, ranked on total task time, from the same store /
  `bell_lt0` leaf of cell `{{ experiment().run }}`, against the same FIFO baseline.
- **Figure 2 and Figure 3 share a number.** `Opt|Rank_labor|noRSL` reads **627,725 items/h** in
  both — the LPT line in Figure 2 and the top line in Figure 3 are the same run. That is the seam
  where the two levers meet.
- **The two throughput measures agree to within about 0.8 pp.** The table's column is a paired
  median of per-batch throughput; Figure 3's legend is a cumulative endpoint ratio. Different
  statistics on the same series, which is why they are close rather than identical.

Where these figures come from:

| Figure | Lever it isolates | Compared against | Cell | Inventory | Channel |
|---|---|---|---|---|---|
| **1** — takeaway table | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |
| **2** — cumulative volume, rr vs LPT | scheduler | cell `{{ experiment().whatif.reference }}` | both | `bell_lt0` | store + fulfillment |
| **3** — cumulative volume by rule | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |

<small>Run <code>{{ experiment().whatif.source_run }}</code>: {{ experiment().whatif.cells }} cells ×
{{ experiment().whatif.arms }} arms × {{ experiment().whatif.n_batches }} batches. Median deltas are
quoted from <code>whatif_volume.csv</code>; the per-run figures are rendered by
<code>Optimization/run_analysis.py</code> from that leaf's own simulation database.</small>

## Go deeper

Ordered shallowest first — stop wherever the question is answered.

1. **[Throughput — the scheduler lever](comparison.md)** — how the rate was measured, why the area
   under these curves would mislead, and the uplift across every assignment function.
2. **[Labor — the placement lever](full-results.md)** — whether the advantage holds over the run,
   the bound on the scheduler's effect on labor, and every arm including the ones that lose.
3. **[How a run works](comparison-overview.md)** — the simulation lifecycle end-to-end, and what
   this sweep holds constant versus varies.
4. **[Formula reference](formula-reference.md)** — the pick-time cost model, the labor
   decomposition, and every assignment function's scoring objective.
5. **[Inventory distributions](inventory.md)** — the catalogue this experiment actually used.
6. **[Glossary](glossary.md)** — terms and symbols, each with a stable anchor.

!!! warning "Not comparable with Experiment 5"
    [Experiment 5](../experiment-5/index.md) asked the same scheduler question and reports a larger
    store uplift. It is a **different run** with different fill, and it quotes a **different
    statistic**. See the [throughput page](comparison.md) for the full reconciliation.
