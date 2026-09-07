# Field the requirement

Type: task
Status: open
Blocked by: 22

Graduated from [Field the floor](19-field-the-floor.md), decisions 1-4 and 8-11. AFK build.
Skills: `codebase-design` (the planner's one contract), `test-developer`, `code-reviewer`.

## Question

Not a decision: the build that makes the floor a PROMISE. The planner fields every SKU at
exactly its derived level, packed the way `bucket_requirements` packed it, and refuses when it
cannot.

What lands:

- **One planner contract.** `plan_warehouse` sizes every bucket in demand mode from
  `bucket_requirements` and fields each SKU at exactly that packing -- no "emptiest bucket"
  re-choice, no phase-2 growth, no shrink. `sample_to_capacity`'s grow/shrink path and the
  fulfillment `mode: fixed` / `distribution` / `target_bins` config are removed (depth classes
  and the aisle split survive). `store_fill` / `ff_fill` keep 0.85 and mean "the share of each
  bucket the levels occupy at setup".
- **Emitted capacity.** Under an aisle split, demand replicas inflate by `1 / (1 - loss)`; the
  promise is checked per bucket against what was emitted: `emitted x fill >= requirement`.
- **Refusal.** A `max_bins` / `max_aisles` cap that binds below a bucket's requirement refuses
  at setup, naming the bucket and the bins short; `min_bins` is honoured as a floor on bins.
- **The record.** `coverage.final[<ch>]` gains `fielded`: `below_floor_skus: 0`,
  `above_floor_skus: 0`, and a per-bucket `requirement / capacity / free` table;
  `planned_sum_q == sum_q` is asserted; the fill rate is priced once (pre-plan equals
  post-plan, and the code says so).
- **Verification.** Unit tests for the exact fielding (a medium-only SKU with a floor of 13
  gets 13 units in medium bins), the split inflation, the refusal message, and the record
  block. Re-plan the check run's pair (`comparison_20260906_222118`, the reference pair) and
  confirm 0 SKUs below floor on BOTH sections at the derived levels, with the fulfillment
  section's aisle count reported against the run's 977.

Done when: the tests above are green; the re-plan reports 0 below / 0 above on both sections;
the glossary's *Line floor* promise sentence is true of the code; `context/` anchors and the
architecture layer are re-synced by their maintainers.
