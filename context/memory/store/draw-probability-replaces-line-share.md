---
name: draw-probability-replaces-line-share
description: "the line share freq/sum-freq is the sampler's WEIGHT, not its inclusion probability, and the two are now distinct glossary terms; but the p_s form dept-cal 38 chartered was NEVER LANDED (ruled out of scope once v3 closed the gap), and every number here was measured under the defective v2 sampler"
metadata: 
  node_type: memory
  type: project
  originSessionId: ac77a40b-9c76-4130-99f0-75e52da2dda8
  modified: 2026-09-12T14:37:37.528Z
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
(stored lifts are the top-20 tail of `U(1,5)`). `relative_frequency` DISPERSION across the two
sections is close, and tilted the WRONG WAY -- measured on the reference
catalogue, which is the BELL profile: fulfillment CV 0.5813 against the store's 0.6389, so the MORE
dispersed section is the LESS concentrated one and a frequency story predicts the opposite
ordering. One mechanism at two sampling densities. Fulfillment also collapses to
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
overshoots" is the signature of two dead variants. The comparability break this
would have bought was never spent on it: the sixth break went to the v3 sampler flip instead
([[v3-sampler-era]]).

Related: [[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[fulfillment-fill-law-overpredicts]], [[a-count-is-not-a-claim]].

**CAUTION, 2026-09-12 (dept-cal 45).** The core distinction here -- the line share is the
sampler's WEIGHT, not its inclusion probability -- is REINFORCED: under v3 the share still
predicts a 7.1x spread of lines across fulfillment frequency deciles where the sampler
delivers 1.6x. But every NUMBER this memory derives the channel asymmetry from was measured
under v2, whose duplicate draws manufactured the concentration
([[v2-defect-manufactured-the-fill-law-evidence]]), and the asymmetry it was built to explain
has itself reversed: the record now over-prices fulfillment by 16% and under-prices the store
by 19%. Re-measure before reusing the cluster-mate densities.

**NEVER LANDED, 2026-09-12 (dept-cal 46, user decision).** The `p_s` form is OUT OF SCOPE. It had
exactly one consumer -- correcting a fill law that under-predicted the realized miss 4x -- and
under v3 that law reads in band on both leaves with no correction at all (fulfillment supply
+0.0033, store +0.0023, tol 0.020). Deriving `p_s` would be a measurement with nothing waiting on
it, and buying a comparability break to land it would be worse. Recorded as ADR-0006. The built
module `Optimization/simdriver/draw_probability.py` (13 green tests) stays in the tree with no
caller, as the sampler effort's starting point; its two `_drawp_*.npz` artifacts are v2 archive,
not input.

**What survives, and it is the durable half:** the line share is a WEIGHT and the draw probability
is an OUTCOME, and they are now separate terms in `CONTEXT.md` so the distinction cannot rot back
into one word. The record still misprices its own prior-line event on the generator -- over on
fulfillment by 16%, under on the store by 19% -- and that is accepted, not corrected.
