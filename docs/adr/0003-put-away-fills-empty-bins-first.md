---
status: accepted
date: 2026-09-07
---

# Put-away fills empty bins first and consolidates into the SKU's own bin only when none fits

Under base stock every pick reorders what it took, so a line that takes part of a shelf leaves
a remnant and the top-up arrives as its own unit. Put-away never added to an occupied bin, so
each top-up opened a second bin; against a warehouse sized at one bin per SKU the free index
ran dry, units sat on order and never on a shelf, and the store section's missed share climbed
for forty days. We decided that put-away tries an empty bin first, and only when no empty bin
fits does it consolidate into a bin already holding the SKU (fullest first, splitting only on
overflow), ahead of the repack and singleton rescues and ahead of pending. The reason for the
order is that the new-bin decision is where placement optimisation happens: a restock that
returned to its own bin would hand the arms nothing to rank until a shelf was taken to zero.
Picks drain a SKU's smallest bin first so remnants clear and bins return to the index.

## Considered options

- **Home bin** -- a SKU's top-up goes back to its own bin whenever it still holds one; the arm
  chooses a fresh bin only when a line empties the shelf, which a Poisson line does on more than
  every second line at every rate. Keeps the setup headroom free as a candidate set. Rejected
  by the user: it forfeits the optimisation moment on every line that leaves a remnant.
- **Relocate on top-up** -- collect the remnant and place the whole quantity in the arm's best
  free bin, freeing the old one. Every top-up an optimisation moment at one bin per SKU.
  Rejected: a re-slot labour term nobody has declared or priced.
- **Take the whole shelf / accept and stamp** -- the first over-serves demand, the second leaves
  the index exhausting and fails the equilibrium check by construction.

## Consequences

- The free index runs to near zero in steady state and a share of SKUs hold two bins; both are
  recorded per day (own-bin share, free-index depth) and reported, not judged.
- The repack and singleton rescues become receiving work, priced per resulting pack at the dock's
  unload price and written as `repack` work events; the staffing record expects zero of them.
- The rule is a no-op in any run whose free index never exhausts, so it needs no knob and moves
  no golden; it may become a knob later, which is why the rework is recorded rather than hidden.
- The 2026-08-25 preference that a putter and a picker never meet in one bin survives as the
  first rung of the rule, not as an invariant.

## Observed (2026-09-10)

Two 40-day runs on the reference pair at 0.85 fill read own-bin share 0.000 and no repack, and the
free index fell on every day of both: 3% of the store section's setup headroom and 10% of
fulfillment's in 40 days, at the rate SKUs take their first partial pick. The mechanism is the
rule as written -- a remnant stays, the top-up opens an empty bin, and the smallest-first drain
clears the remnant only on a later line -- so a picked SKU settles at two bins most of the time.
The first consequence above is that slide's destination; the window does not reach it on the
store. Its stationary level is a closed form over the line law, and the sizing headroom is to be
derived from it rather than assumed (department-calibration 34, 35). A top-up into an occupied
bin and a tier spill are judged at zero; the depth is reported per bucket
(department-calibration 32).
