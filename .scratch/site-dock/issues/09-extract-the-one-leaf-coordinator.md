# Extract the one-leaf receiving coordinator

Type: task
Status: open
Blocked by: 01

AFK. The execution override (map Notes) graduating 01's design into a build. Every governing
decision is in
[Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md); this ticket
adds none.

## Question

Nothing to decide — this is the **byte-identical half** of 01, landable on its own. It needs
nothing from 02 (the coupled work unit), 03 (the site scope) or 08 (the site space view), because
it drives exactly **one** leaf and changes no behaviour. Doing it first de-risks everything
downstream: the coordinator exists and is proven equivalent before any second leaf is attached.

The work:

1. **Add the two ports** to the reorder mixin as public methods, carved out of `_receive_standing`
   (`Warehouse/inventory/inventory_reorder.py:708-833`):
   - `plan_lot(sku, qty) -> list[PutawayItem]` — step 1's body: resolve `_originals`, apply
     `inbound_split`, pack, `_stamp`, debit the pack shortfall against `_deferred_qty`.
   - `accept(item, t0, dur, w) -> None` — step 4's body: the deferred-to-queued flip, `_queue`,
     `_recv_seconds`.

2. **Create `Inbound/receiving.py`** holding `SiteReceiving`: the one `Dock`, the one
   `YardTransit`, the yard-drain record, and `drain(put_deadline, recv_deadline, now_s)`. It takes
   its leaves by injection and calls only the two ports — it must not import
   `Warehouse.inventory` (`forbid: [inbound, wh_inventory]`, `context/architecture.yml:100`).
   `regime_of` (`wh_kernel`) is legal and is what routes step 4.

3. **Keep `check_reorders` untouched.** It stays the single-channel composition; the coordinator is
   a second composition over the same phases. `strategy_runner.py:1359` is unchanged by this
   ticket.

4. **The equivalence test** (01 §9): the same seeded single-channel standing-yard run driven (a)
   through `check_reorders` and (b) through `SiteReceiving` with one leaf, asserting identical
   `dock.records`, identical `_yard_drains` and identical put-queue contents per batch. Assert the
   **record sequences**, never their sums (memory `lockstep-tests-compare-aggregates-only`).

Deliberately **out of this ticket**, all deferred to the coupled build: the `{sku: leaf}` owner
dict, the refusal on a non-standing transit, the leaf-accessor refusals, the `SITE_PHASES`
sequence in `Tests/unit/test_reorder_phases.py`, the ADR, and the `CONTEXT.md` amendments. Those
only mean something once a second leaf exists.

Gates to re-run before handing back (CLAUDE.md §1): `python -m pytest Tests/unit -q`, then
`python context/arch/verify_architecture.py` — the new module adds a node and an import edge, so
the arch layer needs regenerating.
