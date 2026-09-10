# Derive the stationary fragmentation closed form

Type: task
Status: resolved

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

## Answer

**RESOLVED 2026-09-10.** The closed form is derived, checked against both finished runs, and
landed on `develop`; the write-up is
[assets/fragmentation-derivation.md](../assets/fragmentation-derivation.md) (sections 0-7, the
user's review points in 7) and the check
[assets/validate_fragmentation.py](../assets/validate_fragmentation.py).

**The chain.** Each SKU is a Markov chain over the multiset of units on its shelf, a unit being
`(q_now, single, n0)` -- what it holds and the KIND it was packed as, which decides the size tier
the bin keeps while picks thin it. A line of `q` below the shelf drains smallest-first (a tie
between kinds split uniformly over the tied units: the layout interleaves every tier's aisles)
and, when the shelf falls to or below `rp`, lands `packing(Q - on_hand)` -- the plan's leading
slots -- in fresh bins; a line at or above the shelf empties it, refills the fielded state and
applies the residual `(q - S) mod Q` to it (the same decomposition `fired_lots` prices). The
stationary law is `pi = pi P` over the reachable states (lazy power iteration, a pinned direct
solve for a slow mixer); the transient is the forward iterate, mixed per SKU over
Poisson(`lambda_s t`) lines. The chain depends on (regime, law, plan, `rp`) only -- 489 classes
for the pair's 400,000 SKUs, 16 s -- and the tier is a per-SKU lookup through the packer's own
unit classes.

**The check (one-time, on the two finished runs).** Scored at each SKU's REALIZED line count over
25 days the chain reproduces the store's bin change to -3.2% and fulfillment's to -3.0%, the
one-line mean to a third of a percent on both (+0.304 vs +0.301, +0.302 vs +0.300); the store's
39-day drawdown at the declared line share to -4.3% and every tier of its keyframe within 10%
(medium +3,105 expected against +3,113 realized; singleton -5,073 / -5,564; small +5,904 / +6,701).
**Tolerance: +/-5% per SKU on the bin change**; the named residual is the supply jitter, then
same-batch aggregation. The fulfillment TRANSIENT over-reads by 23-28% at the declared line
share, and that is not the chain: the batch sampler's affinity lift spreads a fulfillment
section's lines over SKUs almost flat across frequency deciles (41,669 SKUs touched by day 25
against the share's 54,995), so `section_fragmentation` takes `lines_per_day_by_sku` beside
the share for a trajectory band to read the sampler's rate through (memory
`sampler-affinity-flattens-the-fulfillment-line-rate`; the affinity-aware share is fog).

**What the closed form says about the reference pair.** Store 4.205 -> 4.465 bins/SKU,
**+62,305 bins** (6.2% of the requirement, 28% of the 220,817 setup free); fulfillment 6.554 ->
6.856, **+48,204** (4.6%, 26% of 187,793). The extra sits on plans of one or a few 3-6 item units
(`((False, 4, 1),)` +3,260 over 3,654 SKUs); single-item plans and 2-item plans with a trailing 1
never fragment. **The store's six `small` pallet buckets exceed their setup free two to three
times in steady state** (conveyable/food/small: +33,810 extra against 12,618 free, implied fill
0.652) because every picked singleton remainder returns as a pallet of 1 in `small` and stays
there; eleven `singleton` buckets read negative (conveyable/food/singleton -27,004). On the
40-day run's slope those buckets exhaust around day 47, and a spill up to `medium` (judged at zero)
follows -- the number 35 exists to size, per bucket, migration included. Fulfillment has no
bucket past its free.

**What landed.** `Optimization/simconfig/fragmentation.py` (pure: `packing`, `drain_all`,
`SkuChain`, `poisson_weights`, `section_fragmentation`); `era_coverage.stamp_fragmentation`,
called by `fixed_point` in EVERY mode after the fielded-equals-declared check: every
`coverage.final.<ch>.fielded.buckets[]` row carries `expected_extra` (0.0 on a bucket no plan
reaches, a raise on one the plan never built) and the block `fielded.fragmentation`
(`expected_extra`, provenance `derived`, `method`, `n_classes`, `capped_classes`,
`positive_lead_skus`, bins per SKU, seconds). Two refusals: a plan that packs less than the
order-up-to, and a class past 200,000 states. `Tests/unit/test_fragmentation.py` (19: the
packing against `viable_storage_units`, the drain and its tie split, the two-outcome chain by
hand, above-floor vs base stock, the solver paths, the section sum against
`bucket_requirements`, the tier migration with a negative bucket, the rate seam, purity, the
stamp through `stamp_fragmentation` and through `fixed_point`); `test_coverage_rescale`'s
row-key assertion gained the new key.

**From the two reviews** (`code-reviewer`, `test-reviewer`; nothing critical): the tie split
was per kind and is now per tied unit (no number on the pair moved); the chain refuses a plan
whose total is not the order-up-to; the lead count rounds as the ledger does; `capped_classes`
is on the record; a vacuous mid-transient assertion (past the trajectory's depth) and an
18-second fixture were fixed; the fixed-point test now pins the unconditional call by source.
Found on the way: the landing-state table for every residual below `Q` was O(Q^2) and cost the
coverage tests' thousand-unit fixtures ten minutes -- built on demand now (0.5 s).

**Not on this ticket.** The fill derivation is
[35](35-derive-the-fill-headroom-from-the-fragmentation.md), now unblocked. Supply jitter and a
lead-aware chain are named residuals, not built. The architecture layer needs its regen for the
new module (maintainers after the commit).
