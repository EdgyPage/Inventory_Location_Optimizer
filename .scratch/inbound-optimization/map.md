# Inbound optimization

Label: wayfinder:map

## Destination

Inbound optimization landed on `develop`: heterogeneous seeded leads, a standing yard whose
finite doors bind, the split yard-/dock-priority seams filled by space-aware policy arms
(myopic + standing-demand forecasting) under the yard-overage fee proxy, behavior-neutral
caching at declared freeze points — all flag-off byte-identical — and the phased funnel
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
    timeliness-vs-space tradeoff is embodied purely in ORDERING; pure-key registries suffice.
  - **Doors become real**: at most `doors` trailers staged; a trailer holds its door across
    drains until fully unloaded; the yard-pull fires when a door frees.
  - **Decisions are drain-quantized; data is event-stamped.** The frozen-`ctx` purity contract
    stands; the space timeline carries absolute-clock stamps so a later event-driven cadence
    is a cadence change, not a data redesign.
  - **Leads**: per-trailer, drawn from a seeded distribution (minutes-authored); arrivals
    enter the yard ordered by arrival stamp, `seq` as tiebreak. No batch denomination.
  - **The score — amended 2026-08-29 by the space-timeline resolution (03):** the decision
    to stage/unload a trailer anchors to IMMEDIATELY AVAILABLE bins (the operationally
    stable signal); predicted clears are an UNTIMED set — no unload-window gate, no rate or
    makespan inference. Forecast source stays STANDING DEMAND only (released-but-unpicked,
    one batch deep) — information a real WMS has. Whether placement quality can rank
    trailers at all, and what the decision optimizes instead (on-shelf availability; an
    unload plan), is open in
    [Define the inbound objective](issues/10-define-the-inbound-objective.md); the
    myopic/forecasting family pair stands pending that answer.
  - **Fee proxy**: per-trailer overage = max(0, yard_days − threshold), threshold a knob;
    a reported span-derived metric, never converted to dollars, never mixed into labor.
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

## Not yet specified

- **The builds** — every implementation graduates here once its governing decisions close:
  the space-aware policy wiring (the yard/dock registries and their split LANDED with
  "Build the standing-yard mechanics"; the real policy entries wait on the
  objective/evaluator/arms tickets and arrive as registry entries, not rewiring); the lead
  distribution build; the evaluator + cache builds; the yard-metrics build (columns,
  semantics tags, report surfaces — its raw material, `YardTransit.stamps`, already
  exists); the resume-guard extension to yard state; the funnel build (if its decision
  says build). (The standing-yard/doors mechanics build is DONE — ticket 09, resolved
  2026-08-29; the space-timeline build graduated 2026-08-29 as
  [Build the space timeline](issues/11-build-the-space-timeline.md), unblocked now that
  09 is done.)
- **Timed / deeper lookahead views** — predicted-clear timing and demand beyond the released
  batch ("how far ahead can availability reliably be planned"), a future inbound view-arm
  family; parked by the space-timeline resolution (03), which shipped predictions untimed.
- **The funnel campaign** — actually running phase 1 (inbound-off top-k selection) and
  phase 2 (top-k × inbound policies), and publishing the results; specifiable once the
  machinery and the funnel design exist.
- **Weight-knob sweep design** — which weight grids the space-aware arms sweep; needs the
  arms to exist first.
- **The oracle upper-bound arm** — a forecast that reads the full future demand script;
  optional, only if the funnel results demand a reference ceiling.

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
