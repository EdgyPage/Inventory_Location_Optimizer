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

## Fog

- How the whistle becomes a LADDER. It stands the yard, but it is a service-rate knob whose rungs
  must be stated against the fixture's own uncapped receiving makespan, not as absolute seconds —
  and it starves put-away as it bites, so the ladder needs an inbound-on control that does not stand.
- Whether `_make_pool`'s per-placement aisle-dict copy can be made copy-on-write without the pool
  builders iterating a whole dict somewhere.
- How much of a coupled priced run is currently in **no section at all** — the coupled leaf returns
  before `check_reorders`, so the drain may be entirely unattributed.

## Out of scope

- Changing the inventory generator. Ticket 02 settled that it needs no change.
- The per-cell batch-script re-precompute (~37 s/cell). `inbound-optimization` ticket 31 measured it
  and recorded that it is not worth a build; that record stands.
- Running and publishing phase 2 itself.
