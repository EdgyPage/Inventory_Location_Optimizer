---
name: inbound-pipeline-wayfinder-decisions
description: "The inbound-groundwork wayfinder map CLOSED 2026-08-26/27 — all fifteen tickets resolved, destination reached, everything committed on develop. Records every decision AND every landing: the Inbound/ package skeleton, the column-semantics layer (zero remainder, six families), BatchTransit behind the order port, the v1 trailer pipeline (Inbound/trailer.py, priorities.py, transit.py), and the drain-or-cap shift (SHIFT_DRAIN_OR_CAP, timeline.shift_end). Nothing of this effort remains unbuilt — the successor effort is now its own map, `.scratch/inbound-optimization/map.md` (charted 2026-08-27, commit `25bfdf7`), which starts from THIS map's Out-of-scope list, not from an unbuilt-code list."
metadata: 
  node_type: memory
  type: project
  originSessionId: 6cf27a52-c76a-43c2-a56f-3b7410486717
  modified: 2026-08-27T06:00:00.000Z
---

A planning-only session (2026-08-26) resolved five durable decisions for the not-yet-built
inbound trailer pipeline, a second ticket the same day drew the package boundary (6 more
decisions), a third ticket the same day chose the lead-time denomination (5 more decisions),
a fourth ticket the same day set the column-semantics conventions (5 more decisions), a
fifth ticket the same day validated the semantic-layer accessor design with a runnable
prototype (4 more decisions), a sixth ticket the same day designed the two-level
priority seams (4 more decisions), a seventh ticket the same day shaped the Cart and
Trailer objects (4 more decisions), an eighth ticket the same day EXECUTED the first code
of the effort, the `Inbound/` package skeleton (3 more points, below — this one is a landed
change, not a decision), and a ninth ticket the same day set the drain-or-cap SHIFT-END RULE
(4 more decisions, points 37-40 below) — the map's LAST open decision, graduating three
implementation tickets and closing the planning phase entirely. Full record: `.scratch/inbound-groundwork/issues/01-name-the-inbound-pipeline.md`
`## Answer` (pipeline naming), `.scratch/inbound-groundwork/issues/08-draw-the-inbound-package-boundary.md`
`## Answer` (boundary), `.scratch/inbound-groundwork/issues/02-choose-the-lead-time-denomination.md`
`## Answer` (denomination), `.scratch/inbound-groundwork/issues/03-set-the-column-semantics-conventions.md`
`## Answer` (column semantics), `.scratch/inbound-groundwork/issues/05-prototype-the-semantic-layer-accessors.md`
`## Answer` (accessor prototype), `.scratch/inbound-groundwork/issues/06-design-the-two-level-priority-seams.md`
`## Answer` (priority seams), and `.scratch/inbound-groundwork/issues/07-shape-cart-and-trailer-objects.md`
`## Answer` (cart/trailer shape); fact digest for the boundary ticket:
`.scratch/inbound-groundwork/assets/boundary-facts.md`; ground for the column-semantics ticket:
`.scratch/inbound-groundwork/assets/column-audit.md`; the prototype itself (runnable, throwaway,
kept as the ticket's asset rather than a branch): `.scratch/inbound-groundwork/assets/prototype_semantics.py`;
glossary: root `CONTEXT.md` (new, canonical domain glossary — a system distinct from `context/`,
see `docs/agents/domain.md`); map: `.scratch/inbound-groundwork/map.md`.
**Skeleton landed later the same day (2026-08-26, commit `aae9033`).** The
package move and injection seam are code now. **The column-semantics layer also landed the same
day (commits `c82751a`, `59ff750`, `fc690bd`, `4d9376b`, `f964f60`) — see points 41-43 below.**
**The map CLOSED 2026-08-27 (commit `403e590`): the transit move (commit `c68246f`, points
44-45 below), the v1 trailer pipeline (commit `b7a0ad3`, points 46-48 below), and the
drain-or-cap shift (commit `900baa7`, points 49-51 below) are ALL landed — see those points for
what code exists and what nuance survives (e.g. pack-at-unload's cost model, point 47).** Nothing
of this effort remains unbuilt; the future inbound-optimization effort starts from the Out of
scope list at the end of this memory, not from a residual build list. [[putaway-seams-for-inbound]]
and [[receiving-is-its-own-crew]] describe the pre-skeleton behaviour except where they now note
the move to `Inbound/`.

1. **Pipeline stages**: reorder → (unmodeled loading at an "ordering site", next-fit FIFO into
   trailers) → dispatch → transit (the lead queue becomes the transit leg — trailers model what
   the floating quantity abstracted) → arrival → unload → pack → handoff → put. Trailers carry
   LOOSE items, physically modeled as stacked on maximum-sized pallets.
2. **Packing moves to unload time** (dock-side receiving-crew work) — this deliberately
   OVERTURNS the packing-at-arrival invariant in what is now `Inbound/dock.py`'s docstring
   (moved from the old path Warehouse/inventory/dock.py by the skeleton landing, point 34
   below) and in [[putaway-seams-for-inbound]] ("Packing is per delivery, which is the whole point") and
   [[receiving-is-its-own-crew]] ("packing stays at arrival"). The confound is contained: the
   pack plan stays FIXED per trailer's full per-SKU contents, so tier mix stays independent of
   crew size (see [[stock-plan-overrides-packing]]); interruption pauses packing work, never
   changes packs. User decision, tradeoff explicit.
3. **Site state persists day over day** — a half-unloaded trailer, dock depth, and queued packs
   survive the working-day boundary (extends [[working-day-clock-plan-corrections]]'s open
   step 6b). **REALIZED in-run by the v1 trailer pipeline (point 30, 46 below): `Trailer`
   instances carry state across the day boundary with no reset.** A CHECKPOINT format for
   standing trailers across `--resume` is the one piece explicitly left to the future
   inbound-optimization effort — now chartered as `.scratch/inbound-optimization/map.md`
   (2026-08-27), which declined it again (refusal-until-clean stays the mechanism; see that
   map's Out of scope section).
4. **New code lands in a TOP-LEVEL `Inbound/` package** beside `Warehouse/`, not
   `Warehouse/inbound/` (contradicts the "future `Warehouse/inbound/`" aside in
   [[one-clock-one-speed-one-config]] — that sentence is now superseded). Vocabulary: "site" =
   whole facility, "warehouse" = put/pick side, "inbound" = receive side; "load" = trailer
   contents, never packs; `LoadPlan` will be renamed `PackPlan` in a later convention pass.
5. Root `CONTEXT.md` now exists as the canonical domain glossary, plus
   `docs/agents/issue-tracker.md`, `docs/agents/domain.md`, `docs/agents/triage-labels.md` and a local-markdown issue tracker
   under `.scratch/`.

**Boundary decisions (2026-08-26, second ticket, same day):**

6. **The Inventory Manager is the site's BROKER by injection, never by import.** Inbound sends
   packs to the warehouse and reorder needs flow out, both *through* the manager — the same
   hub-ness pattern the manager already uses for assignment functions (`mgr.placement`, set by
   strategy build hooks). The driver (`Optimization/simdriver/strategy_runner.py`) builds
   Inbound's objects from CONFIG specs at CALL TIME, following `recv_crew_spec`
   (`Optimization/config/sim_config.py:335`) — never the `put_crew_spec` snapshot trap
   (`sim_config.py:418-432`, reads settings attrs directly so later CONFIG writes are silently
   ignored) — and binds them via `enable_*` binders; `enable_receiving`'s lazy `Dock` import
   (`Warehouse/inventory/Inventory_Management.py:930-932`) becomes driver-side construction.
7. **Import edges: zero, both directions, between the planned top-level `Inbound/` and
   Warehouse's stateful layers.** `Inbound/` may import only value layers — `wh_kernel`,
   `wh_layout`, `wh_catalog`, plus `wh_operations` (for the `UnloadCost` → `PutawayCost`
   by-reference anti-drift coupling, then at old-path Warehouse/operations/unload.py:47,65-69 —
   now `Inbound/unload.py` after the skeleton landing, point 34 below — which stays).
   No Warehouse module imports Inbound, not even the manager.
8. **Two ports on the broker.** *Order port*: fired reorders go to an injected transit model —
   v1 the trailer pipeline, a future modeled upstream ordering node reuses the same port.
   *Receipt port*: stamped packs enter via the existing `_queue` entry
   (`inventory_reorder.py:456-501` — `_receive` bypasses `_admit` by design, built for exactly
   this handoff) plus a read-only census surface for reporting.
9. **Inbound owns transit; the manager keeps only the scalar deferred ledger.** The lead queue's
   timing machinery moves behind the order port; the manager keeps only `_deferred_qty`
   credit/debit (the property that made the dock landing zero-edit). The seven `check_reorders`
   phase WRAPPERS (`_tick_batch`, `reclaim_emptied_bins`, `_advance_lead_queue`,
   `_fire_reorders`, `_release_arrivals`, `_receive`, `_drain_putaway`) stay on the manager and
   delegate inside — the phase ratchet (`Tests/unit/test_reorder_phases.py`) pins
   `self.<name>(` call sites, not bodies, so relocating logic inside a phase body passes.
10. **Flat `Inbound/` layout**: `trailer.py`, `dock.py`, `unload.py`, `pack.py`, and a
    priorities module (planned, not yet built — see point 26). The migration set (`dock.py`,
    `unload.py`, `pack.py` — the latter renamed from `inbound.py`) EXECUTED as part of the
    skeleton landing, point 34 below. `put_queue.py` and the reorder ledger stay in
    `Warehouse/`.
11. **Fact correction, dated 2026-08-26**: `check_reorders` has SEVEN phases (`_receive` sits
    between `_release_arrivals` and `_drain_putaway`), not the six
    [[putaway-seams-for-inbound]] states — that memory and CLAUDE.md's "19 import boundaries"
    line (actual count 33 as of this date, `context/architecture.yml:14-105`) are both still
    stale as of this writing; a separate user-side task chip covers fixing them, not this one.

**Lead-time denomination decisions (2026-08-26, third ticket, same day):**

12. **A trailer's lead is an optional per-trailer delay on the absolute clock**, authored in
    MINUTES at the settings surface, stored in SECONDS internally (`TIME_UNIT` stays
    `'seconds'` — an internal-minutes era was rejected as a third unit for zero modeling gain).
    **Default ZERO: trailers arrive in the parking lot instantly.** The delay merchandise
    experiences becomes EMERGENT — parking lot → finite dock doors → crew hours — rather than a
    configured transit tick. This resolves the either/or the ticket opened with (batches vs.
    seconds vs. per-run flag); neither original option was taken.
13. **The batch-review lead queue stays untouched, flag-off.** Order-up-to math already handles
    lead-0 generically (`inventory_reorder.py:407`); flag-off keeps today's per-SKU
    batch-denominated lead queue byte-identical for the archive (era discipline, knob defaults
    OFF, same precedent as `SAMPLER`). Flag-on replaces the queue's transit role with trailers
    carrying optional time-leads. `test_lead_time_unit`'s note is to be rewritten at
    implementation time to record this pick, per the note's own instructions.
14. **New spatial entities**: *parking lot* (unbounded, arrived trailers awaiting a door) and
    *dock door* (finitely many staging slots). Which parked trailer stages next, and which
    staged trailer unloads, are the global-priority seam's decisions (designed at points 26-29
    below and BUILT in `Inbound/priorities.py`, point 46); the door count is a knob
    (`INBOUND_DOCK_DOORS`, landed with the trailer pipeline).
15. **"Every day begins at 0" is a day-local VIEW in minutes** (for reports and authoring) —
    the absolute clock stays the recorded truth, never what lands in `work_events`; the
    frozen-clock 6504-vs-2262-second bug ([[one-clock-one-speed-one-config]]) is the cited
    receipt for why this must stay a view, not a write.
16. **Drain-or-cap ends the SHIFT, not the day** (user's correction to the ticket's framing):
    work stops when no work remains anywhere, or at a global cap (~11 h). This is a new
    scheduler concept that COLLIDES with `SHIFT_SECONDS` (today a reporting frame that
    dispatches nothing) — the naming collision rides the convention pass, but the rule itself
    graduated to its own ticket, `.scratch/inbound-groundwork/issues/10-set-the-shift-end-rule.md`
    — **resolved 2026-08-26, same day, see points 37-40 below** (this memory's earlier
    "open"/"not yet resolved" language is superseded).

**Column-semantics decisions (2026-08-26, fourth ticket, same day):**

17. **Nine canonical storage kinds adopted**: STAMP (a point on a clock, never a length of
    time), SPAN (elapsed time between two stamps), LEVEL (a standing quantity, re-measured
    each snapshot, never summed across snapshots — see [[cut-is-a-level-not-a-flow]] for the
    incident this vocabulary now names), FLOW (an increment belonging to one row's grain,
    additive), COUNT (a FLOW whose unit is discrete), RATE (a flow divided by a span, its
    denominator part of its meaning), SCORE (a policy-relative ordering value, comparable only
    within its policy), SHARE (a proportion of a stated whole), LABEL/ID. Now defined in root
    `CONTEXT.md`'s new `### Measurement` section. The analysis-end `stance` (`level`|`contrast`
    in `Performance_Evaluations/core/quantities.py`) stays a separate figure-side concept, not
    folded into this vocabulary.
18. **Mandatory per-column tags: kind, unit + unit-of-account, grain (per-what).**
    Conditionally mandatory when applicable: clock/axis (sim vs wall vs batch-denominated;
    batch-local vs arm-absolute), null-meaning (every nullable column), discriminator
    (value-dependent kinds, e.g. `carryover.qty` by `reason` — see
    [[carryover-two-producers-one-key]]), plan/actual pair links, id-space (on ids). Nothing
    optional-and-silent.
19. **Rename policy: LOGICAL-layer renames only.** The named-query logical vocabulary carries
    the honest name (logical `release_day` over physical `work_day`); physical column names
    stay frozen — no vintage churn, no archive rename era; strict naming rules bind FUTURE
    columns only. Physical renames stay available case-by-case only where a name is actively
    dangerous and the table is young.
20. **Enforcement staged**: a completeness gate first (every column tagged, killing
    declaration rot), then use-assertions (`sums`, `ratios-against`, `per`) starting at the
    audit's eight ranked at-risk reads — `Diagnostics/receiving_report.py` first (the 101x
    site named in [[cut-is-a-level-not-a-flow]]) — expanding as reads convert.
