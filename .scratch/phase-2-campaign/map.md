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

## The reframe (2026-09-18, evening) -- the campaign is a three-phase FUNNEL, not a factorial

Decided with the user after the three-cell analysis (memory
`inbound-campaign-is-a-three-phase-funnel`; plan `indexed-chasing-octopus`): the 120-unit
factorial stays STOPPED and its three cells are calibration evidence. Phase 2 is re-asked as
the UNLOADING question alone -- `--spec inbound_unload`: the same ten cells, phase 1's winner
(`PHASE2_WINNER` = `rank_cartlabor`/`rank_minlabor`) plus the `fifo` rider, both stock modes,
40 units -- scored by `run_unload_ranking` on TOTAL SITE LABOUR with the yard overage as the
tie-break inside a declared 0.1% floor (picking labour alone was flat to 0.01% across the
three cells). Phase 3 (`inbound_confirm`, registered once the chosen cells are copied into
`PHASE3_UNLOAD`) crosses phase 1's best three pairs with phase 2's best three cells.

Landed for it on develop before the relaunch: ticket 01 (the broken-pool hang: drain +
import probe + a gated test), ticket 02 (the evaluator's all-aisle union is a lazy view over a
counted inverse -- the priced `rank_random` drain 5.2x faster at campaign scale, byte-identical
by the `_toy_priced` digest), the `unload_ranking_json` artifact in the run-tree contract, the
margins as fields on `restock_selection.json`, and `phase2_inbound_axis(keep=)` for a bench
pre-screen. The yard bench itself (plan section 1) is optional and not built yet; phase 2
launches on the full ten cells.

## Phase 2 relaunched as `inbound_unload` -- 2026-09-19 00:21

- Task `ILO_phase2_inbound_unload` (`python.exe` directly, no console, 3-day limit, session
  keep-awake requested), working directory the `git archive` copy of `a65478f8` under
  `code_snapshots/`, `.env` copied in. Run root `comparison_whatif_20260919_002111` under
  `COMPARISON_OUTPUT_DIR`. Same command as the factorial with `--spec inbound_unload`: the
  one-pair reference view, 12 workers, 12 analysis workers, `--max-tasks-per-child 1`.
- 10 cells, pairs `rank_cartlabor`/`rank_minlabor` + the `fifo` rider, 40 units. Preflight read
  current on run-tree schema `041f5407cbb4`; the freeze started at once.
- After it finishes: `python -m Optimization.analyze_run <root>` then
  `python -m Optimization.run_unload_ranking <root>` (the winner pair is the only non-rider
  pair, so no `--pair` is needed); copy `chosen` into `PHASE3_UNLOAD`, register
  `inbound_confirm`, launch phase 3 the same way.

## Stopped again 2026-09-19 08:57 -- ticket 03, the per-drain tier freeze

- At the user's call, after cells 1-3 and the two fifo units of cell 4 completed (28 leaves).
  Root `comparison_whatif_20260919_002111` is resumable; its cells are the campaign-scale
  byte-identity reference for [03](issues/03-a-pool-open-rebuilds-the-tier.md).
- Cell walls: fifo 47 min, lifo 47 min, gmyopic (priced) ~3 h for each winner unit against
  25-40 min unpriced -- the pool rebuilt the whole tier at every one of ~7,400 opens per drain
  to seat ~12 units. 03 freezes each tier once per drain and opens pools as overlays.

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
  **01 RESOLVED 2026-09-18 (evening):** reproduced with a thread dump -- the call queue's feeder
  thread blocked in `send_bytes` on a pipe no worker reads (CPython gh-107219, fixed in 3.11.5;
  this machine is 3.11.4), and the stdlib's own fix measured NOT to work here; the supervisor now
  drains its own read end on the broken path, runs an import probe that logs WHY the workers
  died, and `test_supervisor_broken_pool.py` is in the gate.
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

- 2026-09-18 13:25: **the priced cells are bounded by `rank_random`/`rank_popularity`, not by
  cartlabor.** Cell 3 (`gain_myopic`): those two pairs at batch 8/40 after 55 min (~4.5 h per
  unit, ~25x unpriced), cartlabor/minlabor at 24/40, fifo done in 18.6 min (1.9x). Mechanism in
  [02](issues/02-the-all-idx-union-under-the-evaluator.md): `_all_idx` is rebuilt from every
  aisle on every pool open, and under the evaluator's copy-on-write view that materializes all
  2,774 aisles, T(T+1) times per drain. Projection: ~4.5 h per priced cell, seven of them, so
  the campaign ends around the evening of 2026-09-19 rather than tonight. The run is left to
  run; the fix is a post-campaign ticket because it moves the cost every priced cell publishes.

