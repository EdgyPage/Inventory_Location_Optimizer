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
- [Verify the derived receiving crew under arrivals](issues/23-verify-the-derived-receiving-crew.md):
  **OUT OF BAND -- fulfillment, the supply clause, every arm; the campaign holds.** One 40-day era
  run with the yard on and no crew flag (`comparison_20260910_173151`): every crew of both leaves
  in band (recv 0.173/0.181, 0.694/0.665), the dock never stood a unit overnight, store passes
  every clause -- and fulfillment's supply level reads **0.148 vs 0.025**, trending up. Not the
  receiving crew: the coverage record stamps `lead_days` from the catalogue's `lead_time_mean`
  (0.0), while the trailer pipeline realizes a **1.78-day** order-to-shelf lead (Little's law,
  48,598 units in transit against 27,294 ordered/day; the reference's in-transit is 0). Graduated
  to department-calibration as
  [Declare the coverage against the inbound lead](../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md).
  Second reading, 22's declared stop: under the derived crew (22 receivers, site day) **the yard
  never binds** -- strict contention 0/40, binding cuts 0 (fulfillment), door utilization 15%,
  detention p50 0.18 d; doors 4 / lead 480 / spread 0.7 were pilot outputs under the retired
  per-batch grant. Graduated here as
  [Decide the contention regime under the derived crew](issues/25-decide-the-contention-regime-under-the-derived-crew.md)
  and [Re-verify the gate under the lead-aware record](issues/26-reverify-the-gate-under-the-lead-aware-record.md).
  Built: the standing-yard guard read only the declared crew key and refused every era launch
  (`inbound_spec(recv_crew_size=)` now takes the derived crew; the resume planner's `receiving=`
  had the same blindness); `inbound_pilot` carries its arrival regime as `PILOT_RUN_DEFAULTS`, so
  the gate re-runs as `--spec inbound_pilot --n-batches 40`.
- [Decide the contention regime under the derived crew](issues/25-decide-the-contention-regime-under-the-derived-crew.md):
  **the regime is the SITE'S OWN dock, and the leaf model cannot express it.** One dock, mixed
  trailers, one crew at the record's `rho_recv` (0.846 stamped on the reference pair), the
  lead law held at 480 / 0.7, a declared ten-receiver cap per trailer as physics (even splits,
  additive, `INBOUND_DOOR_TEAM`), receiver utilization REPORTED not re-derived, an acceptance
  band per site (contention on a quarter to a half of drains, detention p50 under the
  threshold, stable depth), the fee threshold owned by 26. Off the route: fewer doors (an
  uncapped team unloads at the crew's rate at any count; under the cap a leaf still cannot
  bind), a wider spread (costs stock), share-slicing, background trailers. Every slack-yard
  number was an artefact of running each channel's inbound alone with the site crew.
  Graduated: [Cap the door team](issues/28-cap-the-door-team.md) (task, frontier); the
  channel coupling at the dock is beyond this destination -- Out of scope, with the successor
  seed. The campaign holds behind it: 27 and 28 -> the site-dock effort -> 26 -> 24 -> phase 1.
- [Chain the supplier lead before the trailer](issues/27-chain-the-supplier-lead-before-the-trailer.md):
  **BUILT**, commit `8b6796b4` -- the flag-off batch countdown now sits in front of the trailer's loading, inside
  `TrailerTransit` (`_at_site`, ticked by `advance`, flushed in fire order before every load and
  at the top of both `release` bodies), `dispatched_s` stamped at the drain the order LOADS in
  so the two stages add; the census counts the site queue; department-calibration 37's interim
  refusal is deleted. Byte-identical at lead 0 drain by drain, against a pre-chain stand-in AND
  a digest pinned from `6eaf30fc`; the two-SKU acceptance runs through the real
  `check_reorders`. [Cap the door team](issues/28-cap-the-door-team.md) is now the frontier;
  26 waits on it and on the site-dock coupling.

- [Cap the door team](issues/28-cap-the-door-team.md): **BUILT** -- `INBOUND_DOOR_TEAM` (default
  None = uncapped) across all five seams, on `PILOT_RUN_DEFAULTS` at 10 and in every axis entry
  (cleared on `inb_off`, which the spec would otherwise refuse); the cap is read in BOTH allocation
  modes because it belongs to the trailer. **The ticket's acceptance example was wrong and was
  corrected, not built**: "10/10/2" is a GREEDY deal and contradicts 25's even splits and the
  ticket's own rule -- measured, 22 receivers over three doors deal 8/7/7 (cap inert) and over two
  10/10 with two idle, against 11/11 uncapped, so the cap binds only when the EVEN division would
  exceed it. The part with teeth was the reassignment: a freed team now SPREADS over the (1)-(2)-(3)
  targets up to each one's room, because the pre-cap step (3) extended a team unconditionally and
  would have seated twice the cap on one trailer. An idle pool for the cut workers was built and
  then REMOVED as provably unreachable (a non-empty pool means every team is exactly the cap, and a
  fresh door's room is exactly the cap). 25's decision 4 landed as two un-banded read-outs -- the
  DOCK CEILING (`cap x doors / crew`, on the scorecard and as a suffix on the audit's receiving row,
  printed whether or not it binds) and the RECEIVER BUSY SHARE -- both stamped onto `sim_result`,
  since CONFIG is not a channel to a spawned analysis worker. **Rendering on the real reference pair
  caught a defect the unit tests missed**: the busy share read 184% because `_arm_span_days` is
  CALENDAR time while a crew grant is WORK days (3x on an 8-hour day) -- now over distinct
  `work_day` values, and store reads 61% against the audit's independently-sourced 0.608,
  fulfillment 16% against 0.165. The ceiling also forced the scorecard's door count to prefer the
  RECORDED value over its resumed-arm-lower-bound derivation. Byte-identical with the cap off drain
  by drain in both modes, both halves of the rule proven to fail by sabotage; unit 2002 green, yard
  e2e 4 green, preflight re-proved the tree shape unchanged (no schema event). Owed: the derived
  arch layer, as at 09/13/15/21.

- [Re-verify the gate under the lead-aware record](issues/26-reverify-the-gate-under-the-lead-aware-record.md):
  **SPLIT -- the yard PASSES on the coupled dock and fulfillment's SUPPLY clause fails; the campaign
  holds.** One 40-day coupled era run on the reference pair (`comparison_20260912_055947`, clean:
  no Traceback, no dead arm, conservation OK on all eight leaves, `receiving_report` 0 FAILED over
  8 arms and 4 pairs). The pilot spec was made coupled FIRST (`PILOT_RUN_DEFAULTS` gained
  `couple_channels`, pinned against phase 2's own key), so the gate is one command, not a
  remembered flag. **The yard reading is the site-dock effort's payoff, measured:** one crew of 22
  over both channels where 23 fielded 22 on EACH leaf, and strict contention goes **0/40 per leaf
  -> 20-40% of window drains**, binding cuts 0-2 -> 11-12 of 20, detention p50 0.18 -> 1.15 d --
  non-saturated throughout (depth FALLS 18.0 -> 15.5 across the window, `recv_depth` max 0, 607+
  of 609 trailers cleared, crew binding at a 182% dock ceiling). 25's band holds. **The failure is
  a DIFFERENT one from 23's, which is the finding that matters:** 23 said "the lead is not in the
  coverage record"; the lead is in it now and BOTH leaves realize it (1.782 / 1.789 d against a
  stamped 1.766), so the explained level is 0.025 and explains none of fulfillment's 0.104. The
  run's own `fill.vs_transit` curve prices 0.1044 at a **~9.2-day** order-to-shelf lead, the yard's
  detention is already inside `in_transit`, and double-counting the whole yard reaches ~3 d (0.039)
  -- so no leg of this pipeline can produce it. Store is the control that rules out the coupling
  (same dock, same put pool, same lead, +0.005), and coupling moved fulfillment the RIGHT way
  (0.148 -> 0.104). Graduated to department-calibration as
  [Close the fulfillment fill-law gap](../department-calibration/issues/38-close-the-fulfillment-fill-law-gap.md).
  **`PHASE2_THRESHOLD_DAYS` is measured but NOT committed:** 3.0 is degenerate under one dock (no
  trailer past 1.837 d, so the fee is identically zero and `gain_gated`'s H grid derives from an
  unreachable number), the leaf model's 3.5x channel compromise no longer exists, and the knee
  measures at ~1.3 d -- the full sweep is recorded on the constant so re-fixing is a lookup, held
  only because the failing gate is not the regime the campaign runs. Successor:
  [Re-run the gate and fix the fee threshold](issues/29-rerun-the-gate-and-fix-the-threshold.md).

- [Re-run the gate and fix the fee threshold](issues/29-rerun-the-gate-and-fix-the-threshold.md):
  **the gate PASSES on both criteria and `PHASE2_THRESHOLD_DAYS` is committed at 0.40 — but 26's
  sweep was read in SITE days while the knob is consumed in CALENDAR days, a factor of exactly
  3.** Committing 26's ~1.3 d knee would have left the fee axis identically zero, the same
  vacuity 3.0 had and reported as `0.00` rather than as an error. Proven three ways: raw seconds
  (26's max span 52,902.9 s is 0.612 d at 86,400 and 1.837 d at 28,800 — 26's recorded figure to
  three decimals), whole-table correspondence (26's grid reproduces exactly with every threshold
  ×3), and both consumers agreeing on 86,400 (`frames._ydf` via `units.SECONDS_PER_DAY`, a
  calendar day by declaration; the urgency gate at `Inbound/gain.py:146`). The knee's LOCATION
  was right — 1.3 site days *is* 0.433 calendar days — only its label was wrong; the defect
  recurred because the sweep was taken by hand rather than through `_ydf`, which is precisely
  what `units.py` exists to prevent. No run was launched: `comparison_20260912_134002` already
  was the gate run. Supply in band on both leaves at a fifth of tolerance (12 arms, 0 failed).
  **The busier-yard prediction is half right and the other half matters:** trailers rose +5.4%
  as predicted, but the yard got EASIER — binding cuts 11-12/20 → 8-9/20, contention 4-8 → 3-7
  of 20, detention p50 0.389 → 0.365 d — because the derived crew grew 22 → 23 on the v3 line
  count and outpaced the arrivals. It still binds on 8-9 of 20 drains so the criterion passes,
  but with less headroom: one step from a yard that does not bind. `recv_depth` max 0 throughout.
  The knee is stable across regimes (26's run bends one notch right at 0.42 d), the H grid is
  un-degenerated to 0.10/0.20/0.40 d, and the "fulfillment-calibrated compromise" caveat is
  retired — one dock has one detention distribution. **Phase 1 is launchable.**

- [Re-size the funnel in site days](issues/24-resize-the-funnel-in-site-days.md): **phase 2 fits
  on one drive — 120 work units and ~169 GiB against the old 480 and 1.1 TB, 37.8 h of
  unit-seconds; phase 1 is 68 units, 12–17 h, ~47 GiB — and one build in the way was a DEFECT
  FIX, not tidiness.** Measured off the passing gate run (`comparison_20260912_134002`, the
  campaign's own regime): per-pair setup 3,633 s once, a coupled unit 1,134 s mean for BOTH
  leaves at 28.3 s per site day, peak RSS 3.2–4.1 GiB, ~1.4 GiB of disk. A coupled unit runs two
  leaves in the wall and at LESS RAM than one old leaf built (one warehouse for the site, not one
  per channel), and the old counts assumed two inventory pairs where the era's reference
  catalogue is one — those two together are the 4x and the 6x. **The phase-2 figure is a FLOOR:**
  the gate priced nothing (`yard_policy='fifo'`, one cell), so the eight gain cells' multiplier
  and the per-cell reshape are both unmeasured — graduated as
  [Measure what a gain cell actually costs](issues/31-measure-what-a-gain-cell-costs.md), which
  must land before phase 2 but not before phase 1. **The defect: phase 2 named its arrival regime
  only on its inbound AXIS, and a multi-cell run freezes its inventory before any cell starts.**
  Since department-calibration decision 11 that freeze reads the trailer's lead law and solves the
  line floor at it — so phase 2 would have frozen a warehouse stocked for transit 0 and run nine
  inbound-on cells against it, logging `0.000` that nothing compares with the cells'. The regime
  is now `INBOUND_ARRIVAL_REGIME`, declared once and carried at RUN level by all three campaign
  specs, which also makes `inb_off` a better control (same warehouse, same stock, no yard). The
  window is declared once too (`CAMPAIGN_DEPTH_DAYS` / `CAMPAIGN_WINDOW_DAYS`, site days, on
  `run_defaults` and behind `equilibrium_report --window campaign`), so the two phases cannot be
  typed to different depths — which matters because the staffing derivation reads the sampled
  SCRIPT. **Phase 1 now runs the arrival regime ON and stays UNCOUPLED** (`PHASE1_RUN_DEFAULTS`):
  the 2026-09-10 instruction to take `PILOT_RUN_DEFAULTS` could not be followed literally once 26
  put coupling in it, and phase 1 may not couple — `run_restock_selection.select` refuses a
  coupled root. **The calibration pin's rationale changed and got stronger:** there is no
  calibration record to go stale any more, but the funnel puts a legal BUILD between the phases by
  design, so a moved derivation leaves phase 2 executing phase 1's ranking against a different
  site. Two refusals — `validate_spec` for the shape, `workunits._check_campaign_pin` per pair for
  the match, over a rounded projection so a reporting change cannot refuse a campaign. The
  `inb_off` anchor's role is recorded on the ticket: the inbound-off pole INSIDE the coupled
  model, never a cross-phase check — and nothing in the campaign is one any more.

- [Pin the day divisor to one declaration](issues/30-pin-the-day-divisor.md): **HOISTED --
  `SECONDS_PER_DAY` is declared once, in `Warehouse/kernel/timeline.py`, beside the SITE day
  it is three times longer than; `units.py` re-exports it, `Inbound/gain.py` and the gate's
  own test import it.** The pin-test alternative was rejected on the ticket's reasoning, not
  its cost: it catches drift and does nothing about the INVISIBILITY that produced 29's
  defect, since neither file named the other. The kernel was already the right home -- it
  owned `TIME_UNIT`, `SECONDS_PER_HOUR` and, decisively, the OTHER day; only the VALUE moved,
  the CHOICE of which day a detention accrues in stays in `units.py` with its rationale. It is
  also the ONLY seam available: `{forbid: [inbound, evaluations]}` is correct and permanent.
  `settings.py`'s "one knob, two readers ... can never disagree about overdue" is now a test
  (section 4b) that hands the same span to BOTH readers through their real entry points
  rather than restating either predicate. **Two findings.** (i) The readers SPLIT the boundary
  instant -- the gate is `>=`, the fee is `> threshold` -- which is deliberately not fixed
  (changing the gate moves arm behaviour) and unreachable anyway, because the two never
  measure the same span at all: `frozen_at - arrived` at a drain versus `emptied - arrived`
  entire. Only the CONVERSION can agree; that asymmetry is now pinned rather than rediscovered.
  (ii) The span set must sit inside the 3x band or the test is VACUOUS -- 0.5 days is not
  sensitive to a site/calendar drift (0.5 x 3 is still under a 2.0 threshold) -- so the
  sabotage test asserts 1.0 and 1.5 specifically flip. The hours ratchet gained a twin for the
  day, and the gap it exposed matters: **the hours ratchet never scanned `Inbound/`**, the
  module the gate lives in, which is a structural reason the divisor drifted there. Strict
  no-op proven bit-exact (`24.0 * 3600.0` and `86400.0` both pack `40f5180000000000`) with
  `is`-identity across all three modules; unit 2393 and yard/receiving e2e 14 green, every
  other tier at its stashed baseline. No schema event. Owed: the derived arch layer, as at
  09/13/15/21/28.

- [Measure what a gain cell actually costs](issues/31-measure-what-a-gain-cell-costs.md):
  **the probe could not price the cell it was sent to price -- `fsight_wall` cannot run AT ALL
  under the coupled model, and neither can `fsight_w5`, so 2 of phase 2's 10 declared cells are
  DEAD rather than expensive.** `Inbound/site_space.py` refuses to compose two futuresight
  windows (deliberately, with the lift rule written beside it and a unit test pinning it), and
  every phase-2 cell couples; measured on a probe run, all four coupled units of the
  `fsight_wall` cell failed identically, and a direct call to `compose_site_view` proves the
  refusal keys on the window's PRESENCE, not its width. Nothing was wrong in `site_space.py`:
  the gap is that **nothing checks the campaign's declared axis against the coupled model's
  refusals** -- successors [Decide the futuresight family's
  place](issues/32-decide-the-futuresight-familys-place.md) (build the zip or drop the family)
  and [Gate the campaign axis on the coupled
  model](issues/33-gate-the-campaign-axis-on-the-coupled-model.md). With the family out, both
  unmeasured costs land at the CHEAP end of 24's brackets, from a three-cell probe
  (`fifo` control + `gmyopic` + `gforecast`, the gate's own `fifo`/`tmin` arms, 12 coupled
  units, 0 failures): **a gain cell is 1.6-1.9x an unpriced one** (arm-dependent, 1.45-2.20
  across two adapters -- size on the upper end), **a reshape is 221 s** against 24's 1.0-10.1 h
  bracket for ten of them, and the freeze is 975 s, so the campaign's whole fixed cost is
  **0.76 h**. Pricing costs TIME, NOT MEMORY -- peak RSS is identical to three digits across
  all three cells, the ARM sets it and the policy does not touch it -- and disk holds at
  1.37 GiB per coupled unit. **The third cell was the control, and section 5 is why it had to
  be in-run: the gate run's absolutes are NOT reproducible.** The probe's freeze does
  bit-for-bit identical work (same line floors, same units fielded, same fragmentation) in
  966 s against the gate's 3,261 s, uniformly ~3.4x faster at every stage, with no worker pool
  anywhere in it to explain the gap. So 24's 1,134 s per unit and 3,633 s setup are numbers
  from a different clock and cannot be multiplied by this ticket's ratio; the RATIO is the only
  machine-independent thing here. Phase 2 restated on the probe's own clock: **8 runnable
  cells, 96 units, 23.8-27.0 h of unit-seconds, ~132 GiB, about 7 h wall at 4 workers.**

- [Decide the futuresight family's place](issues/32-decide-the-futuresight-familys-place.md):
  **BUILD the zip -- phase 2 stays at TEN cells -- and the family is NOT what this map has been
  calling it.** The build is small and its shape was already written down: both leaves' windows
  share a batch index by three existing checks, the union is DISJOINT (a SKU is single-regime),
  so the zip is a strict no-op against per-leaf pricing -- which is also 06's Tier-1 equivalence
  test, nearly free to write, and why `_window_rates` needs no change. "Assert or trust" was
  never open: the composer already raises on collision for `predicted` and `emptied_at` under
  the same argument. Two edits, not one (the refusal, and `window=None` hard-coded in the
  composed return). The unexercised-path objection the refusal was written for dissolves the
  moment an arm reaches it. **The correction that outlives the build: futuresight is a
  CLAIRVOYANCE REFERENCE, not an upper bound.** It replaces the demand rate inside an UNCHANGED
  greedy, so w=inf prices each unit's future picks exactly while the ordering stays a heuristic
  -- it bounds PRICING ACCURACY, never achievable gain, and it may legitimately finish BEHIND a
  lawful arm. So the campaign publishes "perfect demand knowledge buys X% over standing demand",
  symmetrically for X negative, and is BARRED from "the lawful arm is at the ceiling"; X <= 0 is
  a finding (the binding constraint is the ordering heuristic, not the estimate), and 10's
  expect-modest-separation flag rides onto the page rather than staying in the ticket. Both cells
  stay out of the recommendable set whatever X is. `CONTEXT.md` sharpened accordingly. The
  `_window_rates` memo is **probe-gated, not folded in** -- it is five lines plus 06's caching
  contract, the cost has never been measured, and 31 declined the identical trade on the batch
  script -- so 34 runs ONE `fsight_w5` unit against 31's `gforecast` pole (1,236 s, probe clock)
  and either records "not worth a build" or graduates the memo with a number attached. Phase 2
  at ten cells: **120 coupled units, 30.8-35.3 h of unit-seconds, ~164 GiB, ~8.6-9.7 h at 4
  workers plus 0.88 h fixed** -- +3 h and +32 GiB over dropping the family, and neither binds.

- [Gate the campaign axis on what a coupled run can do](issues/33-gate-the-campaign-axis-on-the-coupled-model.md):
  **both a spec-time refusal and a test, and the general predicate is "does this cell's policy
  need a `SpaceView` structure a composed view does not carry"** -- asked against what the
  composer COMPOSES, never against what it refuses, which is what keeps it alive after 34
  empties the refusal set. Two declarations, one join: `POLICY_VIEW_NEEDS` (what each yard/dock
  entry reads, registered beside the entry) and `COMPOSED_VIEW_FIELDS` /
  `UNCOMPOSED_VIEW_FIELDS` (an EXHAUSTIVE partition of `SpaceView.__slots__`, so a new field
  must be classified rather than defaulted), met by `site_space.uncomposable_policies` and read
  by `validate_spec`. The refusal is the half that would actually have caught 31 -- that probe
  ran a throwaway spec no committed test could see -- and the test is the half that costs a
  launch nothing; the gate is conditional on COUPLING, because a one-leaf composition is the
  view by identity. Three things make the test survive 34: the subtraction, the exhaustive
  partition, and **a composed field must SURVIVE a composition, not merely be allowed into one**
  (refusal and silent drop are the same defect arriving two ways). `_KNOWN_DEAD =
  {fsight_w5, fsight_wall}` is pinned, so emptying it is the visible half of 34. And the third
  sub-question is answered NO: `_rule_pairs` checked `rule_pairs` against the rule universe and
  never against `FAITHFUL_GAIN_FAMILIES`, so a hand-copied ranking carrying a family that still
  needs 20's extension would have died at the first drain of every gain cell -- now refused in
  the same place.

## Not yet specified

- **The builds** — every implementation graduates here once its governing decisions close.
  ONE fog item remains: the RESUME-GUARD EXTENSION TO YARD STATE. Nothing inspects inbound
  state on resume today — `_plan_strategy_start` takes `roll_over` and `receiving`, not
  `inbound`. The standing yard is covered because `inbound_spec` refuses a standing yard
  without a receiving crew, so the planner's `receiving=` is never False there (since 23 both
  read the DERIVED crew under the era, not the declared key); but a v1 trailer run with a
  trailer type and NO receiving crew has worker-local trailers in no checkpoint and is not
  refused under `--resume-granularity batch`. Out of scope stays out
  of scope (no trailer checkpoint format) — this is a refusal, not a format.
  (Done: the standing-yard mechanics — 09 —, the space-timeline build — 11 —, the
  ordering-seam generalization — 12 —, the gain evaluator + gain-plan arms — 14 —, the
  futuresight window feed + entry — 13 —, the lead distribution — 15 —, the eviction
  fold — 16 —, the yard metrics — 17 —, the total-production-hours build — 19 —, the
  run-shape layer — 18 —, which paid the seams 3–4 debt every knob deferred to "the first
  sweep", and the fifo rider's gain bundle — 21 —, which was the last build standing between
  the funnel and the pilot, and the day-divisor pin -- 30 -- which was takeable at any time
  and is done. and the gain-cell cost probe -- 31 -- which
  was takeable at any time and is done. THREE tickets remain, one of them new.
  [Decide the futuresight family's place](issues/32-decide-the-futuresight-familys-place.md)
  RESOLVED 2026-09-13 as BUILD, which graduated
  [Build the coupled futuresight window zip](issues/34-build-the-futuresight-window-zip.md) --
  the zip, its Tier-1 pair, the docstrings the glossary correction invalidates, and the probe
  that puts the FIRST number on a futuresight cell -- and unblocked
  [Gate the campaign axis on the coupled
  model](issues/33-gate-the-campaign-axis-on-the-coupled-model.md), whose gate now points the
  other way: the one known failing instance is being removed, so the general predicate and a
  synthesised failing cell are what keep that check non-vacuous (commented there, with the
  ordering note -- it can be proven against the current composer if it lands before 34). The
  third, [Extend the gain bundles](issues/20-extend-the-gain-bundles.md), is unchanged: gated on
  phase 1, since which OTHER families need extending is phase 1's output. So the frontier is 34
  and 33 -- and phase 1, which nothing on this map blocks, can run alongside both.)
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
  **Decided 2026-09-05 at department-calibration's
  [Sequence the inbound funnel](../department-calibration/issues/05-sequence-the-inbound-funnel.md):
  phase 1 is HELD.** It runs under the calibrated era, not the historical continuous regime — a
  ranking taken today would be taken under a cost model (per-item charge) and a batch script (cut
  on) no later run uses, and the `inb_off` anchor would compare across eras. The lift is that map's
  [Take the reference run](../department-calibration/issues/09-take-the-reference-run.md) resolving.
  The pilot's committed regime (`--recv-crew-size 4 --recv-day-seconds 43200`, `PHASE2_RECV_*`) is
  an ERROR under the era — the receiving crew is derived, on the site's day — so the pilot gate is
  re-run as a VERIFICATION, not a search, and phase 2's command line no longer carries an
  experimental condition. **Execution order is now:** reference run (that map) →
  [Verify the derived receiving crew under arrivals](issues/23-verify-the-derived-receiving-crew.md)
  → phase 1 → selection → phase 2 → publish, with
  [Re-size the funnel in site days](issues/24-resize-the-funnel-in-site-days.md) done before
  phase 1 launches (the funnel inherits the reference window, 40 site days with 20–39 measured, so
  the 136/480-unit sizing above is stale). `restock_selection.json` will pin the staffing record
  and `inbound_policies` refuse under a different one.
  **2026-09-10: the verification (23) read OUT OF BAND and the hold is back on.** The receiving
  crew is fine; the coverage record carries no lead and the yard does not bind under the derived
  crew. Execution order is now: department-calibration's
  [Declare the coverage against the inbound lead](../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md)
  and this map's
  [Decide the contention regime under the derived crew](issues/25-decide-the-contention-regime-under-the-derived-crew.md)
  (independent) →
  [Re-verify the gate under the lead-aware record](issues/26-reverify-the-gate-under-the-lead-aware-record.md)
  → 24 → phase 1 → selection → phase 2 → publish. The pilot regime no longer rides a command
  line at all: `PILOT_RUN_DEFAULTS` carries it.
  **2026-09-10, later: the hold is now behind the SITE-DOCK COUPLING** (Out of scope, the
  successor seed). 25 found the slack yard was the leaf model, not a knob: the campaign's dock
  is the site's, and phase 2's cells become PAIRS of arms under one inbound policy. Execution
  order: [Chain the supplier lead before the trailer](issues/27-chain-the-supplier-lead-before-the-trailer.md)
  and [Cap the door team](issues/28-cap-the-door-team.md) (independent, frontier) -> the
  site-dock effort -> 26 on the coupled dock -> 24 -> phase 1 -> selection -> phase 2 -> publish.
  **2026-09-11: 27 and 28 are both RESOLVED, so this map's frontier is EMPTY and the next act is
  not a ticket on it** -- it was the SITE-DOCK CHARTING SESSION, done the same day and now
  [`.scratch/site-dock/map.md`](../site-dock/map.md) (Out of scope, below, carries the seed). The two remaining tickets both wait on that effort:
  [Re-verify the gate under the lead-aware record](issues/26-reverify-the-gate-under-the-lead-aware-record.md)
  reads the gate on the coupled dock, and [Re-size the funnel in site days](issues/24-resize-the-funnel-in-site-days.md)
  cannot size a cell until a cell is a PAIR of arms. The declared physics they will run under is
  now complete: the supplier lead chains in front of the trailer, and the door team is capped at
  ten with the dock's parallelism ceiling reported beside every receiving utilization.
  **2026-09-12: the site dock effort CLOSED and the gate ran on it (26). Half the hold lifts.**
  The coupled dock is no longer a question -- the yard binds, in 25's band, non-saturated -- so
  nothing about the ARRIVAL regime is outstanding. What holds the campaign now is one channel's
  coverage form, a department-calibration decision:
  [Close the fulfillment fill-law gap](../department-calibration/issues/38-close-the-fulfillment-fill-law-gap.md).
  Execution order: 38 -> [Re-run the gate and fix the fee
  threshold](issues/29-rerun-the-gate-and-fix-the-threshold.md) (a confirmation on the yard, a
  verdict on supply, and the threshold fixed off the sweep already recorded on
  `PHASE2_THRESHOLD_DAYS`) -> 24 -> phase 1 -> selection -> phase 2 -> publish.
  **2026-09-12, later: the hold is FULLY LIFTED and phase 1 is launchable now.**
  [Re-size the funnel in site days](issues/24-resize-the-funnel-in-site-days.md) resolved, so
  nothing on this map stands between here and the launch. The sizing above (136 / 480 units,
  ~13 h, ~1.1 TB) is superseded: **phase 1 is 68 leaf units, 12–17 h of unit-seconds and ~47 GiB,
  after ~1 h of per-pair setup; phase 2 is 120 COUPLED units, a floor of 37.8 h and ~169 GiB.**
  The launch is `--spec inbound_select --profiles-dir <the one-pair reference view> --workers N`
  and nothing else: the depth and the arrival regime now ride `run_defaults`, so no remembered
  flag survives. Execution order: **phase 1 -> selection -> [Extend the gain bundles](issues/20-extend-the-gain-bundles.md)
  + [Measure what a gain cell actually costs](issues/31-measure-what-a-gain-cell-costs.md)
  (both between the phases, both before phase 2) -> copy `rule_pairs.chosen` AND
  `staffing.pin` -> phase 2 -> publish.** [Pin the day divisor](issues/30-pin-the-day-divisor.md)
  was the third of that set and is DONE -- taken before phase 1 rather than after, since it is
  a strict no-op and its only risk was leaving a simulation-time divisor unpinned across the
  between-phase build window. Two
  qualifications the campaign publishes rather than discovers are unchanged (it is mostly a
  fulfillment result; phase 1 ranks under 2x the site put labour phase 2 runs), and the phase-2
  wall is a floor until 31 measures the gain multiplier.
  **2026-09-12, later still: phase 2 CANNOT be launched as declared, and its sizing is
  re-based.** [Measure what a gain cell actually costs](issues/31-measure-what-a-gain-cell-costs.md)
  found two of the ten cells unrunnable under coupling (the futuresight family; see the decision
  entry above), so the campaign is 8 cells until
  [Decide the futuresight family's place](issues/32-decide-the-futuresight-familys-place.md)
  says otherwise: **96 coupled units, 23.8-27.0 h of unit-seconds, ~132 GiB, ~7 h wall at
  4 workers, plus 0.76 h of freeze and reshape.** At ten cells, if the zip is built, 120 units
  and 30.8-35.3 h -- a LOWER bound for the two futuresight cells, which nothing has ever timed
  because nothing can until the zip exists. **Every absolute above is on the probe's clock, and
  the gate run's is a different one**: identical work runs ~3.4x faster in the freeze and ~1.6x
  faster per unit, so the 37.8 h / 169 GiB figures from 24 must not be multiplied by 31's ratio
  or compared with these. Execution order: **phase 1 -> selection -> 20 + 32 (-> 33) -> copy
  `rule_pairs.chosen` AND `staffing.pin` -> phase 2 -> publish**, with 32 launchable now since
  it blocks nothing phase 1 does.
  **2026-09-13: the axis is settled at TEN cells and the sizing above is final rather than
  provisional.** [Decide the futuresight family's place](issues/32-decide-the-futuresight-familys-place.md)
  resolved BUILD, so phase 2 is **120 coupled units, 30.8-35.3 h of unit-seconds, ~164 GiB,
  ~8.6-9.7 h wall at 4 workers, plus 0.88 h of freeze and reshape** -- still a LOWER bound on
  its two futuresight cells, and [Build the coupled futuresight window
  zip](issues/34-build-the-futuresight-window-zip.md) is what first lifts that, since its probe
  times one `fsight_w5` unit against 31's `gforecast` pole before the campaign commits. Every
  absolute here is on 31's probe clock and must not be mixed with 24's. Execution order:
  **phase 1 -> selection -> 20 + 34 (-> 33) -> copy `rule_pairs.chosen` AND `staffing.pin` ->
  phase 2 -> publish**, with 34 and 33 both launchable now since neither blocks anything phase 1
  does. What the campaign publishes about the reference arms is pre-committed in 32 and is not a
  launch-time call: "perfect demand knowledge buys X% over standing demand", symmetric in the
  sign of X, and never "the lawful arm is at the ceiling" -- futuresight bounds pricing accuracy,
  not achievable gain, so it may finish behind a lawful arm without that meaning anything about
  headroom.

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
  effort owns the model. **Successor: [`.scratch/department-calibration/map.md`](../department-calibration/map.md)**,
  charted 2026-09-01 — its ticket
  [Sequence the inbound funnel](../department-calibration/issues/05-sequence-the-inbound-funnel.md)
  decides whether phase 1 also waits for the calibrated era (phase 2 already holds). The
  inbound campaign is runnable without it — the pilot proved a passing regime — but runs
  under a scarcity that effort would let someone CHOOSE rather than inherit.
- **Coupling the two channels at the dock -- one site inbound simulation.** Surfaced by
  [Decide the contention regime under the derived crew](issues/25-decide-the-contention-regime-under-the-derived-crew.md)
  and ruled out here the same day (2026-09-10). The regime the campaign wants is the site's own
  dock (one reorder stream of both channels, trailers carrying mixed store and fulfillment
  lots, one yard, one door set, one receiving crew, unloaded lots handed to each channel's own
  put-away and picking), and the leaf model of 2026-07-02 -- each channel an independent
  worker with its own inbound -- cannot express it. Beyond this destination because it reaches
  the leaf model itself, the run-tree contract (a site level above the channel leaves), the
  funnel's cell arithmetic (a phase-2 cell is a PAIR of arms under one inbound policy), the
  faithful-to-arm gain evaluator (pricing one trailer against two arms' machinery) and every
  per-leaf analysis surface. **Successor: a charting session with this seed** -- destination
  "the site dock landed on `develop`: both channels' inbound through one yard, doors and crew,
  flag-off byte-identical, the funnel's cells paired, so the inbound campaign runs on the
  site's own contention"; the sizing inventory linked from 25's assets is its starting map of
  seams. This campaign holds behind it; 27 and 28 do not.
  **CHARTED 2026-09-11 as [`.scratch/site-dock/map.md`](../site-dock/map.md)** (7 tickets,
  4 on the frontier). Its charter settled three things this map's remaining tickets ride on:
  put-away becomes ONE SITE POOL over segregated volume (a pack has exactly one owning
  channel), a phase-2 cell pairs arms on the DIAGONAL by rank rather than the cross product,
  and the coupling rides the inbound flag so phase 1 stays per-channel -- which leaves phase 1
  ranking under 2x the site put labour that phase 2 will run under, a caveat the campaign
  publishes rather than discovers.