21. **Forward naming rules**: mandatory unit suffixes (`_s`, `_abs`, `_pct` only 0-100,
    `_units` packs vs `_pieces` merchandise); authored-minutes knobs only in `settings.py` as
    `*_MINUTES`, converted once at the config seam; clock is always a tag, never inferred from
    a suffix. **`SHIFT_SECONDS` collision resolved**: the reporting frame renames at the
    logical layer (`REPORTING_FRAME_SECONDS` / logical `frame_index`), freeing "shift" for the
    drain-or-cap dispatcher (point 16 above, ticket
    `.scratch/inbound-groundwork/issues/10-set-the-shift-end-rule.md`). The declaration
    mechanism itself (where tags physically live, accessor API, gate wiring) is deferred to
    `.scratch/inbound-groundwork/issues/05-prototype-the-semantic-layer-accessors.md`.

**Accessor-prototype decisions (2026-08-26, fifth ticket, same day):**

22. **Declaration home accepted**: a `SEMANTICS` dict of `Col(kind, unit, grain, ...)` +
    `ByDiscriminator` declared beside each family's DDL, keyed identically to
    `declared_shape()` (which returns `{'tables': {t: {'columns': [{'name': ...}], ...}}}`, the
    nested form consumers read directly — no parallel list to drift) so the completeness gate
    is a dict diff. Latitude: a sibling `*_semantics.py` submodule per family is acceptable if
    the writer module gets heavy — decided at implementation, "beside the DDL" stays true
    either way.
