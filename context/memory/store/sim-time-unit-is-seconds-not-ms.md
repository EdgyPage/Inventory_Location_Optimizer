---
name: sim-time-unit-is-seconds-not-ms
description: "the sim emits SECONDS and the analysis layer divides as if milliseconds, so every published absolute number is 1000x off while every ratio is correct"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T06:14:27.426Z
---

`cost_model.sec_per_inch` is `1/(12·ft_per_s)` and documented as **s/inch**. The constants
added to the same accumulator read the same way: `pick_intercept: 15` and
`cart_swap_coef: 300` are 15 seconds a pick and five minutes a cart swap — warehouse
numbers. At 15 ms and 0.3 s they are not physical.

`Performance_Evaluations/common/units.py` declares *"durations are milliseconds"* and
divides by `3.6e6` for hours. **3.6e6 seconds is 1000 hours.**

Checked against Experiment 8's own staged numbers: `production_time_per_item = 178.7` sim
units and `mean_completion_rate = 0.126` items/sim-unit give ~18 items per picker per hour
read as seconds (a real rate), and ~300 items per picker per *minute* read as milliseconds.

**Why:** so every ABSOLUTE published figure is 1000× off — the site's ~450,000 items/hour is
~450, and `mean_batch_prod_hours = 1.68` is ~1,680 hours of labor per batch. Every RATIO,
percentage and effect size is unaffected, which is exactly why nothing ever caught it and why
every comparative claim on the published site still stands.

**How to apply:** NOT fixed as of 2026-08-24 — correcting the label relabels every absolute
number on a live site and is its own piece of work. It is recorded as
`Warehouse/kernel/timeline.ANALYSIS_DIVISOR_DISCREPANCY` with the arithmetic, and
`Tests/unit/test_timeline.py` fails on the day the two readings agree, so the record gets
deleted with the fix rather than surviving it. Quote no absolute throughput or labor-hours
figure to a stakeholder without applying the 1000×. Related: [[putaway-seams-for-inbound]],
[[determinism-fix-shifted-throughput]].
