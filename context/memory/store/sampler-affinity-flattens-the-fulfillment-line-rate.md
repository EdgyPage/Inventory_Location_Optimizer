---
name: sampler-affinity-flattens-the-fulfillment-line-rate
description: "The batch sampler's affinity lift spreads a fulfillment section's lines over SKUs almost flat across frequency deciles, so any per-SKU transient priced at the line share freq/sum(freq) over-reads the SKUs touched by ~25% on fulfillment; the store is insensitive"
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

**How to apply:** never validate a per-SKU transient on fulfillment against the line share
alone -- score it at the realized line counts (the picks table) first, as
`.scratch/department-calibration/assets/validate_fragmentation.py` does (chain -3% at the
realized counts vs +23% at the share). `fragmentation.section_fragmentation` takes
`lines_per_day_by_sku` for exactly this; an affinity-aware line share on the record is fog
on the department-calibration map. Related: [[window-mix-before-model-error]],
[[free-bins-counts-the-whole-geometry]].
