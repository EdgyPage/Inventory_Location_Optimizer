# The R x A rebuild: one cheap measurement decides whether it is worth attacking

Type: research
Status: resolved

Ticket 04 convicted `_TravelBalancedPool._aisle_best` on HEAD -- k = 1.54 (cfg=none) and 1.57
(split_staging4), 427,497 calls at 8,000 SKUs, per-placement cost more than tripling across the
ladder. It also refuted the belief that `bd29d2eb` had fixed it: the `take` heap removed the O(A)
SCAN from the selection, not the run-boundary rebuild.

## But "the rebuild" is an attribution, and it has not been measured

`_aisle_best` has **two** call sites, and they have different complexity:

| site | line | cost |
|---|---|---|
| the run-boundary rebuild | `for aid in by_aisle:` | **O(A) per SKU run** -- the `R x A` term |
| the winner refresh, after each successful pop | `ab = self._aisle_best(best_aid, var)` | **O(1) per take** -- already linear |

The ladder counts only the TOTAL. If the refresh dominates, the `R x A` term is a small share and
attacking it buys little; if the rebuild dominates, it is the candidate.

**The obvious subtraction does not work and must not be published.** `_aisle_best - placements`
gives -29,492 / -45,987 / -49,804 / +72,727 across the four rungs -- negative at three of them.
So "one refresh per placement" is false (the scenario's `placements` counts every placement path,
not only this pool's takes) and the split cannot be recovered from the two columns the ladder
already prints.

This is the trap `INBOUND_PERF_FINDINGS.md` names twice and then fell into anyway: *"The 0.630 us
was obtained by dividing the very total it then claimed to explain."* A division that closes to
three digits proves nothing about where the time goes.

## The measurement, which is cheap

Count the two sites separately -- a distinct counter at each, or two `_FLOW_COUNTS` entries -- and
re-run the two meso ladders. Report:

1. `rebuild_calls` and `refresh_calls` per rung, and each one's exponent;
2. `rebuild_calls / boundaries` -- this must equal the live aisle count A, and if it does not, the
   instrument is counting something other than the loop it names;
3. `boundaries` itself (distinct SKU runs per wave), because `R x A` grows through BOTH factors
   and an exponent on the product cannot say which.

## Only then, the design question

If the rebuild dominates, the structure is favourable and worth recording now:

`per_pick(m, i, var, 1, pi) + d_m = m*var + (m*(i + pi) + d_m)` -- a **LINE in `var`** with slope
`m`. So `_aisle_best(aid, var)` is the lower envelope of ~3 lines, already O(brackets); the cost
is the A iterations around it, not the work inside.

* **A dynamic convex hull is the textbook answer and is the wrong one here.** The aisle score is
  `load[aid] + fq * min_m(m*var + c_m) + cart(aid)`, a linear form in `(1, fq*var, fq)`, so the
  argmin over aisles is a 3-D lower-hull query -- O(log A) after preprocessing. But `load[aid]`
  increments on every placement, so the hull would need dynamic updates, and dynamic 3-D hulls are
  a research-grade dependency for a simulator. Do not start here.
* **A lazy bound is the tractable one.** The heap only ever needs its TOP. Maintain a cheap
  per-aisle LOWER BOUND that needs no per-boundary work, pop by bound, compute the true score only
  for the popped aisle, and re-push. Aisles whose bound never beats the best true score are never
  evaluated. Byte-identical by construction -- it computes the same argmin -- and the win is
  whatever fraction of A the bound prunes, which is itself a measurement.

## Do not skip to the design

The ordering is the point. Ticket 06 closed the other half of this candidate on a measurement that
took ten minutes and saved a kernel change that would have bought 0.86%. The same discipline
applies here: the split first, the design second, and either a refactor with a guard or a stated
reason at the end.


## Answer -- the rebuild is real, it is k = 1.912, and the decomposition closes exactly

Measured with a probe that wraps `take` (reading `self._run_sku` against the unit's SKU, the same
condition the rebuild branch tests, and recording the live aisle count at that moment). Nothing
under `Warehouse/` was edited; the wrap lives in the measurement.

Staged put-away config, 8 batches, three rungs:

| skus | takes | boundaries R | rebuild calls | refresh calls | A (mean live aisles) |
|---|---|---|---|---|---|
| 600 | 7,288 | 1,767 | 8,245 | 7,288 | 4.7 |
| 1,200 | 15,426 | 3,877 | 34,227 | 15,426 | 8.8 |
| 2,400 | 29,138 | 7,524 | **116,712** | 29,138 | 15.5 |

| series | k vs skus |
|---|---|
| **rebuild calls** | **1.912** |
| refresh calls | **1.000** |
| boundaries R | 1.045 |
| aisles A | 0.867 |

**The two factors sum to the product: 1.045 + 0.867 = 1.912, which is the measured rebuild
exponent to three decimals.** The term is `R x A` and it is near-quadratic.

Two independent checks that the probe counts the loop it names:

* `boundaries x A_mean = 116,712` against `rebuild_calls = 116,712` -- exact;
* `refresh calls == takes` at **every** rung (7,288 / 15,426 / 29,138), which is the O(1)-per-take
  model stated exactly.

## The split, and the direction it is moving

| | 600 skus | 2,400 skus |
|---|---|---|
| rebuild | 53.1% | **80.0%** |
| refresh | 46.9% | 20.0% |

The refresh is **exactly linear and is not the problem**. The rebuild is already four fifths of
the calls at 2,400 SKUs and its share rises with the catalogue, because it is the only half that
grows super-linearly. Extrapolating the two fitted exponents, it passes 90% before 10,000 SKUs.

## What this settles

* **The candidate is confirmed and is the top one.** This is the near-quadratic the effort's
  destination clause asks for: convicted, decomposed, and attributable to a named loop.
* **The obvious subtraction would have been wrong by a wide margin.** It implied a rebuild of
  72,727 at one rung and NEGATIVE rebuilds at three others; the true top-rung figure is 116,712.
  Recording that in the ticket body before measuring was worth more than the measurement.
* **Absolute counts here are NOT the ladder's.** This probe runs its own rung recipe (8 batches,
  `bins_per_aisle=40`), so its 145,850 total is not comparable with the ladder's 289,531 at the
  same SKU count. The exponents and the split are what transfer; the absolutes are not a baseline.

