---
name: fifo-restock-drifts-to-class-uniform
description: "Under FIFO restock a churning section's placement migrates from the initial map to the class-uniform smear; the expected-travel closed form must take a placement DISTRIBUTION, and which one is right depends on the section's reorder churn"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9617dcfc-8328-4357-a2c1-ec23f3b9da05
  modified: 2026-09-06T21:00:34.656Z
---

Found 2026-09-06 while checking the expected-travel closed form against the reference passes
(department-calibration ticket 13, `.scratch/department-calibration/assets/expected-travel-derivation.md` §3).
The formula priced the converged fifth pass to -3.1% on the store leaf using the arm's INITIAL
placement, but -16% on fulfillment; under the class-uniform smear fulfillment came to -6.8% and
the store to +63% tasks (absurd). Cause: FIFO restock places every reorder on a uniformly random
free bin of its class, so a section that churns (fulfillment: ~4,000 reorder placements a day)
has migrated to the class-uniform steady state by day 20, while the store (~60 a day, a 40+ day
cycle) still sits on its initial map.

**Why:** the same formula with a different destination distribution -- so `expected_travel.PlacementDist`
has two adapters (`initial`, `uniform`) by decision, and the pair's DEMAND derives from `uniform`
(arm-independent, and FIFO's long-run state) while an arm's report is judged against `initial`.

**How to apply:** never validate the closed form against a churned FIFO leaf with its initial
placement, and never against an unchurned one with the smear. A ranked restock arm keeps its
placement concentrated, so `initial` is its proxy. If per-arm expectations look wrong, check the
section's reorder churn first. Related: [[no-calibration-simulations]], [[fifo-restock-ignores-initial-placement]].
