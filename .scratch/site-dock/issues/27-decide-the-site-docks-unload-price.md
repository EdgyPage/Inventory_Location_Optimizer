# Decide the site dock's unload price

Type: grilling
Status: open

HITL. Skills: `grilling` + `domain-modeling`.

Graduated by [Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md),
which was told to take this decision if it became obvious once the code was in front of it and
to graduate it otherwise. It did not become obvious — it became SHARPER, and it acquired a
third candidate answer the map had not considered.

## Question

**One site dock has one unload price list. Whose?**

The two channels run DIFFERENT pick configs, and the dock's price is the pickers' price by
reference twice over: `_UnloadCost.from_putaway(_PutawayCost.from_pick(pick_cfg))`
(`Optimization/simdriver/strategy_runner.py`, the receiving-crew block). Today's per-leaf docks
therefore price at **C = 7.6 s (store)** and **C = 5.1 s (fulfillment)** — measured, not
assumed: [Exempt repack rows and pin the unload constant](17-exempt-repacks-and-pin-the-unload-constant.md)
derived both independently from the run's own pick config and check 6 of `receiving_report`
holds them to 4.4e-15 over 505,177-row arms.

Three answers, and they are not equally shaped:

1. **One channel's price wins** (store's, as the dominant volume). Cheap, arbitrary, and it
   makes every fulfillment unload cost 49% more than the same unload costs today.
2. **A units-weighted blend** over the realized receipts. Defensible as an average and
   indefensible as a physics: it is a number no crew ever charges, it moves when the mix
   moves, and memory `window-mix-before-model-error` is about exactly that confusion.
3. **The dock holds a price LIST, keyed by the unloaded unit's own regime.** 21 made this
   the natural one: `SiteReceiving`'s step-4 handoff already resolves `regime_of(item.unit)`
   per unit, so the coordinator has the owner in hand at the instant the charge is computed
   and a per-regime price costs nothing structurally.

**And 3 is why this is a decision and not a preference.** A per-regime price makes
`C_store == C_ful` FALSE BY CONSTRUCTION — and that equality is the clause
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md) calls
"the site dock's sharpest falsifier". Picking 3 retires the check; picking 1 or 2 keeps it and
moves every archived receiving number on one channel or both. Nothing about the code decides
which of those is the better trade.

Sub-questions in scope:

- **What IS the unload price a statement about?** If it is the merchandise (a fulfillment
  tote is quicker to move than a store pallet), 3 is right and 15's clause was a claim about
  plumbing rather than physics. If it is the CREW (one receiving crew, one rate card), 1 or 2
  is right and the per-channel pick configs were never the dock's business.
- **What does it do to 15's check 6 and to `receiving_report`?** The check re-prices rows from
  the sim DB alone; under 3 it needs the row's regime, which `work_events` may or may not
  carry at the receive role.
- **What does it do to comparability?** Whichever answer wins, it is the FIFTH-and-something
  break on the per-item-charge family (memory `per-item-charge-hard-break`), and it needs the
  same treatment: measured per leaf, dated, recorded.
- **Does the answer differ for a REPACK?** `_charge_repack` prices a rescue at the dock's
  `unload_cost` and the rescued order has a regime too.

## What proves it

- The decision recorded with the rejected alternatives and their reasons, in the register the
  other design tickets use.
- Whether `C_store == C_ful` survives as 15's clause, is rewritten, or is retired — stated
  either way, because the map's "Not yet specified" section names it and something has to
  close that entry.
- The seam named: 21 left the site dock's `UnloadCost` to whoever CONSTRUCTS the dock (the
  coordinator is handed one, already priced), so all three answers are still reachable with no
  code deleted. Whichever wins, say which object holds it.
