# {{ experiment().title }}

<!-- Overview / definitions for this experiment. Edit the prose; the data wires up from
     experiment.yml, so there are no hard-coded run IDs below. -->

The first **what-if** experiment. Where Experiments 1–3 swept *assignment functions* over a fixed
warehouse, this one holds the assignment functions as the inner axis and sweeps the **warehouse
itself** — asking what happens to the same catalogue when we **reshape the aisles** and **zone by
velocity**. It reuses [Experiment 3](../experiment-3/index.md)'s exact bell catalogue (the same
seed-42, 150,000-SKU inventory and its two lead-time variants), freezes one planned inventory, and
replays the **same batch stream** across a 9-cell matrix so every cell differs **only** in layout:

1. **A layout sweep, not an assignment sweep.** Each of the 9 cells still runs the full
   17-family × {uni, opt} suite (34 arms) on both channels, but the *cells themselves* vary the
   warehouse: **aisle-split** `k ∈ {1, 2}` (halve every aisle into two shorter segments) ×
   **capacity loss** `∈ {0%, 10%}` (bins genuinely dropped at each cut, no compensating aisles) ×
   **velocity zoning** `∈ {off, 2-band, 3-band}` (ABC "like-with-like" placement). See the
   [Comparison](comparison.md) for the cell grid.
2. **Apples-to-apples.** One frozen inventory + one batch stream is reused by every cell (the batch
   fingerprint is layout-independent), so a cell-to-cell delta isolates the layout/zoning effect
   with nothing else moving.
3. **A new metric axis.** Every cell is diffed against the **`k1_off`** reference (no split, no
   zoning) on two axes: **throughput** (batches/time) and **total labor** (steady-state task
   hours) — because for this warehouse those two do not move together.

One frozen catalogue, 100 batches, 34 arms per channel, every cell measured against the `k1_off`
baseline layout.

!!! abstract "Headline"
    **Splitting aisles is the throughput lever; velocity zoning is a net loss; total labor barely
    moves.** Halving aisle length with no capacity loss (**`k2_l0_off`**) lifts steady-state
    throughput **≈ +11% (store)** and **≈ +6% (fulfillment)** — and it does so for **100% of the
    34 arms on both channels**, with `uni`/`opt` identical, so the gain is **geometric** (shorter
    aisles = less cross-aisle travel per visit), not arm-specific. **Velocity zoning consistently
    hurts**: alone it costs the store channel ≈ **−11%** throughput; stacked on a split it erodes
    the +11% gain to ≈ +1.6% and widens the arm spread. A 10% capacity loss trades a little
    throughput for a slight labor saving. **Total labor stays within ±1%** everywhere — the levers
    move throughput, not labor, because this warehouse's labor is **cart-swap-dominated**.

!!! warning "What this does and doesn't control"
    Because all cells share one frozen inventory + batch stream, the layout comparison **is**
    controlled — the deltas are clean. Two caveats on reading them: (1) the "winner" here is a
    **layout** (`k2_l0_off`), not an assignment function; within each cell the arm ranking is a
    separate question (see [Full results](full-results.md)). (2) The gains are **throughput**, not
    labor — a faster makespan from more parallel-friendly geometry, not less total picking work.

## What's inside

- **[Simulation lifecycle](comparison-overview.md)** — how a run works end-to-end.
- **[Formula reference](formula-reference.md)** — pick-time model, task labor, and every
  assignment-function score.
- **[Comparison write-up](comparison.md)** — the cell matrix, the cross-cell delta table, and the
  labor-vs-throughput scatter.
- **[Full results](full-results.md)** — the winner (`k2_l0_off`) and baseline (`k1_off`) cells
  arm-by-arm.
- **[Inventory distributions](inventory.md)** — the (shared) bell catalogue this experiment used.
- **[Glossary](glossary.md)** — terms and symbols (incl. aisle-split & velocity zoning).

## Inventory variants

{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — replenishment lead time **{{ inv_lead_time(key) }}**.
{% endfor %}

## Calibrations

The four independent warehouse channels (per-channel pick-cost calibrations), unchanged from
Experiment 3:

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}
