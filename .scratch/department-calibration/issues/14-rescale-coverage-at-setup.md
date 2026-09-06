# Rescale stock coverage at setup

Type: task
Status: open

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
