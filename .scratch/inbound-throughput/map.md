# inbound-throughput — a cheaper unload evaluator, a fill trial that can rank it, and the pool below the unit

Label: wayfinder:map
Status: CLOSED 2026-09-23 by the user

## Closing note (2026-09-23)

Closed whole, by the user's decision: the evaluator problem needs re-thinking from the problem
statement, and the open tickets will not be relevant when it is picked up again.  Resolved:
01, 02, 03, 09, and 10's fifo question.  Closed unfinished (`wontfix`): 04, 05's open stock-mode
decision, 06's prototype, 07, 08, 10's priced spec.

**The user's direction for the re-think:** the aisles must CHURN enough to give the bins
breathing room, so that an inbound decision has something to decide.  The measurements that
point there: in the era campaign one inbound pack lands among 4-8 existing bins and moves its
term by 1/(N+1) of a line (ticket 01); in a 40k fill even lifo does not move pick labour,
because only 7.7% of the store's placements are picked again within 40 batches (ticket 06).
An unloading policy can only matter where placement is contended.

**What stays built on develop and remains usable:** the door-scarcity axis and specs
(`door_scarcity_axis`); the fill-trial driver mode (`INBOUND_FILL_SPAN_DAYS`, `_FillDispatch`,
the pick stage at one declared batch, the analysis's `pick_stage` filter); the plan-trace
probe and its scorer (`score_plan_trace.py`: tau, set agreement, regret); the pool-open bench;
the ranking fixes (census pricing, measured floor, rider as control, metric by spec).


Opened 2026-09-22 from a grilling session over the finished phase-2 campaign
(`comparison_whatif_20260920_150203` under `COMPARISON_OUTPUT_DIR`, done 2026-09-21 06:55).
The user named three problems from that run and this map is their design tree: code the
record calls non-optimizable, a score that does not separate the cells, and a pool that
cannot spread work below an atomic unit while workers idle. The successor to
`.scratch/phase-2-campaign/` for the runtime and the metric; that map keeps the campaign's
own results.

## Destination

1. A phase-2 gain policy plans a drain with an evaluator that is at least an order of
   magnitude cheaper than the exact `plan_order` at the campaign's yard depth, gated on order
   agreement with the exact plan (Kendall tau >= 0.9 median, top-1 >= 0.8) and accepted at
   cell level (overage and pick-owed within the ranking's own floor of the exact evaluator's).
2. A **fill trial** (CONTEXT.md) exists as a driver mode and a spec, prototyped on the 40k
   catalogue, and its ranking is `discriminating` on the winner pair with both stock modes
   agreeing -- or it is recorded as not, with the numbers.
3. The steady-state campaign's result is on the record as a RESULT: at this site's demand,
   unloading order cannot move placement, and the write-up says why in the run's own numbers.
4. A multi-cell run's sim-phase occupancy is at or above 90% (wall within 10% of worker-hours
   over the pool), by evaluator cost alone if that suffices, by helpers and leases if not.

## Notes (what the finished run measured, the base every decision stands on)

- **Wall.** 44 units, 81 unit-hours, 16 workers: sim wall 8.1 h against a 5.1 h floor. The
  queue emptied at 01:30 and occupancy fell 14 -> 10 -> 4 -> 2 of 16 until 06:45. Slowest unit
  7.9 h (`k1_off_fsight_w5` uni pair), 97% of it `reord_s`. The flat pool has nothing left to
  give across cells; the remaining 3 h is the atomic unit.
- **Where the unit's hours go.** The winner pair (`rank_cartlabor`/`rank_minlabor`) is a
  SELECTOR family in `Inbound/gain.py`, so every one of `plan_order`'s T(T+1) evaluations
  takes `_place_pool`: the arm's own pool rebuilt per evaluation over copies, with
  `_TravelBalancedPool._aisle_best` at 72% of each open (11,212 calls per open). The pool-free
  `_place_merge` rung from the fidelity ladder (`inbound-optimization` 04: 28 of 29 plans
  reproduced, ~5x cheaper) is built and wired ONLY for the extremal-D family (tmin/tmax).
  Exact memoisation across rounds is refuted (`the-gain-sweep-cannot-be-made-incremental`);
  an APPROXIMATE plan on stale gains is unmeasured. At yard depth 17 a plan costs 306
  placements; at depth 200 it costs 40,000.
