---
name: fill-gap-is-the-line-count-shape
description: "REFUTED 2026-09-12 -- the 3.74x under-pricing and the 71%/52% multiplier were the v2 sampler's duplicate draws, not line-count shape; under v3 the record OVER-prices fulfillment by 16%. What survives is the method, not the finding"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9b9bcde1-2707-4994-94b2-198c1d3cef05
  modified: 2026-09-12T14:17:24.563Z
---

**REFUTED 2026-09-12 (dept-cal 45), the same day it was written.** Everything below was
measured on a **v2** run, and the v2 sampler re-drew SKUs it had already taken
([[v2-sampler-redraws-selected-skus]]). 335 fulfillment SKUs were drawn on at least half the
20 measured days and carried the entire repeat statistic. Re-drawn under v3 the prior-line
probability falls 0.14938 -> **0.03485**, i.e. from 3.74x the record's own Poisson to
**0.87x**, and the fitted multiplier collapses from 3.968 to 1.000 (71% of the gap -> 0%).
The store goes 1.59x -> 1.19x, `m` 1.739 -> 1.189. **Do not carry the numbers below forward.**
See [[v2-defect-manufactured-the-fill-law-evidence]].

Two things here DO survive and are why this memory is amended rather than deleted: the
structural argument that temporal dependence is impossible across batches (each draws from
its own `random.Random(seed_batches + i)` with no cross-batch state), and the METHOD -- score
at realized lines, keep the store as a live control, and read demand off the sampler's own
`_batches_*.pkl` rather than `picks`. The lead result (worth 0.9%) also stands for the DRAWN
law; its realized leg is a run outcome and moves under v3.

Measured 2026-09-12 (dept-cal 39) on `comparison_20260912_055947`, days 20-39, all four arms,
rebuild gate at delta 0.000e+00 on both leaves. Reproduce with
`.scratch/department-calibration/assets/measure_repeat_and_lead.py`.

**Two mechanisms are dead, not merely small.**

- **Temporal dependence is structurally impossible.** Each batch draws from its own
  `random.Random(seed_batches + i)` and the affinity lift mutates only *within* a batch
  (`Warehouse/picking/Workload_Builder.py`), so there is no cross-batch state. Measured against
  a count-preserving within-SKU day permutation it is **-0.0089 on fulfillment** (wrong sign)
  and +0.0012 on store.
- **The lead distribution is worth 0.9%.** `TrailerTransit.lead_for` is a pure function of
  `(seed, tag, seq)`, so every trailer's transit is RECONSTRUCTIBLE from `yard_trailers.seq`
  (`dispatched_s = arrived_s - lead_for(seq)`) -- no inference from a level. Realized K
  1.803-1.829 vs stamped 1.766, E[K^2] 4.26-4.32 vs 4.23; re-priced, fulfillment 0.0251 ->
  0.0258. The parent's Little lead was also whole, not partial: adding the receiving and
  put-away queues moves W by <= 0.031 d.

**What it is: the record under-prices its own event.** The fill law on a floored section
reduces to `P(>= 1 prior line for the same SKU within K days | a line)`. Record 0.0414,
share-law-true control 0.0399, **realized 0.1494** on fulfillment (3.74x); 0.0061 / 0.0066 /
0.0105 on store (1.59x). One multiplier on the declared rate, fitted to that probability ALONE
and never to the missed share, then gives fulfillment 0.0251 -> 0.0816 (realized 0.1044,
**71%** of the gap) and store 0.0248 -> 0.0276 (realized 0.0302, **52%**) -- both leaves moving
the same way, which no earlier variant managed.

**Why:** the record's fill is a demand-weighted average over ALL declared SKUs; the realized
miss is carried by the SKUs that actually get lines, and on fulfillment only 34,683 of 160,062
do in 20 days. The two weightings disagree by a factor of 4, and the disagreement IS the
finding -- which is also why substituting per-SKU rates inverts
([[fill-gap-is-not-the-line-rate]]).

**How to apply:** score any fill hypothesis here at REALIZED lines, keep the store as a live
control (every variant that hit fulfillment by breaking the store has been wrong, twice), and
read demand off the sampler's own `_batches_*.pkl` rather than `picks` -- that is the demand
before any miss or re-offer touches it, so the rollover endogeneity cannot enter. Unexplained:
29% fulfillment, 48% store. Related: [[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[inbound-lead-is-not-in-the-coverage-record]], [[nothing-is-lost-under-the-era]],
[[window-mix-before-model-error]], [[a-count-is-not-a-claim]].
