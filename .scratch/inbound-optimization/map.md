# Inbound optimization

Label: wayfinder:map

## Destination

Inbound optimization landed on `develop`: heterogeneous seeded leads, a standing yard whose
finite doors bind, the split yard-/dock-priority seams filled by space-aware policy arms
(myopic + standing-demand forecasting) under the yard-overage fee proxy, a behavior-neutral
caching contract at declared freeze points — all flag-off byte-identical — and the phased funnel
(inbound-off top-k selection → top-k × inbound policies) decided and ready, so the campaign
"does space-aware inbound beat FIFO, and at what fee cost" can run.

## Notes

- **Execution override: ON** (same as inbound-groundwork). Once a ticket's governing decisions
  close, implementation graduates from fog into `task` tickets on this map.
- **Byte-identical discipline holds** (CLAUDE.md §2): the standing yard / real doors are
  flag-gated; flag-off must remain byte-identical with v1's drain-everything `release()`.
- **Charter — settled during charting (2026-08-27), binding on every ticket:**
  - This effort starts at **arrival**: loading (FIFO next-fit) and dispatch are untouched.
  - **Yard** is the canonical term (glossary updated; *parking lot* retired). The two split
    decisions are **yard priority** (freed door ← best standing trailer) and **dock priority**
    (crew ← best staged trailer); knobs `INBOUND_YARD_POLICY` / `INBOUND_DOCK_POLICY`.
  - **No deferral anywhere.** The dock's information horizon is the yard (in-transit trailers
    are invisible to policies); a freed door is always filled, the crew never idles. The
    timeliness-vs-space tradeoff is embodied purely in ORDERING; the registries carry
    ordering functions, pure keys the degenerate case (amended 2026-08-29 by the objective
    resolution, 10).
  - **Doors become real**: at most `doors` trailers staged; a trailer holds its door across
    drains until fully unloaded; the yard-pull fires when a door frees.
  - **Decisions are drain-quantized; data is event-stamped.** The frozen-`ctx` purity contract
    stands; the space timeline carries absolute-clock stamps so a later event-driven cadence
    is a cadence change, not a data redesign.
  - **Leads**: per-trailer, drawn from a seeded distribution (minutes-authored); arrivals
    enter the yard ordered by arrival stamp, `seq` as tiebreak. No batch denomination.
    (Resolved 2026-08-29 by
    [Choose the lead distribution](issues/02-choose-the-lead-distribution.md): ONE family,
    lognormal — median `INBOUND_LEAD_MINUTES` × spread `INBOUND_LEAD_SPREAD`, σ=0 the
    no-RNG byte-identical degenerate; seq-keyed stateless draws derived from `SEED_WORLD`,
    no new seed knob; spread > 0 requires the standing yard, loudly.)
  - **The objective — resolved 2026-08-29 by
    [Define the inbound objective](issues/10-define-the-inbound-objective.md):** yard/dock
    ordering minimizes EXPECTED FUTURE WORK — the put + pick hours the drain's placements
    will generate (cost-model weights, no new knobs; unload hours are order-invariant and
    drop out). The output is an UNLOAD PLAN with set-composition semantics: the order is a
    priority over which loads meet this drain's bin pool, the clocks cut it into the served
    set, and the arm's own placement machinery assigns seats; the gain evaluator is
    FAITHFUL-TO-ARM. Placement-quality-vs-space scoring is dropped (verified vacuous — bins
    rank SKU-agnostically); on-shelf availability is a REPORTED missed-share axis, never a
    target or selection metric; JIT is emergent, no timing mechanism. Staging still anchors
    to IMMEDIATELY AVAILABLE bins; predicted clears are the next drain's pool gain, untimed
    by design. Forecast sources: STANDING DEMAND for WMS-realistic arms; the FUTURESIGHT
    WINDOW family (w script batches ahead, w=∞ the oracle) is a declared-unlawful
    upper-bound reference.
  - **Fee proxy**: per-trailer overage = max(0, yard_days − threshold), threshold a knob;
    a reported span-derived metric, never converted to dollars, never mixed into labor —
    and never blended with gain into one score: hours and days meet only through the
    urgency GATE (amended 2026-08-29 by the arm roster, 05).
  - **Caching**: staleness is contractual at declared freeze points; within the contract,
    caches are pure memoization keyed by version stamps, provably behavior-neutral
    (cached ≡ recomputed, cross-checked). Trailer loads are NOT cell-precomputable:
    reorders are pick-rollover-bound, so everything downstream of picks is arm-local.
  - **Resume**: no trailer checkpoint format; inbound-on runs resume only at boundaries with
    no standing inbound state, one uniform grain across the operation — refusal-until-clean
    IS the byte-identity guarantee.
  - **Evaluation is a funnel**, not a product: phase 1 inbound-off (matches historical runs)
    selects the top-k placement/scheduling arms; phase 2 sweeps top-k × inbound policies.
