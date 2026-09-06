# Build the equilibrium check and the throughput audit

Type: task
Status: resolved
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

## Answer

Resolved 2026-09-06. BUILT, on `develop`. One pure function, two callers, and the sim never
judges itself.

**1. The check** is `Optimization/simconfig/equilibrium.py`. `check_rows(shift_rows=,
batch_rows=, work_rows=, day_lo=, day_hi=, expectations=)` is the arithmetic over already-loaded
rows (so every clause is proven able to fail without a DB); `check(db_path, run_id, day_lo,
day_hi, *, expectations)` is the loader in front of it (`load_shift_days`, `load_batch_stats`,
`load_work_hours`); `expectations_for(staffing, pair=, channel=)` is the ONE reader of the
staffing record for both callers (the run spec's block, or the copy on `sim_result`), returning
`{day_seconds, band_tol, departments: {pick|put|recv: {crew, expected}}, absent, flags}` -- a
department with no crew or no recorded expectation is ABSENT, never expected at 0.0. The
**verdict** is `Verdict(passed, day_lo, day_hi, clauses)` with `clauses` an ordered dict
`drained | released_late | utilization | missed_share -> Clause(name, passed, reading, reason)`,
`.reasons`, `.as_dict()` (what the calibration record and the audit carry) and `summarize()`
for one log line. Realized utilization is a ratio of sums over the window against the whole
grant (crew x S x days). `InstrumentError` and `RecordError` are its two exceptions.

**Two facts the first era run taught, both of which changed the decided shape:**

- **Lag belongs to the day BEFORE it.** `released_late` lands on the batch released INTO day
  d, but the day that overran is d-1: the smoke run's store leaf capped day 1 and left 69.9 s
  on the batch released into day 2, which then DRAINED. The decision's "released_late = 0 on
  every drained day" read literally raised on a legitimate run. The assertion built is: lag in
  day d exists only behind a CAPPED day d-1; lag behind a DRAINED day, or on day 0 (nothing to
  overrun), RAISES `InstrumentError`. Behind a capped day it is that day's overrun and is
  recorded (`lag_s_behind_capped_days`).
- **The receiving self-check cannot be an average.** Built first as "measured seconds per
  pack vs the derivation's `s_recv`" (08's comment), it failed the smoke run by 7x: the site
  `s_recv` (51.9 s/pack) is dominated by heavy store packs (~149 s/pack) while only the
  fulfillment leaf received anything (~7.4 s/pack), and even within a channel the actual lot
  mix moves a per-pack average by a few percent. The check is now EXACT PER ROW, as decision 2
  intended: every receive row (`receive_event_frame` / `load_receive_events`) is re-priced
  from its SKU and quantity with the channel's `UnloadCost` rebuilt from the run spec the way
  the runner builds it (`unload_price_for`: pick config by reference -> `PutawayCost.from_pick`
  x put scales -> `UnloadCost.from_putaway` x receive scale -> the `inbound_unload_*` overlay),
  against the duration the dock charged, at a FLOAT tolerance (`RECV_TOL` = 1e-6). On the smoke
  run: 3,209.5 s charged vs 3,209.5 s exact over 435 packs. The script's average `s_recv` is
  kept in the pass entry as a diagnostic only.

