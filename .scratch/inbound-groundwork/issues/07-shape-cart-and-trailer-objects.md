# Shape Cart and Trailer objects and their knobs

Type: grilling
Status: resolved
Blocked by: 01

## Question

Design the Trailer and inbound put-cart objects on the `StorageCart` pattern
(`Warehouse/layout/Storage_Primitive.py` — stateless class-as-config, `capacity()` in volume,
selected via config): what each declares, how trailer capacity relates to cart capacity, and
how their knobs are authored in `settings.py` (threading the five seams —
`config-knob-has-five-seams` — and the four-site guard `Tests/unit/test_run_shaping_params.py`).
Decided while charting: volume-denominated like store pick carts, leaving room to optimize on
trailer size later; no physical-volume optimization in this effort.

## Context update (post "Name the inbound pipeline")

Trailers carry LOOSE items, physically modeled as stacked on maximum-sized pallets — capacity is
physical/pallet-position, not pack-count and not `capacity()`-volume-as-packed. The put/pick cart
already exists as the put queue's `cart` axis and is unchanged; this ticket's cart half is mostly
confirming reuse. The Trailer object lands in the top-level `Inbound/` package (ticket 01), not
`layout/`; the StorageCart pattern remains the style anchor. Canonical terms: root `CONTEXT.md`.

## Context update (post "Draw the Inbound package boundary")

The Trailer lands in `Inbound/trailer.py`, constructed driver-side from CONFIG specs and injected
— never imported by Warehouse code. `LoadPlan` non-retention is a hard constraint: a Trailer that
pins packs across batches recreates a removed memory leak. Facts: `../assets/boundary-facts.md`.

## Context update (post "Choose the lead-time denomination")

The Trailer gains an optional lead (minutes-authored, seconds-stored, default zero). The door
count joins the knobs (finite staging slots at the dock). The legacy per-SKU batch lead stays
untouched behind the flag for archive comparability.

## Context update (post "Design the two-level priority seams")

The knob set this ticket threads is now settled: `INBOUND_GLOBAL_POLICY` / `INBOUND_LOCAL_POLICY`
(default 'fifo'), the dock's k_cap-analog ordering bound (trailers, default unbounded, inert
under FIFO), the door count, the trailer's optional lead (minutes-authored), and cart sizes.
All ride the `recv_crew_spec` call-time pattern and the five-seam threading.

## Answer

Resolved in one round; three additions from the user reshaped Q1, the rest confirmed:

1. **Type/instance split on the StorageCart pattern**: `TrailerType` (stateless class-as-config)
   with two concrete types — `Trailer53` and `Trailer28` — whose `pallet_positions` derive from
   footprint arithmetic at the 48-inch square pallet (`PALLET_FOOTPRINT`): 53 ft = 13 rows x 2
   across = 26 positions; 28 ft = 6 x 2 = 12. Class attributes, overridable; single-stacked
   (one pallet tier). `Trailer` is the stateful runtime instance: contents, remaining volume,
   optional lead, arrival stamp, door/staged state — it persists day over day. Its pack plan is
   computed at arrival, held while the trailer stands, dropped when fully unloaded (bounded by
   dock population; respects the removed-LoadPlan-leak constraint).
2. **NEW ENTITY: the load pallet** — the transport grouping loose items ride on in a trailer.
   May MIX SKUs that fit; volumetric fit at 48^3 with the perfect-packing assumption verbatim
   from `StorageCart.add_from_bin`; next-fit closes a pallet, pallets fill positions, next-fit
   opens a new trailer (all decided earlier). FIFO loading makes SKU lots CONTIGUOUS by
   construction ("three pallets of x were loaded"). Canonical name chosen to kill the collision
   with the pallet PACK — glossary entry added to CONTEXT.md.
3. **Refinement flowing to the seams design**: the local-priority key's natural candidate is the
   LOAD PALLET (the thing a crew pulls), degenerating to the SKU lot on single-SKU pallets. A
   sharpening of ticket 06's "key over items", recorded as a comment there, not a reversal.
4. **Cart: pure reuse** — no inbound cart class, no new cart knob; `TrailerType` following the
   pattern satisfies "similar to store pick carts".
5. **Knobs**: `INBOUND_TRAILER_TYPE = '53'` (type selection, the cart-class-as-config
   precedent — positions ride the class, no raw positions integer), `INBOUND_DOCK_DOORS`,
   `INBOUND_TRAILER_LEAD_MINUTES = 0`, joining ticket 06's policy knobs + bound. All on one
   call-time inbound spec (`recv_crew_spec` pattern) through the five seams incl.
   `workunits._shared`, recorded in the run spec; CLI flags arrive when first swept. All inert
   while the trailer flag is off. Mixed fleets (both types in one run) are future work the type
   seam permits — not v1.
