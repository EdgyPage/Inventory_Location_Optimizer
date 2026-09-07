---
name: coverage-in-days-floors-the-store-section
description: "on the reference catalogue the store section's implied coverage is ~1,785 days, so ANY coverage_days short enough to put a reorder wave inside a 40-day window floors 100% of store SKUs at Q=1 (10 d) -- the unit floor is the section, not a tail; fulfillment is 278 days and survives"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6cbf7fb6-ff44-4fc0-ab07-fd51c5082bd3
  modified: 2026-09-07T03:19:14.423Z
---

Measured 2026-09-06 while building "Rescale stock coverage at setup" (department-calibration
14) on `mixed_realistic_bell_lt0` (400,000 SKUs) at the converged line counts (556 store /
1,818 fulfillment lines a day): the average store SKU carries ~0.024 units a day (a line every
~430 days, ~10 units a line), so the catalogue's generation-batch coverage was worth ~1,785 days
on the store side and 278 on fulfillment. Under `Q = max(1, round(coverage_days x d_s))`:

| coverage | store SKUs at Q=1 / demand on them | fulfillment |
|---|---|---|
| 10 d (the default) | 100% / 100% | 73% / 42% |
| 30 d | 87% / 63% | 27% / 7% |
| 90 d | 46% / 13% | 6% / 0.5% |
| 365 d | 18% / 1% | 0.2% / 0% |

**Why:** the first reorder wave lands at `coverage - safety` days for SKUs above the floor, so a
"wave inside 40 days" default and an un-floored store are mutually exclusive on this catalogue.
A Q=1 SKU picked in ~10-unit lines stocks out on every line, so the store channel under the
default is a one-unit shelf, not a warehouse.

**How to apply:** the decision landed 2026-09-06 (department-calibration 15 + 17): the floor is
a LINE (`floor_lines`, 1.0, `Optimization/simconfig/coverage.py:line_floor`), a floored SKU runs
base stock (`rp = Q - 1`), and on this catalogue BOTH sections sit ~100% on it, so
`COVERAGE_DAYS`/`SAFETY_DAYS` (10/2) are inert here by design, not provisional. Read
`staffing.calibration[<pair>].coverage.final[<channel>].floor_line_demand_share` and
`.fill['fill_rate']` (the expected first-pass fill rate `missed_share` is read against) before
interpreting an era run's store numbers; "a wave inside the window" is retired -- base stock is a
trickle from day `lead`, and the store's answer is explicitly no wave.
See [[fifo-restock-drifts-to-class-uniform]], [[no-calibration-simulations]].
