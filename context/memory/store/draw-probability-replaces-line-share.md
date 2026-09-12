---
name: draw-probability-replaces-line-share
description: "the line share freq/sum-freq is the sampler's WEIGHT, not its inclusion probability; dept-cal 38 replaced it with the draw probability p_s (generator-drawn, Binomial not Poisson) and the 0.20-vs-1.45 cluster-mate density derives the channel asymmetry a fitted multiplier only fits"
metadata: 
  node_type: memory
  type: project
  originSessionId: ac77a40b-9c76-4130-99f0-75e52da2dda8
  modified: 2026-09-12T14:34:30.980Z
---

Decided 2026-09-12, dept-cal 38, after [[fill-gap-is-the-line-count-shape]] left 29%/48%
unexplained. **Argued from the sampler's structure and NOT yet measured** -- dept-cal 41 is a
generator-side gate built to falsify it before any warehouse is bought.

**The defect.** `coverage.py` prices a SKU's prior lines at `pi_s = freq / sum freq`, its BASE
WEIGHT SHARE. But `Workload_Builder._lift_weighted_sample` draws `k` DISTINCT SKUs per batch
WITHOUT replacement, multiplying each survivor by `prod lift(A, B)` over partners already drawn.
A weight share is not an inclusion probability, and the map between them is a different function,
not a scaled one -- which is why every per-SKU RATE variant failed ([[fill-gap-is-not-the-line-rate]]).

**The number that makes it structural rather than fitted.** Expected already-drawn cluster-mates
per candidate is `cluster_size * k/N`:

| | store | fulfillment |
|---|---|---|
| batch fraction `k/N` (`settings.py`, era-derived) | 0.00245 | 0.01814 |
| expected drawn cluster-mates (cluster size 80) | **0.20** | **1.45** |
| 39's FITTED multiplier | 1.739 | 3.968 |

The reinforcement engages ~7.4x more often per fulfillment draw and each hit multiplies by ~4-5
(stored lifts are the top-20 tail of `U(1,5)`). `relative_frequency` DISPERSION is near-identical
across the two sections (CV 0.577 vs 0.580 under the uniform profile), so the concentration is not
a frequency story at all -- one mechanism at two sampling densities. Fulfillment also collapses to
ONE `(handling, category)` group against the store's twelve.

**Why the fix is affordable.** Under the era the two `units_per_line` cancel exactly, so
`mean_fraction == STORE_DEMAND / FF_DEMAND` -- `k` is a declared fraction times the section size
and does NOT depend on `n`. There is no fixed-point circularity, so `p_s` is characterised ONCE
before the solve, from `(inv_db, aff_db, batch_cfg, seed)` alone. Batches are otherwise precomputed
AFTER the coverage fixed point, so nothing on disk has this at solve time.

**How to apply:** `p_s` is a PROBABILITY, so `N ~ Binomial(K, p_s)`, never a Poisson rate -- it
breaks outright as `p_s` approaches 1, which is where busy fulfillment SKUs live. Estimate it
with unbiased factorial moments (`c_s(c_s-1)/M(M-1)`), never a plug-in alone: the fill law's
line-weighted functional is CONVEX in `p_s`, and that Jensen inflation was two thirds of the
apparent movement at M=20. Keep the store as a live control -- "fulfillment closes, store
overshoots" is the signature of two dead variants. And when this lands it is the SIXTH
comparability break, era-only, flag-off byte-identical (cf.
[[derived-fill-is-the-fourth-comparability-break]],
[[lead-aware-record-is-the-fifth-comparability-break]]).

Related: [[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[fulfillment-fill-law-overpredicts]], [[a-count-is-not-a-claim]].
