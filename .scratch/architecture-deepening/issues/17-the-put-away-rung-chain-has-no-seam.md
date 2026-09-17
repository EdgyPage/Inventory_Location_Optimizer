# 17 - the put-away rung chain has no seam

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17

### What landed

`Warehouse/inventory/put_rungs.py` — `RungResult`, `PUT_RUNGS`, `DEFAULT_PUT_CHAIN` and a
`chain_for` resolver that refuses an unknown rung. Four rung methods on the manager, each
`(unit, item, queue) -> RungResult`; `_stock_per_unit` walks `self._put_chain` and books what
a rung reports. `mgr.put_chain` is the knob ADR-0003 said "may become a knob later" — a tuple
of names instead of an edit inside a 180-line loop.

The result type is THREE fields, not the ticket's three cases: `bins`, `units`,
`counts_booked`. "Declined" is derived (`__bool__`), deliberately — a rung that reported work
and also reported declining is not a state the chain has, and a fourth field would let one
exist. The partial top-up is why: it places bins AND respawns a remainder, so the cases are
not exclusive.

### What the seam exposed

**The five hand-written `_queued_sku_counts` updates were not one expression.** Leaving the
queue POPS the key at zero (the form `_execute_placement` uses); a split ADDS with a default
of 1. Written once in the driver, the difference is a two-branch `if` with a comment. Spread
over three blocks it was invisible, and this ticket's own "expressed once, in the driver"
phrasing assumed it away.

`counts_booked` is the other asymmetry, now declared: the empty-bin rung's placement call is
shared with callers that never had a queue item, so it drops the count itself.

### Two source-shape tests moved out from under, and both got stronger

- `test_the_budget_counts_placements_not_pops` used `src.index('placed += 1')` between two
  string offsets. It now asks each RESCUE rung whether it reports bins — a property, not a
  position.
- `test_both_rescues_respawn_rather_than_pushing_a_bare_unit` asserted two `appendleft` sites
  both wrapping in `item.respawn`. There is now ONE push site, in the driver, and no rung can
  push at all — so it became `test_every_respawn_goes_through_the_one_push_site`, which is
  the stronger statement. Both were proved to fail against planted damage.

`Tests/unit/test_put_rungs.py` (18 tests) is the file the ticket asked for: each rung answered
on its own, instead of through a warehouse, a queue, a deadline and a budget arranged to make
the loop walk that far.

### Verification

| check | result |
|---|---|
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| `Tests/unit -k "not gpu"` | 2,705 passed / 1 skipped |
| `test_put_rungs.py` | 18 passed |
| `test_empty_first_topup.py` (the ORDER, unchanged) | green |
