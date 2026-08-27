# Boundary fact sweep — digest (2026-08-26, four read-only agents)

Condensed anchors grounding "Draw the Inbound package boundary". Repo-relative only.

## Architecture layer
- 23 layers, longest-match-wins; 33 directed forbid pairs (NOT the "19" CLAUDE.md cites) — context/architecture.yml:14-105
- Boundary check covers only kind=='imports' edges — context/arch/verify_architecture.py:128-142
- A new top-level package is INVISIBLE until added to GRAPH_ROOTS — context/arch/extract.py:44; edges to non-node targets silently dropped — extract.py:448-457
- Layer entry must come AFTER files exist (zero-match prefix is an error) — verify_architecture.py:76-99; unmapped files land layer "unclassified", which check_files rejects — extract.py:710, verify_architecture.py:259-261
- architecture.yml:72-87 designed this seam as "a future Warehouse/inbound/" ON wh_operations — the top-level Inbound/ is a different shape; migrating inbound.py/unload.py out of wh_operations sheds its 4 forbids unless mirrored
- Warehouse→Inbound is unfenced today except from wh_kernel and physical.py (the two dst-wildcards)
- Hardcoded package rosters to update: context/memory/verify_memory.py:37-38 (_TOP_DIRS), Tests/calltree/calltree_tracer.py:82, Tests/calltree/calltree_memory.py:53, Tests/unit/test_allocation.py:179, test_timeline.py:167, test_speed_profile.py:142+165, test_forward_pick_family.py:134, test_putaway_item_consumers.py:138, Tests/architecture/test_schema_compatibility.py:624

## Import census
- dock.py (in inventory/, not operations/) imports only stdlib + kernel.crew_clock — Warehouse/inventory/dock.py:67-72; Dock:100, DockSpec:76
- unload.py imports kernel.cost_model + operations.putaway; UnloadCost defaults are PutawayCost class attrs BY REFERENCE (anti-drift, "the whole point") — Warehouse/operations/unload.py:47,65-69
- operations/inbound.py imports only layout.Storage_Primitive; restates PALLET/SINGLETON/FULFILLMENT to avoid a put_queue edge — inbound.py:48-54; LoadPlan:57
- The putting side already imports the inbound side: inventory_reorder.py:16-17 → operations.{inbound,unload}; manager lazily imports Dock in enable_receiving — Inventory_Management.py:930-932
- PutawayItem PUTAWAY_SOURCES=('intake','reorder','reslot'); 'trailer' is test-FORBIDDEN — inventory_common.py:62-65; Tests/unit/test_putaway_provenance.py:99

## Driver wiring
- CLI→loop: run_simulation main → scenario → cells (_apply_cell mutates CONFIG) → workunits payload (:355-367) → spawn worker → strategy_runner:345
- Manager construction in the worker — strategy_runner.py:531,547; everything injected: placement via strategy build hooks (:101-116,584-593), configure_zoning:558, enable_putaway_timing:689, put_queues setter:652-667, enable_receiving(DockSpec):697-715, BinRecorder wrap:553
- inbound_split has NO production wiring — only tests assign it (Inventory_Management.py:255 defaults None; Tests/unit/test_inbound_load_plan.py)
- put_crew_spec reads settings attrs directly (CONFIG writes silently ignored — the TRAP, sim_config.py:418-432); recv_crew_spec is the blessed call-time CONFIG pattern (sim_config.py:335)
- strategy_runner.py:936-938 reads private mgr._lead_queue for replay rows — needs an accessor when transit moves
- Crew/roster/WorkDay/uid-block state lives in DRIVER locals (:639-715), not on the manager

## Phase order / lead queue
- SEVEN phases (not six): _tick_batch, reclaim_emptied_bins, _advance_lead_queue, _fire_reorders, _release_arrivals, _receive, _drain_putaway — inventory_reorder.py:549-556; deadlines reach only the last two
- Phase ratchet pins the seven names as literal `self.<name>(` calls in check_reorders source, arg-free callable on a fresh manager — Tests/unit/test_reorder_phases.py:137-160; delegation INSIDE a phase body passes, relocation of the call site fails
- Lead-queue entry = mutable [sku, qty, remaining_lead] list; state on the manager (Inventory_Management.py:245-249), methods on ReorderMixin; in_transit_qty sums entry[1] (:561-568)
- _release_to_stock is strictly per-SKU (self._originals[sku].reorder()); consults inbound_split; a multi-SKU trailer has no single-call packing path today — inventory_reorder.py:274-318
- _receive bypasses _admit deliberately (re-admit would re-stamp + re-divert forever); _queue was split out of _admit precisely for already-stamped handoffs — inventory_reorder.py:456-501; Inventory_Management.py:1475-1489
- Dock intercept inside _admit keeps _queued_qty credited (reorder ledger zero-edit) — Inventory_Management.py:1440-1473
- Batch resume REFUSED when a dock is configured — Optimization/simdriver/workunits.py:68-78; standing dock contents are in no checkpoint
- LoadPlans deliberately NOT retained (drain_inbound buffer removed as a memory leak); note_arrivals counts only
- Batch lead-time denomination is a pinned decision whose code note must keep the word "trailer" — dock.py + Tests/unit/test_lead_time_unit.py