- Memories every session should load: `inbound-pipeline-wayfinder-decisions`,
  `putaway-seams-for-inbound`, `receiving-is-its-own-crew`, `one-clock-one-speed-one-config`,
  `config-knob-has-five-seams` (all in `context/memory/store/`).
- Skills: `grilling` + `domain-modeling` on every HITL ticket; `codebase-design` on
  seam/mechanics tickets; `prototype` on the evaluator ticket.
- Prior art: this effort starts from the CLOSED inbound-groundwork map
  (`../inbound-groundwork/map.md`) — its Out-of-scope list is this map's inheritance. Key
  anchors: `Inbound/transit.py` (`release()` is the v1 drain-everything to replace),
  `Inbound/priorities.py` (the registry contract to split), `_emptied_at` (the space
  timeline's substrate), the six-phase `check_reorders` order (pinned by
  `Tests/unit/test_reorder_phases.py`).
- Root `CONTEXT.md` already carries the resolved terms (Yard, Yard/Dock priority); code
  identifiers (`_lot`, `lot_depth`, PARKING LOT docstrings) follow at build time.
- Tracker conventions: `docs/agents/issue-tracker.md` (Wayfinding operations).

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Design the standing-dock mechanics](issues/01-design-the-standing-dock-mechanics.md):
  trailer-held remainders behind `INBOUND_STANDING_YARD` (a `YardTransit` subclass; v1 code
  untouched); plans-at-arrival, deferred-until-unload, yard-arrival stamps; `_receive` owns
  every door/crew decision over drain-frozen rankings; door-team split crew with a 'merged'
  lockstep bridge; canonical handoff makes allocation labor-only; additive yard/dock
  registries; own unload-cost coefficients (by-reference defaults); objective = total
  production hours (unload + put + pick), the greedy-departments-vs-global contrast.
- [Design the space timeline](issues/03-design-the-space-timeline.md): two-tier `SpaceView`
  on `ctx.space` — `_index`-snapshot empties (reclaim-harvested absolute stamps) plus
  UNTIMED predicted clears projected from one batch of released demand by the sim's own
  extracted drain rule; no inferred timing anywhere; per-event-class version counters
  (`demand_v`/`reclaim_v`/`fill_v`, equality-only); `Inbound/space.py` attached to the
  manager, always-on with the flag; the staging decision consumes empties only — and the
  objective question it exposed became
  [Define the inbound objective](issues/10-define-the-inbound-objective.md).
- [Build the standing-yard mechanics](issues/09-build-the-standing-yard-mechanics.md):
  BUILT, commit `64b2d31` — everything ticket 01 decided is code; all four byte-identity
  layers proven on the run DB (merged byte-identical, split labor-stamps-only, capped
  levels relabel with flows conserved); yard/dock registries live, seeded 'fifo';
  `YardTransit.stamps` holds the raw (seq, arrived, staged, emptied) tuples for the
  yard-metrics ticket; CLI flags/run-spec recording deferred to the first sweep, per the
  family precedent.
