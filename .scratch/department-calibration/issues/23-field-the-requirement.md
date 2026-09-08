# Field the requirement

Type: task
Status: resolved
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

## Answer

**LANDED 2026-09-07.** The planner has one contract: `bucket_requirements` states what the
run's declared levels need per bucket, every bucket is sized from that statement, and every
SKU is fielded at exactly its declaration packed the way that statement counted it. Sizing
and fielding are now literally one pass (`PlanningMixin._packing` returns
`(requirement, packing)`), so the two halves cannot drift apart -- and the pass that used to
be run twice costs 1.09s per 20k SKUs instead of 1.86s.

**THE CHECK (the ticket's own gate).** Re-planned the check run's pair
(`comparison_20260906_222118`) declaring at that run's OWN recorded line count
(`declare_from_record`), so the planner is the only thing that differs:

| | the run | re-planned |
|---|---|---|
| store SKUs below their line floor | 7.9% | **0** |
| fulfillment SKUs below their line floor | 48,466 (30.3%) | **0** |
| SKUs fielded above their declaration | 64,989 | **0** |
| planned sum Q vs declared | 4,195,315 vs 4,354,443 | **equal on both sections** |
| fulfillment aisles | 977 | **1,232** (19 predicted 1,232) |
| ff expected first-pass fill | 0.782 | 0.922 |

Buckets sit at 81.7% (store) / 84.9% (ff) occupied against the declared 0.85 -- `store_fill`
/ `ff_fill` now mean what decision 11 says. Preflight ran both canaries end to end; the
run-tree shape is unchanged.

**What landed, against the ticket's list.** All of it. `plan_warehouse` sizes both regimes
from the requirement (fulfillment's `mode` / `distribution` / `target_bins` retired -- and
they RAISE rather than being ignored, because a sizing dict is assembled from CONFIG and
restored from a run spec, and a key nobody reads is how a caller comes to believe it asked
for something it did not get). `sample_to_capacity` became `field_requirement`: deterministic,
no `rng` anywhere in planning, no re-choice, no growth, no shrink. The promise is checked on
EMITTED capacity per bucket (`emitted x fill >= requirement`) and refuses with
`UnfieldableRequirement`, which carries the shortfall STRUCTURALLY (`.short`) as well as in
prose. `coverage.final[<ch>]` gains `fielded`, and `fixed_point` RAISES on it.

**Three defects found while building, each caught by a new test.**

1. **`Singleton` and `FulfillmentBin` SUBCLASS `Pallet`**, so `not isinstance(u, Pallet)`
   calls every singleton a pallet. It repacked a 3-item remainder as a partial pallet and
   charged a bucket the warehouse was not sized for. Test the flag POSITIVELY.
2. **`_aisle_split` rounded its segment count to nearest**, sacrificing more than the
   declared `capacity_loss` -- 1,800 bins at a 30% loss came out 1,200, a realized 33%. That
   extra is a loss `_split_inflation` cannot price, and it landed a bucket 47 bins short.
3. **`_ff_depth_split` had the same defect**, and worse: 30 of ~90 depth-class configurations
   REFUSED (85-295 bins short) on configs that plan fine at HEAD. Decision 9 said "depth
   classes need nothing"; they needed the same `ceil`. 0 of 42 refuse now.

**Two consequences the ticket did not foresee, both worth knowing.**

- **A bin cap is now self-defeating, not merely refusable.** The coverage fixed point sizes
  the warehouse from the levels and reads the levels back off the geometry, so a smaller
  warehouse is a shorter trip, a HIGHER derived lines/day, and BIGGER levels than the cap was
  trying to contain. `--coverage-days` is the lever that makes a run small; the preflight
  canary and eight test fixtures were moved off caps onto it, and the README and the
  `--s-max-bins` warning say so.
- **The aisle split changed what it measures.** Decision 9's inflation adds compensatory
  aisles, so a split arm trades AISLES for travel, not capacity for travel. It used to raise
  fill by shrinking the shelf under a fixed stock. An archived `ks` sweep is not comparable
  to a new one. `cells._tightest_split`'s "largest loss = fewest bins" rationale is likewise
  no longer true (the freeze is still correct, for a stronger reason).

**One deviation from the ticket, flagged.** The record key is `above_declaration_skus`, not
`above_floor_skus`. On the pair that produced this map the two coincided -- 100% of both
sections sat ON the floor, so a level above the floor WAS a grown one -- but on a catalogue
where coverage exceeds the floor, "SKUs above their floor" is nearly all of them and reads as
an alarm in the JSON. What is worth being zero is the count of levels the planner moved.

**Also repaired, off the reviews.** The calltree framework's own determinism gate had broken:
two same-seed captures disagreed because the retired sampler enumerated every SKU x every
tier while planning and thereby warmed `Storage_Primitive`'s fit `lru_cache`s before the
first trace, which `field_requirement` does not. `calltree_scenarios.build_assets` warms them
explicitly, so the counts stay real. `test_the_default_config_does_not_reach_the_put_away_path`
was re-baselined to the HELD path: smaller shelves now fire one refill pass, which is correct.

**Verification.** 1797 unit + 425 e2e/integration green (one PRE-EXISTING failure, unrelated:
`test_runschema_contract::test_the_evaluation_attribution_matches_the_registry_both_ways` --
`throughput.audit` is in the registry but not in the `figures_throughput_pngs` attribution,
and it fails on a clean tree at 697f3128). All nine gates green. Six source mutations run
against the new tests -- the floor counter hardcoded to 0, the `fielded_block` regime filter
inverted, the split inflation disabled, ff tiers sized uniformly, the refusal's detail lines
deleted, every level grown by one -- all caught; three of those were MISSED by the first draft
of the tests and are the reason the assertions read structured values rather than prose.
