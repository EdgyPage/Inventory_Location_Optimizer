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

## Fog

- ~~Is `t_sample`'s archived k = 1.52 an artifact of the retired `v1` sampler?~~ **CLOSED by
  ticket 02: yes.** v1 fits 1.477 independently; v3 fits 0.822.
- Does anything in the analysis half of `Optimization/` grow superlinearly? 78 of its 147 files
  are `Performance_Evaluations`, measured by NOTHING -- but an `ast` sweep finds only 3
  triple-nested sites there, over bounded axes. Expect "found nothing" to be the honest answer.
- What replaces the `R x A` run-boundary rebuild in `_TravelBalancedPool`? The `per_pick` memo is
  a constant factor; the structural fix is an algorithm search and may close with a reason.
