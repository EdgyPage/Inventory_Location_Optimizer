---
name: sampler-affinity-flattens-the-fulfillment-line-rate
description: "The batch sampler's affinity lift really does spread a fulfillment section's lines almost flat across frequency deciles (7.1x predicted spread delivered as 1.6x), so a per-SKU transient priced at the line share is wrong -- but the ~25% touched-SKU over-read was the v2 defect and the sign is now REVERSED"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6067bcf-6d9d-4b76-8d12-a47f0ecc9b9b
  modified: 2026-09-10T18:01:58.843Z
---

Measured 2026-09-10 on the reference pair (`comparison_20260909_204522`, 25 batches) while
checking the fragmentation closed form (department-calibration 34): by batch 25 the
fulfillment run had touched 41,669 SKUs where a Poisson thinning at the line share
`n * freq_s / sum(freq)` predicts 54,995; realized lines per SKU ran 0.42-0.60 across the
frequency deciles where the share predicts 0.13-0.96 (correlation frequency vs realized
lines 0.04; variance/mean 3.3 in the top decile). The cause is `Workload_Builder`'s
lift-weighted sampler: `weight(B) = freq(B) * prod lift(A, B)` over the partners already in
the batch, which the fulfillment affinity CSR dominates. The store reads clean (14,219
touched vs 14,102) because nearly every touched SKU sees exactly one line in 40 days.

**Why:** every closed form on the record (`coverage.daily_demand`, `expected_travel
.accumulate`, the fragmentation transient) weights SKUs by the line share; section SUMS are
right, per-SKU line COUNTS on fulfillment are not. The stationary fragmentation reads no
line rate and is unaffected; only a transient or a trajectory band is.

**AMENDED 2026-09-12 (dept-cal 45): the flatness survives, the touched-SKU number does not.**
Re-measured on the reference pair's whole 40-batch script, v2 against v3 on identical seeds:

| fulfillment | v2 | v3 |
|---|---|---|
| SKUs touched vs a share thinning | 59,534 vs 73,088 (**-18.5%**) | 83,294 vs 77,435 (**+7.6%**) |
| corr(freq, realized lines) | 0.046 | 0.136 |
| realized lines/SKU, lowest -> highest freq decile | 0.59 -> 0.83 | 0.61 -> 0.98 |
| the share law predicts | 0.20 -> 1.43 | 0.22 -> 1.57 |

So the **flattening is real and large**: the share predicts a 7.1x spread across deciles and
the sampler delivers 1.6x, at a correlation of 0.14. But the **touched-SKU shortfall was the
v2 duplicate-draw defect** ([[v2-defect-manufactured-the-fill-law-evidence]]) and its sign has
REVERSED -- a share thinning now UNDER-predicts the touched count by 7.6%, because flattening
moves lines off the busy SKUs and onto more distinct ones. The store is insensitive either way
(-4.1% -> -2.1%). Quote the 2026-09-10 "~25% over-read" nowhere.

**How to apply:** never validate a per-SKU transient on fulfillment against the line share
alone -- score it at the realized line counts (the picks table) first, as
`.scratch/department-calibration/assets/validate_fragmentation.py` does (chain -3% at the
realized counts vs +23% at the share). `fragmentation.section_fragmentation` takes
`lines_per_day_by_sku` for exactly this; an affinity-aware line share on the record is fog
on the department-calibration map. Related: [[window-mix-before-model-error]],
[[free-bins-counts-the-whole-geometry]].
