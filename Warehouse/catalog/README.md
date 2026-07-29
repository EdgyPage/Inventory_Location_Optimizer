# catalog — the SKUs and their demand

What the warehouse stores, and how often each thing is asked for.

| Module | Owns |
|---|---|
| `Order.py` | the SKU model — sampled dims/weight, per-unit labor cost, demand attributes |
| `Inventory_Builder.py` | builds the catalogue: `InventoryConfig` + `Inventory` |
| `Affinity_Store.py` | the SKU affinity/lift matrix, SQLite-backed with an in-memory CSR fast path |
| `Demand.py` | per-SKU demand (relative pick frequency + quantity rate) |

The dependency chain is a clean line: `Affinity_Store → Inventory_Builder → Order → Demand`.

**Data vintage, not a naming trap:** `relative_frequency` is the name in *both* the `Demand` model
and the `cartons` DB column — it was renamed from `demand_frequency` in `418d6bf`, and both sides
moved together. A catalogue generated before that commit still has the old column and raises
`sqlite3.OperationalError` in `load_inventory_from_db`; regenerate it rather than patching a shim in.
A whole committed dataset was deleted for exactly this reason, which is why the failure is worth
recognising on sight.

**Does NOT belong here:** generating catalogues from scratch (→ `Warehouse/generation/`, which is
the CLI layer and its own architecture layer).
