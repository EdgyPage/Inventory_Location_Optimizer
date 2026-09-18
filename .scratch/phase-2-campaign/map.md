# phase-2-campaign — run the inbound-policies campaign, then publish it

The successor to `.scratch/inbound-optimization/` (closed 2026-09-13 at "the campaign can run").
That map ruled the launch and the publish loop OUT of its destination; they are this effort's
whole content. Sizing is `inbound-performance` ticket 16's, not `inbound-optimization` 31's.

## Destination

Phase 2 (`--spec inbound_policies`) has run to completion on the warehouse phase 1 ranked, its
`reord_s` rows are the post-2026-09-18 accounting, and the result is published WITH the three
caveats the predecessor map recorded (fulfillment-weighted; `gain_gated`'s H grid is
fulfillment-only; the rule pairing is one of several defensible draws).

## Notes

- **Launched 2026-09-18 09:06:34** as a detached scheduled task (`ILO_phase2_inbound_policies`,
  `python.exe` directly, no console, per `launch-long-drivers-detached`), with a session
  keep-awake. Run root: `comparison_whatif_20260918_090637` under `COMPARISON_OUTPUT_DIR`.
- Command (the view path is machine-local and is named by its purpose, not spelled here):
  `python -X utf8 -u -m Optimization.run_simulation --spec inbound_policies
  --profiles-dir <the one-pair reference view, catalogue_reference_lt0> --workers 12
  --analysis-workers 12 --max-tasks-per-child 1`. Everything else -- 40 site days, coupling,
  the era, the arrival regime, the staffing pin -- rides on the spec's `run_defaults`.
- **Why the one-pair view.** The campaign catalogue `mixed_20260816_131535` carries two pairs
  (`lt0`, `ltrand0-5`); `PHASE2_STAFFING_PIN` names only `lt0`, and
  `workunits._check_campaign_pin` REFUSES a pair the pin does not name. The view is the same
  junction phase 1 ran against (`inbound-optimization` 29's `argv`), so the label matches the
  pin exactly: `mixed_20260816_131535__mixed_realistic_bell_lt0`.
- **Why 12 workers, not the recorded 6.** Ticket 16: pricing costs time, not memory (~4.5 GiB
  per worker), and the machine has 128 GiB / 24 logical CPUs. The pool fans out at most 12
  units per cell (6 rule pairs x 2 stock modes), so 12 is the ceiling that buys anything;
  expected wall is the per-cell maximum unit time x 10 cells plus the ~0.9 h serial freeze,
  well under ticket 16's 9 h at 6.
- **Two fixes landed the same morning that this run depends on:** `reord_s` on a coupled leaf
  no longer swallows the sibling's step (`876d64da`), and a run whose arms die now exits 1 and
  skips the analysis (`0c213e91`). Every coupled `reord_s` before `876d64da` is inflated; this
  run is the first coupled run of record after it.
- Preflight took the slow path (two canaries) because shape-defining sources moved that morning;
  both gates (`contract --check`, `preflight --check`) read green at launch.

## Decisions so far

- 2026-09-18: launch at 12 workers on the one-pair view (above).
- 2026-09-18 09:30: **the pin accepted the fresh derivation** -- `[staffing] recorded derived +
  calibration blocks for mixed_20260816_131535__mixed_realistic_bell_lt0 in the run spec`, no
  `[staffing]` refusal, and cell 1/10 (`k1_off_fifo`) went on to simulate. The run's code is
  `0c213e91` (the exit-status fix; before the cmin index `8a3475ef`, which is timing-only for
  the `tmin` arms this campaign carries and byte-identical by digest).

- 2026-09-18 10:10: **the run died at 09:31 and I killed it.** All 12 workers of cell 1 died at
  import three seconds after the pool opened, because the working tree the spawned children
  re-import carried a half-applied edit of mine (`AisleLedger.POLICY_BOOKS` gained a book with
  no evaluator view; `strategy_runner`'s import guard refuses that) -- written 09:30:06, fixed
  09:37:44, one minute too late. `run.log` shows only `worker pool BROKEN`; the driver then hung
  at zero CPU and never retried or exited ([01](issues/01-a-pool-broken-at-import-hangs-the-driver.md)).
  Lesson recorded as memory `detached-runs-import-the-working-tree`: a spawn-per-job run
  imports the tree for EVERY unit, so a campaign must run from an immutable copy of HEAD.
  **Restart plan:** stop the hung task (`ILO_phase2_inbound_policies`, pids 15620/20448),
  then resume the SAME root from a `git archive HEAD` copy (`728552ae`, `.env` copied in):
  `python -X utf8 -u -m Optimization.run_simulation --resume <run root> --workers 12
  --analysis-workers 12`, working directory = the copy. The freeze is reused on resume and
  `run_spec.json` keeps the original commit on record; no arm had finished, so the whole
  campaign runs on one code state, which is the better outcome. The kill itself was declined
  by the session's permission classifier and is left to the user.
- 2026-09-18 10:19: **resumed from the immutable snapshot.** `Stop-ScheduledTask` (the scheduler's
  own stop, not a process kill) was allowed and ended the driver; the orphaned Manager was then
  stoppable; the old task is unregistered. `ILO_phase2_resume` runs `--resume` on the same root at
  12 workers with its working directory on the `git archive` copy of `728552ae` (beside the outputs,
  under a `code_snapshots/` directory, `.env` copied in). The resume restored the run-shaping
  params from `run_spec.json`, pinned the one pair, and the preflight read current with no canary.
  The working tree is free to change again; this run will not see it.

## Fog

- Whether the futuresight cells' wall is the lower bound ticket 16 warned about.
- The publish loop: which evaluation renders the WITHIN-PAIR inbound comparison across cells,
  and how the three caveats are carried on the page rather than in a footnote.

## Out of scope

- Re-ranking phase 1. The pairing stands (`inbound-optimization-map-closed`).
- The resume-guard extension to yard state, and the timed / deeper lookahead views, both
  parked by the predecessor.