- [Build the space timeline](issues/11-build-the-space-timeline.md): BUILT, commit
  `72bbffb` — everything ticket 03 decided is code: `Inbound/space.py`
  (`SpaceTimeline`/`SpaceView`) always on with the standing yard, four touchpoints live,
  `ctx.space` frozen once per drain; the drain rule extracted as
  `Workload_Builder.drain_sku` and INJECTED by the driver (the `inbound -> wh_picking`
  forbid — the one deviation from the ticket's letter); every neutrality obligation is a
  passing test (lockstep with timeline ON, purity, drain-rule equivalence, the AST-guard
  replacement); the eviction-not-versioned gap is flagged as a comment on
  [Draw the cache-sharing boundary](issues/06-draw-the-cache-sharing-boundary.md).
- [Define the inbound objective](issues/10-define-the-inbound-objective.md): the objective
  is EXPECTED FUTURE WORK — the put + pick hours a drain's placements will generate
  (availability demoted to a reported missed-share axis; placement-quality scoring dropped
  as verified-vacuous; JIT declared emergent); the output is an UNLOAD PLAN with
  set-composition semantics (ordering-function registries, pure keys degenerate — the
  within-drain seats belong to the arm's own pool, so the lever is who meets this drain's
  pool vs the next); the gain evaluator is faithful-to-arm; the FUTURESIGHT WINDOW family
  (w=∞ absorbs the oracle) is a declared-unlawful reference with settled plumbing; regime
  acceptance criteria for the leads ticket (yard contention + binding cuts under FIFO,
  rollover off).
- [Generalize the ordering seam](issues/12-generalize-the-ordering-seam.md): BUILT, commit
  `7e26a4c` — a yard/dock entry may be an `@ordering` function `(candidates, ctx) ->
  ordered list` (tag probed getattr-style, the `STANDING` idiom); `bounded_order` resolves
  both kinds, so `transit.py` and the manager stayed untouched — kind-blind by
  construction; the bound composes bound-first, one entry call per drain; a
  non-permutation proposal raises loudly; the seeded fifo key path kept its exact former
  body, pinned by seam tests plus a registry end-to-end where a reversing entry provably
  differs.
- [Prototype the load-score evaluator](issues/04-prototype-the-load-score-evaluator.md):
  the ordering stops changing at CONTENTION-AWARE SET CONSUMPTION, not pool machinery —
  a k-cheapest merge with priority pairing reproduced the full `_RankedAssignPool` plan
  in 28/29 comparisons (the residue is the co-occurrence term moving near-ties;
  membership never moved), while dropping within-load contention flips the argmax
  routinely; cost is O(T²·(U log B + A)), 19–156 ms per drain at realistic scale — arm-run
  overhead seconds, caching stakes MODEST (posted on 06); evaluator contract recommended
  (driver-injected arm bundle, two-tier gain, no new knobs) and graduated as
  [Build the gain evaluator and the gain-plan arms](issues/14-build-the-gain-evaluator-and-arms.md),
  blocked by the arm roster (05).
- [Name the policy arms and their knobs](issues/05-name-the-policy-arms.md): SIX arms,
  each setting both registry knobs to one name — `fifo` (baseline AND the
  department-greedy fee pole: with one shared threshold, fee-greedy ordering IS
  arrival order), `lifo` (adversarial control), `gain_myopic`, `gain_forecast`,
  `gain_gated` (the FIFO urgency gate over the plan — the two-separate-scores rule:
  hours and days NEVER blend into one scalar, the gate is the only composition),
  `futuresight` (unlawful reference); knobs `INBOUND_FEE_THRESHOLD_DAYS`,
  `INBOUND_URGENCY_HORIZON_DAYS`, `INBOUND_FUTURESIGHT_BATCHES` (`'all'` = oracle); no
  weight grids exist — the swept scalars are H and w, their grids folded into the
  funnel (08); bound stays outside the roster.
- [Choose the lead distribution](issues/02-choose-the-lead-distribution.md): ONE family,
  no selector — lognormal, median `INBOUND_LEAD_MINUTES` (renamed now, window cheap) ×
  dimensionless `INBOUND_LEAD_SPREAD`, σ=0 constructs no RNG and is byte-identical by
  construction; draws are seq-keyed stateless (`SeedSequence([SEED_WORLD, TAG, seq])` —
  common random numbers across arms, nothing to pickle or resume), seed derived from
  `SEED_WORLD`, no new knob; spread > 0 without the standing yard (or with a zero median)
  raises loudly; arrival integration already built (`YardTransit`'s `(arrived_s, seq)`
  sort); machinery only — defaults inert, pilot values (first probe: median ≈ one working
  day, σ ≈ 0.7) belong to the funnel (08); build graduated as
  [Build the lead distribution](issues/15-build-the-lead-distribution.md).
- [Draw the cache-sharing boundary](issues/06-draw-the-cache-sharing-boundary.md):
  legality-first — 04's modest stakes earn NO cache machinery; the table (computation ×
  grain × key × cross-check) binds the builds verbatim: gain work arm-local ONLY, the
  futuresight feed cell-shared by inheritance (no artifact, read-ahead on the in-memory
  script), freeze inputs legal-keyed-not-built (the projection's `demand_v` key makes
  recompute the keyed behavior); the vector versions the free index + demand stream, never
  bin quantities — a quantity-reading, demand-blind computation reopens 03; the
  `requeue_bin` eviction FOLDS into `reclaim_v` ("+1 per bin returned to the free index",
  graduated as [Fold the eviction into reclaim_v](issues/16-fold-the-eviction-into-reclaim-v.md));
  two-tier cross-checks (equivalence + sabotage with 14, paired-run byte-identity for any
  future cache); spawn rule: cell-shared = file re-opened per worker, arm-local = rebuilt
  in-worker, cache knobs ride all five seams.
