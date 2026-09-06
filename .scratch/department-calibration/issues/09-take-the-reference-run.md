# Take the reference run

Type: task
Status: resolved
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

## Answer

TAKEN 2026-09-06 -- and the procedure as decided produced **no measured record**. Two passes,
both windows discarded, fixed point cut off; the candidate record is `RE-SEED ONLY`. The
receiving self-check was exact on every leaf of every pass (charged == exact to the float
tolerance), so the cost model that ran is the one the derivation priced with. What failed is
the window, for reasons that are properties of the catalogue and the procedure, not of the code.

**The launch.** `python -m Optimization.run_reference --n-batches 40 --window 20 39 --max-passes 2
--workers 2 -- --profiles-dir <one-pair view>`; the view is a directory beside the catalogue tree
under `PROFILE_INPUT_DIR` holding the suite's `profile_layout.json` and a junction to the
`mixed_realistic_bell_lt0` pair only (the resolver is descriptor-first and existence-checked, so
the absent `ltrand0-5` pair is skipped and the pair label is unchanged). One pair, one cell,
`fifo` both arms, `lpt`, both channels, era on, crews derived, pickers 25 / 20 (the committed
defaults). Wall ~20 min per pass on 2 workers (the runs are tiny: ~4.5 GB per pass, 40 days in
5-7 min per leaf); records and pass logs under `calibration_reference_20260906_114651`, run roots
`comparison_20260906_114653` (pass 0) and `comparison_20260906_120744` (pass 1), all under
`COMPARISON_OUTPUT_DIR`. `repo_dirty` stamps true: the dirt was regenerated architecture docs and
one new memory file, no simulation code.

**Two Windows traps fixed on the way** (both in code the smoke runs never hit because they ran
from the repo drive): `run_reference` echoed the child's log through a cp1252 stdout and died
mid-pass on the first unencodable character, orphaning `run_simulation` (fixed: stdout is
reconfigured with `errors='replace'`); `calibration.record_stub` took `relpath` of the candidate
record against the repo and raised across drives (fixed: absolute path when relpath refuses --
the run tree is not tracked). Both unit files green (7 + 26).

**What the passes measured** (window days 20-39, both leaves, ratio of sums):

| pass | seed s_pick store / ff | measured s_pick store / ff | travel share | s_put (site) | derived demand store / ff (u/day) |
|---|---|---|---|---|---|
| 0 | 74.3 / 3.11 (analytic x 1.01 / 1.04) | 86.0 / 21.5 | 1.34 / 7.20 | 41.2 (share 4.73) | 8,237 / 157,515 |
| 1 | 86.0 / 21.5 | 95.2 / 25.6 | 1.47 / 8.55 | 36.9 (share 1.87) | 7,120 / 22,745 |

K_max: store 37 -> 32, fulfillment 40 -> 628 (the pass-0 fulfillment value is a saturated
artefact). Daily demand moved 13.6% (store) / 85.6% (fulfillment) between passes -> not converged.

**Why every window failed -- three separate causes.**

1. **The picking fixed point contracts but is slow.** Measured `s_pick` rose each pass as demand
   fell (fewer lines per day = more travel per unit): store x1.16 then x1.11, fulfillment x7.2
   then x1.19. The ratio of successive steps is ~0.7, so the 5% tolerance needs several more
   passes than 02's cap of two. Every measured day capped (0/20 drained on both leaves, both
   passes) because the seed under-priced picking by 10-19% and the day's work therefore exceeded
   the 0.85 grant: pick utilization 0.96-0.99 against expectations of 0.82-0.89. Roll-over makes a
   capped day sticky -- a backlog built under the wrong seed (fulfillment 3-5k units, store ~8k
   standing at day 39) does not clear inside the window. The continuation below says whether the
   pick constant alone closes when the cap is lifted.
