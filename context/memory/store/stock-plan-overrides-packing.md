---
name: stock-plan-overrides-packing
description: "an Order's stock_plan bypasses viable_storage_units' pallet/singleton logic entirely, so a planned catalogue's tier mix is decided at generation time — and it is why splitting a delivery can produce FEWER units"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T13:13:20.462Z
---

`viable_storage_units(order, quantity)` looks like a packer with a pallet-fill rule and a
singleton remainder. On a **planned** catalogue it is mostly not running that rule at all.
`Order.stock_plan` — a run-length list of `(is_singleton, qty_per_unit, count)` slots assigned
at warehouse-planning time — short-circuits the whole thing: slots are filled from the front
until the arrival is exhausted, and only a surplus beyond the plan's total falls through to
the default logic.

**Why it costs time when you forget.** Measured 2026-08-25 on a 200-SKU planned catalogue,
**200/200 orders carried a plan**. Set the reorder quantity to 40 and you get 2,178 pallets
against 34 singletons — so the store *cart* stream (singletons) is nearly empty and any
experiment about cart put-away is measuring almost nothing. The knob that actually moves the
tier mix is the generation-time plan, not the reorder quantity and not the packer.

**The consequence that surprised me.** Default packing is monotone under splitting — cut a
shipment up and you get the same units or more, verified over volumes 200–2400 and quantities
2–200. `stock_plan` is **not**. A whole arrival can run off the end of the plan's pallet slots
and spill into its singleton tail, while each half stops short of that tail and stays on
pallets. sku 21: `34 pallets + 26 singletons` whole, `40 pallets` split — twenty FEWER units.
So "splitting a delivery is worse" is false on exactly the catalogue we generate, and
`inbound.split_penalty` returns a **signed** number for that reason.

**How to check before assuming:** `getattr(order, 'stock_plan', None)`. Absent, the docstring's
examples are accurate; present, they describe a branch that never runs.

See [[putaway-seams-for-inbound]], [[build-inventory-tests-no-reorders]].
