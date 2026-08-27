# Inbound groundwork

Label: wayfinder:map

## Destination

Landed on `develop`: (1) a v1 trailer pipeline — reorders loose-loaded into trailers FIFO at the
unmodeled ordering site (next-fit), dispatched FIFO, riding the lead queue as the transit leg,
unloaded and packed dock-side by the receiving crew, packs handed to warehouse putting — its
in-scope optimization seams (unloading, packing, putting; global/local priority) each a swappable
policy with FIFO/trivial as the only implemented one; (2) a terminology + column-semantics coherence pass
(convention pass AND semantic layer, everything) across `Warehouse/`, `Optimization/`, `Schema/`,
centered on the analysis path, so a column's meaning is declared in exactly one place; (3)
cart/trailer objects mirroring the `StorageCart` pattern, knobs authored in
`Optimization/config/settings.py`. Full inbound decision logic stays out — the seams make it addable.

## Notes

- **Execution override: ON.** This map's destination is code landed, not a spec. Once a ticket's
  governing decisions close, implementation graduates from fog into `task` tickets carried on this
  map. Absent that, tickets still resolve decisions, not deliverables.
- **Byte-identical discipline holds** (CLAUDE.md §2): every pipeline seam lands inert-by-default;
  anything that could perturb the store-only path needs an equivalence test.
- Memories every session should load: `putaway-seams-for-inbound`, `one-clock-one-speed-one-config`,
  `receiving-is-its-own-crew`, `working-day-clock-plan-corrections`, `stock-plan-overrides-packing`
  (all in `context/memory/store/`).
- Skills: `grilling` + `domain-modeling` on every HITL ticket; `codebase-design` on seam-interface
  tickets. `CONTEXT.md` (repo root) is created lazily as terms resolve — it is UNRELATED to
  `context/` (see `docs/agents/domain.md`).
- Anchor pattern for objects: `StorageCart`/`StoreCart` in `Warehouse/layout/Storage_Primitive.py`
  — stateless class-as-config, `capacity()` in volume, selected via `PickConfig.cart`.
- The resolved pipeline model and every canonical term: root `CONTEXT.md` (the glossary) and the
  answer on [Name the inbound pipeline](issues/01-name-the-inbound-pipeline.md). Headlines:
  loading is UNMODELED; packing is dock-side with the pack plan fixed per trailer; site state
  persists day over day; new code goes in a top-level `Inbound/` package.