2. **The replenishment cycle is longer than the window, so the put and receiving clauses cannot
   pass on this catalogue at 40 days.** The catalogue authors stock coverage as
   `equilibrium_coverage_batches = 10` in GENERATION-TIME batches (published depth: ~33.8k store
   / ~79k fulfillment units per batch). An era day is 7,120 / 22,745 units, so one generation
   batch is 4.7 / 3.5 era days and the first reorder wave lands at ~42 days (store) / ~31 days
   (fulfillment) -- at or beyond the window. Observed: reorder units per day inside the window
   were 2% of picked units on the store leaf (117 vs 7,328) and 30% on fulfillment (6.5k vs
   21.6k), still rising (6.1k -> 7.0k between the window halves). The bands assume reorder flow
   equals pick flow, so put read 0.03 / 0.27 against expectations of 0.21 / 0.63 and receiving
   0.04 / 0.07 against 0.70 / 0.14. No pass count fixes this; it is the window versus the
   catalogue's coverage. Note the circularity: coverage in era days depends on the calibrated
   demand, which is what the run measures.
   **The same lag also defeats the DRAINED clause through stockouts, not capacity.** On the
   third pass (continuation, below) fulfillment picking sat IN band (0.880 vs 0.826) and on
   day 23 the pickers finished 6,701 s before the whistle -- yet 1,426 units stood at close and
   the day counted CAPPED. The `carryover` ledger for days 20-39 says why: 89% of the
   fulfillment carry is `unpicked_unstocked` (5,764 rows vs 725 `unpicked_daycut`) -- demand on
   SKUs the replenishment wave has not yet refilled. Every task realized every planned item
   (363,402 / 363,402), so this is not picker shortfall. The store is the mirror image: 95%
   `unpicked_daycut` (7,995 vs 414) -- still over capacity because its pick constant has not
   converged, and its stock has not depleted yet because its wave lands after day 40.
3. **Realized demand runs above the derived target.** Batch content is a line fraction
   (mean_fraction 0.0029 / 0.0135), and units per line vary, so the released units per day
   averaged ~3-6% above `daily_demand_units` (store: picked 7,328/day plus a growing carry
   against a 7,120 target). Inside the 10% band, but it eats headroom the seed error then
   overruns.

**Continuation (evidence for the decision, not the record):** the fixed point was resumed from
the pass-1 candidate with `--seed-record` for up to four more passes, out dir
`calibration_reference_20260906_114651_cont`. **The picking constant converges at the FIFTH
overall pass** (demand moved 4.3% store / 3.2% fulfillment; the steps before were 13.6/85.6,
7.9/8.3), and at that pass pick utilization sat dead on its expectation -- store 0.891 vs 0.890,
fulfillment 0.821 vs 0.829 -- so the picking half of the procedure is sound and only the pass
cap was wrong. Converged values (seed the re-take from the `_cont` candidate, never the
analytic seed):

| constant | converged | travel share over analytic |
|---|---|---|
| s_pick store | ~106-108 s/unit | 1.63 |
| s_pick fulfillment | ~29.2 s/unit | 9.75 |
| s_put (site) | ~37.1-37.6 s/unit | 1.81 |
| K_max store / fulfillment | 46 / 529 (window minimum, last pass) | -- |

Every window in the continuation still failed, on the replenishment clauses only (put 0.24 vs
0.62, receiving 0.06 vs 0.13 on fulfillment; put 0.02 vs 0.22, receiving 0.02 vs 0.70 on the
store; 0/20 days drained, the carry stockouts). A sixth pass ran as confirmation; its numbers
are in the `_cont` dir's candidate and change nothing above.

**Outcome for the map.** The task is done -- the run was taken and the procedure's behaviour on
the production catalogue is now measured -- but no record can be committed, and 02's procedure
needs amending before a re-take can succeed. That is a decision, graduated to
[Fit the reference window to the replenishment cycle](11-fit-the-reference-window-to-the-replenishment-cycle.md);
the re-take is [Re-take the reference run](12-re-take-the-reference-run.md), blocked on it. The
inbound map's two cross-map gates (23, 24) now wait on 12, since they need a MEASURED record.
