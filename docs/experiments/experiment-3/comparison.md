# {{ experiment().title }} — comparison

<!-- The run write-up. Fill the Summary/Discussion prose; tables, figures, and formulas
     resolve from experiment.yml. -->

!!! note "Summary"
    On steady-state production hours vs the **{{ experiment().baseline }}** baseline, the **store**
    channel is won by **Rank_cartlabor ≈ Rank_labor** (≈ **−4.9%** at base ergonomics, **−6.4%**
    under the `store_high_weight` penalty); the **fulfillment** channel by **Compact**
    (≈ **−1.2%** at base, **−0.7%** with fast walkers). The ranking holds across both lead-time
    variants (`bell_lt0`, `bell_ltrand0-5` — `ltrand` gains run marginally larger), and the `opt`
    initial layout beats `uni` by <0.1 pp everywhere: the restock rule, not the starting layout,
    drives the savings.

## Setup

**Pick-time cost model** (per-channel calibrations — store `StoreCart`, fulfillment
`FulfillmentCart`; full model + all calibrations on the
[Formula reference](formula-reference.md)):

{{ pick_time_formula((experiment().inventories.keys() | list) | first) }}

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}

**Top-3 assignment functions** (full catalogue on the [Formula reference](formula-reference.md)):

{{ assignment_formulas() }}

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

{{ setup_table(key) }}

{{ run_section(key) }}

!!! note "Notes"
    Store configs: the labor family wins (Rank_cartlabor / Rank_labor), Map / Map_rank are second
    (~−3%); the cohesion & compaction arms (CluMap, Compact, Expand) and the Rank_maxlabor bracket
    land worst — the intended sanity ordering. Fulfillment configs: Compact leads, with most other
    arms clustered near the FIFO baseline.
{% endfor %}

## Discussion

Bell demand moves the two channels in **opposite** directions. The **store** channel gains *more*
than under uniform demand: the per-category normals create a strong, high-variance spread of pick
frequencies (food ~0.80 down to furniture ~0.05), and the labor-balancing family
(Rank_labor / Rank_cartlabor) converts that skew into travel savings — ≈−4.9% at base ergonomics,
rising to −6.4% once the weight penalty is steep. The **fulfillment** channel gains *less*
(≈−1.2%): its bell mixture (means 0.15 / 0.25 / 0.55, overall mean ~0.32) is actually **less
dispersed** than the uniform `U(0,1)` it replaced, so there is less demand concentration for
Compact to exploit.

Two caveats bound the reading (see the [Overview](index.md) warning): this catalogue is 150k SKUs
/ ~40% fulfillment (vs Experiment 2's 130k / 23%), and the store channel now runs the full suite
rather than a 3-arm subset — so cross-experiment deltas are indicative, not a controlled A/B. The
clean test is a **uniform** catalogue generated at these exact parameters and the same full sweep;
comparing its per-channel savings against this run isolates the demand-shape effect.

See [Full results](full-results.md) for every strategy arm.
