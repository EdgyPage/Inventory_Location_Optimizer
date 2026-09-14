# Inbound performance

Label: wayfinder:map

## Destination

The `Inbound/` package is **measurable**, its growth is **fitted**, what the instrument convicts is
**refactored**, and phase 2's sizing is restated on the arms it will actually run.

Concretely, the destination is reached when:

1. A growth ladder can run the inbound pipeline — standing yard, real doors, a gain arm — and fit
   an exponent against **measured yard depth**, on a fixture that is not degenerate.
2. The pool-adapter pricing multiplier is measured. Ticket 31 of `inbound-optimization` sized phase 2
   at 1.63–1.93x, but measured it on `('fifo','tmin')` — **the only two adapters that open no pool**.
   Eight of `PHASE2_PAIRS`' twelve arm-slots are pool adapters. The campaign's 8.6–9.7 h is therefore
   sized on the cheap end of an untested spread.
3. Every conviction the ladder produces is either refactored with a guard, or closed with a stated
   reason and the scale at which it would become real.

## Notes

- **Successor to `inbound-optimization`** (closed 2026-09-13). That map's Out-of-scope list named
  running and publishing phase 2 as the successor's first act; this effort is the other half —
  making that act cheaper and its sizing honest before it is paid for.
- **Numbers may move.** User decision: phase 2 has not launched, so comparability may be broken where
  it buys speed. This is not merely tolerated — `whatif_config.py:118` states the funnel
  *deliberately* allows a build between the phases, and phase 1 is the inbound-OFF selection, so no
  inbound change can invalidate it. Every change is still digest-classified so a number-move is
  always deliberate and named, never discovered.
- **Runtime first, but both.** Operational inefficiencies the instrument exposes are reported with
  numbers; no default changes without a decision.
- **The fixture must be real.** User instruction: "Q should NOT equal 1 as a rule. Make the run real."
  See ticket 02 — this turned out to be a property of the *test fixture*, not of the generator.

## Decisions so far

- **[01] The instrument was rotted, and one rot blocks the highest-value refactor.**
  `Tests/calltree` is 1-failed/21-passed on clean `develop`
  (`test_rank_cache_equivalence.py::test_minlabor_cache_matches_frozen_oracle`), and `run_fullfid`
  cannot run at all. `rank_minlabor` is fulfillment's #1 and store's #2 arm in `PHASE2_PAIRS` and the
  one family copying the two-level `aisle_member_pos`, so its frozen oracle is the designated guard
  for the `_make_pool` refactor. It must be trustworthy first.
  → [01-prove-the-instrument-is-honest.md](issues/01-prove-the-instrument-is-honest.md)

- **[02] The generator was never the problem; the test fixture was.** "1 item per bin" is not a
  coverage effect — coverage moves bins-per-SKU, not units-per-bin. It came from
  `perf_simulation._build_inventory` calling `Order(storage_type)` directly, which bypasses the
  creation plan and uses the naive triangular-at-48 dimension sampler. The production generator
  already samples each family from 2-component dimension mixtures with per-family weight laws.
  A fresh catalogue from the **existing** generator, same plan as production's 400k profile, fixes it:
  fits/pallet median 2 → 4, frac(fits==1) 45.8% → 14.5%, median volume 32,760 → 1,526 in^3, and it
  carries a real fulfillment section (16,120 of 40,000 SKUs) that the synthetic builder lacks entirely.
  → [02-field-a-real-catalogue.md](issues/02-field-a-real-catalogue.md)

- **[03] `_make_pool` pays TWO O(warehouse) costs per virtual placement, not one** — the
  `AISLE_COPIERS` dict copy AND the pool's own `set().union(*aisle_idx_sets.values())`. The second
  survives a copy-on-write fix. Then NARROWED by checking arm by arm: only `rank_random` among
  phase 2's arms reaches that union, so it is a one-arm problem and must not be used to complicate
  the fix for every arm.
  → [03-two-costs-per-pool-open.md](issues/03-two-costs-per-pool-open.md)