- **Why the score is flat** (agent measurement 2026-09-22, all from the run's own tables).
  `ss_pick_owed` = sum over planned lines of the SKU's mean bin cost by bin COUNT, weighted by
  planned batches containing the SKU: ~1 line for 92% of touched store SKUs and 67% of
  fulfillment. One inbound pack lands among N = 4-8 existing bins and moves its term by
  1/(N+1) of one line's cost. Picked units served from inbound bins: store 8.7%,
  fulfillment 23.9%; inbound units never picked inside the window: 91% / 75%; implied
  coverage 477 d / 83 d; 90.6% of the store catalogue is never asked in 40 days. The 0.143%
  fifo-vs-gforecast cell gap is entirely the fulfillment leaf (the store leaf flips sign at
  +-0.05%, its noise), and ~35% of it is availability priced at zero (gforecast strands
  170-250 more lines per batch unservable; priced at the leaf mean the gap is 0.118%).
- **The ranking as it stands.** Winner pair: ten cells in two tie groups, every rank inside a
  group decided by the overage tie-break; rider: one tie group, four cells byte-identical;
  `rank_agreement` false. The record's own reading: the rider is a CONTROL (gain policies
  degenerate to arrival order under FIFO restock), not a replication.
- **A fill's volume.** Declaration 5.68M units (3.09M store, 2.60M fulfillment) against the
  campaign cell's 1.44M through the yard in 40 days at ~2,250 units per trailer: ~2,500
  trailers, ~160 site days at the era's derived crew. A dispatch span shorter than that leaves
  the remainder standing (dispatched over 20 days the yard peaks near 2,200), so the fill
  derives its OWN crew from the declaration over a declared span, and depth comes from a
  declared arrival-to-drain ratio, not from understaffing (ADR-0004: demand declared, crew
  derived). No empty-start path exists today: initial stock is placed at the freeze (cause
  `initial`) by the placement rule; the yard carries reorders only. Leads are authored per
  trailer (0 = instant), so a dispatched declaration bursts by construction.
- **Instruments.** Nothing between the 90 s `_toy_priced` digest and a 3 h campaign-scale
  probe can price a pool open (`phase-2-campaign` 04, open); the meso ladder gave the wrong
  sign once. `Tests/calltree/calltree_inbound_ladder.py` cannot stand a yard deeper than
  T = 2.3 at its 40k cap (`645cc6ec`). The toy fixture ties unload policies exactly
  (`toy-fixture-cannot-discriminate-unload-policies`).

## Decisions so far (the grilling, 2026-09-22; every one the user's)

- **Q1 The exponent is attacked, not accepted.** "Something different with comparable
  results" -- the exact `plan_order` is replaced by a cheaper evaluator, not made faster by
  constants. Ticket 03 measures which reduction; ticket 04 builds it.