- 2026-09-18 15:26: **cell 3's random pair is not settling.** Its four-batch checkpoint windows
  took 37, 55 and 78 minutes (batches 4-8, 8-12, 12-16), about twenty minutes more each, while
  every other unit of the cell finished by 14:23. Six windows remain; extrapolated, that unit
  ends near 06:00 on 2026-09-19 and each later priced cell would take a similar day, so the
  campaign as launched is a four-to-five-day run. The growth is the cubic drain under a yard
  that keeps deepening beneath a policy that places poorly, on top of ticket 02's per-open
  union. Options put to the user: let it run; stop, drop pair 5 (`rank_random`/`rank_popularity`,
  the negative control) and relaunch fresh, running pair 5 alone after ticket 02; or fix ticket
  02 first. Recommended the second. Awaiting the decision; the run continues meanwhile.

- 2026-09-18 20:50: **stopped after cell 3 on the user's decision**, to analyze what exists
  and take the runtime work (ticket 02, the no-index-insertion pattern already in the snapshot,
  and further cache work) before the remaining seven cells. Cells 1-3 complete: 36 units, 6
  groups finalized, 47 GiB. Cell 4 (`k1_off_gforecast`) had begun its setup only -- a directory
  with no arm DBs; a resume re-walks it. Cell 3's per-unit walls: fifo 18.6 min, ranked-labour
  families 40-75 min, the random pair 7.8 h. The three-cell analysis was run from the snapshot
  (`analyze_run` with `cells=` the three names, reference `k1_off_fifo`, 12 workers, graph
  granularity); its `analysis.log` is in the run root. The task is unregistered; the root is
  resumable with `--resume` from a snapshot of whatever HEAD carries the fixes.

- 2026-09-18 21:00: **the three-cell analysis is done** (`analysis.log` in the run root; every
  evaluation rendered, no evaluation raised; the leaf-scope yard views are skipped by design on a
  coupled run and the site-scope ones answered from each unit's `_site` DB; cross-cell what-ifs and
  the dossier written, reference `k1_off_fifo`). What it says, over all 12 units per cell:

  | cell | mean detention | p90 | max | standing at end | contended drains | binding-cut drains |
  |---|---|---|---|---|---|---|
  | fifo | 0.35 d | 0.46 d | 0.60 d | 97 | 134/480 | 193/480 |
  | lifo | 0.35 d | 0.59 d | 2.29 d | 101 | 126/480 | 201/480 |
  | gain_myopic | 0.34 d | 0.52 d | 1.36 d | 89 | 113/480 | 188/480 |

  Pick-side labour hours are identical across cells to 0.01% (the yard policy does not touch
  picking, as it should not); the cross-cell pick deltas are within 0.1%. The priced policy
  reduces contention and the standing tail modestly and lengthens the detention tail: it defers
  some trailers on purpose. Small effects at this arrival rate against four doors -- most drains
  see a yard of one -- which is the campaign's real signal size on this catalogue.

  **Runtime, the reason for stopping:** unpriced cells 5.3 h of leaf-time each, the priced cell
  85.4 h, and 95.8% of the priced time is `reord_s` -- the site drain under the evaluator. The
  I/O half (`save_s`) is 11-12 min per cell whether priced or not; the no-index-insertion pattern
  is in this run already. The per-family multiples: fifo 1.9x, ranked-labour 7-16x, the random
  pair 39-59x (ticket 02). The optimization target is the drain's per-open cost, not I/O.

## Fog

- Whether the futuresight cells' wall is the lower bound ticket 16 warned about.
- The publish loop: which evaluation renders the WITHIN-PAIR inbound comparison across cells,
  and how the three caveats are carried on the page rather than in a footnote.

## Out of scope

- Re-ranking phase 1. The pairing stands (`inbound-optimization-map-closed`).
- The resume-guard extension to yard state, and the timed / deeper lookahead views, both
  parked by the predecessor.
