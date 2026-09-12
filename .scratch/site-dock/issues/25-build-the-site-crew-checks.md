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

**`C_store == C_ful` may not be well-posed, and this ticket is where that is settled.** The map's
fog says the two channels run different pick configs, so today's per-leaf docks price at C = 7.6 s
and C = 5.1 s; equality is a claim about the SITE dock owning one price list, which
[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md) decides
by having a price list at all. If 21 gave the site dock one price, this clause is a test; if it
did not, this clause is void and must be recorded as such rather than written to pass.

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
