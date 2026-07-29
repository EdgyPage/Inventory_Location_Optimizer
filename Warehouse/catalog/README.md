# catalog — the SKUs and their demand

What the warehouse stores, and how often each thing is asked for.

| Module | Owns |
|---|---|
| `Order.py` | the SKU model — sampled dims/weight, per-unit labor cost, demand attributes |
| `Inventory_Builder.py` | builds the catalogue: `InventoryConfig` + `Inventory` |
| `Affinity_Store.py` | the SKU affinity/lift matrix, SQLite-backed with an in-memory CSR fast path |
| `Demand.py` | per-SKU demand (relative pick frequency + quantity rate) |

The dependency chain is a clean line: `Affinity_Store → Inventory_Builder → Order → Demand`.

**Naming trap:** the Python attribute is `relative_frequency`, but the SQLite column is still
`demand_frequency`. A catalogue DB written before that rename cannot be loaded by current code — a
whole committed dataset was deleted for exactly this reason.

**Does NOT belong here:** generating catalogues from scratch (→ `Warehouse/generation/`, which is
the CLI layer and its own architecture layer).
