# layout — physical geometry

The warehouse as a physical object: what a unit is, what a bin is, how aisles are shaped, and how
the whole thing gets assembled.

| Module | Owns |
|---|---|
| `Storage_Primitive.py` | size/type taxonomy, Pallet/Singleton/FulfillmentBin units, carts, fit/packing |
| `Aisle_Storage.py` | the `Aisle` model and its physical bins (grid location, centre geometry, occupancy) |
| `Aisle_Dimensions.py` | dimension helpers — bins per aisle for a given unit type and size |
| `Warehouse_Builder.py` | `AisleConfig`/`WarehouseConfig` specs assembled into a `Warehouse` |

**`Storage_Primitive` and `Aisle_Storage` import each other.** That cycle is why they are in the
same package; they cannot be separated without breaking one of the two edges first.

**Does NOT belong here:** what goes IN the bins (→ `catalog/`), how it is chosen (→ `placement/`),
or how it is picked (→ `picking/`).
