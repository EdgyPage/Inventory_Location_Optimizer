---
name: fill-gap-is-not-the-line-rate
description: "the fulfillment fill-law gap is NOT the per-SKU line RATE -- substituting realized rates moves it the WRONG way, because it zeroes every SKU a finite window never touched; any rate-substitution test here is confounded by finite-window granularity and by rollover re-offer endogeneity"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9a6d85ac-5da3-451b-b75b-6bbafc85ba51
  modified: 2026-09-12T14:17:18.100Z
---

Measured 2026-09-12 on `comparison_20260912_055947` (coupled era, reference `lt0` pair, days
20-39) while working department-calibration 38. The rebuild gate passed first
(`era_coverage.declare_from_record` reproduces the stamped `fill_rate` to delta 0.000e+00 on both
leaves), so the scoring is trustworthy.

Re-pricing `coverage._served_under_lead` with realized per-SKU rates, everything else held, moves
fulfillment **0.0251 -> 0.0209 -- the wrong direction**. The only variant reaching the realized
region (0.1370) also switches the demand weighting to realized, overshoots 0.1044 by 31%, and
fails its own control by driving the store to 0.0909 against a realized 0.0302.

Two confounds, both measured, that make a naive rate-substitution test look conclusive when it is
not:

1. **Finite-window rate granularity.** A SKU seen once in 20 days scores at 0.05/d against a true
   0.018/d. Draw counts from `Poisson(20 * lambda_share)` -- the share law exactly TRUE -- and push
   them through the identical machinery: fulfillment 0.0651, store 0.0776, against stamps of
   0.025. Two thirds of the apparent movement is the estimator, not the world.
2. **Rollover re-offer endogeneity.** A missed unit is re-offered and picked on a later batch,
   adding a distinct `(batch_id, sku)`, so a realized line count is endogenous to the miss rate it
   is meant to predict. Fulfillment's 6+ line bucket: 11,946 of 12,875 line-batches coincide with
   an `unpicked_unstocked` carryover row. (Both confounds vanish if you read the sampler's own
   `_batches_*.pkl` instead of `picks` -- see [[fill-gap-is-the-line-count-shape]].)

**Why the substitution inverts, established 2026-09-12 by dept-cal 39:** it sets `rate = 0` for
the 78% of fulfillment SKUs a 20-day window never touched, and removing their loss outweighs what
the busy SKUs add under stamped `d_s` weighting. The concentration is a SHAPE on the declared
section, not a per-SKU rate. **An earlier version of this memory blamed temporal clustering
("the sampler clusters a SKU's lines in TIME"); 39 measured that and it is FALSE** -- each batch
draws from its own `random.Random(seed_batches + i)` with no cross-batch state, so day-to-day
dependence is structurally impossible, and where the gap is it measures NEGATIVE.

**How to apply:** never score a per-SKU hypothesis on this simulation without a synthetic control
in which the null law is exactly true, pushed through the identical machinery, and report the
INCREMENT over that control rather than the raw number. A line is a distinct `(batch_id, sku)`,
never a `picks` row. Also: the frequency law in `_served_under_lead` is `Poisson(K * rate)` but
the sampler draws DISTINCT SKUs per batch at one release a day, so a SKU takes at most one line a
day -- `Binomial(K, p_s)` is the right (a,b,0) member and Panjer already covers it. Related:
[[fill-gap-is-the-line-count-shape]], [[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[coverage-in-days-floors-the-store-section]], [[window-mix-before-model-error]],
[[a-count-is-not-a-claim]], [[nothing-is-lost-under-the-era]].
