---
name: sim-time-unit-is-seconds-not-ms
description: "the sim emits SECONDS; until 2026-08-24 the analysis divided by 3.6e6 as if ms, so pre-fix DURATIONS are 1000x too SMALL (multiply) and pre-fix RATES are 1000x too LARGE (divide)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T20:51:32.875Z
---

`cost_model.sec_per_inch` is `1/(12·ft_per_s)` — **seconds**. `pick_intercept: 15` and
`cart_swap_coef: 300` are 15 seconds a pick and five minutes a cart swap.
`Performance_Evaluations/common/units.py` declared *"durations are milliseconds"* and divided
by `3.6e6`. **3.6e6 seconds is 1000 hours.**

**FIXED 2026-08-24.** `units.py` imports `SECONDS_PER_HOUR` from `Warehouse.kernel.timeline`
instead of restating a literal, so the two cannot diverge again. Five modules had each
restated it — `units`, `run_whatif_labor`, `chartkit`, `headline/throughput_vs_labor`,
`tables/per_run` — and three were found only because a new ratchet failed.

## Correcting a pre-fix number: the direction depends on what it is

|                       | pre-fix formula        | pre-fix value is | to correct |
|---|---|---|---|
| **DURATION** (hours)  | `seconds / 3.6e6`      | 1000× too **small** | **× 1000** |
| **RATE** (items/hour) | `per-second × 3.6e6`   | 1000× too **large** | **÷ 1000** |

So `mean_batch_prod_hours = 1.68` is really **1,680 hours**, and a published
**~450,000 items/hour is really ~450**. An earlier version of this memory gave a blanket
"every absolute number is 1000× too large — divide by 1000", which is right for rates and
**backwards for durations**, while its own worked example multiplied. Every RATIO,
percentage and effect size was and is correct, which is why nothing caught the bug and why
every comparative claim still stands.

The sanity check that settles the unit: 1,158 items against 14,288 sim units of labor is 292
items/picker-hour as seconds, and 291,763 — 81 picks a second — as milliseconds.

**How to apply:** the published site was **not** re-analysed and still shows pre-fix figures.
Before quoting any absolute duration or rate off an experiment page, check whether that run
was analysed after 2026-08-24; if not, apply the table above — and note which kind of
quantity you are holding, because the two go opposite ways. One real casualty is recorded in
[[putaway-break-even-is-nine-minutes-per-trip]], whose conclusion inverted once the duration
was corrected. Related: [[one-clock-one-speed-one-config]], [[putaway-seams-for-inbound]].
