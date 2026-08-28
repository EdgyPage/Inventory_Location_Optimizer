# Design the standing-dock mechanics

Type: grilling
Status: resolved

## Question

Doors become real and the yard stands — design the mechanics that make that true. A trailer
holds its door across drains until fully unloaded; at most `doors` trailers are staged; the
yard-pull fires (drain-quantized) when a door frees. Decide: how partial-unload state is
carried across drains (the pack plan is fixed at arrival — interruption pauses work, never
re-plans); how the receiving crew's per-batch budget spreads across staged trailers; where in
the pinned six-phase `check_reorders` order the pull and unload decisions fire; what single
flag gates the whole standing model; and what flag-off byte-identity means measured against
v1's drain-everything `release()` (`Inbound/transit.py`). Consult `codebase-design`; the dock
intercept lives inside `_admit` (memory: `receiving-is-its-own-crew`), and the phase order is
behaviour, pinned by `Tests/unit/test_reorder_phases.py`.

This is the foundational ticket: the charter's no-deferral, drain-quantized, and
doors-become-real decisions are inputs, not open questions.

## Answer

Resolved 2026-08-27 through four grilling rounds. One premise follows directly from the
charter's inputs and was not asked: **the standing buffer is the trailer itself** — staging
never dumps a load onto the dock floor, the crew pulls units straight off staged trailers, and
the dock keeps owning the crew clocks, the cost model, the records and the cut counter. The
dock-floor deque stays empty flag-on. (Correction absorbed along the way: the phase ratchet
pins SEVEN phases, not the six this ticket's question said — `_receive` sits between
`_release_arrivals` and `_drain_putaway`.)

**1. Partial-unload state.** The pack plan is computed at YARD-ARRIVAL by the MANAGER (which
owns `_originals` and `mgr.packer`; the transit can reach neither), per contiguous lot — the
same portions v1 packs, fixed at loading — and stowed in the existing `Trailer.plans` slot. The
remainder carried across drains is the not-yet-unloaded planned units (a consumed index, no
re-pack). Interruption stops BETWEEN units: the whistle is a START gate, one unload of overtime
per worker, exactly as today. Tier mix stays independent of crew size because portion
boundaries come from loading, never from where the budget stopped.

**2. Ledger and age.** Standing trailer contents stay in `_deferred_qty` (they remain inside
the transit's census, so position = on_hand + queued + deferred conserves and nothing
re-orders). The deferred-to-queued flip happens PER UNIT at unload, when the crew hands it to
`_queue`. The `PutawayItem` stamp is the trailer's yard-arrival batch — never the unload batch,
which would be the age inversion `_admit`'s docstring forbids.

**3. Phase placement.** No new phase, no reorder; the ratchet stands. `_release_arrivals` /
`release()` is PURE CALENDAR flag-on: departs loaders, arrivals join the yard, returns no
deliveries. `_receive` owns every door and crew decision: freeze the drain's ctx (after
arrivals joined), fill free doors from the frozen yard ranking, unload budget-gated, refill
doors as trailers empty. The door-fill is NOT budget-gated — staging is yard-jockey work, not
receiving-crew labour, so even a zero-budget drain fills free doors.

**4. Crew allocation: SPLIT (door teams) — the user's model.** At ctx-freeze the workers are
dealt across staged trailers in dock-priority order, cycling (top ranks get the extras when the
division is uneven; `Warehouse/kernel/allocation.partition` is the reuse). Each team charges
earliest-free WITHIN the team (a small `crew_clock` extension: charge/can_start over a subset
of clocks). When a trailer empties: the door frees, the yard-pull stages the
frozen-ranking-next trailer, and the freed team reassigns — (1) to the top-ranked staged
trailer with NO workers (prevents the one-worker-many-doors inversion), (2) else to its own
door's replacement, (3) else to the top-ranked trailer with the fewest workers. A worker idles
only when nothing staged has units. Dock priority is thereby a worker-ALLOCATION preference:
decisive when workers < staged trailers, graded otherwise. Doors free STAGGERED — the realistic
dynamic for the fee and space signals. Rejected alternative: an interleaved feed (one pool
cycling staged trailers) frees doors in simultaneous waves and decays dock priority to a
tiebreak.

**5. The allocation knob.** `INBOUND_CREW_ALLOCATION = 'split' | 'merged'` — a plain enum on
the inbound spec, NOT a policy registry (a mechanics mode, not a scoring policy). Default
'split' when the standing yard is on; 'merged' is v1's pooled-gang semantics, kept both as
honest physics and as the verification bridge (see 10). Inert flag-off.

**6. Mid-drain semantics.** Same-drain refills consume the DRAIN-FROZEN rankings; no mid-drain
re-scoring (the frozen-`ctx` purity contract). The crew continues onto newly staged trailers
within the drain until the budget gates or nothing stands anywhere.

