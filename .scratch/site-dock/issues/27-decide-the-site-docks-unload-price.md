# Decide the site dock's unload price

Type: grilling
Status: resolved

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

## Answer

**THE UNLOAD PRICE IS A STATEMENT ABOUT THE MERCHANDISE. The site dock holds a price LIST,
keyed by the unloaded unit's own regime** — answer 3, decided by the user, 2026-09-11.

A fulfillment tote genuinely is quicker to move than a store pallet, and that is a fact about
the thing being moved, not about who moves it. One crew can work at two rates without any
incoherence — which is exactly what
[Build the site put-away pool](19-build-the-site-putaway-pool.md) already established one crew
over, and tested: `s_put` is keyed by channel while the putters are one pool, on the reasoning
that the price is a property of the work's geometry and handling rather than of the person. The
dock is the same argument at the same site, so the two crews now say the same thing about
pricing rather than opposite things.

### Why the other two were rejected

**One channel's price wins** moves every fulfillment unload by +49% against every number on
disk, and buys nothing except a smaller diff. The 7.6 s / 5.1 s spread is not noise to be
rounded away: [Exempt repack rows and pin the unload constant](17-exempt-repacks-and-pin-the-unload-constant.md)
derived both independently from each run's own pick config and check 6 holds them to 4.4e-15
over 505,177-row arms. Declaring one of them wrong requires an argument about the warehouse, and
there is not one — only that store has the larger volume, which is a statement about the mix.

**A units-weighted blend** is worse than arbitrary, it is unfalsifiable: it is a number no crew
ever charges, and it MOVES WHEN THE MIX MOVES. Memory `window-mix-before-model-error` is about
precisely this confusion — a residual that is the finite window's SKU mix read as model error —
and a price defined as a mix average would bake that confusion into the cost model itself, where
no later analysis could separate the two. It also fails the test 15 wanted: `C_store == C_ful`
would be trivially TRUE under a blend, so the check would pass while measuring nothing.

### What this does to 15's clause — it is RETIRED, and that is the price paid

`C_store == C_ful` is **false by construction** under a per-regime list, so
[Build the site crews' cross-leaf checks](25-build-the-site-crew-checks.md) cannot write it.
The map's "Not yet specified" entry that has been carrying it closes here.

That is a real loss and it should be recorded as one: 15 called it "the site dock's sharpest
falsifier". But it was sharp about the wrong thing. Re-read under this decision, `C_store ==
C_ful` was a claim about PLUMBING — that two leaves resolved one price list — dressed as a claim
about physics. **What replaces it is strictly better**, and 15's own machinery already supports
it: check 6 re-prices receiving rows from the sim DB and compares against the recorded constant.
Under a list it re-prices each row **against its own regime's constant** and the check becomes
*two* exact equalities instead of one — which fails if a row is ever charged at the other
channel's rate, the defect the equality was reaching for and could not see.

**The condition that makes that buildable, and 25 owes it:** the check must be able to resolve a
receive row's regime FROM THE SIM DB ALONE. `work_events` at the `receive` role may or may not
carry it today. If it does not, that is a column, and a column is a schema change that rides the
pipeline (`--sync` before the DDL edit, `--accept` after) and never a consumer edit. **25 must
establish this before writing the check, not after** — a check that silently cannot resolve the
regime would fall back to one constant and pass.

### The repack takes the same answer

`_charge_repack` prices a rescue at the dock's `unload_cost`, and the rescued order has a regime
like any other. Under a list it is priced at its own — a repacked fulfillment tote is a
fulfillment tote. Same rule, no exception, which is the only reason it needs no second decision.

### Where it lives

**On the `Dock`, and the driver builds it.** 21 left `SiteReceiving` handed an already-priced
dock and never building one, which is what kept all three answers reachable; that stays true, and
what changes is only the object the driver constructs. `DockSpec`/`Dock` take a per-regime
mapping instead of one `UnloadCost`, and `Dock.unload_seconds` resolves per unit at the charge
site — where 21's step-4 handoff already has `regime_of(item.unit)` in hand, so the resolution
costs nothing structurally and needs no new lookup.

**Flag-off and uncoupled keep ONE price and construct no list**, structurally, in the
`recv_crew_spec` style: an uncoupled leaf builds its own dock from its own pick config exactly as
today. The list is not a mode the single-channel path runs with a one-entry map; it is not
constructed at all. That is what keeps every archived run byte-identical.

### Comparability

**This breaks nothing on the archive.** Every run on disk is uncoupled, and an uncoupled leaf
keeps the price it has always had — the two constants that exist today ARE the two entries of the
list, unchanged. So unlike the put pool's, this decision costs no comparability break at all: it
is the first site-scoping decision on this map that is free, and it is free precisely because the
per-channel prices were already right and only the SITE had nowhere to put them both.

What does move is anything that would have been produced under answers 1 or 2 — which is nothing,
because no coupled standing run has been fielded yet. Dated here so a later reader does not go
looking: **2026-09-11, before the first coupled standing run.**

### What this hands onward

- **[Build the site analysis stage](24-build-the-site-analysis-stage.md)** is unblocked. It
  builds the dock at unit scope with the per-regime list and fields the first coupled standing
  run — which is where this map's second comparability break actually lands.
- **[Build the site crews' cross-leaf checks](25-build-the-site-crew-checks.md)** loses
  `C_store == C_ful` and gains the two-constant form of check 6, plus the obligation to
  establish that a receive row's regime is resolvable from the sim DB before writing it.
- **No new glossary term.** `CONTEXT.md`'s **Unload price** says what it says; what changed is
  that the site holds two of them, which is a fact about the site dock and already covered by
  **Site dock**. Nothing is owed the glossary.
