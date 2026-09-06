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

## Comments

2026-09-05, from resolving [Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md):
UNBLOCKED. What this build inherits: (1) `Optimization.persistence.Picking_Data.load_shift_days(path,
run_id)` -> rows `{day, cap_end, end_s, drained, standing, standing_put, standing_dock,
standing_carry, last_finish}` (named query `shift_day_frame`; `[]` on a pre-era vintage AND on an
era-less run -- both "no day was closed"); `drained` is the per-day verdict, the `standing_*` are
LEVELS and never sum; `last_finish > cap_end` is START-gate overtime. `CONDITIONAL_READS` names it,
so a quantity reading it needs a capability the way the yard ones do. (2) The expected values ride
the run spec and the sim_result stamp: `staffing.derived[<pair label>]` (per PAIR -- pick
`sim_result['inventory']`'s block) carries `channels.<ch>.expected_utilization.pick`,
`put.expected_utilization.<ch>`, `receiving.expected_utilization.<ch>`, the crews, `day_seconds`;
`staffing.inputs.band_tol` (0.10, `assumed`) and the three `rho_*`; `staffing.calibration[<pair>]`
carries `calibration_stale` / `calibration_measured` and every constant with its provenance for
the audit's caveat line. The sim_result stamp is the WHOLE `staffing` block (run_analysis
`_staffing_record`), unchanged in shape by 08 -- `EvalContext` still reads
`staffing['inputs'][<pickers key>]`. (3) The re-analysis-side re-derivation ("warn and stamp", 03
decision 3) is NOT built: it needs the batch caches (`_batches_*.pkl` in the pair dir, loaded by
`batch_precompute.load_batches(path, fingerprint)`), which the audit reads anyway if it wants the
script's analytic values live; `staffing.derive` + `derived_differs` are the functions to call.
(4) Realized picking utilization per leaf is Σ `task_makespan` ÷ (K x S x days); put and
receiving from `work_events` spans joined to the day through `batch_stats.work_day`; the ledger
gives `end_s` per day so a "drained early" day's granted time is still S (utilization is against
the grant, not the shift end). (5) The check lives beside the derivation
(`Optimization/simconfig/equilibrium.py`, pure, no CONFIG) -- the same discipline `staffing.py`
keeps, so the reference-run driver and the audit evaluation call one function.
