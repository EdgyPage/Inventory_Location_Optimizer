# Inbound

The receiving side of the site: trailers, the yard and dock doors, unloading, and packing.
Split out of `Warehouse/` so the two domains meet only at the Inventory Manager's broker
seam.

## What belongs here

- **Receiving mechanics**: the `Dock` (spec, crew clocks, standing merchandise), the unload
  cost model, the packer and its `LoadPlan` record.
- **The trailer model**: `trailer.py` (`Trailer53`/`Trailer28` config types, stateful
  `Trailer` instances, load pallets) and `transit.py` (`TrailerTransit` on the order-port
  seam; `YardTransit`, the standing yard where doors become real).
- **The dock's decision surfaces**: `priorities.py` (the global/local + yard/dock policy
  registries mirroring `put_policy`, the frozen `DockContext`) and `space.py` (the space
  timeline: the per-drain frozen `SpaceView` — current empties plus untimed predicted
  clears — that standing decisions read via `ctx.space`).

## What does NOT belong here

- **Anything that reads `CONFIG`.** Policy/spec selection is the driver's job
  (`Optimization/simdriver/`); this package receives constructed objects.
- **Imports of Warehouse machinery.** Value layers only (`wh_kernel`, `wh_layout`,
  `wh_catalog`, `wh_operations`); the boundaries in `context/architecture.yml` enforce it in
  both directions — nothing in `Warehouse/` imports this package either.
- **The put/pick side.** Putting, storing and picking are the warehouse; packs cross the seam
  through the manager's `_queue` entry and never come back.