- The broker pattern (from [Draw the Inbound package boundary](issues/08-draw-the-inbound-package-boundary.md)): the driver builds Inbound objects from CONFIG specs (`recv_crew_spec` pattern, never the `put_crew_spec` snapshot trap) and binds them on the manager; no Warehouse module imports Inbound, ever.
- Tracker conventions: `docs/agents/issue-tracker.md` (Wayfinding operations).

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Name the inbound pipeline](issues/01-name-the-inbound-pipeline.md): stage order reorder → (unmodeled load, next-fit FIFO) → dispatch → transit (= lead queue) → arrival → unload → pack → handoff → put; trailers carry LOOSE items; pack plan fixed per trailer (overturns dock.py's arrival-packing); site state persists day over day; new code in top-level `Inbound/`; glossary in root `CONTEXT.md`.
- [Draw the Inbound package boundary](issues/08-draw-the-inbound-package-boundary.md): the Inventory Manager is the site's broker BY INJECTION — zero import edges either way between `Inbound/` and Warehouse's stateful layers (Inbound imports value layers only); two ports (order out / receipt in); Inbound owns transit, the manager keeps the scalar ledger; phase wrappers delegate; flat `Inbound/` layout; migration set + recording checklist in the answer.
- [Choose the lead-time denomination](issues/02-choose-the-lead-time-denomination.md): no transit tick — a trailer's lead is an optional absolute-clock delay (minutes-authored, seconds-stored), default ZERO (instant arrival); queueing turns spatial (parking lot → finite dock doors); ledger stays batch-review untouched; flag-off = archive byte-identical; drain-or-cap ends the SHIFT (graduated to its own ticket); minutes are a surface, never the internal unit.
- [Set the column-semantics conventions](issues/03-set-the-column-semantics-conventions.md): nine canonical kinds (stamp/span/level/flow/count/rate/score/share/label); mandatory tags kind+unit+grain, conditional clock/null-meaning/discriminator/pair/id-space; renames happen at the LOGICAL layer, physical names frozen; staged enforcement (completeness gate, then use-assertions at the eight at-risk reads); forward suffix rules, minutes only as `*_MINUTES` knobs; the reporting frame renames so *shift* is free for the dispatcher.
- [Prototype the semantic-layer accessors](issues/05-prototype-the-semantic-layer-accessors.md): validated by reaction — `SEMANTICS` dict of `Col(...)` beside each family's DDL (submodule latitude granted); guards at BOTH altitudes (frame boundary + Quantity-layer use-assertions); refusal messages keep incident history; NO remainder — every column in every family gets tagged. Prototype: `assets/prototype_semantics.py`; sim_db alone is ~173 tags.
- [Design the two-level priority seams](issues/06-design-the-two-level-priority-seams.md): two registries in `Inbound/priorities.py` mirroring `put_policy` — one GLOBAL key over trailers (`key(trailer, ctx)`, both decision moments), one LOCAL key over items (never touches pack composition); frozen per-drain `ctx` is where the warehouse-space signal will arrive; `INBOUND_*_POLICY` knobs via the call-time spec pattern; the dock's k_cap-analog bound ships NOW (trailers, unbounded default, inert under FIFO).
- [Shape Cart and Trailer objects](issues/07-shape-cart-and-trailer-objects.md): `TrailerType` config classes `Trailer53` (26 positions) / `Trailer28` (12), positions from 48-inch footprint arithmetic, single-stacked; stateful `Trailer` instances persist day over day; NEW entity **load pallet** (transport grouping, may mix SKUs, contiguous by FIFO loading — named to kill the collision with the pallet PACK); cart is pure reuse; knob is type selection `INBOUND_TRAILER_TYPE='53'`; local-priority candidate sharpened to the load pallet.
- [Land the Inbound package skeleton](issues/09-land-the-inbound-package-skeleton.md): EXECUTED — `Inbound/` exists (dock/unload/pack migrated with history), the broker inversion is real (`enable_receiving(dock)`, `mgr.packer` + byte-identical default), the `inbound` layer + 16 boundaries verify against the actual graph, nine rosters + an AST provenance sweep extended; all gates and 2,088 tests green; uncommitted, awaiting review.
- [Set the shift-end rule](issues/10-set-the-shift-end-rule.md): the LAST decision — drain-or-cap is a new day-end MODE over the existing working-day knobs; standing work excludes the lead queue and drain requires no remaining releases; one site-wide shift when on; days origin-aligned ("the shift timers happen at the same time every day"); one default-off flag, cap = day length, cap implies cut/carry.
- [Build the semantic layer and its gates](issues/11-build-the-semantic-layer.md): EXECUTED — `Schema/semantics.py` (nine kinds, guards with scar-carrying refusals, use-assertions, completeness over `declared_shape()` verbatim), `sim_semantics.py` tagging the four epicenter tables (62 columns, logical `release_day`/`frame_index` live), and a stdlib-only ratchet that is sabotage-checked both directions; tags are metadata, byte-identical trivially.
- [Run the convention pass to zero remainder](issues/12-run-the-convention-pass.md): EXECUTED — every column of all six families tagged (~310 total), the gate enforces no-remainder itself, seven at-risk readers carry AST-validated `SEMANTIC_USES`, SPAN joined the additive kinds (labour sums), `REPORTING_FRAME_SECONDS` freed *shift*, and the two worst DDL prose blocks collapsed to pointers; 753/753 architecture+integration, shape ids unmoved.
- [Move transit behind the order port](issues/13-move-transit-behind-the-order-port.md): EXECUTED — `BatchTransit` owns lead-queue timing on the `mgr.transit` seam (manager keeps the scalar ledger); phase wrappers delegate, `_lead_queue` is a compatibility property, the replay viewer reads `transit_snapshot()`; the LEAD_TIME_UNIT note records the made pick; byte-identical, 1,740+6 tests green, committed.
- [Build the trailer pipeline v1](issues/14-build-the-trailer-pipeline-v1.md): EXECUTED — trailer.py/priorities.py/transit.py landed on the order-port seam; FIFO next-fit loads with contiguous lots, per-portion packing (the split realized), absolute-clock leads failing safe, the inert-under-fifo bound, the 'trailer' provenance, and one call-time spec; structurally absent until a type is named; 12 new tests + 1,752 untouched.
- [Audit the wrong-column incidents](issues/04-audit-the-wrong-column-incidents.md): 12 incident classes (all silent), 8 at-risk read sites; key surprise — every preventing semantic already exists as DDL-comment prose that provably rots, while the attachment hooks (Requires, named-query logical columns, Quantity.Source) are already built.

## Not yet specified

- **Dock backpressure / floor limit** — the parking lot is unbounded and the dock refuses
  nothing; the finite DOORS are now the natural backpressure surface, but whether anything
  upstream feels it is undecided; revisit if the trailer-pipeline build surfaces a need.
- **The unload seam's warehouse-space signal** — WHERE it arrives is now decided (a named
  view on the priority seams' frozen `ctx`); WHAT it computes from `_emptied_at` /
  upcoming-slot knowledge is still undesigned, and belongs to the future feature unless the
  v1 build needs a stub.
- **Day-over-day persistence mechanics** — how preserved inbound state (a half-unloaded
  trailer, parked trailers, dock depth, queued packs) interacts with checkpointing and
  `--resume` (batch-granular resume already refuses with a dock or carry on); sharpens inside
  the trailer-pipeline build.

## Out of scope

- **Inbound decision logic**: the trailer-assignment tradeoff (unit demand x empty-slot
  utilization), forecasting-sorter arm, optimal-split policies beyond FIFO. Seams only.
- **Space constraints and bin-conflict modeling** between crews — the future feature's job.
- **Physical-volume optimization of loads** — the objects leave room for it; nothing optimizes on
  it in this effort.
- **A non-Python config format** — `settings.py` stays the single authoring surface (Q3, charted).
- **Broad structural overhaul** of the three packages beyond inbound-touched surfaces (Q4, charted).
