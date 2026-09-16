# Complexity round

Label: wayfinder:map

## Destination

Nothing in `Optimization/`, `Inbound/` or `Warehouse/` grows quadratically without either a
**refactor** or a **recorded reason plus the scale at which it becomes real** -- and the
instrument that decides which is trustworthy enough to be believed.

Concretely, reached when:

1. `Tests/calltree` is green on HEAD and its offender table ranks by something a reader can act
   on, rather than by bare exponent.
2. The measurement fixture runs the configuration PRODUCTION runs. A ladder measuring code the
   run does not execute is the failure this repo has already paid for three times.
3. A HEAD offender table exists and is archived. Every archived artifact today predates
   `bd29d2eb` and none may be cited.
4. Each convicted offender is refactored with a guard, or closed with a stated reason.
5. The guards are permanent and cheap -- call counts, never wall clocks -- so a fix cannot
   silently regress.

## Notes

- **Measured exponent, not nesting depth.** A static sweep finds 221 double-nested and 37
  triple-nested loop sites across the three packages; almost all have a bounded inner dimension.
  Meanwhile the cheap antipatterns are already ABSENT: no `pop(0)`-as-queue anywhere, no
  `concat`-in-a-loop, no string concat in a loop, and `heapq`/`bisect`/`deque` throughout. What
  is left is structural, and only a fitted exponent finds it.
- **Byte-identity is the default; a break is DECLARED, never discovered.** User decision: a
  candidate may break comparability when that is the only way to buy a complexity class, but only
  if it is digest-classified, dated, recorded beside the six existing breaks, and the win is
  measured FIRST.
- **Deep ladder per candidate** (user decision). Three constraints that choice runs into:
  `--config` is refused on `--ladder deep` (pass real `run_simulation` flags instead); a rung
  above its catalogue is truncated in SILENCE and looks exactly like a subsystem that stopped
  growing; and the RUN wall needs a quiet host -- four samples of identical work once differed by
  +19.7, +24.0, -0.6 and +1.4 s.
- **Runs after `config-centralization`**, which gives this effort a clean baseline.

## Decisions so far

- **[01] The offender table's top entry was timer noise, and its ranking answered the wrong
  question.** The deep ladder's ONLY offender was `t_task` at k=3.916, r2=0.946 -- fitted on a
  4.9e-05 s anchor, over a section worth 93 ms. The existing `max(ys) < 0.01` gate asks whether a
  section ever got BIG; what poisons a log-log fit is the SMALLEST positive point. `MIN_WALL_S`
  now suppresses such a fit INTO `report['suppressed']` with its reason rather than dropping it.
  Separately, `sort(key=-exponent)` ranked seconds, call counts, ratios and knees in one order on
  a number that means something different in each -- against this project's own lesson, "element
  counts must be weighted by COST CLASS before ranking". `_severity_sort` groups by class then by
  projected magnitude at 10x the top rung.
  **Worth less than it first appears, and the ticket says so**: the deep table becomes empty, but
  on the meso ladder the re-ranking moves exactly ONE entry (rank 9 -> 5). The bigger apparent
  difference against the archived report is fitter drift since 2026-08-26, not the new sort.
  -> [01-the-offender-table-cannot-be-read.md](issues/01-the-offender-table-cannot-be-read.md)

- **[02] The ladder has been fitting an exponent on a sampler production retired on 2026-09-12 --
  and switching to the era moves no measurement at all.** `calltree_scenarios` built its
  `BatchConfig` without a `sampler`, taking the dataclass default `'v1'` (the O(k*N) cumsum) while
  `settings.SAMPLER` is `'v3'` (the segment tree). `Batch.__init__` is SECTION_MAP's anchor for
  `t_sample`, so every archived `t_sample` exponent describes code no run executes. Measured
  directly at k=0.15*N over 500..8,000: **v1 k=1.477 r2=0.993, v3 k=0.822 r2=0.901, 9.6x apart at
  N=8,000** -- against the archived ladder's `t_sample` k=1.522, so the attribution closes.
  The surprise, and it makes the fix free: at 2,000 SKUs over ten seeds **v1 and v3 draw
  byte-identical batches** -- same SKUs, quantities and draw ORDER. They implement the same
  selection rule and part company only where v1's float accumulation does, which is the production
  catalogue's ~1e26 weight range, not a benign fixture. So `Workload_Builder`'s comment that v3
  "moves every batch sequence" does not hold at fixture scale, the switch changed the COST of
  drawing and not the batch, and count exponents stay comparable with the archive.
  -> [02-the-ladder-measures-a-retired-sampler.md](issues/02-the-ladder-measures-a-retired-sampler.md)

