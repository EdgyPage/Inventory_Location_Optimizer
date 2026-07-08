# {{ experiment().title }}

<!-- Overview / definitions for this experiment. Edit the prose; the data wires up from
     experiment.yml, so there are no hard-coded run IDs below. -->

The third placement-strategy sweep. It keeps [Experiment 2](../experiment-2/index.md)'s
**two independent warehouses** — a **store** channel and a **fulfillment** channel — but changes
two things:

1. **Bell-shaped demand.** Where Experiments 1–2 drew each SKU's pick-frequency from a *uniform*
   distribution, this catalogue draws it from **normals assigned by category** — food is the
   fast mover (~0.80), furniture the slow mover (~0.05), with chemical/clothing/electronic/seasonal
   in between — and fulfillment SKUs from an equal-weight **mixture of three normals**
   (means 0.15 / 0.25 / 0.55). See [Inventory distributions](inventory.md).
2. **Full assignment-function sweep on *both* channels.** Experiment 2 ran the store channel on a
   3-family labor subset; here **both** channels run the complete 17-family suite × {uni, opt}
   initial layout (34 arms), so store and fulfillment are compared arm-for-arm.

One shared seed-42 catalogue (150,000 SKUs, ~40% fulfillment), 100 batches, every arm measured
against a first-in-first-out (**{{ experiment().baseline }}**) baseline.

!!! abstract "Headline"
    Bell demand **sharpens the store win and shrinks the fulfillment win**. On steady-state
    production hours vs FIFO, the **store** warehouse is won by **Rank_cartlabor ≈ Rank_labor**
    (≈ **−4.9%** at base ergonomics, **−6.4%** under the steeper weight penalty) — larger than
    Experiment 2's ≈−3.7%. The **fulfillment** warehouse is still won by **Compact**, but only
    ≈ **−1.2%** (vs Experiment 2's ≈−3.4%) — its bell mixture is actually *less* dispersed than the
    uniform it replaced, so there is less demand skew to exploit. Across every arm the **initial
    layout barely matters** (`opt` beats `uni` by <0.1 pp); the restock rule drives the result.

!!! warning "Not a controlled A/B vs Experiment 2"
    This catalogue is **150k SKUs / 40% fulfillment**, whereas Experiment 2 was 130k / 23% and ran
    the store channel on a 3-arm subset. The cross-experiment numbers above are therefore
    *indicative*, not apples-to-apples. A clean uniform-vs-bell test needs a uniform run generated
    at these same parameters and the same full sweep.

## What's inside

- **[Simulation lifecycle](comparison-overview.md)** — how a run works end-to-end.
- **[Formula reference](formula-reference.md)** — pick-time model, task labor, and every
  assignment-function score.
- **Comparison write-up** — headline findings (top-3 vs baseline).
- **[Full results](full-results.md)** — every strategy across the sweep.
- **[Inventory distributions](inventory.md)** — the catalogue this experiment used.
- **[Glossary](glossary.md)** — terms and symbols.

## Inventory variants

{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — replenishment lead time **{{ inv_lead_time(key) }}**.
{% endfor %}

## Calibrations

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}
