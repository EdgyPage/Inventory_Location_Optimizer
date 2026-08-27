# Inventory Location Optimizer

A warehouse-placement simulator: one modeled **site** where inbound freight is received and a
warehouse puts, stores and picks it, compared across competing placement and scheduling policies.
This glossary is the canonical vocabulary; `context/` (anchors, flows, INDEX.md) is a different,
verifier-backed system and shares nothing with this file but the name.

## Language

### The site

**Site**:
The whole modeled facility — inbound plus warehouse — on one absolute clock.
_Avoid_: facility, building

**Inbound**:
The receiving side of the site: trailers, the dock, unloading and packing. Holds reorders in
transit but does not decide reordering.
_Avoid_: receiving (as a domain name; the crew keeps the name)

**Warehouse**:
The storage-and-fulfillment side of the site: putting, storing, picking. Narrower than the
`Warehouse/` package, whose name is historical.

**Ordering site**:
The unmodeled origin that loads and dispatches trailers. A reorder is a notification sent to it
automatically; optimizing its behaviour is out of scope.
_Avoid_: supplier, vendor, DC

**Inventory manager**:
The site's broker. Tracks stock position, sends orders toward the ordering site, receives packs
from inbound, and feeds putting — coordinating the parts without belonging to any of them.

### Inbound

**Trailer**:
The vehicle that carries loose items from the ordering site to the dock. Capacity is physical —
items ride as if stacked on maximum-sized pallets. Replaces what the lead queue abstracted as a
floating quantity.

**Load**:
A trailer's contents: loose items, never packs.
_Avoid_: load plan (for packing — see Pack plan), shipment, delivery (for trailer contents)

**Dispatch**:
A trailer leaving the ordering site. v1 dispatches in strict FIFO reorder priority; when an item
does not fit the open trailer, a new trailer is started (next-fit) so FIFO order is preserved.

**Lead**:
The delay, possibly zero, between a trailer's dispatch and its arrival in the parking lot.
_Avoid_: lead time (for anything but this), transit time

**Parking lot**:
Where arrived trailers wait for a dock door. Unbounded.
_Avoid_: yard, staging area (that is the doors)

**Dock door**:
One of finitely many staging slots at the dock. A trailer must hold a door to be unloaded.

**Arrival**:
A trailer reaching the site. Age is stamped at arrival, never at unload.

**Dock**:
The receiving area — its doors and the crew working them. Its depth is the report; it refuses
nothing.

**Unloading**:
Taking loose items off an arrived trailer. Receiving-crew work; costs time.

**Packing**:
Converting a trailer's unloaded items into packs. Receiving-crew work at the dock; costs time.
The pack plan is fixed by the trailer's full contents — interruption pauses packing, never
changes it.
_Avoid_: cartize, palletize (as verbs for this stage)

**Pack**:
One storage unit a person lifts — pallet, carton or singleton. What packing produces and
putting consumes.
_Avoid_: load pallet (that is the transport grouping, not a pack)

**Load pallet**:
The transport grouping loose items ride on inside a trailer. May mix SKUs that fit; what the
receiving crew pulls when unloading. Not a pack — packing has not happened yet.

**Pack plan**:
The packs a trailer's contents become, determined per trailer at arrival.
_Avoid_: load plan

**Receiving crew**:
The inbound workforce. One crew does both unloading and packing, on its own hours.

### Priorities

**Global priority**:
The order in which arrived trailers are worked at the dock. v1: FIFO by arrival.

**Local priority**:
The order in which one trailer's items are unloaded and packed. v1: FIFO.

### Warehouse

**Putting**:
Moving packed freight from the inbound handoff into bins via the put queues and placement pools.

**Cart**:
The volume-bounded vehicle a putter or picker pushes; a channel's putter and picker push the
same cart.

**Picking**:
Retrieving units from bins to fill demand batches.

### Measurement

**Stamp**:
A point on a clock. Never a length of time.

**Span**:
Elapsed time between two stamps.

**Level**:
A standing quantity, re-measured each snapshot. Never summed across snapshots.

**Flow**:
An increment belonging to one row's grain; additive. A count is a flow of discrete things.

**Rate**:
A flow divided by a span; its denominator is part of its meaning.

**Score**:
A policy-relative ordering value, comparable only within its policy.

**Share**:
A proportion of a stated whole.

**Grain**:
The per-what of one row: per batch, per arm, per picker, session-cumulative.

**Clock**:
Which axis a time value lives on — sim-modeled, wall-compute, or batch-denominated. Two values
on different clocks never form a ratio.

**Unit of account**:
What a count counts — packs or merchandise pieces. Never added across accounts.

### Day-over-day

**Working day**:
The calendar boundary for every crew. Site state — a half-unloaded trailer, dock depth, queued
packs — is preserved across the boundary, never reset or re-derived.

**Shift**:
One working stretch of the site's crews on the shared clock. It ends when no standing work
remains and none is still scheduled to release, or at a global cap, whichever comes first;
days stay origin-aligned — the shift timers happen at the same time every day.
_Avoid_: shift (for the reporting frame that merely labels hours)

**Standing work**:
The labor a shift can end on: released-but-unpicked demand, queued puts and held items, and
the dock floor. Merchandise in transit is calendar, not standing work.
