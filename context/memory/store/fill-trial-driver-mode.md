---
name: fill-trial-driver-mode
description: the fill trial runs (7bcd0cec); its traps -- markers in pick-stage batches, fill length differs per cell, fill crews door-bound at 4 doors x 10, uni stock mode refused
metadata:
  type: project
---

Built 2026-09-22 as inbound-throughput ticket 05 (`INBOUND_FILL_SPAN_DAYS`; toy spec
`_toy_fill`, smoketest profile `fill`). Four facts a later session will need:

- **Checkpoint markers are in PICK-STAGE batches** (`i + 1 - script_off`, final pin
  `n_script`), and fill days write none. Every planner compares markers against the pick
  stage's `n_batches`; markers counting fill days replayed finished pairs into their own DBs
  (code review, caught before commit).
- **The fill's length (`fill_batches`, in sim_meta) can differ between cells**, so pick-stage
  batch = `batch_id - fill_batches` per arm, and every reader pairing arms by raw `batch_id`
  must re-base (or the pick stage must start at one declared batch). The analysis suite does
  not yet select the pick stage; that is ticket 06.
- **Fill crews are door-bound at the campaign dock.** The toy derived 115 receivers against
  40 seats (4 doors x door team 10); the dispatch rate is priced off the SEATS
  (`staffing.fill_dispatch_rate`), so a door-bound fill runs longer than its span, never past
  a queue. At 400k the derived fill crew (~4x the era's 23) will be door-bound too.
- **Uniform stock arms are refused** in a fill until the user decides what the mode means
  there ([[gain-evaluator-prices-by-the-policy-record]]).

Related: [[inbound-yard-is-a-stable-queue-under-the-era]], [[empty-batch-clock-stall-is-a-contract]].

**Map closed 2026-09-23.** The mode stays built on develop; the uniform-stock-mode question was
never decided, and the 40k prototype was never launched because the depth probe showed lifo
cannot move pick labour there ([[inbound-needs-churn-before-unload-order-matters]]).
