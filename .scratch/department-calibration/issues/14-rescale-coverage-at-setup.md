# Rescale stock coverage at setup

Type: task
Status: resolved

Graduated from [Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md),
decision 2 (user, 2026-09-06): coverage is a RUNTIME rescaling under declared days, never a
generator knob. The derivation note's section 5 holds the argument
([assets/expected-travel-derivation.md](../assets/expected-travel-derivation.md)). AFK build,
flag-off byte-identical. Skills: `codebase-design` (the fixed point through the sizing is a seam
on `plan_warehouse`).

## Question

Land `coverage_days` and `safety_days` as declared staffing inputs (`STAFFING_KEYS`, provenance
`assumed` until typed), and under the era re-derive every SKU's `equilibrium_qty =
max(1, round(coverage_days * d_s))` and `reorder_point` by the generator's own formula with the
day as the unit, where `d_s = n * pi_s * (lambda_s + e^-lambda_s)` and `n` is the pair's fixed
point. Because the warehouse is sized from Q and `n` depends on the built geometry, this is a
pair-level fixed point -- Q(n) -> `plan_warehouse` -> geometry -> `expected_travel.solve_n` -> n --
converging in 2-3 rounds under the class-uniform model (each round is seconds: the routing is
per aisle shape). The catalogue's `equilibrium_coverage_batches` stays as the flag-off shape;
its rounding floors (Q >= 1, r >= 1) still make the lowest-demand SKUs reorder on their first
pick, which the answer should measure and state.

Also rides along: a PLACEMENT FINGERPRINT beside the per-arm `expected_pick` stamp (a hash of
the arm's bin map), so an expectation can be tied to the placement it was computed from --
today the stamp names `placement: initial` and the geometry fingerprint only.

Done when: the two inputs ride all five seams and the record; a 40-day era run on the
reference pair sees its first reorder wave inside the window (ticket 09's lag was 42 / 31
days); the flag-off path is byte-identical (a test proves it); the derivation note's section 5
is amended with the measured rounding-floor share.

## Answer

**Resolved 2026-09-06.** Built as specified, flag-off byte-identical, verified on a 40-day era
run of the reference pair -- and the run is the finding: under the formula's unit floor a
coverage short enough to put the first reorder wave inside the window turns the store section
into a one-unit shelf. The build stands; the floor is a decision, graduated to
[Choose the coverage floor](15-choose-the-coverage-floor.md).

- **The seam.** `Optimization/simconfig/coverage.py` (pure): `d_s = n * pi_s * (lambda_s +
  e^-lambda_s)`, the generator's formula in days (`stock_levels`), `rescale_section` (mutates
  Q / rp, resets `stock_plan`, returns the floor shares), `implied_coverage` (what the
  catalogue's own levels were worth), `converged`. `Optimization/simdriver/era_coverage.py`:
  `stage_a` (the derivation's first half, factored out so the loop and
  `_derive_staffing_for_pair` price a section with ONE function; the derivation reuses the
  loop's last pricing) and `fixed_point` -- `Q(n) -> plan_warehouse -> build -> solve_n -> n`
  at pair level, driven from `sim_assets.build_shared_assets` behind `era_on()` wherever a
  plan samples (an analysis-shape rebuild and a frozen inventory keep their levels). Flag-off
  the planner is called exactly once, as before; a test proves the builder's plan is the
  planner's plan and the loop is never entered.
- **The two inputs.** `coverage_days` (10.0) and `safety_days` (2.0) in settings, CONFIG,
  `STAFFING_KEYS` and `_SCALAR_DEFAULTS`, `--coverage-days` / `--safety-days`; the flags, the
  run-spec record, both restore sites and the worker payload come by construction from the
  list. Provenance `assumed` until typed. Both defaults mirror the generator's 10 / 2 batches
  and are PROVISIONAL (below).
- **The record.** `staffing.calibration[<pair>].coverage`: the declared days, `catalogue`
  (the implied coverage the generation-batch levels amounted to), every round's line count,
  aisles and bins, `final` (the floor shares of the levels the run fields, pre-plan),
  `planned_sum_q`, `residual`, `converged`.
- **The loop is not a plain iteration.** `n_out(n_in)` is monotone decreasing with gain above
  one on this catalogue (fulfillment went 1,818 -> 14,832 -> 4,048 -> 10,797 lines a day
  under the plain iterate), so `next_guess` iterates plainly only until the root is bracketed
  and then takes the log-space secant between the bracket ends (pulled to the midpoint near
  an end). 4 rounds / 490 s at full scale (400,000 SKUs) to a 0.5% residual; a 20k-SKU
  canary converged in 3 rounds / 52 s.
- **The placement fingerprint.** `strategy_runner.placement_fingerprint(bin_map)` (16 hex
  digits, order-independent) rides the per-arm `expected_pick` stamp with `bins_filled`.
  Both FIFO arms on both leaves stamp the SAME fingerprint -- the byte-identical `uni_fifo`
  / `opt_fifo` pair (memory `fifo-restock-ignores-initial-placement`), now a checkable fact.
- **The measurement** (`comparison_20260906_173635`, full detail in
  [assets/expected-travel-derivation.md](../assets/expected-travel-derivation.md) section 5a):
  the catalogue's levels were worth **1,771 store days / 246 fulfillment days**. At 10 days the
  store's warehouse collapses from 5,814 to 722 aisles, so its 25 pickers fill 4.7x the lines
  (556 -> 2,633 a day; fulfillment 1,818 -> 6,902), and at the fixed point 70.9% of store SKUs
  hold ONE unit (37% of store demand; fulfillment 20.5% / 4.0%). The sharper number is the
  line: planned median stock 1 (store) / 4 (fulfillment) against ~10.4 units a line, so
  93% / 87% of SKUs hold less than one line. The closed form prices that starvation
  (`units_per_line` 2.02 / 4.92 vs the analytic 10.4), and the run shows it: reorders from
  day 1, **0 of 40 days drained** on both leaves, 3.8% / 4.6% of demanded units picked,
  90% / 87% of the carry `unpicked_unstocked`, 0.53 M / 1.21 M units standing on day 39, crews
  put 237 / receiving 82 (42 / 16 under the catalogue's levels). The done-when's "first
  reorder wave inside the window" is met on day 1, trivially: the section reorders on its
  first pick, every day. A default that keeps the store a warehouse and one that lands a wave
  inside 40 days are mutually exclusive under a unit floor -- that is ticket 15.
- **Tests**: `Tests/unit/test_coverage_rescale.py` (24: the formulas and floors by hand, the
  loop with a fake planner incl. a gain-above-one map, the seams, flag-off identity through
  the real builder, the fingerprint); the six existing staffing / era / shaping files still
  green (173), full `Tests/unit` green. Gates: context, run-tree contract (preflight re-proved
  the tree with its two canaries: unchanged), profile tree, path guard, docref, memory.
- **Docs**: `CONTEXT.md` gained *Stock coverage* (and *Staffing record* names the days);
  the derivation note's section 5a; memory `coverage-in-days-floors-the-store-section`.
- **Rides along**: `expected_travel.expected_pick` takes its pace from `SpeedProfile` -- the
  hand-conversion ratchet (`test_the_hand_conversion_count_only_falls`) stood at 22 against
  its budget of 21 since 13, and `Tests/unit` is green again (1,731).
- **Not on this ticket**: the architecture layer needs its regen for the two new modules
  (the `architecture-maintainer`'s job, as after 13).
