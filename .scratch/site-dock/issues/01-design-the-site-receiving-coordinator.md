# Design the site receiving coordinator

Type: grilling
Status: open

HITL. Skills: `grilling` + `domain-modeling` + `codebase-design`.

## Question

The standing receiving drain is a ~220-line **manager method** — `_receive_standing`
(`Warehouse/inventory/inventory_reorder.py:706-826`, with `_unload_merged` `:828-866` and
`_unload_split` `:868-944`) — that reaches `transit.unplanned()`, `self.packer`, `self._originals`,
`dock.note_arrivals`, `transit.stage/dock_order/door_freed` and the per-unit ledger flip, all as
one leaf's. Under one site dock it must live **above both managers**. Decide what that thing is,
where it lives, and what each manager keeps.

Four sub-questions, all in scope here:

1. **The object.** A site receiving coordinator holding the one `Dock`, the one transit and the
   one yard, with the two managers injected? Or the drain stays a manager method and one manager
   is elected to run it for both? (`Inventory_Management.py:275` `self._dock`, `:1141`
   `enable_receiving`, `:1763`/`:1804`/`:1850` `self._dock.takes(source)` all read the dock as
   *mine*; `:254` and `:620-648` do the same for transit.) Note the cheap half: these are already
   injection seams, so one object can serve two managers without touching `Warehouse/`.

2. **The phase order.** `check_reorders` (`inventory_reorder.py:956-1004`) is six phases, per
   manager, sequential — "THE ORDER IS THE BEHAVIOUR", pinned by `Tests/unit/test_reorder_phases.py`.
   Coupled, the charter implies phases 1–3 (tick, reclaim, advance) run for **both** leaves, then
   one shared phase 4 (receive), then phase 5 per leaf. Confirm or replace that interleave, and
   decide whether it is a site-level contract with its own test or an extension of the existing one.
   `strategy_runner.py:1358` is the single call site.

3. **Pack routing at the release seam.** The charter settles pack-level ownership: packing
   partitions by channel first, so every pack has exactly one owning channel. Decide where the
   partition is actually made (`self.packer`'s plan, or a pre-pass over the unloaded items), what
   carries the owner (a field on the pack, or a parallel map), and how `_release_to_stock`
   (`:442-495`) consumes it instead of resolving `sku` against `self._originals`. `CONTEXT.md`
   currently says "The pack plan is fixed by the trailer's full contents" — that entry needs the
   channel-partition amendment, and this is probably an ADR (hard to reverse, surprising without
   context, a real trade-off against mixed packs).

4. **The crew gang.** `_unload_merged` / `_unload_split` already hold one `dock.crew_size` gang,
   which the sizing inventory calls "already the right shape for a shared crew" — confirm that the
   door-team cap (`PHASE2_DOOR_TEAM`, inbound-optimization 28) still means what it meant when the
   trailer is mixed.

Flag-off must stay byte-identical: whatever this becomes, a single-channel or inbound-off run walks
the v1 path unchanged.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1. Re-resolve its line numbers before trusting one.