- **Q9 Comparable, and REPLACE.** Gate at build time on per-drain order agreement with the
  exact plan (tau >= 0.9 median, top-1 >= 0.8, the ladder's own instrument); accept at
  campaign scale on cell-level overage and pick-owed within the ranking's floor. The cheap
  evaluator replaces `gain_myopic`/`gain_forecast` under their names (a new era); the exact
  one survives behind a fidelity knob on the policy, set by a probe cell the way
  `k1_off_gmyopic_k8` sets its trailer bound. One ADR when ticket 04 lands (Q28): "unload
  policies are planned by an approximate evaluator gated on order agreement".
- **Q10 Instrument first.** One probe cell with `plan_order` logging every round's candidate
  gains as a declared run-tree artifact; both fidelities (pool-free adapter for the selector
  families; top-m re-evaluation on stale gains) computed offline from one trace; build in
  the order the numbers say. They compound: ~5x per placement times T/m placements.
- **Q2/Q11/Q12 Intra-unit parallelism: yes, elastic, and AFTER the evaluator.** A coupled unit
  cannot split by leaf (shared dock; `_plan_strategy_start` refuses a torn pair) or by batch;
  the only seam is a drain's now-side sweep over T candidates. Helpers hold a replica the
  parent advances one trailer's takes per round; the pool grants LEASES of idle slots per
  drain through a Manager value, never while jobs are queued; the reduction is ordered by
  candidate index so the plan is identical at any lease count (toy digest at 0 and 4). The
  evaluator's bin identity is `id(bin)` today (28 sites) and must be re-keyed first, which
  the pool-free adapter does anyway -- so helpers wait on ticket 04 and are built only if
  the tail still breaks the 90% target (Q7). "Helper" and "lease" are harness words: they
  live in `workpool.py`'s docstring and here, not in CONTEXT.md.
- **Q4 The rider is a control** (CONTEXT.md: Rider). Its ranking never vetoes the winner
  pair's; `rank_agreement` compares replications only when there are two non-degenerate
  pairs.
- **Q5 Byte-identity per issue.** IDENTICAL by `run_digest.py --cell` against the finished
  root for the pool and for the parallel sweep; the metric is additive and rides the schema
  pipeline, no floor line, never compared across runs.
- **Q6 Bench first** (ticket 02): `Tests/bench/bench_pool_open.py` at the campaign shape,
  with a small-shape non-vacuity form in the CLAUDE.md pytest selection; it also reports the
  frozen-tier replica's RSS, which sizes a helper before one is spawned.
- **Q7 The target is occupancy**, not a wall: sim-phase occupancy >= 90%, read off the run
  log's `[pool]` lines from the first submit to the last unit's `done`.
- **Q8 A new map**, this one.
- **Q13/Q14 The steady-state result STANDS (c).** It is recorded and published as a finding
  with the availability caveat; phase 3 confirms phase 1's pairs alone; the fill trial is a
  new phase with its own spec, not a rescope. Rides with the write-up (ticket 01): the ranking
  tool prices unservable lines at the leaf's mean line instead of zero, and the 0.1% floor is
  re-declared against the score's own batch-to-batch noise (~0.05% on the store leaf).
  Restricting pick-owed's basis to inbound-touched SKUs was considered and dropped: those
  SKUs still carry ~1 planned line and three quarters of the placed stock has none.
- **Q15/Q20 The fill's depth is declared, not understaffed.** Crew derived from the
  declaration over a 40-site-day fill span (~4x the campaign's receiving crew; every 40-batch
  instrument stays valid); arrivals press it at a declared ratio of 0.95 -- as deep as a queue
  gets while still being one. Above 1 the drain order is forced by what stands and the
  policies converge on arrival order (the trailer-bound result from the other side).
- **Q16 Fill, THEN pick.** No picks during the fill; the pick stage is an ordinary 40-batch
  era run (base stock, reorders, Q22) whose only difference is where it starts, so it is
  byte-comparable across cells.
- **Q17 The fill's score.** Pick labour over the pick stage ranks (phase 1's own metric,
  comparable because the placements genuinely differ); the realised **future work** of the
  placed stock -- each placed unit's at-location cost times expected visits from catalogue
  frequency, the policy's own objective on the realised placement -- is the diagnostic, read
  once at the fill's end and at each keyframe of the pick stage; disagreement is the
  informative direction (the closed-form pattern).
- **Q18 40k first.** A dispatched declaration contends at any scale, so the fill trial
  prototypes on the 40k perf catalogue in the profiles tree in minutes; the 400k confirmation
  takes the prototype's top three plus the reference (Q23).
- **Q19 The term is Fill trial** (CONTEXT.md). Avoid cold start, initial fill, empty-warehouse
  run, stocking run.
- **Q21 Dispatch order is a seeded random world order** every arm shares, like the leads. By
  frequency would be an unloading policy in disguise.
- **Q23 Nine cells**: `inbound_unload`'s axis minus `inb_off` (places nothing in a fill) and
  `gmyopic_k8` (refuted); the fill mode REFUSES a spec naming either, the way the pin refuses
  an unnamed pair.
- **Q24 The fill trial hands off nothing.** Published as its own result.
- **Q25 Order**: 01 record + ranking fixes; 02 bench; 03 probe; 04 evaluator; 05 fill mode;
  06 fill spec + 40k prototype; 07 helpers (conditional); 08 400k confirmation. The pool work
  the effort was opened for lands seventh and conditionally, by the user's decision: the
  evaluator is where the hours are, and a pool that spreads a 10x cheaper unit may have
  nothing left to spread.
- **Q26 The fill trial's bar**: `discriminating` true on the winner pair at a re-declared
  floor AND the two stock modes agree on the order. A margin is arbitrary until the noise is
  measured.
- **Q27 Publish the steady-state result after ticket 01**, not before: the fixes change the
  ranks the page would show.
