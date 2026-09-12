# Build the site crews' cross-leaf checks

Type: task
Status: open
Blocked by: 24

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md) settled the
rule and named the ordering itself: the second `SEMANTIC_USES` family is **behind 03's site-DB
family registration**, because `semantics_for` RAISES on an unregistered family. That
registration is part of [Build the site analysis stage](24-build-the-site-analysis-stage.md),
which is why this is blocked on it rather than on the coordinator directly.

## Question

Build 15's held half: the two site-scope uid clauses (section 1), the site-total closure against
the coordinator's own accumulator (section 2), constant-C's cross-leaf `C_store == C_ful` clause
(section 3 item 4), the sibling `reconcile_pair` entry with its `coupled`-marker grouping and its
FAIL on an absent site DB (section 5), the second `SEMANTIC_USES` family (section 6), and the
per-batch site-total table in the site DB (section 7).

**The uid clauses have a fact now that 15 could only assume.**
[Build the site put-away pool](19-build-the-site-putaway-pool.md) made the put uid block start at
`max(k_pickers)` over both channels, so a putter's `actor_uid` means the SAME person in both
leaves' DBs — which is precisely what a cross-leaf, role-qualified disjointness check needs. It
also left the smaller leaf a deliberate GAP in its uid space, so any clause that assumes a dense
uid range is wrong by construction. Check role-qualified identity, never density.

**`C_store == C_ful` IS RETIRED, and what replaces it is stronger.**
[Decide the site dock's unload price](27-decide-the-site-docks-unload-price.md) settled it: the
unload price is a statement about the MERCHANDISE, so the site dock holds a price LIST keyed by
the unloaded unit's regime and the equality is false BY CONSTRUCTION. Do not write it, and do not
write a weakened version of it. 15 called it "the site dock's sharpest falsifier" and it was
sharp about the wrong thing — re-read under 27 it was a claim about PLUMBING (that two leaves
resolved one price list) dressed as a claim about physics.

**What replaces it is check 6 in a two-constant form**, which 15's own machinery already
supports: re-price each receiving row against ITS OWN regime's constant, so the check becomes two
exact equalities instead of one and FAILS if a row is ever charged at the other channel's rate —
the defect the equality was reaching for and could not see.

**The precondition, and it is this ticket's to establish BEFORE writing the check:** a receive
row's regime has to be resolvable from the sim DB alone. `work_events` at the `receive` role may
or may not carry it. If it does not, that is a COLUMN, and a column is a schema change that rides
the pipeline (`--sync` before the DDL edit, `--accept` after), never a consumer edit. Establish it
first — a check that silently cannot resolve the regime falls back to one constant and passes.

## Amendment (2026-09-12, from ticket 24)

**THE PRECONDITION IS ESTABLISHED — no schema change is needed, and the reason matters.** A
receive row's regime IS resolvable from the sim DB alone, but not the way this ticket assumed.
Under coupling the coordinator partitions the dock's records **by SKU before the driver stamps
anything**, so every `receive` and `repack` row lands in its OWNING channel's `work_events`, and
`simulation_runs.channel` names that channel.

**So do NOT write check 6 as "read the regime off the row".** The two-constant form is `C_store`
over the STORE LEAF's rows and `C_ful` over the FULFILLMENT LEAF's, each re-priced against its own
regime's entry in the dock's price list. **One DB does not hold both regimes' rows** — it cannot,
by construction — and a check written on that assumption finds one constant, re-prices every row
against it, and passes.

**15's other precondition is built too:** each leaf's `batch_stats` receiving scalars are now its
own share (the dock is partitioned back by SKU and the shares are CLOSED against its own totals),
so the site-total closure check has both sides to compare.

**One gap 24 names rather than leaves silent:** `equilibrium_report`'s accumulation loop is not
unit-tested — `_site_verdicts` is, but the loop that feeds it needs a run tree with a staffing
record and a closed ledger. If this ticket's site-total closure runs through that loop, it is
testing two things at once and only one of them is pinned.

**And the trap 24 fell into, which this ticket sits one layer above.** `SiteContext` left `_caps`
uninitialised; because it SUBCLASSES `EvalContext` the era gate found the inherited
`capabilities()` and called it, three of four evaluations raised inside the driver's swallow, and
the tally reported a GRANT while nothing rendered. Memory `a-grant-is-not-an-output` is about
exactly this and it still caught nobody out until the stage was driven end to end on a real
coupled tree. **A subclass is not exempt from a duck-typed gate**, and the `[access]` summary
reports INPUTS — only the `[render]` run summary says whether anything was written. Drive whatever
you build on a real coupled tree before believing a tally.

## What proves it

- **The site-total closure FAILS on a planted leak**, not merely passes on a clean run — memory
  `conservation-ledger-is-bin-only` is the shape: a check that can only pass proves nothing.
- **The re-pricing check re-prices ROWS.** Memory `equilibrium-check-two-traps`: an average was
  7x off, so the constant is compared against re-priced rows, never against a mean.
- **`_TOL`'s form is decided here or explicitly deferred.** The map's fog carries
  `receiving_report`'s absolute 1e-6 s tolerance against sums that grow with row count — a
  relative error of 3e-13 reported as a FAIL on four archived arms today, and it gets sharper
  under coupling where a site total is the sum of two leaves'. If the right form is clear once
  the site totals are in front of you, fix it; if not, say so and leave it in the fog.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).
