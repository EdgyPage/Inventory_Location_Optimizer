---
name: putaway-seams-for-inbound
description: the seams built 2026-08-24 so an inbound trailer/dock feature lands without re-cutting the pick path — and what is still missing
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T13:14:17.369Z
---

Nine byte-identical commits (`d45364b..266c324`, 2026-08-24) decoupled put-away from picking
ahead of a trailer/inbound-sorter feature. Intended shape, confirmed with the user: a
**separate inbound crew** (two worker pools, one timeline; bins and aisles are the contended
resource, not people), **myopic and forecasting sorters as two arms of one experiment**, and
**scheduled arrivals with a finite dock count**.

**Seams now available:**

- `check_reorders` is six callable phases (`_tick_batch`, `reclaim_emptied_bins`,
  `_advance_lead_queue`, `_fire_reorders`, `_release_arrivals`, `_drain_putaway`). The ORDER
  is behaviour, pinned by `Tests/unit/test_reorder_phases.py`.
- `PutawayItem(unit, source)` rides in `_stock_queue`; a trailer is the fourth source.
- `_stock(budget=)` — a dock/crew cap. Ranked waves are ATOMIC: the budget is spent per
  group, checked before `place_wave`, because that call mutates aisle running balances.
- `_emptied_at[id(bin)]` records WHEN a bin ran dry. Inert; the substrate for "upcoming slots".
- `Warehouse/kernel/allocation.partition` — round-robin/LPT over any work type, zero-dependency.
- `Warehouse/kernel/timeline.epochs` — batch-local picker time onto one absolute axis.
- `Cell` NamedTuple + `reference_cell` — a fifth sweep axis is one field, not five unpacks.

**Still missing when the feature starts:** ~~put-away consumes NO simulated time~~ (closed
2026-08-24, see [[one-clock-one-speed-one-config]]); lead time is denominated in BATCHES not
seconds; `picker_id ∈ [0,k)` is assumed by `_group_events_by_picker` and
`picking_pct + traveling_pct == 1` by construction, so a second crew needs its own actor
space; bin reclaim still happens once, at the top of the next batch.

**The arrival seam now exists (2026-08-25, `c380a31`).** `Warehouse/operations/inbound.py`:
`receive(order, qty)` / `receive_all(order, deliveries)` wrap `viable_storage_units`
**unchanged** and return `LoadPlan`s carrying the counterfactual (`unsplit()`,
`split_penalty()`). A trailer model is a *producer* into `Inventory_Manager.inbound_split(sku,
qty) -> list[int] | None`, consulted at the lead-queue release; default None = whole delivery
= byte-identical. It must stay in the caller — `wh_operations → optimization` is a forbidden
edge, so anything reading `CONFIG` cannot live in `Warehouse/operations/`. Packing is per
delivery, which is the whole point: see [[stock-plan-overrides-packing]] for why the resulting
unit count moves in **both** directions.

**How to attach an observer:** rebind on the manager instance, as `BinRecorder.attach` does.
The whole pick→inventory boundary is three notifications and two reads, allowlisted in
`Tests/architecture/test_sim_inventory_boundary.py`. Deliberately NO listener registry — see
[[gpu-broker-dormant-not-for-placement]] for why unconsumed infra is not free here.

See also [[sim-time-unit-is-seconds-not-ms]], [[a-grant-is-not-an-output]].
