# The R x A rebuild: one cheap measurement decides whether it is worth attacking

Type: research
Status: ready-for-agent
Blocked by: none

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
