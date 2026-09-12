---
name: fill-gap-is-not-the-line-rate
description: "the fulfillment fill-law gap (0.0251 stamped vs 0.1044 realized) is NOT the per-SKU line rate -- substituting realized rates moves it the WRONG way, and against a share-law-true synthetic control sampler concentration explains only 14%; any rate-substitution test here is confounded by finite-window granularity and by rollover re-offer endogeneity"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9a6d85ac-5da3-451b-b75b-6bbafc85ba51
  modified: 2026-09-12T13:50:19.181Z
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
   an `unpicked_unstocked` carryover row.

Difference-in-differences against the control: fulfillment +0.0109 of a +0.0793 gap (**14%**),
store +0.0019 of +0.0054. About 86% is something else.

**Why:** sampler concentration is real (fulfillment touched 34,993 SKUs against 46,325 predicted,
-24.5%, while total lines moved -1.8%; 1,467 SKUs at 6+ lines against 4 predicted) but the
section rate is RIGHT (realized/predicted 0.9819). What the sampler does is cluster a SKU's lines
in TIME -- a dependence a per-SKU rate cannot express, and one a 20-day mean rate smooths away
entirely, so the substitution test is structurally blind to it.

**How to apply:** never score a per-SKU hypothesis on this simulation without a synthetic control
in which the null law is exactly true, pushed through the identical machinery, and report the
INCREMENT over that control rather than the raw number. Exclude re-offered picks from any
conditioning event. A line is a distinct `(batch_id, sku)`, never a `picks` row. Also: the
frequency law in `_served_under_lead` is `Poisson(K * rate)` but the sampler draws DISTINCT SKUs
per batch at one release a day, so a SKU takes at most one line a day -- `Binomial(K, p_s)` is the
right (a,b,0) member and Panjer already covers it. Related:
[[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[coverage-in-days-floors-the-store-section]], [[window-mix-before-model-error]],
[[a-count-is-not-a-claim]], [[nothing-is-lost-under-the-era]].
