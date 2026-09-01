# Extend the gain bundles

Type: task
Status: open
Blocked by: 08

## Question

Extend `_gain_bundle_for` so the gain policies can run on the placement families phase 1
actually selects. **Scope is unknown until phase 1's ranking exists** — this ticket is
blocked by 08 on paper, but genuinely gated on the phase-1 run, and must not start before
that ranking is recorded in the selection artifact.

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

2026-08-31, from resolving "Design the phased funnel" (08): a related unmeasured cost that
belongs to whoever runs phase 2 rather than to this build. 13 recorded the `'all'`
futuresight window as O(n²·|batch|), which is verified — `_futuresight_window` copies the
whole remaining script once per batch, n(n−1)/2 batch-copies over a run. But that is the
MINOR term: `_window_rates` re-aggregates the window once per ENTRY CALL, i.e. once per
DRAIN, so the real cost is the n² multiplied by drains per batch. If the pilot's bench shows
it biting, the legal fix is memoizing `_window_rates` keyed on `demand_v` (the window is
replaced wholesale by the injection that bumps it) — 06's "legal-keyed-not-built" case, not
new cache machinery.
