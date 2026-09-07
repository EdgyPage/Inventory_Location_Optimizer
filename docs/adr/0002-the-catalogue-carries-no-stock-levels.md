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
- Setup pays the fixed point in every mode (~8 minutes per pair on the reference catalogue;
  its inputs are fingerprint-cached).
- The fulfillment fixed tier distribution loses its reader and is retired with the levels;
  depth classes and the aisle split survive.
- The run's own planned inventory still records the fielded levels and packing; that is a run
  artefact, not the catalogue.
