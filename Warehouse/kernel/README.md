# kernel — zero-dependency value objects

The primitives everything else in the domain is built from.

| Module | Owns |
|---|---|
| `cost_model.py` | the pick-cost primitives — `sec_per_inch`, `height_multiplier`, `handle_var` |
| `regime.py` | storage-regime identity (store vs fulfillment) |

**The rule:** nothing here may import anything else from `Warehouse`. That is what makes these safe
to import from anywhere without creating a cycle, and it is why they have the highest fan-in in the
package (15–16 importers each).

`Warehouse/physical.py` is the same idea taken further — it is declared as its own `leaf` layer with
`forbid: [leaf, "*"]`, so it stays at the package root rather than living here.