## And priced, because a count is not a cost

`_aisle_best` measured on its real shape (three height brackets, the `(D, seq, bin)` heap head,
the real `per_pick`): **0.434 us per call**. Paired with the LADDER's own 2,400 rung, so the count
and the wall come from one run:

| | ms | share of the 3.78 s rung |
|---|---|---|
| all `_aisle_best` (289,531 calls) | 126 | 3.32% |
| **the rebuild half** (80%) | **100** | **2.66%** |

**So the headline is: k = 1.912 on a term currently worth 2.7%.** That is the number to quote, and
it is not yet a reason to refactor anything.

## Why it is still the top candidate: the share itself grows

The rung wall on that ladder fits k = 1.26 (0.66 / 1.60 / 3.79 s). The rebuild fits 1.912. So the
rebuild's SHARE grows as n^0.651:

| catalogue | projected share of the wall |
|---|---|
| 2,400 (measured) | 2.7% |
| 24,000 | ~12% |
| 240,000 | ~53% |
| 400,000 (campaign) | ~74% |

> **TESTED 2026-09-16 BY THE DEEP LADDER, AND THE ATTRIBUTION DID NOT SURVIVE.** The 8x
> ladder (10k-80k, 400k catalogue) shows `reord_s` bending exactly as a growing
> super-linear term would -- local exponents 0.88 -> 1.13 -> 1.31 -> 1.38, and the rise is
> specific to that section. But fitting it as `A*n + B*n^1.912`, using the exponent
> measured below, fits WORSE (16.9% worst rung) than a plain power law (8.6%). So the bend
> is real and the claim that it IS this rebuild is unsupported. **Do not quote the 74%.**
> See ticket 14.

**This is a PREDICTION, not a result, and it must not be quoted as one.** It extrapolates a fit
taken over a 4x span out to 167x, which is precisely the move this repo has been burned by --
`inbound-performance` ticket 12, where a ladder that stopped growing its own driving variable
reported "flat" and looked exactly like a subsystem that does not grow, and the correction cost
three retractions.

The honest statement is: **small now, and the exponent says it becomes the dominant term somewhere
between 10x and 100x.** Testing that is what the deep ladder is for, and it is the next thing to
run -- before, not after, any lazy-bound work.
