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
The delay, possibly zero, between a trailer's dispatch and its arrival in the yard.
_Avoid_: lead time (for anything but this), transit time

**Lead distribution**:
The seeded per-trailer draw every lead comes from: lognormal, authored as a median with a
dimensionless spread. Zero spread is the constant-lead degenerate case; same seeds, same
lead schedule. Heterogeneous leads are what make arrival order differ from dispatch order.

**Yard**:
Where arrived trailers stand waiting for a dock door. Unbounded in capacity; standing too long
is what the fee proxy measures.
_Avoid_: parking lot, staging area (that is the doors)

**Dock door**:
One of finitely many staging slots at the dock. A trailer must hold a door to be unloaded.

**Standing yard**:
The mode where doors bind: a trailer holds its door across drains until fully unloaded, the
yard genuinely stands, and the yard and dock priorities decide who advances. Off, every
arrival drains whole the batch it lands.

**Arrival**:
A trailer reaching the site. Age is stamped at arrival, never at unload.

**Yard wait**:
The span from a trailer's arrival to its staging at a door.

**Door span**:
The span a trailer holds a dock door, staging to emptied.

**Detention span**:
The whole span a trailer is held on site — arrival to emptied, yard wait plus door span. What
the fee proxy accrues over: a policy cannot shed it by staging early and unloading slowly. A
trailer still standing when the run ends has a censored detention, counted to the run-end
clock and reported as such.
_Avoid_: yard days (that is detention denominated in days, not a separate quantity)

**Overage**:
The days a trailer's detention runs past the fee threshold: max(0, detention days − threshold).
Span-derived and reported, never converted to dollars, never mixed into labor hours; summed
over trailers it is the fee axis.

**Binding cut**:
A drain ending with inbound work still standing — unserved trailers in the yard, or staged
trailers with a remainder. Evidence the regime binds; across policy arms, fewer is better.

**Yard contention**:
Standing trailers versus free doors, read at drain start. Whether doors were the scarce
resource.

**Door utilization**:
The share of available door-time occupied — summed door spans against doors times the run's
span. An inspection read-out with no better direction: high can mean smooth flow or a choked
yard.

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

**Crew allocation**:
How the receiving crew spreads across staged trailers: split (door teams) or merged (one gang
working one trailer at a time in dock-priority order). Changes labor stamps and makespans,
never placement.

**Door team**:
The sub-crew dealt to one staged trailer for a drain, in dock-priority order; a freed team
reassigns to unmanned staged trailers first. What makes several trailers unload at once.

### Priorities

**Yard priority**:
The order in which standing trailers take a freed dock door. A freed door is always filled —
the dock cannot see beyond the yard, so holding a door open is never rational. v1: FIFO by
arrival.
_Avoid_: global priority (the retired single ranking over both dock moments)

**Dock priority**:
The order in which staged trailers are unloaded by the crew. The crew never idles while staged
work stands. v1: FIFO by arrival.

**Local priority**:
The order in which one trailer's items are unloaded and packed. v1: FIFO.

**Future work**:
The expected put and pick hours a drain's placements will generate — what inbound ordering
minimizes. Unload hours are order-invariant and excluded.

**Unload plan**:
The ordered list a yard or dock policy emits for one drain: a priority over which standing
loads meet this drain's bin pool. The clocks cut it into the served set; within the served
set the arm's own placement machinery assigns bins. A pure key is the degenerate plan.
_Avoid_: unload sequence (seats are not the plan's to give)

**Urgent set**:
The standing trailers close enough to crossing the fee threshold that a gated policy
serves them first, in arrival order, ahead of its plan. How close is the policy's one
dial (a horizon in days). The only lawful meeting point of fee pressure and future work:
scores never blend hours with days — a policy weighs both by gating, not by mixing.
_Avoid_: hybrid score (the rejected blended-scalar shape)

**Futuresight window**:
A declared-unlawful forecast: a policy arm reading w future batches of the precomputed
demand script, as an upper-bound reference only. w=∞ is the oracle. No real WMS has this,
so no recommendable policy may.

### Space

**Space timeline**:
The dock's continuously maintained view of storage space: bins empty now, with the stamps
they emptied at, plus the bins standing demand will clear. Read only through frozen views.

**Space view**:
The per-drain frozen snapshot of the space timeline that yard and dock priorities read.

**Standing demand**:
Released-but-unpicked pick demand — the batch just released plus any carry. The demand slice
of standing work, and the only lawful forecast source; never the future demand script.

**Predicted clear**:
A bin standing demand will empty, identified by the same rule the pick sim plans with. A
fact about which bins, never about when.

### Warehouse

**Putting**:
Moving packed freight from the inbound handoff into bins via the put queues and placement pools.

**Cart**:
The volume-bounded vehicle a putter or picker pushes; a channel's putter and picker push the
same cart.

**Picking**:
Retrieving units from bins to fill demand batches.

**Per-item charge**:
The fixed labour to label one handled unit — an item at pick, a pack at receiving. Charged per
unit handled and scaled by the height bracket like the rest of at-location work; smaller for
putting than for picking.
_Avoid_: sticker cost, per-item intercept

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

**Travel share**:
The ratio of a department's measured seconds per unit to the seconds the demand script alone
predicts — the part of the price a run had to be taken to learn.
_Avoid_: travel fraction, overhead ratio

### Day-over-day

**Working day**:
The calendar boundary for every crew — one day for the whole site, a calendar day with a
declared stretch of production time inside it. Site state — a half-unloaded trailer, dock
depth, queued packs — is preserved across the boundary, never reset or re-derived.

**Shift**:
One working stretch of the site's crews on the shared clock. It ends when no standing work
remains and none is still scheduled to release, or at a global cap, whichever comes first;
days stay origin-aligned — the shift timers happen at the same time every day.
_Avoid_: shift (for the reporting frame that merely labels hours)

**Standing work**:
The labor a shift can end on: released-but-unpicked demand, queued puts and held items, and
the dock floor. Merchandise in transit is calendar, not standing work.

**Duty cycle**:
The share of the site's production time a crew is actually granted to work. Denominated
against the working day, never against a batch — a budget granted per batch scales with batch
cadence rather than with time, so it drifts silently whenever a batch outlives a day. A
department's capacity is denominated in the same unit as the work that arrives at it.

**Era**:
A declared regime of the site — its working day, its labour model, its demand script — under
which results are comparable. A change of era ends comparability with every earlier result;
nothing is read across one.
_Avoid_: regime (for the comparability boundary), version

**Reference run**:
The one run, taken under the era and the reference arm, whose measured labour per unit prices
the travel-bearing departments for a catalogue. Its product is the calibration record.
_Avoid_: calibration run, baseline run (the baseline is the arm compared against, not this run)

**Calibration record**:
The committed declaration of a catalogue's labour constants together with the provenance that
produced them — seeded, measured, or derived. A run reads it and copies what it ran under; it
never edits it.
_Avoid_: calibration constants, tuning file
