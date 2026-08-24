---
name: sim-time-unit-is-seconds-not-ms
description: "the sim emits SECONDS; the analysis layer divided by 3.6e6 as if milliseconds until 2026-08-24, so every absolute number published BEFORE that is 1000x too large"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T18:58:51.285Z
---

`cost_model.sec_per_inch` is `1/(12·ft_per_s)` — **seconds**. The constants added to the
same accumulator read the same way: `pick_intercept: 15` and `cart_swap_coef: 300` are 15
seconds a pick and five minutes a cart swap.

`Performance_Evaluations/common/units.py` declared *"durations are milliseconds"* and
divided by `3.6e6`. **3.6e6 seconds is 1000 hours.**

**FIXED 2026-08-24.** `units.py` now imports `SECONDS_PER_HOUR` from
`Warehouse.kernel.timeline` instead of restating a literal, so the sim's unit and the
analysis divisor cannot diverge again. Five modules had each restated it — `units`,
`run_whatif_labor`, `chartkit`, `headline/throughput_vs_labor`, `tables/per_run` — and
three were found only because a new ratchet failed. The unit kinds were renamed with it
(`duration_ms` → `duration_s`, `rate_per_ms` → `rate_per_s`).

**Why it matters now:** every ABSOLUTE number published **before** that commit is 1000×
too large — the site's ~450,000 items/hour is ~450, and `mean_batch_prod_hours = 1.68` is
~1,680 hours. Every RATIO, percentage and effect size was and is correct, which is why
nothing caught it and why every comparative claim on the published site still stands. The
sanity check that settles it: 1,158 items against 14,288 sim units of labor is 292
items/picker-hour as seconds, and 291,763 — 81 picks a second — as milliseconds.

**How to apply:** the published site was **not** re-analysed by the fix and still shows the
1000× figures. Before quoting any absolute throughput or labor-hours number off an
experiment page, check whether that run was analysed after 2026-08-24; if not, divide by
1000. Re-running the analysis and restaging is its own piece of work. Related:
[[putaway-seams-for-inbound]], [[determinism-fix-shifted-throughput]].
