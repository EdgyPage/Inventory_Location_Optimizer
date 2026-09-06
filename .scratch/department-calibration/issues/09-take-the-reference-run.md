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

2026-09-05, from resolving [Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md):
the launch is `python -m Optimization.run_simulation --spec calibration_reference --n-batches 40`
(`whatif_config.SPECS['calibration_reference']`: one cell, `fifo`, `lpt`, both channels, the era
as `run_defaults`; the crews derive, so pass NO crew flags). What this run inherits and what it
must write: (1) the committed record is the pass-0 SEED -- `constants.s_pick.<ch>.travel_share`
1.01 / 1.04 and `s_put.travel_share` 1.05 over the catalogue's analytic prediction, every entry
`provenance: seed`, `value: null`, no warehouse fingerprint, `k_max` null -- so this run's job is
to REPLACE it with `value`s stamped `measured`, the warehouse fingerprint (`run_spec.json`
`staffing.calibration[<pair>].run_fingerprint`), the batch fingerprint, the cost-model
parameters, the pass list and `k_max` per channel (`calibration.RECORD_SCHEMA` 1; keep the
travel shares too: `measured / analytic_s_pick` from the derived block is the travel share 02's
decision 7 prices a new catalogue with). (2) The analytic side is already in the run spec:
`staffing.derived[<pair>].channels.<ch>.script.analytic_s_pick` (the script's own seconds per
unit at ground) and `.analytic` (the catalogue's); measured `s_pick` is Σ `batch_stats.task_makespan`
÷ Σ `total_items` over the window per channel leaf, `s_put` from `work_events` put spans site-wide,
and the exact `s_recv` self-check value is `derived.receiving.s_recv.value` (seconds per pack)
against `batch_stats.recv_seconds` ÷ packs. (3) The FIXED POINT: pass 1 = `--s-pick-store X
--s-pick-ff Y --s-put Z` (recorded `declared`) or a candidate record via
`--calibration-record PATH`; daily demand is re-derived by the same code, so "moved less than 5%"
is read off `derived.channels.<ch>.daily_demand_units` between passes. (4) Window precondition:
`load_shift_days(db, run_id)` gives every day's `drained` verdict (the final day included); 10's
check reads it. (5) `_record_derived` RAISES on a resume whose re-derivation disagrees -- a
crashed reference run resumes with `--resume DIR` and nothing retyped, or starts over. (6) On the
smoke catalogues two pickers saturated a 134-SKU fulfillment section (`batch.saturated`, a
warning); at production scale check the `[staffing]` lines for that word before trusting a pass.

2026-09-06, from resolving [Build the equilibrium check and the throughput audit](10-build-the-equilibrium-check-and-audit.md):
UNBLOCKED. The launch is now ONE command, not a hand-driven loop:

    python -m Optimization.run_reference --n-batches 40 --window 20 39 --max-passes 2 --workers N -- --profiles-dir DIR [--store-pickers K --ff-pickers K ...]

(flags after `--` go to run_simulation untouched). It runs each pass as a subprocess under the
current candidate record, judges days 20-39 of every leaf with `equilibrium.check` (a failing
window is DISCARDED and RE-SEEDS the next pass with provenance `seed`; nothing from it is
`measured`), measures `s_pick` per channel, `s_put` one site value with per-leaf diagnostics,
`K_max`, and the EXACT per-pack receiving self-check (float tolerance; a failure is a broken run
and stops the driver), writes `calibration_record.pass<n>.json` / `.candidate.json` under
`--out` (default: a `calibration_reference_<ts>` dir under COMPARISON_OUTPUT_DIR), and stops at
<5% movement in derived daily demand. `--install` copies a MEASURED final record over
`Optimization/simconfig/calibration_record.json` (refused for a re-seed); committing it is the
human's act. `--measure RUN_ROOT --window lo hi` re-judges a finished run.

What to check before trusting a pass, from the 5-day smoke run: (1) the STORE leaf placed zero
reorders in 5 days (coverage 10 batches) -- its put and receiving utilization read 0.000; confirm
`reorder_placements` is nonzero inside days 20-39 or the site crews are being sized against a
load the window never carried; (2) the fulfillment seed was 5x off (travel share 5.1 vs 1.04)
on a saturated 128-SKU section -- watch `[staffing] ... clamped` and expect pass 0 to fail its
window and re-seed; (3) `released_late` behind a capped day is that day's overrun and is
recorded, not an error; (4) the `passes` list in the candidate carries every window's verdict
with readings, so "converged" vs "cut off" is readable off the record. The audit
(`throughput.audit`) renders on every era leaf and logs the same verdict per arm.
