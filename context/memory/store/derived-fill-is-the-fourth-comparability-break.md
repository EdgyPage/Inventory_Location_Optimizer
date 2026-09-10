---
name: derived-fill-is-the-fourth-comparability-break
description: "since 2026-09-10 the era sizes every bin bucket from the stationary fragmentation (fill derived per bucket, floored at min_headroom 0.05), so the reference warehouse moved 2,761 -> 2,536 aisles and absolute travel numbers across that commit are not comparable; flag-off is unchanged"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4ed53723-9756-4232-86c4-295634dbb5e5
  modified: 2026-09-10T21:05:56.028Z
---

The fourth comparability break on the department-calibration map (after the per-item charge
fc7a46a5, the placement pools a033aff, and ADR-0003's drain order): **under the era the planner's
fill is no longer the typed 0.85.** Each bucket is sized to HOLD `max(requirement + E[extra],
requirement / (1 - min_headroom))` bins, `E[extra]` being the stationary fragmentation the record
stamps per bucket (`fielded.buckets[].expected_extra`, ticket 34) and `min_headroom` a new era-only
staffing key (0.05, `assumed`). Landed 2026-09-10 (department-calibration ticket 35).

**The reference pair moved:** 2,761 aisles / 2,466,650 bins (2026-09-09 run) -> 2,536 aisles /
2,311,000 bins. Store capacity 1,229,750 -> 1,198,300 (section fill 0.872, 20 of 48 buckets at the
5% floor); fulfillment 1,236,900 -> 1,112,700 (fill 0.944). The store's `small` pallet buckets GREW
(conveyable/food/small 76,000 -> 98,000, fill 0.652: picked singleton remainders return as pallets
of 1) while every `singleton` bucket SHRANK to the floor (fill 0.95). Crews (K = 31 / 23) and floors
(1.2728 / 1.2668) did not move.

**Why:** a warehouse with 15% headroom nobody derived was where the store's free-index slide ended
(ticket 32); now the warehouse holds the declaration AND the fragmentation base stock creates, and
the free share at setup is a derived number per bucket. Every travel distance changes with the
geometry, so pick/put/labor numbers on either side of the commit are not row-comparable.

**How to apply:**
- A run's `staffing.calibration[<pair>].coverage.final[<ch>].fielded.fill` block says which regime
  sized it (`provenance: derived` vs `assumed`); compare only within one.
- A rebuild (`run_analysis`, `run_map_precompute`) reads the holds off that record
  (`era_coverage.holds_at`) and never re-derives; an older era record (no `fill` block) rebuilds at
  the typed fill it ran under, correctly.
- `--store-fill` / `--ff-fill` are refused under the era; `--min-headroom` is refused flag-off.
- Found on the way: before this commit a MULTI-cell era run lost its coverage record (the first
  cell's derived block overwrote the freeze's), so its analysis rebuilt nothing; fixed in
  `workunits._record_derived`, but a multi-cell era run recorded earlier cannot be re-analysed from
  its catalogue.

See [[free-bins-counts-the-whole-geometry]], [[empty-bin-preference-is-structural]],
[[per-item-charge-hard-break]], [[drain-order-is-smallest-first]],
[[placement-pools-and-the-audit-point]], [[config-knob-has-five-seams]].
