# Name the inbound pipeline

Type: grilling
Status: resolved

## Question

Fix the domain model for the v1 trailer pipeline: canonical names for the entities (Trailer, the
put-cart, the four optimization seams — trailer loading, unloading, packing, putting) and the
exact stage order from reorder release to put queue, including where the existing `Dock`,
receiving crew, `LoadPlan`/`inbound_split`, and `PutQueueSet` sit in it. Decide where the new
code lives (`Warehouse/inbound/`? — `operations/` imports only `wh_kernel` so no inversion is
needed, per `one-clock-one-speed-one-config`) and which existing seam each stage attaches to.
Resolves into `CONTEXT.md` entries (created lazily) and the skeleton every later ticket names.

## Answer

Resolved over three grilling rounds; the glossary lands in root `CONTEXT.md` (created by this
ticket — see it for definitions). The decisions:

1. **Stage order**: reorder (a notification to the unmodeled *ordering site*) → loading, unmodeled
   (assumed perfect FIFO; **next-fit** — an item that doesn't fit starts a new trailer, preserving
   FIFO; loading variance is future loading-seam territory) → **dispatch** → **transit** (the lead
   queue IS the transit leg — the trailer models what a floating quantity abstracted) → **arrival**
   (age stamped) → dock → **unloading** → **packing** → handoff of packs to the warehouse →
   **putting** → picking.
2. **Trailers carry loose items**, physically modeled as stacked on maximum-sized pallets. The
   only new entity; mirrors the `StorageCart` pattern (ticket 07's business).
3. **Packing is dock-side receiving-crew work** — this OVERTURNS `dock.py`'s packing-at-arrival
   invariant, decided with the tradeoff on the table. The confound is contained by: **the pack
   plan is fixed by the trailer's full per-SKU contents** — deterministic at arrival; crew
   interruption pauses packing work but never changes the packs, so tier mix stays independent
   of staffing and sweeps stay comparable. Pack-follows-the-crew remains reachable later as a
   packing-seam policy.
4. **Site state persists day over day** — a half-unloaded trailer, dock depth, queued packs
   survive the working-day boundary; nothing resets or re-derives.
5. **Vocabulary**: *site* = whole facility; *warehouse* = put/pick side (narrower than the
   historical `Warehouse/` package name); *inbound* = receive side, holds reorders in transit but
   does not decide reordering; *load* = trailer contents, never packs; `LoadPlan` is misnamed and
   becomes `PackPlan` during the convention pass (not now). Unloading and packing are two named
   stages worked by the one receiving crew.
6. **Priorities**: *global priority* orders arrived trailers at the dock (not dispatch — loading
   is unmodeled); *local priority* orders items within a trailer. v1: both FIFO.
7. **New code lives in a top-level `Inbound/` package** beside `Warehouse/` — the bigger
   architectural move, chosen deliberately; boundary edges and the handoff seam are a new ticket.

Assets: `CONTEXT.md` (repo root, created), map Notes updated to the resolved model.
