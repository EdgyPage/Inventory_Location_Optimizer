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

## Not yet specified

- **The builds** — every implementation graduates here once its governing decisions close:
  the resume-guard extension to yard state; the funnel build (if its
  decision says build). (Done or ticketed: the standing-yard mechanics — 09 —, the
  space-timeline build — 11 — and the ordering-seam generalization — 12 — are DONE;
  [Build the gain evaluator and the gain-plan arms](issues/14-build-the-gain-evaluator-and-arms.md)
  and [Build the lead distribution](issues/15-build-the-lead-distribution.md)
  are on the FRONTIER now that the arm roster (05) and the lead distribution (02) are
  resolved, and
  [Build the futuresight window feed](issues/13-build-the-futuresight-window-feed.md)
  follows behind it — 13 also builds the `futuresight` entry, so it is blocked by 14;
  [Fold the eviction into reclaim_v](issues/16-fold-the-eviction-into-reclaim-v.md) is on
  the frontier too — the cache boundary (06) resolved to NO cache build beyond that fold
  and the Tier-1 equivalence test riding 14; and
  [Build the yard metrics](issues/17-build-the-yard-metrics.md) joined the frontier when
  the yard metrics (07) resolved.)
- **Timed / deeper lookahead views** — predicted-clear timing and LAWFUL demand beyond the
  released batch ("how far ahead can availability reliably be planned"), a future inbound
  view-arm family; parked by the space-timeline resolution (03), which shipped predictions
  untimed. (The unlawful version — reading the future script — is no longer fog: it is the
  futuresight window reference family, decided by the objective resolution, 10.)
- **The funnel campaign** — actually running phase 1 (inbound-off top-k selection) and
  phase 2 (top-k × inbound policies), and publishing the results; specifiable once the
  machinery and the funnel design exist.

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