- **[04] The instrument is alive, and the quadratic is measured.** Four fixes in `Tests/`: the stale
  minlabor frozen oracle re-priced for ADR-0001 (proven by reproducing production's digest exactly),
  `run_meso` given the absolute clock it never had (without it lead-bearing trailers never arrive and
  fifo/lifo collapse into one stream), `run_fullfid`'s self-defeating bin caps removed, and
  `build_assets` taught the driver's own standing-yard construction site. First measurement ever taken
  of the inbound path: `plan_order` costs **T(T+1)** `place_load` calls and each opens ~5 pools, so
  pool opens run at **~5·T² per entry call** — 65,496 of them in a 10-batch run at yard depth 59,
  while useful placements FELL from 8,519 to 5,230.
  **The receiving whistle is the only lever that stands a yard**, which refutes the doors/crew/
  trailer-type knobs this effort's plan proposed.
  → [04-revive-the-instrument.md](issues/04-revive-the-instrument.md)

- **[05] The first yard ladder was mis-calibrated; its exponents are artifacts.** It reported
  `_make_pool` at k=1.85, r²=1.00 — the convicted suspect at a textbook exponent with a perfect
  fit — but its x axis moved 2.29 → 2.37, a **4% range**, so both numbers are noise dressed as a
  law. Root cause: the whistle rungs were calibrated on the TIGHT put recipe, and under production
  coverage the whistle cannot stand a yard at all (even 2 s reaches depth 12 against 59). The
  binding constraint is the ARRIVAL rate, not the service rate — which inverts the prediction made
  when the recipe was designed ("higher coverage helps twice"): at coverage 10 a SKU depletes ~80%
  of its stock before reordering, so reorders are larger but far rarer, and over a short run rarity
  wins. The archived artifact must not be cited.
  → [05-the-first-yard-ladder-was-miscalibrated.md](issues/05-the-first-yard-ladder-was-miscalibrated.md)

- **[06] PRODUCTION NEVER STANDS A YARD — the convicted quadratic is not phase 2's cost.**
  Measured on the real driver, real catalogue, standing yard, `gain_forecast`: **T = 1.26, max 2**,
  28 entry calls, 80 `place_load`s. `RECV_DAY_SECONDS = None` is the production default ("None = no
  whistle"), it is not derived under the era, and `PHASE2_RUN_DEFAULTS` does not set it — so by
  ticket 04's mechanism every freed door immediately pulls the next trailer and the yard empties
  inside every drain. This RETRACTS the ordering of this effort's own refactor queue: the O(T²)
  term is real, and at T=1.26 it costs six calls. It keeps its value as a standing risk with a
  named trigger (anyone who sets a receiving day walks into it), but it is not what the campaign
  pays for. The open contradiction: ticket 31 measured a gain cell at 1.6–1.9×, and 80 `place_load`
  calls cannot cost 450 s — so the evaluator's cost is **per UNIT, not per candidate**, which
  reorders the queue.
  → [06-production-never-stands-a-yard.md](issues/06-production-never-stands-a-yard.md)

- **[07] Copy-on-write aisle views: 64–117x less copying, byte-identical.** The first refactor,
  and the first measured BEFORE it was built. A pool touches 1.22 of 46 live aisles; the eager copy
  walked all forty-six, 9.3M set-element copies in 20 batches on one leaf. `AISLE_VIEWS` now sits
  beside `AISLE_COPIERS`, asserted to cover each other at import. Floats copy nothing at all (a read
  falls through; only writes overlay); sets and the two-level list dict materialize one aisle per
  access. `values()`/`items()` stay eager on purpose — a lazy one would hand out the live container.
  Byte-identical on all four pool adapters with the live dicts inside the digest, and four different
  digests across arms so the comparison is not vacuous. It also broke an existing sabotage test by
  moving the seam it patched — repaired by pointing it at the table production reads.
  **Worth −45.5% / −49.3% / −77.7% on the receive drain** (labor / cartlabor / minlabor), paired
  same-process. `rank_minlabor` is the big one and it is fulfillment's #1 and store's #2 arm.
  → [07-copy-on-write-aisle-views.md](issues/07-copy-on-write-aisle-views.md)

## Fog

- **The recipe is being asked for two things that pull against each other**: a real fixture wants
  HIGH coverage (Q > 1), a standing yard wants FREQUENT reorders. Low coverage buys the yard by
  degrading the fixture, which is what this effort was told not to do. The candidate answer is
  MORE BATCHES — a yard behind a binding whistle is an unstable queue, unlike the put queue the
  `growth-ladder-use-the-skus-knob` memory measured — but that is under test, not settled.
- Where the 1.6–1.9x pricing multiplier actually lives, now that the candidate loop is ruled out
  at production scale. The per-UNIT hypothesis (entries x units_per_trailer x tiers) is stated in
  ticket 06 and not yet measured.
- **Whether the campaign's 1.6–1.9x priced/unpriced multiplier moves.** The drain is 45–78% cheaper
  at meso scale, but that ratio is what actually restates phase 2's 8.6–9.7 h sizing, and it needs a
  same-day priced-vs-unpriced control at campaign shape.
- How much of a coupled priced run is currently in **no section at all** — the coupled leaf returns
  before `check_reorders`, so the drain may be entirely unattributed.

## Out of scope

- Changing the inventory generator. Ticket 02 settled that it needs no change.
- The per-cell batch-script re-precompute (~37 s/cell). `inbound-optimization` ticket 31 measured it
  and recorded that it is not worth a build; that record stands.
- Running and publishing phase 2 itself.
