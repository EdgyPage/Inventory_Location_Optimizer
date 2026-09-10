# Derive the stationary fragmentation closed form

Type: task
Status: open

Graduated 2026-09-10 from
[Band the own-bin share and the free-index depth](32-band-the-own-bin-share-and-free-index.md),
decision 7 and the finding behind it. AFK derivation, written up for review before anything
lands (the precedent is
[Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md)). Skills:
`codebase-design`; `code-reviewer`. No calibration simulations: the two finished runs are a
one-time correctness check for the formula, never a pipeline step.

## Question

How many bins does a SKU hold in steady state under the era's rules, and how many extra bins
per bucket does that add to the requirement the planner sizes from?

The rules, all decided: base stock (every line reorders what it took, lead as stamped per SKU;
lead 0 on the reference pair); a top-up lands in an EMPTY bin first (ADR-0003) and consolidates
into the SKU's own bin only when its whole tier chain is dry (never, once the headroom is sized
right); picks drain the SKU's smallest bin first; a line is `Demand.line` (`poisson_max1`,
stamped on the SKU); the level is the solved line floor (`coverage.solve_floor_lines`) or, above
it, the position rule with fractional lots (28's `fired_lots`).

So each SKU is a Markov chain over its on-hand split across bins: a line of k against a remnant
r and a top-up of the previous k' clears the smallest first, and the reorder of k opens a new
bin. Derive the stationary distribution of the bin count (E[bins] - 1 is the SKU's expected
extra bins), sum it per bucket, and -- if it falls out of the same chain -- the transient: the
expected drawdown after t days from a fresh fielding, which the reference store is nowhere near
finished with (a store SKU sees a line every ~400 days).

Check it against the two finished runs before it is trusted: the per-batch `free_bins` series
(corrected to the section, 32's table), and the keyframe sidecar's per-size-class occupancy at
batches 0 and 25 (store `small` 143,581 -> 150,282, `singleton` 138,732 -> 133,168, `medium`
451,013 -> 454,126; fulfillment `ff_medium` 664,028 -> 669,782). State the tolerance and where
the residual comes from (window SKU mix first: memory `window-mix-before-model-error`).

What lands: a pure module beside `expected_travel.py`, the stamp on the record
(`coverage.final.<ch>.fielded.buckets[].expected_extra` and a per-section sum with provenance
`derived`), and the write-up. The fill derivation is
[35](35-derive-the-fill-headroom-from-the-fragmentation.md), not this ticket.

## Done when

- The write-up is reviewed by the user; the module is pure, imports no CONFIG; the stamp lands
  on the record for every mode; the formula reproduces both leaves' corrected drawdown within
  the stated tolerance, per size class where the keyframes allow it; tests pin the chain on a
  hand-computable SKU (a line law with two outcomes) and the sum over a fixture section.
