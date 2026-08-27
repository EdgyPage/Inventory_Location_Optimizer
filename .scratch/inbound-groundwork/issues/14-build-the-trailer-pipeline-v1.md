# Build the trailer pipeline v1

Type: task
Status: resolved
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

## Answer

EXECUTED 2026-08-26, commit `b7a0ad3`. Everything the decisions specified, plus the
implementation choices they left open (each documented in the code):

1. **Objects**: `Inbound/trailer.py` — `TrailerType`/`Trailer53` (26)/`Trailer28` (12) on the
   StorageCart pattern, `POSITION_VOLUME = 48^3`; stateful `Trailer` (contents, lead,
   dispatch stamp, door state; persists); `LoadPallet` (mixed SKUs, volumetric perfect-pack,
   next-fit, contiguous lot merging). Capacity quantizes per PALLET, not per trailer —
   inter-pallet waste is real and tested.
2. **Priorities**: `Inbound/priorities.py` — two registries on `put_policy`'s contract,
   frozen `DockContext` (the space signal's future home), `bounded_order` = the k_cap
   analog ("of the bound longest-waiting, take the best"), proven inert under fifo and
   biting under a real key. No INHERIT, by decision.
3. **Transit**: `Inbound/transit.py` — `TrailerTransit` mirrors `BatchTransit`'s seam
   exactly (`SOURCE`/`dispatch`/`advance`/`release`/`depth`/`merchandise`/`snapshot`), so
   phase bodies cannot tell which is bound. Release emits one delivery per contiguous
   per-trailer lot — never merged across trailers (each portion packing on its own IS the
   split model); the ledger debits every portion to zero.
4. **Clock**: leads are seconds on the absolute clock; the runner passes `arm_clock` into
   `check_reorders(now_s=)`, stowed as a manager field so every phase stays arg-free (the
   ratchet). A positive lead with no clock fails SAFE — merchandise waits.
5. **Provenance**: `'trailer'` is the declared fourth `PutawayItem` source; both provenance
   pins flipped exactly as this ticket planned (the unknown-source probe now uses
   'teleport'; the producer sweep reads `Inbound.transit` too). Receiving's `DockSpec`
   gains `('reorder','trailer')` sources when both features are on.
6. **Knobs**: `INBOUND_TRAILER_TYPE` (None = structurally absent), `INBOUND_DOCK_DOORS`,
   `INBOUND_TRAILER_LEAD_MINUTES` (converted once at the spec seam), the two policies and
   the bound — one call-time `inbound_spec()` on the `recv_crew_spec` pattern, carried in
   the worker payload, built and bound by the driver (broker rule).

Implementation choices the decisions left open, taken and documented: a trailer waits for
nothing (anything loading departs at release); doors are BOOKKEEPING in v1 (a throttle would
invent staffing physics the shift decision declined — the census and knob exist for the
policies that make them bite); unload+pack stay charged as the existing per-pack receiving
cost (a separate pack-time model is a future knob); CLI flags and run-spec recording arrive
with the first sweep, per the objects ticket.

Verification: 12 new pipeline tests (loading arithmetic, splits, leads, bound, provenance,
conservation, per-portion packing vs packer ground truth); 1,752 unit+integration + 6
receiving-e2e untouched; all gates green with the three new modules in the graph.