- **01 RESOLVED 2026-09-22** -- the steady-state result is recorded in the phase-2 campaign map ("The result -- 2026-09-22") and memory `pick-owed-cannot-see-inbound-at-this-demand`; `run_unload_ranking` prices the census at the leaf mean, measures the floor per rule pair (0.080% winner / 0.017% rider), reads the rider as a control and the metric off the spec; on the finished root `chosen` became `ggated_h050, ggated_h025, gforecast` ([01](issues/01-record-the-steady-state-result-and-fix-the-ranking.md)). Publish is next.
- **02 RESOLVED 2026-09-22** -- `Tests/bench/bench_pool_open.py` prices a pool open at the campaign shape in seconds for both winner-pair families (travel template 1.35x over eager, min-labour 1.22x; prologue 4.0 ms, 71% cursors) and weighs a helper replica at ~13.6 MiB for 25,200 bins; its smoke form is in the CLAUDE.md gate ([02](issues/02-bench-a-pool-open-at-campaign-shape.md)).
- **09 RESOLVED 2026-09-22** -- site yard figures name the arm pair (the `rank` collapse was `_assignment_of` splitting the key on underscores), the detention rows are named, the scorecard fits, the register lists the speeds; Experiment 9's workaround caption goes at the next campaign re-analysis ([09](issues/09-the-yard-family-cannot-say-which-arm-is-which.md)).
- **03 ANSWERED 2026-09-23** -- no reduction clears the Q9 gate (tau >= 0.9, top-1 >= 0.8); the merge rung is not order-equivalent for the selector families (first pick 75% / 39%); gmyopic's disagreements are near-ties (first wrong pick ~1-2% of the round's spread, p90 10-39% of the winner), gforecast's are substantive.  04 is not buildable as specified and waits on the user's reframing of the problem statement ([03](issues/03-instrument-the-plan-and-measure-two-fidelities.md)).
- **05 BUILT 2026-09-22, one decision open** -- the fill mode runs end to end on the toy (fill settles, crews shrink in place, pick stage runs, conservation OK, two runs IDENTICAL; off, `_toy_priced` IDENTICAL on 40 arms).  Built ahead of 04 because 04 gates a priced fill's COST, not its code.  OPEN, the user's: what the uniform stock mode means in a fill (no initial stock to place; options on the ticket) -- until then a fill runs opt arms only ([05](issues/05-the-fill-mode-in-the-driver.md)).
- **06 depth probe 2026-09-23: lifo does not separate from fifo on pick labour at 40k** (gaps -0.175% / +0.013% against a measured 0.24% floor; overage 8 vs 68 trailer-days); only 7.7% of store placements are picked again in 40 batches.  The prototype is NOT launched -- the user's direction is needed.
- **06 BUILT, launch blocked** -- the pick stage starts at one declared batch in every cell; the analysis, the what-if writers and the ranking read it alone; `inbound_fill` and `_probe_fill_depth` registered (`unpinned`, Q18); future work = the keyframe closed form.  The prototype waits on 04 (evaluator) and 05's stock-mode decision ([06](issues/06-the-fill-trial-spec-and-the-40k-prototype.md)).
- **10 depth probe 2026-09-23** -- 3 doors = 4; 2 doors is the short dock (depth 25 mean, 42 max and still deepening, 28/40 drains waiting on a door); 1 door is unstable (depth to ~290).  The priced 2-door spec waits on 04.
- **10 CLAIMED 2026-09-22** (the user's suggestion: fewer doors) -- a short dock by door count, the one understaffing lever that leaves the crew derived (ADR-0004); `door_scarcity_axis` + `_toy_doors` / `_probe_door_depth` / `door_scarcity` built and toy-verified; the fifo depth probe launches after 03's probe, the priced spec waits on its depth or on 04 ([10](issues/10-a-short-dock-by-door-count.md)).
- Assumptions stated and accepted: both stock modes run as in the campaign; one ranking tool
  takes its metric from the spec and ranks both the era run and the fill trial; the probe's
  trace is a declared artifact, not log lines.

## Fog

- Whether the pool-free adapter's order equivalence holds for the selector families at all:
  the ladder proved it for extremal-D, where the taken SET is the k cheapest bins whatever the
  unit order; `rank_cartlabor`'s aisle balancing and `rank_minlabor`'s heap choose different
  sets. Ticket 03 answers it before ticket 04 commits to a shape.
- Why `gforecast` strands more fulfillment lines unservable than `fifo` (a yard/lead timing
  question the agent could not resolve); it bears on the caveat ticket 01 writes.
- The replica's memory: 16 workers at ~4 GB already hold 64 GB of 128; a helper that
  replicates the whole sim is unaffordable, one that holds only the frozen tier and the cost
  model may not be. Ticket 02 measures it.
- Whether a fill's pick stage at 40k reaches the churn the diagnosis says the site lacks, or
  whether the fill trial at 40k is again a placement-rule question wearing an unloading name.

## Out of scope

- Re-ranking phase 1, and phase 3's spec (it confirms phase 1's pairs alone, per Q14).
- Changing the campaign catalogue's demand shape (Q13 option (c)): the fill trial answers the
  placement question without it, and the sampler flattening is department-calibration fog.
- The ~6 min per-cell parent setup and cross-cell asset reuse (`worker-pool` map fog).
