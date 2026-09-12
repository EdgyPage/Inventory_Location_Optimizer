# Inbound

The receiving side of the site: trailers, the yard and dock doors, unloading, and packing.
Split out of `Warehouse/` so the two domains meet only at the Inventory Manager's broker
seam.

## What belongs here

- **Receiving mechanics**: the `Dock` (spec, crew clocks, standing merchandise), the unload
  cost model, the packer and its `LoadPlan` record.
- **The trailer model**: `trailer.py` (`Trailer53`/`Trailer28` config types, stateful
  `Trailer` instances, load pallets) and `transit.py` (`TrailerTransit` on the order-port
  seam, serving each order's supplier lead at the ordering site before it loads; `YardTransit`,
  the standing yard where doors become real).
- **The dock's decision surfaces**: `priorities.py` (the global/local + yard/dock policy
  registries mirroring `put_policy`, the frozen `DockContext`) and `space.py` (the space
  timeline: the per-drain frozen `SpaceView` — current empties plus untimed predicted
  clears — that standing decisions read via `ctx.space`).
- **The site's coordinators** — the objects that sit ABOVE two inventory managers, which
  only this package may do (`wh_operations -> wh_inventory` is forbidden, and `Warehouse ->
  Inbound` is forbidden both ways, so nowhere under `Warehouse/` can serve two channels at
  once): `receiving.py` (`SiteReceiving` — one dock, one yard and the site receiving
  carry, N leaves through three ports, with the `{sku: leaf}` owner dict routing a mixed
  trailer and `SITE_PHASES` composing the same seven reorder phases with two of them
  site-scoped)
  and `putaway_pool.py` (`PutawayPool` — one crew of putters over both channels' segregated
  volume: the shared clock list, the day's division, the once-per-site-day reset and the
  site put carry). Both hold no merchandise and own no bins; they decide WHO works and
  WHEN, and reach each leaf through named public ports. `site_space.py` is the third and
  the smallest: one pure function composing the leaves' frozen space views into the one a
  drain reads, with its key set partitioned by regime. It is separate from `space.py`
  because that module's defining property is that it imports nothing from `Warehouse/`,
  and the composer needs `regime_of`.

## What does NOT belong here

- **Anything that reads `CONFIG`.** Policy/spec selection is the driver's job
  (`Optimization/simdriver/`); this package receives constructed objects.
- **Imports of Warehouse machinery.** Value layers only (`wh_kernel`, `wh_layout`,
  `wh_catalog`, `wh_operations`); the boundaries in `context/architecture.yml` enforce it in
  both directions — nothing in `Warehouse/` imports this package either.
- **The put/pick side.** Putting, storing and picking are the warehouse; packs cross the seam
  through the manager's `_queue` entry and never come back. `putaway_pool.py` is not an
  exception: it schedules the SITE's putters across two managers and places nothing itself —
  every bin write still happens inside a manager, behind `drain_putaway`.