- **[04] THE HEAD OFFENDER TABLE -- and it refutes the claim that the `take` heap fixed
  `_aisle_best`.** Measured 2026-09-16 on `608fbfc9`, sequentially on a quiet host, both configs.
  `pytest Tests/calltree -q` re-confirmed green with the era sampler (33 passed, 434 s).
  Ticket 02's pre-registered prediction held: `t_sample` fell from k=1.522 to **1.11 / 1.09** and
  left the table.
  **Nothing is a section-wall offender** -- the highest is `t_reord` at 1.48 against a 1.50 flag.
  Every conviction is a call count: `delta_lift_idxs` k=1.59, `_TravelBalancedPool._aisle_best`
  and `._score_of` k=1.54-1.57 (427,497 calls at 8,000 SKUs), and -- new, never in the archive --
  **`cost_model:per_pick` k=1.31 at 542,781 calls**.
  **One mechanism, not five.** Dividing by the run's own placements, work per unit IS growing and
  all three grow together: `_aisle_best` k=0.573 (0.366 -> 1.205 per placement), `delta_lift_idxs`
  k=0.502, `per_pick` k=0.414. They are the same thing -- every family re-scores EVERY AISLE at
  every SKU-run boundary, so per-unit cost tracks the aisle count.
  **`bd29d2eb` removed the SCAN in `take`'s selection, not the run-boundary rebuild.** `_aisle_best`
  is convicted on HEAD under both configs and its per-placement cost more than triples across the
  ladder. This is the first HEAD measurement of the `R x A` term, and it stands.
  **Coverage gap, stated rather than read as an acquittal:** `_RankedAssignPool` (ticket 03) does
  not appear in this table at all, because these rungs run the default strategy and not the
  ranked-assign arms. That is the ladder's blind spot, not evidence the scan is harmless.
  -> [04-the-head-offender-table.md](issues/04-the-head-offender-table.md)

- **[06] CLOSED: the `per_pick` memo is worth under 1%, and the form the findings doc suggests is
  the worse of the two.** `INBOUND_PERF_FINDINGS.md` names it as the cheap half of the `R x A`
  candidate, and the HEAD ladder convicted it independently (k=1.31, 542,781 calls). Priced before
  building, against that exact count: today 42.8 ms, the suggested dict memo 15.5 ms (**0.72%** of
  the 3.78 s rung), hoisting the invariant 10.4 ms (**0.86%**). A float-keyed dict pays hashing
  where a hoist pays one multiply; what both actually remove is the Python FUNCTION CALL.
  Closed rather than landed because 0.86% cannot justify the change it needs: the hoist means NOT
  calling `per_pick`, and that primitive exists to stop callers re-deriving the expression ("
  previously inlined at 7 sites... one helper makes the invariant structural"). Bit-identity was
  verified and is not the obstacle (`m * base` is IEEE byte-equal at qty=1).
  **The scale at which it becomes real:** it would need ~10x the share, and its share is
  scale-stable -- so the answer is not "run it bigger", it is that the STRUCTURAL half of the same
  candidate makes the question disappear by removing the calls.
  -> [06-the-per-pick-memo-is-worth-under-one-percent.md](issues/06-the-per-pick-memo-is-worth-under-one-percent.md)

- **[05] The "flows: ALL ZERO" warning read the wrong quantity and fired on EVERY rung.** It was
  the `else` of the per-ENTRY-CALL branch (an inbound-only quantity), so on every non-inbound
  config it printed "the put-away/receiving path did not execute under cfg=split_staging4" two
  lines below `held_appends=40,720`, advising a switch to the config already in use. The same
  nesting hid the per-placement RATIOS, which are not an inbound quantity either -- they were
  computed and fitted but never printed per rung. This is the exact failure the package README
  records being burned by (a held path that ran thirteen million times reporting `held: 0`), and a
  warning that cries wolf on every rung trains the reader to skip the line that exists to stop
  them trusting a zero. Now a testable `_flows_warning(flows, config)` helper.
  -> [05-the-all-zero-warning-cried-wolf-on-every-rung.md](issues/05-the-all-zero-warning-cried-wolf-on-every-rung.md)

## Fog

- ~~Is `t_sample`'s archived k = 1.52 an artifact of the retired `v1` sampler?~~ **CLOSED by
  ticket 02: yes.** v1 fits 1.477 independently; v3 fits 0.822.
- Does anything in the analysis half of `Optimization/` grow superlinearly? 78 of its 147 files
  are `Performance_Evaluations`, measured by NOTHING -- but an `ast` sweep finds only 3
  triple-nested sites there, over bounded axes. Expect "found nothing" to be the honest answer.
- What replaces the `R x A` run-boundary rebuild in `_TravelBalancedPool`? **Now the convicted
  top candidate** (ticket 04), not a speculative one. The `per_pick` memo is the measured cheap
  half -- 542,781 calls for a value depending only on `(m, var)`; the structural half is an
  algorithm search and may still close with a stated reason.
- The `skus` ladder runs the DEFAULT strategy, so no ranked-assign arm is exercised by any rung.
  Ticket 03's scan is therefore unmeasured rather than acquitted. Does the ladder need a strategy
  axis, or is a per-arm capture the right instrument?
