# Build the trailer pipeline v1

Type: task
Status: open
Blocked by: 13

## Question

Execute the v1 trailer pipeline, flag-gated (`INBOUND_TRAILER_TYPE` selection implies on;
absent = byte-identical). The decided design, across tickets 01/02/06/07: `Inbound/trailer.py`
— `TrailerType` config classes `Trailer53` (26 positions) / `Trailer28` (12, both overridable,
48-inch footprint arithmetic, single-stacked), stateful `Trailer` instances (contents,
remaining volume, optional lead, arrival stamp, door state; persist day over day; pack plan
held only while standing), `LoadPallet` (mixable, volumetric 48^3 perfect-packing fit,
next-fit closes a pallet; FIFO loading keeps SKU lots contiguous); `Inbound/priorities.py` —
the GLOBAL (trailer, both dock moments) and LOCAL (load pallet) key registries on the
`put_policy` contract, frozen per-drain `ctx`, FIFO v1, plus the k_cap-analog ordering bound
(trailers, unbounded default, inert under FIFO); the parking lot and `INBOUND_DOCK_DOORS`
staging; per-trailer leads (`INBOUND_TRAILER_LEAD_MINUTES`, default 0 = instant, seconds
internally); the `'trailer'` `PutawayItem` source declared (flip `test_putaway_provenance`'s
forbidden-source pin to an allowed-and-stamped pin); all knobs on one call-time inbound spec
through the five seams; equivalence tests proving flag-off byte-identity and the FIFO-on path
against hand-computed small cases.

Done when: gates green; the flag-off digest is byte-identical; the new path has its own tests;
nothing committed without the user's go-ahead.
