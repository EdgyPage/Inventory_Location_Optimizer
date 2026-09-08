---
name: a-bin-cap-is-self-defeating
description: "a --s-max-bins/--ff-max-bins cap now REFUSES when it binds below the declared levels, and it cannot make a run small anyway: the coverage fixed point reads the levels back off the geometry, so a smaller warehouse raises lines/day and grows the levels"
metadata:
  node_type: memory
  type: project
  modified: 2026-09-07T22:30:00.000Z
---

Since [[field-the-requirement-one-planner-contract]] a `max_bins` / `max_aisles` cap that binds
below what a bucket's declared levels need RAISES `UnfieldableRequirement` at setup, naming the
bucket and the bins short. That is decision 3 of "Field the floor" working as intended.

**The part that is not obvious.** A cap cannot make a run smaller even in principle, because it
feeds back through the coverage fixed point: the warehouse is sized from the levels, and the
levels are derived from `lines_per_day`, which is derived from the built GEOMETRY. A capped
warehouse is a shorter trip, so a picker fills its day with MORE lines, so the derived daily
demand and every SKU's order-up-to go UP -- and the requirement the cap was trying to contain
grows past it. Measured on the e2e fixtures: a 250-SKU catalogue under a 40,000-bin cap
demanded 237,632 ff_medium bins.

**Use the declaration instead.** `--coverage-days` (with `--max-skus`) is the lever that
actually shrinks a run. The preflight canary was rebuilt on it (`--coverage-days 1`; its old
`--s-max-bins 900` sat below the 60-aisle structural floor and was being warned past anyway),
and eight e2e/integration fixtures dropped their caps.

See [[warehouse-size-comes-from-the-levels]], [[coverage-in-days-floors-the-store-section]].
