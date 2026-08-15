# {{ experiment().title }}

!!! info "Superseded — kept for reference"
    [**Experiment 6**](../experiment-6/index.md) is the current sweep and asks this same
    scheduler question with a better measurement. Read it first. This page is a **different run**
    — fill was 0.90 store / 0.92 fulfillment here against 0.85 on both there — and it quotes a
    **different statistic**: steady-state means over the last 50 batches, where Experiment 6
    quotes full-run cumulative endpoints. The two store numbers are not comparable and must not be
    read as a change over time. Like Experiment 6, this run predates commit `753d01e`, which lifts
    absolute throughput ~1.4 % and leaves labor unchanged — the percentages quoted here are ratios
    in which that shift largely cancels.

<!-- Overview / definitions for this experiment. Edit the prose; the data wires up from
     experiment.yml, so there are no hard-coded run IDs below. -->

The second **what-if** experiment. Where [Experiment 4](../experiment-4/index.md) reshaped the
**warehouse** (aisle-split × velocity zoning), this one holds the warehouse fixed and sweeps the
**picker scheduler** — the rule that hands a batch's per-aisle *tasks* to the pickers. It reuses
Experiment 4's exact bell catalogue (the same seed-42, 150,000-SKU inventory and its two lead-time
variants), freezes one planned inventory, and replays the **same batch stream** across a two-cell
scheduler A/B so the cells differ **only** in how tasks are dispatched:

1. **A scheduler A/B, told in hours.** Both cells run the full 17-family × {uni, opt} suite
   (34 arms) on both channels, layout and zoning pinned **off**. The cells differ only in the
   scheduler: **`k1_off_rr`** hands tasks to pickers **round-robin by aisle** (the reference);
   **`k1_off_lpt`** uses **LPT** (longest-processing-time) load-balancing — always give the next
   task to the picker who will finish earliest. Everything is reported in **modeled labor hours**
   (Σ pick-time ÷ 3.6 M ms/h), not milliseconds.
2. **Two orthogonal levers.** The scheduler decides how the same task-seconds are **packed across
   pickers** (batch makespan → throughput); the **assignment function** decides how many
   task-seconds **exist at all** (total labor). Because they act on different quantities, the
   experiment measures each cleanly: throughput vs the round-robin cell, labor-hours-saved vs the
   FIFO arm within each cell.
3. **Total labor hours saved.** Every arm is measured against **FIFO** (uniform-random placement)
   *within its own scheduler* — the absolute modeled **labor hours** a better assignment function
   removes from the store channel, alongside the throughput the LPT scheduler adds.

One frozen catalogue, 100 batches, 34 arms per channel, two schedulers.

!!! abstract "Headline"
    **The scheduler is a throughput lever at zero labor cost; the assignment function is the labor
    lever — and they stack.** Swapping round-robin for **LPT** lifts steady-state throughput
    **≈ +20% (store)** and **≈ +5% (fulfillment)** while **total labor stays flat** (Δtask-makespan
    ≈ 0): LPT re-packs the *same* task-seconds into a shorter batch makespan, it does not remove
    work. Independently, **labor-balancing assignment functions** (`Rank_labor`, `Rank_cartlabor`,
    `Map`) cut total **store** labor by up to **≈ 10 modeled hours per 100 batches** vs FIFO, while
    the deliberately-adversarial `rank_maxlabor` *adds* ≈ 13. **Fulfillment labor barely moves with
    the assignment function** (±0.05 h) — that channel is single-item-dominated, so its only lever
    here is the scheduler.

!!! warning "What this does and doesn't control"
    Because both cells share one frozen inventory + batch stream, the scheduler comparison **is**
    controlled — the throughput deltas are clean. Two caveats on reading them: (1) the "winner" here
    is a **scheduler** (`k1_off_lpt`), not an assignment function; within each cell the arm ranking
    is the separate *labor* question (see [Full results](full-results.md)). (2) All "hours" are
    **modeled sim pick-time** (batch_stats ms ÷ 3.6 M), a comparable effort figure — **not**
    wall-clock or staffing hours.

## What's inside

- **[Simulation lifecycle](comparison-overview.md)** — how a run works end-to-end, and where the
  task→picker scheduler sits.
- **[Formula reference](formula-reference.md)** — pick-time model, task labor, the modeled-hours
  conversion, and every assignment-function score.
- **[Comparison write-up](comparison.md)** — the scheduler A/B, the cross-cell delta table, and the
  four labor-hours charts.
- **[Full results](full-results.md)** — the LPT winner (`k1_off_lpt`) and round-robin baseline
  (`k1_off_rr`) cells arm-by-arm.
- **[Inventory distributions](inventory.md)** — the (shared) bell catalogue this experiment used.
- **[Glossary](glossary.md)** — terms and symbols (incl. the two schedulers and modeled labor hours).

## Inventory variants

{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — replenishment lead time **{{ inv_lead_time(key) }}**.
{% endfor %}

## Calibrations

The two independent warehouse channels (per-channel pick-cost calibrations), unchanged from
Experiment 4:

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}
