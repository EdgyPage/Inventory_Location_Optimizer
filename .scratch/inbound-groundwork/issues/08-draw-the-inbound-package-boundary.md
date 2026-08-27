# Draw the Inbound package boundary

Type: grilling
Status: resolved

## Question

`Inbound/` is a new top-level package beside `Warehouse/` (decided in "Name the inbound
pipeline"). Decide its shape: which `Warehouse` layers Inbound may import (`kernel`? `layout`?
`catalog`?); whether Warehouse may know Inbound exists at all or the driver (`Optimization/`)
wires both sides of the handoff; who owns the lead queue once it is the transit leg (it lives in
`Warehouse/inventory` today, inside the manager's phase order pinned by
`Tests/unit/test_reorder_phases.py`); which existing modules migrate (`inventory/dock.py`,
`operations/unload.py`, `operations/inbound.py`) versus stay; and how
`context/architecture.yml` records the new boundaries (19 exist today). Constraints:
`wh_operations → optimization` stays forbidden, anything reading `CONFIG` stays caller-side,
byte-identical while the seams are inert. Consult `codebase-design`.

## Answer

Resolved over two grilling rounds plus a four-reader fact sweep (digest:
`.scratch/inbound-groundwork/assets/boundary-facts.md`). The decisions:

1. **The Inventory Manager is the site's broker — by injection, never by import.** Inbound sends
   packs to the warehouse *through* the manager; the warehouse's reorder needs flow out through
   the manager. Hub-ness is realized the way the manager already holds assignment functions
   (`mgr.placement`, set by strategy build hooks): the driver builds Inbound's objects from
   CONFIG specs (the `recv_crew_spec` call-time pattern — NOT the `put_crew_spec`
   settings-snapshot trap) and binds them via `enable_*` binders. `enable_receiving`'s lazy
   `Dock` import becomes driver-side construction.
2. **Import edges: zero between `Inbound/` and Warehouse's stateful layers, both directions.**
   Inbound imports only value layers: `wh_kernel`, `wh_layout`, `wh_catalog`, plus
   `wh_operations` (the `UnloadCost` → `PutawayCost` by-reference anti-drift coupling stays).
   No Warehouse module imports Inbound — not even the manager.
3. **Two ports on the broker.** *Order port*: fired reorders go to an injected transit model —
   v1 the trailer pipeline, future a modeled upstream ordering node, same port (the preemptive
   seam). *Receipt port*: stamped packs enter via the existing `_queue` entry (built for exactly
   this — `_receive` bypasses `_admit` by design), plus a read-only census surface for
   reporting. Trailers, dock, crews, priorities: all hidden behind the seam.
4. **Inbound owns transit** (Q4=b): the lead queue's timing machinery moves behind the order
   port; the manager keeps only its scalar ledger (`_deferred_qty` credit/debit — the property
   that made the dock landing zero-edit). The seven `check_reorders` phase WRAPPERS stay on the
   manager and delegate inside — the phase ratchet pins `self._phase(` call sites, not bodies,
   so no ratchet re-cut. The `strategy_runner.py:936` private `_lead_queue` read gets an accessor.
5. **Flat package layout**: `Inbound/trailer.py`, `dock.py`, `unload.py` (unload cost/speed —
   handling-seconds only until a travel term exists), `pack.py` (packing logic), `priorities.py`.
   Migration set: `inventory/dock.py` → `Inbound/dock.py`; `operations/unload.py` →
   `Inbound/unload.py`; `operations/inbound.py` → `Inbound/pack.py`. `put_queue.py` and the
   reorder ledger stay in Warehouse. `PutawayItem`'s `'trailer'` source stays test-forbidden
   until the implementation wave declares it.
6. **Recording is mechanical and sequenced** (files first — a layer matching zero files is a
   verifier error): add `Inbound` to `GRAPH_ROOTS` (`context/arch/extract.py:44`, without which
   the package is INVISIBLE — edges silently dropped), add the `inbound` layer, mirror the four
   forbids `wh_operations` sheds, add the new both-direction forbids (legal once the graph
   satisfies them), update the ~8 hardcoded package rosters (`context/memory/verify_memory.py`
   `_TOP_DIRS`, calltree tier, unit-test rosters, `test_schema_compatibility.py:624`), and
   extend `test_putaway_provenance`'s file scan to Inbound modules so the single-admission
   guard still covers the migrated code.

Known ground conditions recorded for the implementation wave: batch resume is already refused
when a dock is configured (day-over-day persistence lands on ground that opted out of
checkpointing); `LoadPlan`s are deliberately not retained (a Trailer pinning packs for hundreds
of batches recreates a removed memory leak).

Graduated: [Land the Inbound package skeleton](09-land-the-inbound-package-skeleton.md) (task).