23. **Guard altitude: BOTH, not either/or** — frame-boundary accessors (sum/ratio/add
    refusals) where `common/frames.py` hands columns downstream, AND declaration-time
    use-assertions at the `Quantity` layer (extending the `Requires` idea, `core/era.py`'s
    `QUANTITY_READS` bridge is the pattern).
24. **Refusal messages carry incident history** (e.g. the 101x headline from
    [[cut-is-a-level-not-a-flow]]) — the user wants them for future audits, not just a bare
    type-error string.
25. **No remainder, no staged-by-table carve-out**: every column in every family gets tagged;
    the completeness gate ends EMPTY. sim_db alone is 14 tables / ~173 columns (26 tagged in
    the prototype's demo subset). Two task tickets graduated from this ticket:
    `.scratch/inbound-groundwork/issues/11-build-the-semantic-layer.md` (the declaration
    machinery, both-altitude guards, and the completeness ratchet scoped to sim_db's epicenter
    tables — `batch_stats`, `carryover`, `put_queue_state`, `work_events` — as the proving set)
    and `.scratch/inbound-groundwork/issues/12-run-the-convention-pass.md` (tagging every
    remaining family to zero, the logical renames including `release_day`/`frame_index`/
    `REPORTING_FRAME_SECONDS`, converting the audit's eight at-risk reads, and the docs
    coherence collapse), the second BLOCKED BY the first.

**Priority-seam decisions (2026-08-26, sixth ticket, same day):**

26. **Two policy registries in a planned priorities module**, would-be path
    Inbound/priorities.py (unbuilt as of this writing — see point 35 below), mirroring
    `Warehouse/inventory/put_policy.py`'s contract (pure key functions, higher-first,
    batch-frozen state, registry raises on unknown name): **Global**, `key(trailer, ctx) ->
    comparable`, consulted at BOTH dock moments — a freed door goes to the top-ranked
    unstaged trailer, the crew unloads the top-ranked staged trailer; **Local**,
    `key(item, ctx) -> comparable`, may reorder a trailer's item work but may NEVER change
    pack composition (the pack plan is fixed per trailer, ticket 01 / point 2 above). v1 for
    both is `'fifo'` by arrival; no `INHERIT` entry — there is no pool whose precedence could
    stand in at the dock.