**2. Caller one, the reference-run driver**, is `Optimization/simconfig/reference.py` +
the CLI `Optimization/run_reference.py` (the tenth root entry point). `measure_leaf` (s_pick =
sum task_makespan / sum total_items; K_max = window minimum of floor(sum task s / heaviest task
s) per day off `task_stats`; put seconds / units and receive seconds / packs off the
`work_hours_frame` fold, which gained `units` = sum qty), `measure_pass(run_root, day_lo=,
day_hi=)` (walks `rt.sim_dbs()`, expectations per leaf, the check, the exact self-check per
leaf, s_pick per channel and s_put one site value as ratios of sums, daily demand the pass RAN
under), `candidate_record(prev, result, pass_no=)`, `converged(prev_daily, new_daily)` (< 5%
per channel) and `fixed_point(launch=, measure=, seed_record=, out_dir=, max_passes=2)` with
the two callables injected so the loop is tested without a simulation. **A discarded window
re-seeds the next pass**: the derivation is deterministic, so re-running the same seed
reproduces the same capped days; a failed window's ratios are written as the next record's
constants with provenance `seed` (never `measured`), its verdicts go into `passes`, and no
provenance (fingerprints, commit) is stamped from it. A record leaves the loop `measured` only
from a PASSING window; `converged` / `cut_off` say whether the fixed point closed. The CLI
launches `python -m Optimization.run_simulation --spec calibration_reference --n-batches N
--calibration-record <candidate> --no-analyze` as a SUBPROCESS per pass (spawn pool, never a
heredoc), tees each pass's log, refuses a pass whose log carries `Traceback` / `produced no
data`, and `--install` copies a MEASURED final record over the committed one (a re-seed is
refused). `--measure RUN_ROOT [--window lo hi]` judges and measures a finished run without
launching. A receiving self-check failure is a `ReferenceRunError` (a broken run), not a
discarded window.

**3. Caller two, the throughput audit**, is the evaluation **`throughput.audit`**
(`Optimization/Performance_Evaluations/throughput/audit.py`, family `throughput`, preset group
`_TRENDS`, `needs=('shift', 'batch')`, `shape=('ranked', 'inspection')`). Routed R3 through
the declared-quantity path: ONE new quantity, **`days_capped`** (count of ledger rows with
`drained = 0`, direction lower, kind `shift` -> `shift_days`, capability **`shift_days`**, a new
`SIM_CAPABILITIES` entry), drawn ranked as `absolute_days_capped.png` / `percent_days_capped.png`.
Realized-vs-expected utilization per department has NO honest direction (a below-band picking
read is a campaign arm's saving and a reference run's failed precondition), so it is NOT a
Quantity -- exactly the `yard.scorecard` precedent -- and lives in the inspection table
`absolute_throughput_audit.png`: arm x department, days / drained / capped ("n - not
delivered") / overtime days, crew, expected, realized, band, and a one-word read (`in band`,
`below: travel saving` on a non-baseline picking row, `below band`, `ABOVE band`, `n/a (why)`),
with the calibration stamps (seed / stale / K_max exceeded / saturated) in the subtitle. The
check runs over every day the ledger closed and is logged per arm; a capped day is reported,
never failed; `InstrumentError` still raises (the driver records it as an evaluation error).
Infrastructure it rides on: the per-day frame `frames._sdf` (ledger joined to the batches and
the work legs through `work_day`, priced per day against the expectations; empty frame when no
day was closed, NaN utilization without a crew), `FRAME_TABLE['shift']`, the `shift` request
(EraUnmet when no arm closed a day), `ctx.shift_df()` / `ctx.staffing_expectations()` (memoised;
None on a flag-off run), and `work_day` / `released_late` on the batch frame (`work_day` is
bookkeeping in `tables.tidy`). No CSV: an undeclared file under `figures/` fails preflight; the
readings are in the table and the log. On every archived run the era gate refuses the audit
with a reason (no ledger rows), which is the right answer.

**4. Tests** (31 new, real asserts): `Tests/unit/test_equilibrium_check.py` (a passing window;
one capped day in twenty fails only `drained`; a day the ledger never closed fails; lag behind a
drained day and on day 0 RAISE, behind a capped day is recorded; a department out of band;
the band is around EXPECTED not rho; ratio of sums vs mean of days; a trending missed share
fails and a high stable one passes; an absent department is not gated; expectations off the
record incl. `RecordError`; a real sim DB round trip through the loaders),
`Tests/unit/test_reference_run.py` (a failing window re-seeds and is never averaged in -- the
final s_pick is the passing pass's value, not the mean; two failing passes leave a re-seed;
early stop on convergence; `converged` arithmetic; a travel share below 1.0 is dropped with its
reason and the record still loads; the exact self-check passes a mixed pack set and fails one
row charged a second too much; `measure_leaf` against a hand computation), and
`Tests/unit/test_throughput_audit.py` (the day frame, the reading vocabulary, registration).
The unit tier is 1704 green. The R3 gates pass (era gate, view coverage, column semantics,
figure registry, schema compatibility after `scripts/schema_report.py --sync`, which refreshed
`Schema/shapes/INDEX.json` -- no DDL changed, only a `DDL_SOURCES` file).

**5. What the smoke run showed** (300 SKUs, 40% fulfillment, 2 pickers per channel, 5 days,
era on): both leaves render the audit; the window fails everywhere, as a toy should. Measured
s_pick: store 58.6 s/unit against the seed's 40.2 x 1.01 (travel share 1.46), fulfillment 15.9
against 3.10 x 1.04 (share 5.1 -- the seed is far off for a saturated 128-SKU section);
s_put 40.9 vs analytic 22.3 (share 1.83 vs the guessed 1.05); K_max store 1, fulfillment 3. The
store leaf placed ZERO reorders in 5 days (coverage 10 batches), so its put and receiving
utilization read 0.000 against expectations of 0.26 / 0.44 -- the 40-day window with days
20-39 measured is what the derivation's steady state needs, and 09 must check the store leaf
actually reorders inside it. The re-seed path produced a candidate from the failed window.

**Not built, by decision.** The re-analysis-side "warn and stamp" re-derivation (03 decision 3):
the audit reads the RECORDED derived block, and a record measured on another catalogue shows as
the `calibration STALE` subtitle. The interaction-effects fog is untouched. Whether a capped
campaign day is a comparison caveat is still reported, not judged.

**Docs:** README (CLI table row + the tenth entry point), `Optimization/config/README.md`
(two rows). No glossary change: *Equilibrium*, *Reference run*, *Utilization* already say it.