- [Build the gain evaluator and the gain-plan arms](issues/14-build-the-gain-evaluator-and-arms.md):
  BUILT — `Inbound/gain.py` (bundle + evaluator + 10's greedy) with the three
  `@ordering` entries in both registries, `lifo` seeded as a pure key, `ctx.gain`
  delivered at freeze, `_gain_bundle_for` in the driver (tmin/tmax k-cheapest merge;
  rank_popularity/rank_random pool-over-copies, rank_random by no-RNG expectation;
  every other family and zoning refuse loudly), the two days-knobs on `inbound_spec()`
  with 0.0 surviving; ONE recorded deviation: the deferral pool prices leftovers
  leave-one-out over the other candidates' takes — the prototype's current-pool
  reading makes `gain_myopic` a fifo in disguise, the LOO form makes its signal
  contention; 06's Tier-1 equivalence + sabotage and every neutrality obligation are
  passing tests (unit tier 1413 green); pool-family drain cost flagged to the funnel.
- [Build the futuresight window feed](issues/13-build-the-futuresight-window-feed.md):
  BUILT, commit `dd44d8a` — the window slot rides `inject_demand`/`freeze` on the
  `demand_v` event (no fourth counter, projection-blind, `()` legal at script end), the
  driver slices copies via `_futuresight_window` (clamped at n_batches) only when the arm
  is named; the `futuresight` entry is `gain_forecast` with PRICING swapped to realized
  window demand (absent = put-only; per-event draw, visits capped at the event count —
  the cap makes w=∞ the honest oracle), machinery faithful-to-arm — the one designed
  decision, from 10's "edge = sampling-noise knowledge"; refusals at all three layers
  incl. a new spec guard: non-fifo yard/dock policies without the standing yard now
  refuse loudly (the fake-arm hole a reviewer found); O(n²) 'all'-window cost flagged to
  the funnel (t_reord reads inflated for this arm).
- [Define the yard metrics](issues/07-define-the-yard-metrics.md): the fee accrues over the
  DETENTION span (arrived→emptied; glossary gained yard wait / door span / detention span /
  overage / binding cut / yard contention / door utilization); two new sim_db tables —
  `yard_trailers` (raw stamps + `status`, censored standing trailers flushed at run end so
  `lifo` reads concentrated, not clipped) and `yard_drains` (contention + binding-cut
  LEVELs from `freeze_ctx`) — raw stamps only, spans/overage derived at analysis with the
  run's recorded threshold (the fee axis is re-reportable under a new threshold without
  re-simulating); `INBOUND_FEE_THRESHOLD_DAYS` defaults 2.0 placeholder, calibration rides
  the funnel; a new `yard` leaf figure family (five `'yard'`-gated Quantities + a
  no-direction scorecard; availability lands in `throughput`, ungated — inbound's FIRST
  figure coverage); build graduated as
  [Build the yard metrics](issues/17-build-the-yard-metrics.md).
- [Build the lead distribution](issues/15-build-the-lead-distribution.md): BUILT, commit
  `50149dc` — everything 02 resolved is code: `INBOUND_LEAD_MINUTES` (renamed) +
  `INBOUND_LEAD_SPREAD`, `lead_sigma`/`lead_seed` on `inbound_spec()` (seed = `seed_world()`,
  no knob of its own), and `TrailerTransit.lead_for(seq)` drawing
  `median · exp(σ · Z)` from `SeedSequence([seed, 0x1EAD, seq])` at trailer creation — TAG
  literal `0x1EAD`, never derived. Spread zero constructs NO generator and returns the
  median as the same float, proven three ways (exact-equality seam, a booby-trapped
  `default_rng` never reached, and a drain-by-drain manager lockstep whose real target is a
  seed leaking entropy while the spread is off). One refusal beyond the two the ticket
  named: both guards sit ABOVE the trailer-type early return, or a spread with no trailer
  type is discarded by a silent `None`. Gradient confirmed — eight trailers at one epoch
  with σ=0.7 land `[1,2,6,5,0,4,7,3]`, `[0..7]` at σ=0. CLI/run-spec deferred, values
  unpicked (defaults inert); the derived arch layer is owed to the maintainer, as at 09/13.
