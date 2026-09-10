---
name: nothing-is-lost-under-the-era
description: "under the era a missed or cut pick is re-offered next day and a lead-0 top-up lands first, so every crew must be sized on DEMANDED units (never demand x fill); decided 2026-09-08 that demand is declared and the picking crew derived from one joint first-time confidence (0.95, per pick) -- ticket 29 (ADR-0004) LANDED 2026-09-08 (4bd9323a); 30/31 outstanding, numbers provisional until 31 reads clean"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8e904077-e98e-48df-bf37-3218dc483108
  modified: 2026-09-09T04:03:31.695Z
---

Measured 2026-09-08 on the store leaf of the fifo era check (department-calibration 27): picked
plus standing carry equals the script's demand exactly; 92% of stocked-out SKU-days are back on
shelf the next day; the supply-only missed share is FLAT at ~0.07, and the entire missed-share
hump was `unpicked_daycut` labour overflow from a crew sized on served units (0.92 real load at a
nominal 0.85). The day's line count is one Gaussian draw with a DECLARED cv (a third on the
store), so 14 of 40 days exceed a full shift at any headroom.

**Why:** `derive` priced pickers on demand x fill rate (5,647/day), but a pick the day cut or the
shelf could not fill is not lost -- it is re-offered until done -- so the crew picks all of demand.
Fulfillment passed the same check only because its sampled script came in 6.7% light.

**How to apply:** never reduce a crew's load by a fill rate; a first-pass fill is a SUPPLY
expectation for the supply clause only. As of 2026-09-08 (ADR-0004, map charter) the declared
input FLIPS: demand per channel is declared in the sampler's own unit, `rho_pick` is replaced by
a joint first-time confidence (reached on its day AND filled, per pick, split equally: floor
solved for fill >= sqrt(c), smallest integer crew for expected cut share <= 1 - sqrt(c)), and
"every day drained" becomes a reading. Put-away and receiving keep rho. Until ticket 31 reads
clean the era's numbers are provisional and the inbound hold stands. The 2026-09-08 runs' pick
numbers were produced under the wrong crew and are not a baseline.

**Update 2026-09-08 (ticket 29, ADR-0004, 4bd9323a):** landed. Under the era `store_demand` /
`ff_demand` (the sampler's unit) and `first_time_confidence` are the declared inputs
(`sim_config.ERA_ONLY_KEYS`); the line floor is solved per section
(`Optimization/simconfig/coverage.py:solve_floor_lines`) and the picking crew is solved from it
(`Optimization/simconfig/staffing.py:solve_pickers`). The one reader of a channel's crew is
`Optimization/simconfig/staffing.py:channel_crew` -- read the record through it, never
`inputs['store_pickers']` / `inputs['ff_pickers']`, which are recorded `None` under the era
(`sim_config.FLAG_OFF_ONLY_KEYS`, `staffing_spec()`). `rho_pick` and `--store-pickers` /
`--ff-pickers` / `--rho-pick` are refused under the era (`run_simulation._check_era_flags`).
Tickets 30 and 31 are still pending, so the era's numbers stay provisional until 31 reads clean.
See [[coverage-in-days-floors-the-store-section]], [[no-calibration-simulations]],
[[a-count-is-not-a-claim]], [[config-knob-has-five-seams]].
