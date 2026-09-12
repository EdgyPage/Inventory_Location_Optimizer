# Gate the form on the generator

Type: task
Status: resolved  <!-- closed OUT OF SCOPE -->
Blocked by: 40

Graduated 2026-09-12 from
[Close the fulfillment fill-law gap](38-close-the-fulfillment-fill-law-gap.md), decision 9. AFK.
This ticket exists to make the form FALSIFIABLE before a sixth comparability break is bought:
it builds no warehouse and runs no simulation, and it must be answered before
[Land the draw probability through every closed form](42-land-the-draw-probability.md) starts.

## Question

Does the corrected form reproduce the generator's own realized behaviour?

Compute the record's line-weighted `P(>= 1 prior line for the same SKU within K days | a line)`
under the draw probability `p_s` from
[Characterise the draw probability](40-characterise-the-draw-probability.md) and
`N ~ Binomial(K, p_s)`, with `K` drawn from the stamped `transit_day_law` and levels, weights and
line laws all held at the stamp. Compare against the empirical values
[Measure the repeat structure and the realized lead
distribution](39-measure-the-repeat-structure-and-lead-distribution.md) measured:

| | store | fulfillment |
|---|---|---|
| the record's Poisson at the declared line share | 0.00607 | 0.04140 |
| share-law-true control | 0.00661 | 0.03991 |
| **EMPIRICAL (v3, the declared sampler)** | **0.00721** | **0.03485** |

**RE-TARGETED 2026-09-12** by
[Re-measure the fill-law targets under v3](45-remeasure-the-fill-targets-under-v3.md). The v2
targets this ticket was written against were **0.01052 / 0.14938** and they were an artifact:
the v2 sampler drew 335 fulfillment SKUs on at least half the measured days, and those carried
the repeat statistic (lag-1 co-occurring pairs 4,815 -> 1,037 under v3, on 9% MORE lines).

**The question is now INVERTED on fulfillment.** The record no longer under-prices its event
3.74x; it **over**-prices it by 16% (0.04140 against an empirical 0.03485), while still
under-pricing the store by 19% (0.00607 against 0.00721). The two channels err in OPPOSITE
directions, so no single multiplicative correction can close both -- the pre-registered fatal
signature below, arriving before the form was built. Note also that `Binomial(K, p_s)` gives a
strictly LOWER `P(N >= 1)` than a Poisson of the same mean, so at equal mean it moves
fulfillment toward its target and the store away from it; whether the measured `p_s` differs
enough from the declared rate to change that is precisely this ticket's question, and it must
be answered rather than assumed in either direction.

**PASS: within 10% RELATIVE on BOTH channels.** Relative rather than absolute because the two
targets are 14x apart, so an absolute band would be vacuous on one and brutal on the other.

**Two rejections fixed in advance, not judged afterwards (38 decision 9):**
- **Fulfillment closing while the store overshoots is FATAL.** It is the signature of the two
  dead variants -- 38's best reached fulfillment 0.1370 only by driving the store to 0.0909
  against a realized 0.0302 -- and the store control has caught a wrong answer twice already.
- **Landing short on BOTH leaves is a REJECTION, not a stamped residual.** `m` already did that
  (71% / 52%). The value of this form over `m` is that it is derived; a stamped known-bias is how
  a wrong constant survives for months.

On a rejection, do NOT patch with a second parameter: record what the form produced against each
target, and graduate a fresh ticket. The campaign is already parked and one more ticket is cheaper
than a buried residual.

Also report, separately, the increment attributable to `Binomial(K, p_s)` alone against a Poisson
at the same `p_s` (38 decision 5) -- it ships inside the form but its contribution should be
legible.

Done when the two numbers are computed and scored against the band, the Binomial increment is
reported, and the verdict is stated plainly as pass or reject. Reproducible in one command, as
[measure_repeat_and_lead.py](../assets/measure_repeat_and_lead.py) is.

**Method warnings:**
- Reuse 39's asset where it already does this arithmetic rather than writing a second scorer; its
  rebuild gate reproduces the record's `fill_rate` to delta 0.000e+00 on both leaves, and that
  gate passing first is what makes any substituted number trustworthy.
- Score at realized lines, not over all declared SKUs. The two weightings disagree by a factor of
  4 on fulfillment and that disagreement IS the finding (39, method notes).
- Floats compare with a tolerance, never `==`.

## Closed: OUT OF SCOPE, 2026-09-12

Ruled out of scope by user decision while resolving
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md), which measured the fill-law gap CLOSED under the
era's declared sampler: 12 arms judged, **0 failed**, fulfillment supply 0.1044 -> 0.0284
against an expected 0.0251 at tol 0.020. The v2 sampler's duplicate draws were 95.5-97.1% of
the gap this chain existed to explain.

There is no new form to gate. The record does still misprice its own prior-line event on
the GENERATOR -- over-prices fulfillment by 16% (0.04140 vs 0.03485) and under-prices the
store by 19% (0.00607 vs 0.00721), in opposite directions -- but the instrument accepts the
REALIZED result on both leaves, which is what the era is calibrated against. A two-sided
~18% error on an intermediate quantity does not earn a comparability break. This ticket's
own pre-registered rejection (fulfillment closing while the store overshoots is fatal) is
what a single-multiplier fix would now trigger, in mirror image.

A scope boundary, not a step on the route: this ticket is NOT in the map's Decisions-so-far.
It returns only if the destination is redrawn.
