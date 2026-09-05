# Build the equilibrium check and the throughput audit

Type: task
Status: open
Blocked by: 08

Blocked by
[Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md)
because the check reads the persisted `shift_days` ledger and the `staffing` record's expected
utilizations, both of which 08 builds.

## Question

Build what [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md) decided, as
one pure function with two callers:

- **The check.** `equilibrium.check(db, day_lo, day_hi) -> verdict` beside the derivation
  (`Optimization/simconfig/`), four strict clauses over the window: every day drained
  (`shift_days`); `released_late` = 0 on every drained day (a nonzero there RAISES — instrument
  bug, not a failure); realized utilization per department per leaf within `band_tol` of the
  record's `expected_utilization`, as a ratio of sums (picking from `batch_stats.task_makespan`,
  put and receiving from `work_events` intervals joined through `batch_id`); missed share
  second-half minus first-half mean within ±0.02 absolute, level recorded. The verdict carries
  every clause's reading, not only pass/fail.
- **Caller one, the reference-run driver** (consumed by
  [Take the reference run](09-take-the-reference-run.md)): a failing window is discarded and the
  next pass runs; a passing window and its readings are stamped into the calibration record.
- **Caller two, the throughput audit evaluation**: reads targets, `band_tol` and expected values
  from the `staffing` record stamped onto `sim_result`, calls the same function per run, and
  renders realized vs expected per department plus the drained/capped day count. On a
  non-reference run it REPORTS — a capped day is flagged "declared throughput not delivered",
  a below-band picking read is the arm's travel saving — and never fails the run. Route the new
  figures through the declared-quantity path (`route-reviewer-finding`; memory
  `figure-views-are-derived`).
- **Tests** that PROVE the check can fail: a synthetic window with one capped day, one with a
  nonzero `released_late` on a drained day (must raise), one out-of-band department, one
  trending missed share; and that the reference-run driver discards a failing window rather
  than averaging it in.

The answer records the module path, the verdict's shape, and the evaluation's name in the
analysis catalogue.