**7. Canonical handoff.** Regardless of allocation, unloaded units are handed to `_queue` in
canonical merged order — trailers by dock rank, units by local rank, filtered to what actually
unloaded — never in labor-completion order. v1 already works this way (handoff is floor order
while `charge()` schedules the clocks in parallel). This buys the containment property: crew
allocation changes labor stamps and makespans ONLY, never placement physics.

**8. The flag.** `INBOUND_STANDING_YARD = False` gates the whole model: real doors, standing
yard, plans-at-arrival, deferred-until-unload, yard/dock registry consultation, allocation. It
rides `inbound_spec()` through the five seams like every inbound knob. Standing-on with
`INBOUND_TRAILER_TYPE = None` is a config contradiction and FAILS LOUDLY at spec build — never
silently inert.

**9. Structural home.** A new transit class, `YardTransit(TrailerTransit)`, overrides
`release()` and adds the standing surfaces (arrived-this-drain, the unload pull, door/census
state); the driver binds it when the flag is on. `TrailerTransit` and `BatchTransit` are not
edited; they satisfy the standing surfaces trivially (no standing work). The `_lot` to yard
identifier/docstring rename rides this build.

**10. Byte-identity, four layers.** (1) Flag-off binds the v1 classes UNTOUCHED — identity by
construction, plus the existing 12 pipeline tests. (2) The degenerate lockstep: standing-on,
FIFO policies, doors >= every trailer that ever stands, no receiving cap, allocation='merged'
must produce a FULLY BYTE-IDENTICAL run DB vs v1 drain-everything — pinned in `Tests/unit` at
small scale. (3) The containment test: the same degenerate config with allocation='split' must
produce a DB identical EXCEPT the dock `work_events` rows' t0/worker — proving allocation is
labor-only. (4) Under a receiving cap the models legitimately diverge in the unloaded set
(split makes partial progress on every staged trailer; merged completes top-ranked trailers
first) — that divergence is the feature, documented, never equality-tested. Under cuts vs v1,
flows stay identical and levels relabel by design (v1's floor remainder is queued; the standing
remainder is deferred).

**11. Registry split.** The yard and dock registries land WITH this build in
`Inbound/priorities.py`, both seeded 'fifo'; knobs `INBOUND_YARD_POLICY` /
`INBOUND_DOCK_POLICY`, ADDITIVE. `INBOUND_GLOBAL_POLICY` and the v1 global registry stay
untouched and unread in standing mode (the flag-off path is not refactored). Local policy is
shared, unchanged. The one existing ordering bound applies to BOTH new rankings until an arm
needs them separate.

**12. Census, cut, stamps.** `depth` / `merchandise()` / `snapshot()` extend over yard + staged
remainders, so conservation and replay work unchanged. `dock.cut` at the whistle re-counts
units remaining on STAGED trailers; the yard is never cut (waiting there is calendar — the
fee's domain). The trailer records the minimal event-stamp set: `arrived_s` (yard entry),
`staged_s` (per staging), `emptied_s` (epoch + crew-clock offset of its last unit). Column
naming and reporting belong to the yard-metrics ticket.

**13. Unload cost: own coefficients, same form.** The existing shape stands — pack intercept +
per-item weight/volume term, no height, no travel, no speed — but gains its OWN coefficient
set: `INBOUND_UNLOAD_INTERCEPT` / `INBOUND_UNLOAD_WEIGHT_COEF` / `INBOUND_UNLOAD_VOLUME_COEF`
on the inbound spec. Unset = today's by-reference `PutawayCost` values, so every existing run
is byte-identical and no era splits; set = the independent inbound price lever. The
`Dock(cost=)` seam carries it as-is. No new functional form (a second set of invented magic
numbers is the drift this repo already paid for twice), and no slow-unload-for-benefit
mechanism — the model has no benefit channel for it.

**14. The objective, routed.** The evaluation's objective is TOTAL PRODUCTION HOURS = unload +
put + pick per arm, reported beside the fee — the surface that answers "is there an incentive
to cost time in inbound vs saving it in pick time" and carries the headline contrast the user
wants: two departments greedily optimizing vs a globally cost-aware policy. Routing: the
columns land in "Define the yard metrics" (07); the funnel's selection metric consumes it in
"Design the phased funnel" (08, comment left); the arm roster frames the greedy-vs-global pair
in "Name the policy arms" (05, comment left); and the unload cost with team size sizes each
trailer's UNLOAD WINDOW — the forecast horizon "Prototype the load-score evaluator" (04) scores
against. Honesty note, recorded: total unload hours vary across arms only through the reorder
feedback loop; the first-order lever is placement quality buying cheaper puts and picks.

Graduated: "Build the standing-yard mechanics" (09, task) — every decision above, buildable
now. Glossary: CONTEXT.md gained Standing yard, Door team, Crew allocation.
