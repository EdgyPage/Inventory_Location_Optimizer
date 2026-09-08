---
name: warehouse-size-comes-from-the-levels
description: "the warehouse bin count is demand-derived from the stock levels on EVERY planning path, sample=False included, so an undeclared catalogue silently builds a warehouse an order of magnitude too small"
metadata:
  node_type: memory
  type: project
  modified: 2026-09-07T00:00:00.000Z
---

`Inventory_Manager.plan_warehouse(..., sample=False)` reads as "shape only, no re-stock", and the
log line says so. It is misleading in the one way that matters: `sample` gates ONLY the SKU
sampling (`sample_to_capacity`). The bin count is computed before that gate and is
**demand-derived from each order's order-up-to** — `bucket_requirements` runs every order through
`viable_storage_units(c, _equilibrium_qty(c))`, and the per-bucket replica count is
`ceil(required / (bins_per_aisle x target_fill))`. Both regimes take that path under the committed
sizing (`composition`, `min_bins`, `target_bins` are all None by default).

**Why this cost time.** When the catalogue stopped carrying stock levels (ADR-0002), the obvious
reading was "the shape-only rebuild doesn't need levels, it only wants the geometry". It does need
them. `_equilibrium_qty` used to answer `1` for an order with no level, so an undeclared catalogue
would have sized every bucket for one unit per SKU and built a warehouse an order of magnitude too
small — with **no error**, at the one moment nothing downstream can detect it. That is why it now
raises `UndeclaredStock` instead of defaulting.

**The consequence for anything that re-plans a finished run.** `run_analysis` and
`run_map_precompute` rebuild a run's warehouse from its ORIGINAL catalogue, which holds no level.
They must re-declare first, and must declare what the run declared — one `rescale_section` pass at
the `lines_per_day` in that run's own `staffing.calibration[<pair>].coverage`
(`era_coverage.declare_from_record`), not a fresh fixed point, which would re-derive from this
checkout's geometry instead of the one the run fielded. A rebuild with neither a declaration nor a
record refuses.

**And for the batch cache.** The batch fingerprint hashes no level, but it is taken over the
SAMPLED inventory, and which SKUs get sampled follows the levels. So changing how levels are
derived moves the fingerprint without any hashed input changing.

See [[stock-plan-overrides-packing]], [[build-inventory-tests-no-reorders]],
[[verify-tree-uses-the-runs-own-contract]].
