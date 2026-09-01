# Extend the gain bundles

Type: task
Status: open
Blocked by: 08

## Question

Extend `_gain_bundle_for` so the gain policies can run on the placement families phase 1
actually selects. **Most of the scope is unknown until phase 1's ranking exists** — that part
is blocked by 08 on paper but genuinely gated on the phase-1 run, and must not start before
that ranking is recorded in the selection artifact.

**One piece is NOT gated and blocks phase 2 outright: the mandatory `fifo` rider has no
faithful bundle**, so every phase-2 gain cell refuses it at worker startup. See the first
comment below — that piece can and should be done first.

The gain evaluator today serves exactly four restock families — `tmin`/`tmax` (the proven
k-cheapest merge), `rank_popularity` (its own pool rebuilt over aisle-state copies) and
`rank_random` (the pool with a deterministic stand-in selector under expectation pricing).
Every other family raises loudly by design, and the raise's own docstring anticipated this
ticket: *"phase 2's top-k should extend this map consciously, not silently."*

Constraints from 08's resolution:

- **The cap is THREE new families**, decided before phase 1 ran so the campaign stays
  costable. If phase 1's top five holds more than three unfaithful families, take the three
  highest-ranked and backfill from the faithful set — do not quietly widen the cap.
- **Faithful-to-arm is the whole contract.** An evaluator that cannot rebuild the arm's own
  pool over copies would price a fiction under that arm's name. The `rank_popularity`
  precedent is the pattern: call the arm's OWN builder over the copies, so its selector
  closes over the copied `aisle_demand_sum` exactly as the production pool closes over the
  live one — a future tiebreak change then cannot leave the evaluator pricing a stale
  policy under the arm's name.
- **The zoning refusal stays.** A gain policy under velocity zoning still raises: the
  virtual pool ignores the band filter, so its gains would price bins the arm cannot grant.
  Phase 2 runs zoning off, so this is not in the way — do not weaken it to make a family fit.
- 06's Tier-1 cross-checks (equivalence + sabotage) apply to every family added, as they did
  for the families that landed with 14.

Likely candidates, from what tends to rank well on labor: the `rank_labor` family,
`cluster_map`/`cluster_map_rank`, and the `map`/`map_rank` pair. Two cautions if the map
family is drawn: its exact-LAP gate admits a small share of BinKey classes and a very small
share of assigned UNITS, so greedy earns nearly all of a Map-family result at scale — an
evaluator that models the exact solver faithfully would be modelling the rare path. And
`cluster_map_rank` is already the top `t_reord` cost in the suite, so extending it has a
runtime consequence beyond the build.

## Comments

2026-08-31, from resolving [Build the run-shape layer](18-build-the-run-shape-layer.md).
**THIS TICKET IS NO LONGER OPTIONAL, AND ITS FLOOR IS KNOWN BEFORE PHASE 1 RUNS.** `fifo`
needs a faithful bundle, mandatorily, or phase 2 cannot run at all.

The chain: `strategy_runner` builds `_gain_bundle_for(strat, …)` for EVERY arm in the set
whenever a gain policy is named — the gate is on the POLICY, not on the arm. 08 makes `fifo` a
mandatory rider in the phase-2 arm set. `fifo` is not in `FAITHFUL_GAIN_FAMILIES`. So every one
of phase 2's five gain cells refuses `uni_fifo_norsl` / `opt_fifo_norsl` at worker startup.
Verified directly, not inferred: `_gain_bundle_for` raises for both fifo arms and builds for
`uni_tmin_norsl` (pinned by
`Tests/unit/test_restock_selection.py::test_a_gain_bundle_really_is_refused_for_the_rider`).

Consequences for the scope here:

- **`fifo` sits OUTSIDE 08's cap of three.** The cap governs which OPTIONAL families are worth
  extending; the rider has no opt-out, so counting it against the cap would silently buy one
  fewer real family. `run_restock_selection` reports it in its own field,
  `rider_needs_bundle_extension`, and logs it loudly.
- **It can start BEFORE phase 1.** The rest of this ticket is genuinely gated on the ranking,
  but this piece is not: `fifo` is in every possible arm set. Doing it first also de-risks
  phase 2 rather than discovering the refusal at the start of a 480-unit sweep.
- **The faithfulness question is real, not clerical.** `fifo` is `_build_uniform` — a uniform-
  random enqueue, not a ranked pool — so neither existing adapter fits directly. `rank_random`
  is the nearest precedent (a deterministic stand-in selector under expectation pricing, the
  no-RNG deviation 04 recorded), and the same argument may carry: an ordering entry may consume
  no RNG. Whether an expectation over uniformly-chosen bins is FAITHFUL to what the arm does,
  or is a fiction priced under its name, is this ticket's first decision.
- A rejected shortcut, named so it is not re-proposed: exempting `fifo` from the bundle (a
  policy-and-arm gate instead of a policy gate) would leave the baseline arm running v1 ordering
  inside a gain cell, so the cell's own baseline would be measured under a different inbound
  policy than the rows it baselines. That is worse than refusing.

The faithful set is now a NAMED constant, `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, and this ticket
extends it rather than only extending `_gain_bundle_for`'s dispatch chain. It exists because
the phase-1 selector has to know which chosen rules need extending without importing the
simulation; `Tests/unit/test_restock_selection.py::test_the_faithful_set_is_the_one_the_driver_
actually_accepts` pins the constant against the branches the driver really has, so adding a
family to one without the other fails.

Two consequences for the work here:

- **The cap is enforced upstream now, not remembered.** `run_restock_selection` applies
  `--extension-cap` (default 3) while choosing, backfills from the faithful set, and records
  `backfilled_past` in the artifact. So this ticket's scope arrives as a LIST in
  `restock_selection.json` (`channels.<ch>.needs_bundle_extension`), per channel — and the two
  channels may legitimately hand over different families.
- **Adding a family is now two edits, both required**: the branch in `_gain_bundle_for` AND the
  name in `FAITHFUL_GAIN_FAMILIES`. A branch without the name would leave the selector
  backfilling past a family it could actually run; a name without the branch would put a rule
  into phase 2's arm set that dies at its first drain.

2026-08-31, from resolving "Design the phased funnel" (08): a related unmeasured cost that
belongs to whoever runs phase 2 rather than to this build. 13 recorded the `'all'`
futuresight window as O(n²·|batch|), which is verified — `_futuresight_window` copies the
whole remaining script once per batch, n(n−1)/2 batch-copies over a run. But that is the
MINOR term: `_window_rates` re-aggregates the window once per ENTRY CALL, i.e. once per
DRAIN, so the real cost is the n² multiplied by drains per batch. If the pilot's bench shows
it biting, the legal fix is memoizing `_window_rates` keyed on `demand_v` (the window is
replaced wholesale by the injection that bumps it) — 06's "legal-keyed-not-built" case, not
new cache machinery.
