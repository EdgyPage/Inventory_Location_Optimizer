# Build the line floor

Type: task
Status: open
Blocked by: 16

Graduated from [Choose the coverage floor](15-choose-the-coverage-floor.md), decisions 1-6 and
8. AFK build, flag-off byte-identical. Skills: `codebase-design` (the floor is a change to
`coverage.stock_levels`' contract), `domain-modeling` (the glossary entries land with it).

## Question

Replace the unit floor in `Optimization/simconfig/coverage.py` with the line floor, read off
the stamped line distribution:

- `L_s = ceil(floor_lines x line.mean())`; `Q = max(round(coverage_days x d_s), L_s)`;
  `rp = min(Q - 1, max(round(d_s x (lead + safety_days)), L_s))`. A floored SKU therefore runs
  base-stock (`rp = Q - 1`).
- **`floor_lines`** (1.0, `assumed`) on `STAFFING_KEYS` and all five seams (`--floor-lines`),
  beside `coverage_days` / `safety_days`, whose defaults stay 10 / 2.
- **The record** (`calibration[<pair>].coverage.final[<channel>]`): the floor stats become
  line-denominated (`floor_line_skus`, `floor_line_share`, `floor_line_demand_share`,
  `realized_coverage_days` over the SKUs above the floor) and gain the demand-weighted
  **expected first-pass fill rate**, `sum d_s x E[min(X, S_s)] / sum d_s x E[X]`, per section.
- **`pipeline_qty`** stamped per SKU under the era (`round(d_s x lead)`), preferred by
  `_fire_reorders` when present, the `rp x lead / (lead + 1)` heuristic kept otherwise.
- `rescale_section` still resets `stock_plan`; the planner re-packs line-sized quantities.
- **Glossary**: *Stock coverage* rewritten (already done at resolution), *Line floor*,
  *Base stock*, *Line distribution* present.

Done when: the formulas are pinned by hand in `Tests/unit/test_coverage_rescale.py` (a floored
SKU's `rp = Q - 1`, an above-floor SKU's line-sized safety, the fill rate for a known Poisson);
the input rides every seam; the record carries the fill rate; flag-off is byte-identical; and
ONE 40-day era run on the reference pair, read through the equilibrium report after
[Narrow the drained clause to labour](18-narrow-the-drained-clause-to-labour.md) lands, shows put
and receiving utilization in band on both leaves, the store's realized missed share against its
stamped fill rate, and days drained -- with the store's answer recorded as "no wave: a trickle in
lockstep with picks". That run is a check of the build, never a calibration step.
