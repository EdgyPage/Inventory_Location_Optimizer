---
name: no-calibration-simulations
description: "User decision 2026-09-06 -- staffing constants are closed-form expectations over the inventory distribution and the runtime geometry, never measured by a calibration run; nothing on the catalogue may carry an implicit batch"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: aa6f89e7-91b2-49c5-a80d-485be5208823
  modified: 2026-09-06T19:23:01.626Z
---

Never propose a calibration simulation, a reference run, a measured travel share or a
fixed-point loop of passes to find a staffing constant. Seconds per unit for picking and
put-away is a closed-form expectation over the SKU demand distribution, a placement
DISTRIBUTION, and the warehouse geometry the run built (department-calibration ticket 13;
built as `Optimization/simconfig/expected_travel.py`). The pair's demand derives at setup from
the class-uniform distribution (one shared script, arm-independent); each arm's own
expectation under its initial placement is stamped by the worker for the report only
([[fifo-restock-drifts-to-class-uniform]]). Travel speeds, handling coefficients and the cart are known; the geometry is
known at simulation runtime, so the derivation reads what was built. Also: nothing authored
on the catalogue may carry an implicit batch or day (stock coverage per SKU in days of its
own expected demand, not `equilibrium_coverage_batches`), so the method scales to any item
distribution unchanged.

**Why:** the hybrid procedure (analytic seed, era run, re-derive) was executed on
2026-09-06 and could not close on the production catalogue: six passes to converge picking,
a replenishment cycle longer than the window because coverage was denominated in
generation batches, stockouts defeating the drained clause. The user ruled that measuring
was the wrong frame -- every quantity was already an expectation over known distributions.

**How to apply:** when a constant is "arm-dependent" or "needs a run", derive its
expectation from the simulator's own charging rules instead (`cost_model.travel_cost`,
`aisle_exit_cost`, `cart_step`, `put_cost`); use a finished run only as a one-time check of
the formula's residual, never as a pipeline step. See [[launch-long-drivers-detached]] for
the launch trap if a check run is ever needed, and [[config-knob-has-five-seams]] for
where a derived constant must be stamped.
