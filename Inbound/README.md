# Inbound

The receiving side of the site: the dock, unloading, and packing — with the trailer model to
follow. Split out of `Warehouse/` so the two domains meet only at the Inventory Manager's
broker seam.

## What belongs here

- **Receiving mechanics**: the `Dock` (spec, crew clocks, standing merchandise), the unload
  cost model, the packer and its `LoadPlan` record.
- **Coming next** (decided, not yet built): `trailer.py` (`Trailer53`/`Trailer28` config types,
  stateful `Trailer` instances, load pallets) and `priorities.py` (the global/local policy
  registries mirroring `put_policy`).

## What does NOT belong here

- **Anything that reads `CONFIG`.** Policy/spec selection is the driver's job
  (`Optimization/simdriver/`); this package receives constructed objects.
- **Imports of Warehouse machinery.** Value layers only (`wh_kernel`, `wh_layout`,
  `wh_catalog`, `wh_operations`); the boundaries in `context/architecture.yml` enforce it in
  both directions — nothing in `Warehouse/` imports this package either.
- **The put/pick side.** Putting, storing and picking are the warehouse; packs cross the seam
  through the manager's `_queue` entry and never come back.