- [Design the phased funnel](issues/08-design-the-phased-funnel.md): phase 1 is a FRESH,
  inbound-off, PURE SELECTOR run (no phase-1 number is ever published — which is what makes
  a between-phase build legal and confines the un-re-derivable TAG to one phase); the
  archive cannot price the objective at all (five boundaries, and put-away was untimed
  before the one-clock refactor). The selectable unit is a restock RULE, not an arm
  (`CHANNEL_RESTOCKS` filters on `restock`, so each pick costs 2 arms); k = 5 rules PLUS a
  mandatory `fifo` — which is both the analysis baseline (`_baseline_entry` silently falls
  back to `strategies[0]` without it) and the order-blind negative control — ranked per
  CHANNEL on total production hours SUMMED across profiles, extensions capped at 3 families
  so `(b)` stays costable, recorded as a post-analysis artifact carrying ALL 17 rules
  ranked. Phase 2 is a TEN-CELL matrix on a new FIFTH `Cell` field (six runs would fire no
  cross-cell writer at all): `fifo` the reference, `lifo`, three gain arms with H at 0.25/
  0.5/1.0 × the calibrated threshold, `futuresight` at one finite w and `'all'`, plus an
  inbound-off anchor; scheduler fixed at `lpt`, series depth, `yard_overage_days` granted a
  `headline` slot (never blended). A throwaway pilot gates everything (both of 10's criteria
  still unproven — 15's demo shows reordering, a precondition, not contention) with a
  DECLARED STOP if contention will not bind; the win rule is pre-registered (moving-block CI
  excluding zero, missed share not degraded, fee reported beside). 13's O(n²) verified and
  CORRECTED: the copies are the minor term, the real multiplier is DRAINS PER BATCH, with a
  `demand_v` memo as the legal fix. Graduated as
  [18](issues/18-build-the-run-shape-layer.md) (cell axis + seams 3–4 + selection artifact,
  bundled on one schema event), [19](issues/19-build-total-production-hours.md) (the metric
  does not exist — put hours have never been read from `work_events`) and
  [20](issues/20-extend-the-gain-bundles.md) (gated on phase 1's ranking).
- [Fold the eviction into reclaim_v](issues/16-fold-the-eviction-into-reclaim-v.md): BUILT,
  commit `d266c07` — one `is None`-guarded `evict` call beside `requeue_bin`'s `_index_add`,
  no fourth counter. The counters now partition by WHAT CHANGED rather than by which
  function ran (`_index` grows through exactly two doors, both bumping `reclaim_v`), which
  is what makes the vector a complete description of the free index instead of a log of
  call sites. `evict` writes no stamp and expires none — verified structurally, not
  assumed: bin occupancy has a SINGLE site (`Inventory_Management.py:919`, the fill hook's
  own line), so no stale stamp can survive to be popped. This is the one touchpoint
  reachable with the standing yard OFF (the reloader gates on `reslot_frac` alone), so its
  silence is pinned against an unattached manager rather than argued. Three pins, each
  proven to fail by sabotaging the hook — exact count, an eviction ALONE moving the vector
  (the gap as its own failure), and attached ≡ unattached; all three override BOTH
  degenerate reloader defaults, the known `move_limit_pct` and a second one found here
  (`ref_size='extra_large'` does not exist in the fixture warehouse, flooring the cap a
  second way). Unit tier 1451 green; one PRE-EXISTING, unrelated gate failure surfaced and
  spun off (`test_the_dead_site_is_still_dead` substring-matches a docstring citation).

- [Build the yard metrics](issues/17-build-the-yard-metrics.md): BUILT — everything 07
  decided is code across TWO schema events (sim_db `be2a593727be`, run-tree
  `51f99901f03c`): `yard_trailers`/`yard_drains` raw-stamps-only, semantics + a `yard`
  capability, named queries and negotiated loaders, the per-drain levels frozen BEFORE the
  door fill, and a run-end censored flush with its OWN writer because the final checkpoint
  block does not fire at a batch count divisible by the cadence — exactly where an
  adversarial ordering's overage lives. The `yard` family (four evaluations, all proven to
  render on a real standing-yard run) plus `throughput.missed`; five yard Quantities and
  two availability ones; re-report under a second threshold asserted with no re-simulation.
  Its quantities were the FIRST to name a capability, which fired the era gate's
  written-to-fail test and pulled in the RUNTIME half it specified: `ctx.capabilities()`
  (intersected across arms), `EraUnmet` distinct from `Denied` because the two prescribe
  different actions, an `[era]` run summary, and `GATED_CONSUMERS` as a second sweep
  category rather than an exemption inside the first. `yard_overage_days`' headline slot is
  deferred to 19 — it needs a steady-state scalar that does not exist, and adding it now
  would put an empty panel on every archived publish.

- [Build total production hours](issues/19-build-total-production-hours.md): BUILT — the
  objective is reportable. ONE recorded deviation, forced by the era gate: the ticket
  specified the unload leg as `batch_stats.recv_seconds`, which is NOT in the guaranteed
  sim-DB surface and POSTDATES `work_events`, so no capability honestly covers it and an
  unguarded read fills every pre-dock vintage's leg with a plausible zero — BOTH inbound
  legs therefore read `work_events`, and the licence for that (the two surfaces agree
  seconds-for-seconds) is now a test on a real run rather than a claim. `work` is the
  fourth metric-source kind (SQL fold + `_wdf`), EMPTY-never-zeros when the table has no
  rows, and IS in `PAIRED_KINDS` because 08's decision rule is a moving-block CI over
  paired batches; `_metric_series` became a kind dispatch that RAISES, ending the "batch,
  else the task frame" fallthrough. `_build_series` opened to two more frames, paying both
  owed headline slots — `yard_overage_total` is a run TOTAL, not a steady-state mean
  (trailers have no batch index), and `_METRIC_GROUPS` gained a PAIRABLE flag for it. The
  new `quantities_optional=` seam lets the headline draw a quantity it survives without,
  paid for by `_drawable` dropping an all-NaN group rather than printing a blank panel that
  reads as "measured, and it was nothing". Plus `labor.production_legs`, the stacked
  three-leg decomposition. No schema event (both ids held; both fingerprints re-recorded
  through the pipeline). Flagged to 18: rank on summed `ss_prod_total`, NEVER the
  cross-profile CSV, which is normalized to ratios.

- [Build the run-shape layer](issues/18-build-the-run-shape-layer.md): BUILT — the funnel is
  runnable. ONE schema event (`5c9bc35db55b`, the added `restock_selection_json`) carries all
  three parts. The fifth `Cell` field is a `CONFIG['global']` inbound record, defaulted so every
  four-argument construction survives, and its suffix sits BEFORE the scheduler's because
  `_scheduler_of` parses the LAST name token. Four collapses refused rather than documented —
  no suffix, duplicate suffix, an unknown key, and the one the ticket did not name: an entry
  omitting a key another entry sets, which would let a cell inherit the previous cell's policy
  under its own name (CONFIG is never reset between cells). That rule is why the phase-2 spec
  carries the whole ARRIVAL REGIME and not just the policies. `is_reference` gained
  `inbound is None` and a swept axis must declare `reference`; `PHASE2_ARMS` is refused while
  None; and 08's `fifo` rider is enforced twice — the matrix refuses a fifo-less subset at
  minute zero, and `_baseline_entry` now RAISES instead of falling back to `strategies[0]`.
  Seams 3–4 derive from ONE list (`sim_config.INBOUND_KEYS`): 17 flags each defaulting FROM
  CONFIG, the whole family in the run spec with the imported lead TAG beside it, and both
  restore sites. One defect found in the wiring: 07's derive-late fee report could never have
  read the recorded threshold — `_sim_result_from_meta` copies five keys, and CONFIG is not a
  channel to a SPAWNED analysis worker — so the threshold is now stamped onto the pickled job.
  The selector ranks RULES (via `STRATEGY_BY_KEY`, never by parsing arm keys) on hours summed
  over the identical leaf set, disqualifies any rule with a missing reading, records all 17,
  and resolves its own output path HEAD-first. **One blocker surfaced and routed, not fixed
  here:** a gain cell builds a bundle for EVERY arm in its set (the gate is on the POLICY, not
  the arm), and the mandatory `fifo` rider has no faithful bundle — so all five phase-2 gain
  cells would refuse it at worker startup. Verified directly and pinned; it sits OUTSIDE 08's
  cap of three, is knowable before phase 1 runs, and graduated as
  [Give the fifo rider a faithful gain bundle](issues/21-give-fifo-a-faithful-gain-bundle.md)
  rather than sitting unreachable inside [20](issues/20-extend-the-gain-bundles.md), which
  stays genuinely gated on the ranking.

- [Give the fifo rider a faithful gain bundle](issues/21-give-fifo-a-faithful-gain-bundle.md):
  BUILT — phase 2's gain cells accept the rider. The faithfulness question resolves FOR an
  evaluator and more strongly than the ticket hoped: a uniform draw's expectation is EXACT, not
  a defensible approximation, because sequential draws without replacement leave every unit's
  bin marginally uniform over the frozen tier and `_pair_cost` is AFFINE in a bin's (x, y,
  height multiplier). `rank_random` does NOT carry the answer — its adapter's whole argument is
  "call the arm's own builder", and `fifo` has no pool to call — so this is a THIRD adapter
  (`_place_uniform`), checked against brute-force enumeration, with the height moment the mean
  OF the steps and never the step at the mean. Exact pricing is what empties a take's identity:
  consumption becomes a SEAT COUNT and capacity is the arm's only lever, which forced the one
  shared-machinery change — a per-round block allocator, WRAPPING not truncating (truncating
  was tried: it hands the win to whoever swept LAST on an oversubscribed tier). THE FINDING:
  with seats for everyone `gain_myopic` over `fifo` gains exactly zero and plans arrival order —
  a property of the arm, which is what makes the rider a clean order-blind control rather than
  an inert one; forecast/gated/futuresight all still move it. Cheapest adapter in the suite.
  Owed: the derived arch layer (as at 09/13/15); no schema event, fingerprint refreshed.

