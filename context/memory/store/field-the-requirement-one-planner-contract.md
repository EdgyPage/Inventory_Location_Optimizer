---
name: field-the-requirement-one-planner-contract
description: "the planner sizes every bucket from bucket_requirements and fields every SKU at exactly its declaration -- no growth, no shrink, no rng; Singleton and FulfillmentBin SUBCLASS Pallet, and both aisle splits must round UP or they refuse the run"
metadata:
  node_type: memory
  type: project
  modified: 2026-09-07T22:30:00.000Z
---

Landed 2026-09-07 (department-calibration 23). `PlanningMixin._packing` answers BOTH halves of
the planner in one pass -- what the declared levels need per bucket, and how each SKU is packed
to need it -- so sizing and fielding cannot drift apart. `sample_to_capacity` is gone;
`field_requirement` records that packing as each order's `stock_plan`. Planning is deterministic:
`plan_warehouse` takes no `rng`.

**Three traps, each of which cost a debugging cycle here.**

- **`Singleton` and `FulfillmentBin` SUBCLASS `Pallet`.** `not isinstance(u, Pallet)` therefore
  calls every singleton a pallet. In the packing's run-length encoder that repacked a 3-item
  remainder as a partial pallet and charged a bucket the warehouse was not sized for. Test
  membership POSITIVELY: `isinstance(u, (Singleton, FulfillmentBin))`.
- **Both aisle splits must round UP.** `_demand_replicas` leaves only the ~3% slack of one
  `ceil`, so rounding a segment or depth-class aisle count to NEAREST sacrifices bins the levels
  were sized for and REFUSES the run. `_aisle_split` lost 33% at a declared 30%;
  `_ff_depth_split` refused 30 of ~90 depth-class configurations.
- **The aisle split changed what it measures.** `_split_inflation` adds compensatory aisles, so
  a split arm trades AISLES for travel, not capacity for travel. It used to raise fill by
  shrinking the shelf under fixed stock, so an archived `ks` sweep is not comparable to a new one
  (`cells._tightest_split`'s "largest loss = fewest bins" is likewise no longer true).

**A side effect worth knowing.** The retired sampler enumerated every SKU x every tier while
planning, incidentally warming `Storage_Primitive`'s fit `lru_cache`s; `field_requirement` does
not, which broke `Tests/calltree`'s same-seed determinism gate until `build_assets` warmed them
explicitly.

See [[a-bin-cap-is-self-defeating]], [[warehouse-size-comes-from-the-levels]],
[[stock-plan-overrides-packing]], [[hand-run-test-tiers-rot-silently]].
