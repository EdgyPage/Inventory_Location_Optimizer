---
status: accepted
date: 2026-09-07
---

# The catalogue carries no stock levels; every run derives them

A generated catalogue authored each SKU's order-up-to quantity, reorder point and a packing
plan, as `coverage_batches x expected batch demand`. A batch is not a unit of time, so the
authored level was a bespoke conversion implicit in the inventory: on the reference catalogue
it was worth 1,771 store days and put the first reorder wave past the end of any window. The
calibrated era already discarded it at setup -- `rescale_section` overwrites both levels from a
declared coverage in days and clears the packing -- while the flag-off planner kept it only as
the base it grew and shrank by share. Two planner contracts for one quantity, and the quantity
was doing nothing in either. We removed it: `equilibrium_qty`, `reorder_point` and `stock_plan`
leave the `cartons` table as a schema vintage, the generator's coverage-in-batches knobs go
with them, and the coverage fixed point runs in every mode, using the reporting frame as its
day when no era is on. Stock is a run's declaration, never a SKU's fact.

## Considered options

- **Keep the authored levels as the flag-off base** -- byte-identical, the planner unchanged.
  Rejected: an inherited default nobody chose, the coverage-in-batches trap kept at its source,
  and an era-only planner rule to carry forever beside the legacy one.
- **Declare `lines_per_day` flag-off and skip the fixed point** -- cheaper setup. Rejected: a
  knob nobody will set right, and a second way to get a level.
- **Retire the flag-off regime** -- one clock as well as one contract. Not decided here: a
  scope call for a different map.

## Consequences

- A hard break for every flag-off run's inventory: levels, packing, warehouse shape and every
  placement golden change once. The era had already ended comparability (ADR-0001).
- **Cached batch scripts move with it.** The fingerprint hashes no level, but it is taken over
  the SAMPLED inventory, and which SKUs the planner samples follows the levels. A flag-off run
  therefore re-precomputes its batches once; no archive is invalidated, because a finished run's
  fingerprint is a property of the file it was written with.
- Setup pays the fixed point in every mode (~8 minutes per pair on the reference catalogue;
  its inputs are fingerprint-cached).
- The fulfillment fixed tier distribution loses its reader and is retired with the levels;
  depth classes and the aisle split survive.
- The run's own planned inventory still records the fielded levels and packing; that is a run
  artefact, not the catalogue.

## What landed (2026-09-07)

- **The declaration is a table, not four columns.** `cartons` lost `equilibrium_qty`,
  `reorder_point`, `stock_plan` *and* `pipeline_qty`; a new `stock_levels` table
  (`sku` + those four) holds a run's declaration. The fourth column moved with the other three
  because it is the same kind of fact — the lead pipeline the coverage rescaling stamps — and
  leaving it in `cartons` would have kept exactly the always-NULL column this decision rejected.
  A generated catalogue leaves `stock_levels` EMPTY, and that emptiness is the contract:
  `Order.stock_declared()` is False, as against a NULL column, which reads as an authored level
  that happens to be missing. Both files remain ONE family and one shape.
- **`inventory_db` moved `025f4b1548a9` -> `4536857cb860`.** The outgoing vintage stays vetted,
  and its `stock_levels` override reads the four columns out of ITS `cartons` — so an archived
  planned inventory still yields the levels its run fielded, under the same logical names, with
  no consumer branching on a version. The two older vintages are served the same way, with
  `pipeline_qty` NULL.
- **`Order.declare_stock` is the one mutation site** for the four slots, and
  `Order.stock_declared()` reports whether a run has made one. `Order.build` takes no level.
- **`inventory_common._equilibrium_qty` RAISES `UndeclaredStock`** instead of defaulting to 1.
  The default was the dangerous half of this change: the warehouse bin count is demand-derived
  from the levels on *every* path (`sample=False` skips only the SKU sampling, never
  `bucket_requirements`), so an undeclared catalogue would have sized every bucket for one unit
  per SKU and built a warehouse an order of magnitude too small, silently.
- **A rebuild re-declares from the run's own record.** `run_analysis` and `run_map_precompute`
  re-plan a finished run's warehouse from its original catalogue, which now holds no level.
  They pass that run's `staffing.calibration[<pair>].coverage` to `build_shared_assets`, which
  applies one `rescale_section` pass at the RECORDED `lines_per_day`
  (`era_coverage.declare_from_record`) — exact, and a second rather than the eight minutes a
  re-run of the fixed point would cost, which would also have re-derived from this checkout's
  geometry rather than the one the run fielded. A rebuild with neither a declaration nor a
  record REFUSES; `run_analysis` no longer lets that failure become "Config stage: 0 job(s)".
- **Round 0 of the fixed point is an analytic seed** (`era_coverage.seed_lines`), because there
  is no longer a level to plan. It prices the section at its analytic seconds per unit — no
  geometry, no travel term — which starts `n` too high, the direction the bracketed secant
  needs. The record's `catalogue` block (the implied coverage of the authored levels) is gone,
  replaced by `seed`; `coverage.implied_coverage` is deleted.
- **One planner contract means one pipeline contract.** `load_run_inventory` no longer clears
  the `pipeline_qty` stamp flag-off. That guard existed so flag-off stayed byte-identical with
  an archive whose levels the catalogue authored; now that every run declares its own, honouring
  the stamp in one mode and discarding it in the other would field a level whose reorder point
  encodes a LINE while pricing its pipeline by a heuristic that assumes the reorder point
  encodes lead-time demand.
