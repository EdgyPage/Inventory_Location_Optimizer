# 17 - the put-away rung chain has no seam

Type: refactor
Status: needs-triage
Blocked by: 02

## Context

ADR-0003 fixes a five-rung chain for a put: empty bin (`placement.place_one`) -> the SKU's own bins
-> repack into a smaller tier -> singleton rescue -> pending. All five are written inline in one
180-line `while waiting:` loop, `Warehouse/inventory/Inventory_Management.py:1511-1687`
(`_stock_per_unit`), each rung hand-maintaining `_queued_sku_counts`, `placed`,
`waiting.appendleft(item.respawn(...))` and `pending` with subtly different bookkeeping:

- `:1601-1618` counts topped-up bins off a FLOW -- `self._put_topups` before and after -- because
  `_top_up_own_bins` (`:885-961`) "keeps its one-number interface"
- `:1637-1642` and `:1666-1671` adjust `_queued_sku_counts` by a split delta
- `_charge_repack` at `:1141-1167`

There is no seam: a new rung is an edit INSIDE that loop, which is the hottest and most
invariant-dense loop in the manager.

**The contrast is one file away.** `Warehouse/inventory/put_policy.py` answers the ADJACENT
question -- which waiting item to work next -- with exactly the right shape: a one-line interface
(`(PutawayItem) -> comparable, higher first`), five adapters, a `key_for` resolver that raises on
an unknown name, a documented "what is NOT here yet" section, 130 lines. Two sibling policy
questions; one has a real seam with five adapters, the other has none.

## What to build

A `PutRung` sequence. Each rung is `(unit, item, ctx) -> RungResult`, where `RungResult` is one of
**placed(n_bins)**, **respawned(units)**, or **declined**. The loop becomes `for rung in
self.put_chain:`. The bookkeeping that differs per rung (the `_queued_sku_counts` delta, the budget
charge) is expressed once, in the driver, off the `RungResult`.

Default chain is today's five in today's order, byte-identical.

## ADR note

**This does not re-litigate ADR-0003; it acts on it.** The ADR parks two rejected alternatives
(home bin, relocate on top-up) and says the rule "may become a knob later, which is why the rework
is recorded rather than hidden". Today that knob would cost an edit inside the 180-line loop. This
makes it a rung instead of a diff. The ADR's ruling -- empty bin first, consolidate only when none
fits, ahead of the repack and singleton rescues and ahead of pending -- becomes the default chain's
ORDER and stays exactly as decided.

Take care with the reason the ADR gives for that order: "the new-bin decision is where placement
optimisation happens: a restock that returned to its own bin would hand the arms nothing to rank
until a shelf was taken to zero." A reordering rung must not quietly forfeit that.

## Verification

- Each rung independently testable through a three-case return type, instead of through a loop that
  needs a warehouse, a queue, a deadline and a budget to reach rung 3.
- Byte-identical: `run_digest.py` DB-row neutrality against a baseline. A tier spill or a repack
  is "expected to happen never", so a recorded one after this change is a finding about the
  refactor, not about the warehouse.
- The empty-bin-preference invariant was AMENDED by ADR-0003 and memory
  `empty-bin-preference-is-structural` records that the never-adds-to-an-occupied-bin form held
  only while reorder lots were pallet-sized. Do not reintroduce the old invariant as an assertion.
- Gates 1, 2, 10.