- [Run the pilot gate](issues/22-run-the-pilot-gate.md): **GO, with three conditions** — both of
  10's criteria hold in a stable non-saturated regime at `--recv-crew-size 4
  --recv-day-seconds 43200`, doors 4, lead 480 min σ 0.7, published depth (committed as the
  `inbound_pilot` spec). Three findings qualify it. (i) A receiving WHISTLE is a precondition,
  not a preference: with no `--recv-day-seconds` the crew's day is unbounded, the yard drains
  completely every drain, and neither criterion can fire *by construction*. (ii) The FIRST
  attempt — a physically plausible dock, 2 receivers on an 8-hour day — passed both criteria
  emphatically (70/75, 71/75) while starving the warehouse to a **49.6% missed share**, fill
  85%→35% and yard depth running away to 2,208; the cause is a CLOCK MISMATCH between
  departments, `_recv_deadline` granting one day-REMAINDER per BATCH while a store batch spans
  **19 working days** (effective capacity is `crew × day / 2` per batch, measured 38–53% of
  nominal). (iii) Receiving demand spans **7.4× across the four leaves** against one global crew
  knob, so fulfillment binds hard (46–60 of 75) and store lightly (14–17) — the campaign is
  mostly a FULFILLMENT experiment — and the fee threshold wants 2–3 d there against 7–10 d in
  store, so `PHASE2_THRESHOLD_DAYS = 3.0` is a compromise and `gain_gated`'s H grid is a
  fulfillment-only result. Also settled: 10's criterion (a) has two readings that disagree and
  the STRICT one (`free_doors == 0`) is right; 08's "unmeasured multiplier" on the futuresight
  window is **1.00 drains/batch** (structural), so `'all'` is affordable and 06's `demand_v`
  memo is not a prerequisite. Not done, and named: the futuresight wall-clock bench.

## Not yet specified

- **The builds** — every implementation graduates here once its governing decisions close.
  ONE fog item remains: the RESUME-GUARD EXTENSION TO YARD STATE. Nothing inspects inbound
  state on resume today — `_plan_strategy_start` takes `roll_over` and `receiving`, not
  `inbound`. The standing yard is covered only BY ACCIDENT, because `inbound_spec` refuses a
  standing yard without a receiving crew, so `recv_crew_spec()` is never None there; but a
  v1 trailer run with a trailer type and NO receiving crew has worker-local trailers in no
  checkpoint and is not refused under `--resume-granularity batch`. Out of scope stays out
  of scope (no trailer checkpoint format) — this is a refusal, not a format.
  (Done: the standing-yard mechanics — 09 —, the space-timeline build — 11 —, the
  ordering-seam generalization — 12 —, the gain evaluator + gain-plan arms — 14 —, the
  futuresight window feed + entry — 13 —, the lead distribution — 15 —, the eviction
  fold — 16 —, the yard metrics — 17 —, the total-production-hours build — 19 —, the
  run-shape layer — 18 —, which paid the seams 3–4 debt every knob deferred to "the first
  sweep", and the fifo rider's gain bundle — 21 —, which was the last build standing between
  the funnel and the pilot. ONE ticket remains and it is NOT on the frontier:
  [Extend the gain bundles](issues/20-extend-the-gain-bundles.md) stays gated on phase 1,
  since which OTHER families need extending is phase 1's output. So the frontier is empty by
  design: the next act is the pilot, not a ticket.)
- **Timed / deeper lookahead views** — predicted-clear timing and LAWFUL demand beyond the
  released batch ("how far ahead can availability reliably be planned"), a future inbound
  view-arm family; parked by the space-timeline resolution (03), which shipped predictions
  untimed. (The unlawful version — reading the future script — is no longer fog: it is the
  futuresight window reference family, decided by the objective resolution, 10.)
- **The funnel campaign** — no longer a design gap OR a build gap: 08 specified it end to end
  (pilot → phase 1 → selection → phase 2 → publish) and 18 landed the last build in the way,
  so what remains is EXECUTION and it is UNBLOCKED. `--spec inbound_select` runs phase 1,
  `run_restock_selection` writes the hand-off, `PHASE2_ARMS` takes its answer, and
  `--spec inbound_policies` runs the ten-cell matrix (refusing to start until the arm set is
  set), plus phase 2's command line carrying `PHASE2_RECV_CREW_SIZE` /
  `PHASE2_RECV_DAY_SECONDS`, which have no cell axis and ARE the experimental condition.
  The pilot (22) has RUN and gated through: the regime that makes both acceptance criteria
  hold is known and committed. Two qualifications it attached, which the campaign must publish
  with rather than discover — the comparison is mostly a FULFILLMENT result (store binds 14–17
  of 75 drains against fulfillment's 46–60), and `gain_gated`'s H grid is fulfillment-only,
  because one global fee threshold cannot serve channels whose non-saturated bands sit 3.5×
  apart. **Whether phase 2 is worth running BEFORE the department-calibration effort lands is
  now an open call, not a settled yes**: the passing config still carries a 15.9–22.0%
  fulfillment missed share, so a campaign run there measures inbound ordering under a scarcity
  the staffing model never chose. Sizing, if it runs as-is: phase 1 is 136 work units, phase 2
  is 480; measured at published depth one unit is ~1,230–1,320 s wall at ~5.5 GB peak RSS, so
  phase 2 is ~13 h of simulation and ~1.1 TB — archive-as-you-go is mandatory, workers are
  RAM-bound before CPU-bound, and the grids are the trimming lever.

## Out of scope

- **Deferral / hold capability** — ruled out by the information horizon (the dock cannot see
  beyond the yard, so a free door is always worth filling); returns only if trailers become
  visible in transit, which would be a different model.
- **Event-driven decision cadence** — decisions stay drain-quantized this effort; the
  event-stamped timeline keeps the door open, but the cadence change is not this map's work.
- **A trailer checkpoint format for mid-flight resume** — declined again (uniform-grain
  refusal-until-clean is the chosen mechanism).
- **Loading/dispatch optimization at the ordering site** — upstream of arrival stays as v1
  built it (FIFO next-fit, dispatch-when-passed-by).
- **The full multiplicative sweep** (34 × inbound policies) — the funnel replaces it by
  design.
- **Cross-department capacity calibration — baseline staffing and interaction effects.**
  Surfaced by [Run the pilot gate](issues/22-run-the-pilot-gate.md) and ruled out here the same
  day. The pilot found that picking, put-away and receiving have never been sized against one
  another: `_recv_deadline` grants the dock one working-day REMAINDER per BATCH while a store
  batch spans 19 working days (fulfillment 2.6), so a plausible dock runs at a ~5% duty cycle
  against store picking and starves the warehouse to a 49.6% missed share; and the four leaves'
  receiving loads span 7.4× against ONE global crew knob. Ruled out of scope rather than
  ticketed here for two reasons: it reaches every department, not just inbound — so it is not
  reachable from this map's destination, which stops at "the campaign can run" — and its
  deliverable is a different artifact, config-file records of expected throughput and baseline
  staffing per department. This map's contribution is the measurements above; the successor
  effort owns the model. **Successor: `.scratch/department-calibration/map.md`** (to be
  charted). The inbound campaign is runnable without it — the pilot proved a passing regime —
  but runs under a scarcity that effort would let someone CHOOSE rather than inherit.