27. **`ctx` is a frozen per-drain dock context** — doors, free doors, and batch-frozen
    warehouse-space views, built once per drain. The future space-aware signal (`_emptied_at`
    / upcoming slots) arrives as a named view on `ctx` with no signature change; keys stay
    pure functions of (candidate, ctx).
28. **Selection via settings knobs**: `INBOUND_GLOBAL_POLICY = 'fifo'`,
    `INBOUND_LOCAL_POLICY = 'fifo'`, carried by the `recv_crew_spec` CONFIG-at-call-time
    pattern (explicitly not the `put_crew_spec` snapshot trap, point 6 above), bound
    driver-side into the injected `Inbound` objects per the broker decision (ticket 08).
29. **The dock gets its `k_cap`-analog ordering bound NOW** (user's explicit call, against the
    deferral recommendation): a bound on the GLOBAL ranking denominated in TRAILERS — the
    policy proposes an order, the bound limits departure from arrival order (mirrors
    `PutQueueSpec.k_cap`, `Warehouse/inventory/put_policy.py:10-17`). Default unbounded; with
    FIFO it is inert, so v1 stays byte-identical. A local-level analog waits for a non-FIFO
    local policy to land.

**How to apply:** when the pipeline actually ships, `Inbound/dock.py`'s docstring (this is the
skeleton-landing path — see point 34 below; it was the old path Warehouse/inventory/dock.py
before 2026-08-26), [[putaway-seams-for-inbound]] and [[receiving-is-its-own-crew]] need real edits
(not just this pointer) to describe pack-at-unload instead of pack-at-arrival, the seven-phase
count, and the new `Inbound/` import boundaries (now recorded in `context/architecture.yml`,
point 34); lead-time-in-batches
([[one-clock-one-speed-one-config]]'s "the trailer feature should choose") is now RESOLVED —
optional absolute-clock per-trailer minute-authored/second-stored leads, flag-on, default zero;
the legacy batch-denominated lead queue stays byte-identical flag-off (point 12-13 above).
Recording the boundary itself is mechanical and sequenced (files
first, then `GRAPH_ROOTS` in `context/arch/extract.py:44` — a new top-level package is
INVISIBLE to the graph until added there — then the `inbound` layer, then the forbid pairs,
then ~8 hardcoded package rosters listed in the fact digest). **The column-semantics conventions
(points 17-21) and the accessor design (points 22-25) are NO LONGER a decision-only item — see
points 41-43 below: `Schema/semantics.py` (kinds/guards/completeness gate), the six per-family
`*_semantics.py` modules, and `Tests/architecture/test_column_semantics.py`'s stdlib-only ratchet
are all built and committed** (`.scratch/inbound-groundwork/assets/prototype_semantics.py`
remains the throwaway prototype and is superseded, not the production code — read
`Schema/semantics.py` directly). Both graduated tickets
(`.scratch/inbound-groundwork/issues/11-build-the-semantic-layer.md` and
`.scratch/inbound-groundwork/issues/12-run-the-convention-pass.md`) are DONE.
The priority-seam design (points 26-29) is **NO LONGER decision-only — `Inbound/priorities.py`
landed with the v1 trailer pipeline (commit `b7a0ad3`, point 46 below), mirroring
`Warehouse/inventory/put_policy.py`'s registry shape exactly as designed.** The skeleton landing
(point 34 below) was a separate, earlier pass — it moved `dock.py`/`unload.py`/`pack.py` and
built the injection seam, but did NOT itself touch packing, priorities, or the trailer model;
those landed in the later tickets (44-51 below). The whole effort is now shipped; this memory is
the history of how it got there, not a forward pointer to unbuilt code.

**Cart/trailer-shape decisions (2026-08-26, seventh ticket, same day):**

30. **Type/instance split on the `StorageCart` pattern** (`Warehouse/layout/Storage_Primitive.py`):
    `TrailerType` is a stateless class-as-config with two concrete types — `Trailer53` (13 rows x
    2 across = 26 pallet positions) and `Trailer28` (6 x 2 = 12) — derived from footprint
    arithmetic at the 48-inch square pallet, `PALLET_FOOTPRINT` (`Warehouse/physical.py:16`).
    Positions are overridable class attributes; single-stacked (one pallet tier). `Trailer` is
    the stateful runtime instance — contents, remaining volume, optional lead, arrival stamp,
    door/staged state — and PERSISTS day over day (extends point 3 above). Its pack plan is
    computed at arrival, held only while the trailer stands, and dropped when fully unloaded
    (bounded by dock population; respects the removed-LoadPlan-leak constraint).
31. **NEW ENTITY: the LOAD PALLET** — the transport grouping loose items ride on inside a
    trailer; may MIX SKUs that fit; volumetric fit at 48^3 with the perfect-packing assumption
    taken verbatim from `StorageCart.add_from_bin`; next-fit closes a pallet, pallets fill
    positions, next-fit opens a new trailer. FIFO loading makes SKU lots CONTIGUOUS by
    construction. Name chosen deliberately to kill the collision with the pallet PACK; root
    `CONTEXT.md` now carries both entries with cross-avoid notes.
32. **Refinement to the priority seams (point 26 above)**: the local-priority key's natural
    candidate sharpens to the LOAD PALLET (what a crew pulls), degenerating to the SKU lot on
    single-SKU pallets — recorded as a comment on the priority-seams ticket, not a reversal;
    the planned priorities module's local-key interface is unchanged.
33. **Cart is pure reuse** — no inbound cart class, no new cart knob; `TrailerType` following the
    `StorageCart` pattern satisfies "similar to store pick carts". Trailer knobs:
    `INBOUND_TRAILER_TYPE = '53'` (type selection, the cart-class-as-config precedent — positions
    ride the class, no raw positions integer), plus `INBOUND_DOCK_DOORS` and
    `INBOUND_TRAILER_LEAD_MINUTES = 0` (restating point 12's default-zero lead as a named knob),
    joining the priority-seam knobs (point 28) on one call-time inbound spec through the five
    seams (extends [[config-knob-has-five-seams]]) including `workunits._shared`. CLI flags
    arrive when first swept; all inert while the trailer flag is off. Mixed fleets (both types
    in one run) are future work the type seam permits, not v1.

**How to apply (cart/trailer shape):** full record
`.scratch/inbound-groundwork/issues/07-shape-cart-and-trailer-objects.md` `## Answer`. **Points
30-33 are NO LONGER decision-only — `Inbound/trailer.py` (commit `b7a0ad3`, point 46 below)
built `TrailerType`/`Trailer53`/`Trailer28`/`Trailer`/`LoadPallet` exactly as designed here,
mirroring `Warehouse/layout/Storage_Primitive.py`'s `StorageCart` type/instance split and
reusing its perfect-packing volumetric-fit assumption rather than re-deriving it.**

**Skeleton landed (2026-08-26, eighth ticket, same day, commit `aae9033`):**

34. **The `Inbound/` package skeleton EXECUTED** —
    `.scratch/inbound-groundwork/issues/09-land-the-inbound-package-skeleton.md`, the first code
    change of the whole effort. Old path Warehouse/inventory/dock.py → `Inbound/dock.py`,
    old path Warehouse/operations/unload.py → `Inbound/unload.py`, old path
    Warehouse/operations/inbound.py → `Inbound/pack.py` (git-mv, history preserved); plus new
    `Inbound/__init__.py` and
    `Inbound/README.md`. `Inventory_Management.enable_receiving` now takes a CONSTRUCTED `Dock` —
    the lazy import named in point 6 above is gone, zero lazy imports remain.
    `Optimization/simdriver/strategy_runner.py` builds and binds it. `context/architecture.yml`
    gained the `inbound` layer and 16 new boundaries (point 7's "zero import edges" rule is now
    enforced, not just planned); `Inbound` is in `context/arch/extract.py`'s `GRAPH_ROOTS`; the
    graph/catalog/nodes/HTML are regenerated and `verify_architecture.py` passes at 25 layers
    (supersedes point 11's "33 boundaries" snapshot — recount at implementation time, do not
    trust either number without rerunning the verifier). 9 hardcoded package rosters (point 23's
    "~8 hardcoded package rosters") gained `Inbound`. `Tests/unit/test_putaway_provenance.py`
    gained an AST sweep over `Inbound/`.
35. **`mgr.packer` is now the injected packer seam** — a manager-local default,
    `_pack_plain(order, quantity, deliveries=None)` in `Warehouse/inventory/inventory_reorder.py`
    (module level, near the top), used whenever `self.packer is None`
    (`Inventory_Management.py:258-261`, `inventory_reorder.py:328`) — byte-identical to before
    this landing. `Dock` gained `unload_seconds(weight, volume, quantity)`
    (`Inbound/dock.py:185`) — the dock now owns its own price list rather than reaching into
    `Warehouse/operations/` for it; `inventory_reorder.py:507` calls it.
36. **What did NOT land in this pass**: the trailer/`TrailerType` model (points 30-33), the
    transit move off the batch-denominated lead queue (points 12-14), the pack-at-unload
    overturn (point 2), and the priorities module (points 26-29, still would-be path
    Inbound/priorities.py) were all still decisions, not code, AT THIS POINT IN THE SESSION
    (2026-08-26, eighth ticket). This pass was purely a package move plus an injection-seam
    refactor — no simulated behaviour changed (the packer default and unload pricing are
    relocated, not altered). **All four landed in the tickets that followed the same day and
    the next — see points 44-51 below.**

**How to apply (skeleton):** the moved files are the new anchors for every path in points 1-29
above that pre-date this landing — read `Inbound/dock.py`, `Inbound/unload.py`, `Inbound/pack.py`
directly, not their pre-2026-08-26 `Warehouse/` locations. The next ticket in sequence is
whichever of points 26-29 (priorities), 12-14 (trailer transit), or 30-33 (trailer/cart shape)
the user picks next — none is blocked on the others per the boundary decisions above, but the
priorities module is the one with a concrete, already-drawn file target.

**Shift-end-rule decisions (2026-08-26, ninth ticket, same day — the map's LAST open decision):**

37. **Drain-or-cap is a new DAY-END MODE over the existing working-day machinery**
    (`WORK_DAY_SECONDS` / `RELEASES_PER_DAY` / `CUT_AT_DAY_END` in
    `Optimization/config/settings.py`), not a rebuild — one new mode flag, default OFF =
    byte-identical (continuous, no cap, today's behaviour exactly). **The shift is the day's
    labor window; batches stay demand waves within it** — multiple batches release inside one
    shift by the schedule (shift ≠ batch, restating point 5 of the priority-seam section's
    framing). The cap REUSES the day length (no second duration to reconcile), and capping
    IMPLIES the existing cut/carry semantics — work standing at the cap rolls via the existing
    carryover machinery (extends [[cut-is-a-level-not-a-flow]], [[carryover-two-producers-one-key]]).
    Minutes authoring rides the convention pass's `*_MINUTES` surface (point 21 above).
38. **STANDING WORK is a new `CONTEXT.md` term** defining "no work left anywhere": released-but-
    unpicked demand (including carry) + the stock queue + put queues + `_held` + the dock floor
    (+ the parking lot, once trailers land). **The lead queue is explicitly EXCLUDED** — it is
    calendar, not labor. Drain additionally requires no releases remaining in the day: scheduled
    afternoon work keeps the shift open even if every current queue is empty.
39. **One site-wide shift when the mode is on** — all crews (pick, put, receiving) share the
    same drain-or-cap boundary; per-crew day lengths remain the flag-off configuration.
    Per-crew offsets are explicitly deferred as a later policy, not designed here.
40. **Days stay ORIGIN-ALIGNED on the absolute clock** (user's correction, verbatim: "the
    simulation does not model global time for no reason, the shift timers happen at the same
    time every day") — an early drain stops labor accrual at the drain instant, but the next
    day still begins at the next day's ORIGIN, not immediately after the drain. The dead evening
    is skipped for labor only, never for the calendar, so release schedules and day labels stay
    aligned and day-over-day persistence (point 3 above) carries as already decided. This
    resolves the framing correction the ticket opened with ("day" → "shift").

    Three implementation tickets graduated with this decision, completing the map's planning
    phase — every decision on `.scratch/inbound-groundwork/map.md` is now closed, remaining open
    tickets are execution only:
    `.scratch/inbound-groundwork/issues/13-move-transit-behind-the-order-port.md`,
    `.scratch/inbound-groundwork/issues/14-build-the-trailer-pipeline-v1.md` (blocked by 13), and
    `.scratch/inbound-groundwork/issues/15-build-the-drain-or-cap-shift.md`.

**How to apply (shift-end rule):** full record
`.scratch/inbound-groundwork/issues/10-set-the-shift-end-rule.md` `## Answer`. **Points 37-40 are
NO LONGER decision-only — `SHIFT_DRAIN_OR_CAP` landed in `settings.py` with commit `900baa7`
(point 49 below), exactly as designed here.** `SHIFT_SECONDS`
HAS since been renamed to `REPORTING_FRAME_SECONDS` at the authoring surface as part of ticket 12
(see points 41-43 below) — the collision this point's parenthetical worried about is resolved,
"shift" is now free for the drain-or-cap dispatcher, which now uses it. This ticket
closed the map's planning phase entirely; tickets 11 and 12 are DONE (points 41-43 below), and
tickets 13, 14, and 15 are ALSO DONE (points 44-51 below) — the map is fully closed.

**Column-semantics layer EXECUTED (2026-08-26, tenth and eleventh tickets, same day, commits
`c82751a`, `59ff750`, `fc690bd`, `4d9376b`, `f964f60`):**

41. **Ticket 11 built** — `Schema/semantics.py` declares nine kinds (STAMP/SPAN/LEVEL/FLOW/COUNT/
    RATE/SCORE/SHARE/LABEL-ID, point 17 above) via a `Col` dataclass with conditional axes enforced
    AT CONSTRUCTION (not by a separate lint pass), a `ByDiscriminator` helper for value-dependent
    kinds, guards whose refusal messages carry each column's incident history (point 24 above,
    e.g. citing the [[cut-is-a-level-not-a-flow]] 101x headline), and `check_completeness` that
    diffs declared semantics against `Family.declared_shape()` verbatim (point 22's dict-diff
    design) so nothing can drift silently. `Optimization/persistence/sim_semantics.py` tags the
    four epicenter sim_db tables first (`batch_stats`, `carryover`, `put_queue_state`,
    `work_events` — 62 columns). `Tests/architecture/test_column_semantics.py` is a stdlib-only
    ratchet (cannot `importorskip`-vanish per CLAUDE.md §3's pyyaml trap) and is sabotage-checked
    in both directions.
42. **Ticket 12 built, zero remainder** — every column of all six families now carries semantics
    (~310 columns): `Optimization/persistence/sim_semantics.py` (remaining eleven sim_db tables),
    plus new `Optimization/persistence/runtime_semantics.py`,
    `Optimization/persistence/warehouse_semantics.py`,
    `Warehouse/generation/affinity_semantics.py`, `Warehouse/generation/inventory_semantics.py`
    — each declared beside its own family's DDL (point 22). Seven of the audit's eight at-risk
    readers (`Diagnostics/receiving_report.py`, `Diagnostics/replay_run.py`,
    `Optimization/Performance_Evaluations/catalog/inventory.py`,
    `Optimization/Performance_Evaluations/common/frames.py`,
    `Optimization/persistence/Picking_Data.py`, `Optimization/run_whatif_labor.py`, and one more)
    carry AST-validated `SEMANTIC_USES` literals; the two bench log-parsers are excluded WITH
    CAUSE (they parse logs, not declared columns) rather than silently skipped. SPAN joined the
    additive kinds (spans of work sum to labour — the task_makespan invariant) while LEVEL stays
    refused. `SHIFT_SECONDS` renamed to `REPORTING_FRAME_SECONDS` at the authoring surface (the
    `settings.py` CONFIG key `'shift_seconds'` and every already-recorded row stay frozen; the
    logical name is `frame_index`) — freeing "shift" for the drain-or-cap dispatcher (point 40
    above). The `put_queue_state.cut` 15-line DDL warning and the `carryover` reason-family prose
    both collapsed to pointers at `sim_semantics.py` — one home per fact. Schema store resynced,
    0 outgoing shapes (tags are metadata only — no DDL changed, no shape id moved).
43. **Two durable lessons from the execution, not present in the planning-phase points above**:
    (a) a clock tag is required of temporal KINDS (stamp/span/rate) but NOT of a COUNT whose unit
    happens to say `'batches'` — refines point 17's kind list; (b) the run-tree ratchet
    (`context/verify_context.py` / `context/arch` tooling) counts hand-written contract tokens
    wherever they appear, prose included — a semantics note that named the run-manifest filename
    in a docstring tripped it and had to be reworded (commit `4d9376b`), the same failure mode as
    [[case-only-rename-deletes-its-own-page]] in spirit (a generator/checker sees literal text, not
    intent). Final state: 753/753 architecture + integration tests green, 1,345 unit tests green.

**How to apply (column semantics):** the layer is BUILT, not just designed — read
`Schema/semantics.py` for the `Col`/`ByDiscriminator`/guard API, and any of the six
`*_semantics.py` modules for a worked example of declaring beside a DDL.
`Tests/architecture/test_column_semantics.py` is the completeness ratchet: it fails if a new
column ships untagged, so a schema change that adds a column MUST add its semantics tag in the
same commit (extends CLAUDE.md §2's "schema changes ride the pipeline" rule with a machine-checked
consequence). The trailer pipeline (tickets 13-15, points 44-51 below) is the first consumer of
this discipline outside its own build — its new columns inherited the same completeness
requirement from day one.

**Transit-off-the-lead-queue EXECUTED (2026-08-26, twelfth ticket, same day, commit `c68246f`,
docs commit `0d244ed`):**

44. **Ticket 13 built** — `BatchTransit` (module scope in `Warehouse/inventory/inventory_reorder.py`,
    beside the default packer) now owns `dispatch`/`advance`/`release`/`depth`/`merchandise()`/
    `snapshot()` — the lead-queue timing machinery moved behind the order port exactly as
    designed in point 9 above. The manager keeps only the scalar `_deferred_qty` ledger. All
    seven `check_reorders` phase wrappers (point 9's list) stay and delegate — the phase ratchet
    (`Tests/unit/test_reorder_phases.py`) still pins call sites, and `test_lead_time_unit` now
    ALSO pins the delegation. `_lead_queue` survives as a compatibility PROPERTY so every
    historical reader and `__new__`-built test manager works unchanged.
    `Optimization/simdriver/strategy_runner.py` reads the new public `transit_snapshot()`
    (3-tuple shape preserved) instead of the old private attribute.
45. **The `LEAD_TIME_UNIT` note now records the made pick** (point 13's instruction, fulfilled):
    flag-off routes through `BatchTransit` (batches); flag-on routes through the trailer model's
    absolute-clock leads (point 46 below). One lesson from the landing: the note-location guard
    reads the FIRST occurrence of its token, so the transit docstrings reference "the unit note"
    WITHOUT naming it — the same failure-mode family as point 43(b) and
    [[case-only-rename-deletes-its-own-page]]. Byte-identical by construction; 1,740
    unit+integration + 6 receiving-e2e green.

**How to apply (transit move):** the INJECTED trailer transit (point 46) lands on the SAME
`mgr.transit` seam this ticket built, mirroring `BatchTransit`'s method set exactly
(`SOURCE`/`dispatch`/`advance`/`release`/`depth`/`merchandise`/`snapshot`) — phase bodies cannot
tell which is bound. Read `Warehouse/inventory/inventory_reorder.py`'s `BatchTransit` for the
seam contract before touching either implementation.

**The v1 trailer pipeline EXECUTED (2026-08-26, thirteenth ticket, same day, commit `b7a0ad3`,
docs commit `6bafa46`):**

46. **Ticket 14 built, everything the decisions specified**: `Inbound/trailer.py` —
    `TrailerType`/`Trailer53` (26 positions)/`Trailer28` (12) on the `StorageCart` pattern,
    `POSITION_VOLUME = 48^3`; stateful `Trailer` (contents, lead, dispatch stamp, door state;
    persists day over day per point 3); `LoadPallet` (mixed SKUs, volumetric perfect-pack,
    next-fit, contiguous lot merging). **Capacity quantizes per PALLET, not per trailer —
    inter-pallet waste is real and tested** (a durable lesson, see the description field).
    `Inbound/priorities.py` — the two registries designed at points 26-29 (Global over trailers,
    Local over items), a frozen `DockContext` (the future space-signal's home, point 27),
    `bounded_order` as the `k_cap` analog, proven inert under `'fifo'` and biting under a real
    key; no `INHERIT` entry, by decision (point 26). `Inbound/transit.py` — `TrailerTransit`
    mirrors `BatchTransit`'s seam exactly (point 44); release emits one delivery per contiguous
    per-trailer lot, never merged across trailers — each portion packing on its own IS the
    `inbound_split` model realized structurally (point 1's "inbound_split" phrase now has code).
    The ledger debits every portion to zero.
47. **What the ticket left as an implementation choice, taken and documented, not a reversal of
    any decision**: unload+pack stay charged as the EXISTING per-pack receiving cost — a
    separate pack-time cost model (the literal cost-side half of point 2's pack-at-unload
    overturn) is a future knob, not built in this pass. The STRUCTURAL half of point 2 (packing
    now happens per-portion, at release/unload rather than as one lump at trailer arrival) IS
    realized by `TrailerTransit.release`'s per-portion packing (point 46). A trailer waits for
    nothing (anything loading departs at release); doors are BOOKKEEPING in v1 — a throttle
    would invent staffing physics the shift decision (point 39) declined; the census and knob
    (`INBOUND_DOCK_DOORS`) exist for the future policies that make doors bite.
48. **Clock, provenance, knobs**: leads are seconds on the absolute clock (minutes-authored,
    `INBOUND_TRAILER_LEAD_MINUTES`, converted once at the spec seam); the runner passes
    `arm_clock` into `check_reorders(now_s=)`, stowed on a manager field so every phase stays
    arg-free; **a positive lead with no clock fails SAFE** (merchandise waits, never silently
    drops). `'trailer'` is the declared fourth `PutawayItem` source (point 1's stage list now
    has a fourth producer); both provenance pins flipped as planned (the unknown-source probe
    now uses `'teleport'`). Receiving's `DockSpec` gains `('reorder','trailer')` sources when
    both features are on. `INBOUND_TRAILER_TYPE` (`None` = structurally absent — the whole
    feature is inert until a type is named, same discipline as every other flag in this effort),
    `INBOUND_DOCK_DOORS`, the two `INBOUND_*_POLICY` knobs and the bound (point 29) ride one
    call-time `inbound_spec()` on the `recv_crew_spec` pattern (point 6, point 28), carried in
    the worker payload and built/bound by the driver (the broker rule, point 6). CLI flags and
    run-spec recording are explicitly deferred to the first sweep. 12 new pipeline tests
    (loading arithmetic, splits, leads, the bound, provenance, conservation, per-portion packing
    vs packer ground truth); 1,752 unit+integration + 6 receiving-e2e untouched.

**How to apply (trailer pipeline):** read `Inbound/trailer.py`, `Inbound/priorities.py`,
`Inbound/transit.py` directly — they are the production code for every design decision in points
1, 2 (structural half only, see point 47), 12-14, 26-29, and 30-33 above. The feature stays
byte-identical while `INBOUND_TRAILER_TYPE` is unset; flipping it on is the only way any of this
code executes.

**The drain-or-cap shift EXECUTED (2026-08-26, fourteenth ticket, same day, commit `900baa7`;
the map CLOSED 2026-08-27, commit `403e590`):**

49. **Ticket 15 built**: one mode flag, `SHIFT_DRAIN_OR_CAP` (default OFF = byte-identical),
    riding the working-day record (`work_day_spec`) so the mode and the day it caps against
    cannot disagree in a worker. **The cap IMPLIES the cut** — mode-on forces `_cut_at_day_end`
    rather than trusting two flags to agree (a cap without carry would lose demand);
    `roll_over_unpicked` stays independent since two of its three causes have no day boundary in
    sight. **One site-wide boundary**: the receiving crew shares the cap when the mode is on;
    its own day knobs remain the flag-off configuration (point 39).
50. **The end instant is pure kernel arithmetic** — `timeline.shift_end(cap_end, last_finish,
    drained)`: a drain before the cap ends the shift when the crews finish ("off the clock");
    standing work, or START-gate overtime finishing past the whistle, ends it at the cap —
    whichever came FIRST. Days stay origin-aligned exactly as decided (point 40): an early
    drain stops the labour, never the calendar. **The judgment lives in the runner's per-day
    ledger**, closed at the first batch of the next day: drained = nothing cut all day (pick
    carry AND `recv_cut`) and no standing work (put queues + held + the dock floor + carried
    demand — never the lead queue, per point 38). Reported as a `[shift]` log line per day — a
    REPORT, never a scheduler, so no schema change. `Tests/unit/test_shift_end.py` pins the
    drain-early, capped, overtime-past-the-whistle, and exact-boundary cases, plus source-pins
    on the forced-cut and shared-receiving-day implications. 1,758 unit+integration + 6
    receiving-e2e untouched flag-off.
51. **A durable placement lesson from the landing**: the per-day ledger reads that batch's
    `put_clock`, which is computed LATE in the batch-loop iteration — the shift-judgment block
    must live at the loop TAIL, where every clock and column for the batch is already final.
    Placing it earlier reads a stale clock silently (no error).

**The map CLOSED (2026-08-27, commit `403e590`)**: all fifteen tickets on
`.scratch/inbound-groundwork/map.md` are resolved; "Not yet specified" is now empty. The fog
remnants named at earlier points in this memory (day-over-day `--resume` for standing trailers,
point 3 above; the unload seam's warehouse-space signal, point 27's `ctx`; dock backpressure,
points 14/47's bookkeeping-doors choice) moved to the map's **Out of scope** section as
signposts for the FUTURE inbound-optimization effort — that effort starts there, not from a
residual build list. Nothing described as "still a decision, not code" anywhere earlier in this
memory remains true as of 2026-08-27; every such sentence above has been annotated at its
original location rather than deleted, per this store's own history-preservation rule.
**That future effort now EXISTS**: charted 2026-08-27 as `.scratch/inbound-optimization/map.md`
(commit `25bfdf7`) — a new wayfinder map, git-tracked and canonical, so its charter (yard as the
canonical term replacing parking lot, the yard-priority/dock-priority split, no deferral, real
doors, drain-quantized decisions over an event-stamped timeline, seeded per-trailer leads,
standing-demand-only forecasts, arm-local caching, refusal-until-clean resume, the phased
top-k funnel) lives there, not duplicated here.

See also [[putaway-seams-for-inbound]], [[receiving-is-its-own-crew]],
[[one-clock-one-speed-one-config]], [[working-day-clock-plan-corrections]],
[[stock-plan-overrides-packing]], [[cut-is-a-level-not-a-flow]],
[[carryover-two-producers-one-key]], [[config-knob-has-five-seams]].
