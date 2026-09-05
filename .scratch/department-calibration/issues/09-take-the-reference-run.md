# Take the reference run

Type: task
Status: open
Blocked by: 08, 10

AFK once unblocked. Blocked by
[Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md)
because nothing below is launchable before it.

## Question

Run the procedure decided in
[Choose the calibration procedure](02-choose-the-calibration-procedure.md) and commit the
resulting calibration record:

- One cell, `fifo` only, scheduler `lpt`, one inventory pair, both channels, minimal mechanics
  (legacy batch-denominated restock, receiving crew on at its derived size, no trailers, no yard),
  under the new cost model from 06.
- Pass 0 seeded from the analytic prediction scaled by the pilot's picking/traveling split;
  40 days per pass, days 20–39 measured; 04's equilibrium check as the window precondition (a
  failing window is discarded, never averaged); stop at <5% movement in derived daily demand, at
  most two passes.
- Measure `s_pick` per channel (Σ `task_makespan` ÷ Σ `total_items`), `s_put` as one site value
  with per-stream diagnostics (confirm the `work_events`-span instrument first — no duration
  column exists), the receiving self-check against the exact `s_recv` (mismatch beyond float
  tolerance fails the run), `K_max` per channel (window minimum), and the analytic prediction
  beside every measured value with its travel share.
- Write the calibration record with full provenance (run root name, commit, warehouse
  fingerprint, batch fingerprint, cost-model parameters, era flags, pass count, per-pass values,
  date) and `provenance: measured`.

The answer records the measured constants, travel shares, `K_max` per channel, the pass count,
whether the fixed point converged or was cut off, and the outcome of the receiving self-check.
Run output stays out of git (CLAUDE.md §2); only the record is committed.

## Comments

2026-09-05, from resolving [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md):
now also blocked by
[Build the equilibrium check and the throughput audit](10-build-the-equilibrium-check-and-audit.md)
— the window precondition is that ticket's `equilibrium.check`, four strict clauses (every day
drained, `released_late` = 0 on drained days, utilization within `band_tol` of expected, missed
share not trending). The passing window's readings are stamped into the calibration record.
