# Design the two-level priority seams

Type: grilling
Status: resolved
Blocked by: 01

## Question

Define the policy interfaces for the four optimization seams: what a global-priority policy
(trailer ordering) and a local-priority policy (within-trailer packed-item ordering) each
receive and return, how a seam is selected/parameterized from `settings.py`, and what the FIFO
v1 policy of each seam is. The shift being modeled: prioritization moves from programmatic
sort-by-priority to trailers-by-global, packed-items-by-local. Consult `codebase-design`;
the placement-pools precedent (`placement-pools-and-the-audit-point`) is the prior art for
detaching policy from mechanism.

## Context update (post "Name the inbound pipeline")

Loading is UNMODELED, so the in-scope seams are unloading, packing, putting. Global priority acts
at the DOCK — which arrived trailer is worked next (warehouse-space-aware later); local priority
orders items within one trailer. The pack plan is FIXED per trailer: a packing policy may reorder
work but never change pack composition. New code lives in top-level `Inbound/` (see ticket 08 for
its boundary). Canonical terms: root `CONTEXT.md`.

## Context update (post "Draw the Inbound package boundary")

Policies live Inbound-side and are held by the manager the way it holds assignment functions —
built driver-side from `settings.py` (the `recv_crew_spec` call-time pattern) and injected. The
order port (fired reorders → transit) and receipt port (stamped packs → `_queue`) frame what a
policy may see. The pack plan stays fixed per trailer. Facts: `../assets/boundary-facts.md`.

## Context update (post "Choose the lead-time denomination")

Global priority is now TWO decisions at the dock: which parked trailer stages to a free door, and
which staged trailer unloads next ("yet to be developed" — the seam exists, FIFO v1). Doors are
finite; the parking lot is unbounded. Arrival instants come from optional per-trailer leads on
the absolute clock (default zero), so a spread of arrivals is available to policies later.

## Answer

Resolved in one grilling round, mirroring the proven `put_policy` seam (pure key functions,
batch-frozen state, registry that raises on unknown names, policy-proposes / bound-disposes
composition):

1. **Two registries, matching the two priority levels** (`Inbound/priorities.py`):
   - **Global**: one key over TRAILERS, `key(trailer, ctx) -> comparable`, higher first,
     consulted at BOTH decision moments — a freed door goes to the top-ranked unstaged
     trailer, the crew unloads the top-ranked staged trailer. A separate staging-vs-unload
     split is a later registry addition if a real policy ever needs it (one adapter = a
     hypothetical seam).
   - **Local**: one key over a trailer's items, `key(item, ctx) -> comparable`. May reorder
     work; may NEVER change pack composition (the pack plan is fixed per trailer, decided in
     ticket 01).
   v1 for both: `'fifo'` by arrival. No `INHERIT` entry — there is no pool whose precedence
   could stand in at the dock.
2. **`ctx` is a frozen dock context**, built once per drain: doors, free doors, and
   batch-frozen warehouse-space views. The future "takes advantage of space in warehouse"
   signal (`_emptied_at` / upcoming slots) arrives as a named view on `ctx` — no signature
   change. Keys stay pure functions of (candidate, ctx), computed once per drain.
3. **Selection**: `INBOUND_GLOBAL_POLICY = 'fifo'`, `INBOUND_LOCAL_POLICY = 'fifo'` in
   `settings.py`, carried by a CONFIG-at-call-time spec (the `recv_crew_spec` pattern —
   explicitly not `put_crew_spec`), bound driver-side into the injected Inbound objects per
   the broker decision (ticket 08).
4. **The dock gets its `k_cap` analog NOW** (user's call, against the deferral
   recommendation): an ordering-tolerance bound on the GLOBAL ranking, denominated in
   trailers — the policy proposes an order, the bound limits departure from arrival order.
   Default unbounded; with FIFO it is inert, so v1 stays byte-identical. A local-level
   analog is a later addition if a non-FIFO local policy lands.

## Comments

2026-08-26, from "Shape Cart and Trailer objects": trailer contents are contiguous LOAD PALLETS
(may mix SKUs), so the local-priority key's natural candidate is the load pallet — what a crew
pulls — degenerating to the SKU lot on single-SKU pallets. Sharpens "key over a trailer's
items"; the interface shape (key(candidate, ctx), pure, batch-frozen) is unchanged.
