# kernel — zero-dependency value objects

The primitives everything else in the domain is built from.

| Module | Owns |
|---|---|
| `allocation.py` | splitting a fixed set of work across N workers — round-robin and LPT, over any work type |
| `cost_model.py` | the pick-cost primitives — `sec_per_inch`, `height_multiplier`, `handle_var`, `SpeedProfile`, `cart_step` |
| `crew_clock.py` | one crew's workers as a `list[float]`, and the five rules over it — greedy assignment, the START-gate whistle, the per-batch reset |
| `regime.py` | storage-regime identity (store vs fulfillment) |
| `timeline.py` | what the clock is made of — `WorkDay`, `ReleaseSchedule`, `epochs`, `shift_index` |

This table listed two of the five for some time; nothing verifies it, so it will drift again.

**The rule:** nothing here may import anything else from `Warehouse`. That is what makes these safe
to import from anywhere without creating a cycle, and it is why they have the highest fan-in in the
package (15–16 importers each).

`Warehouse/physical.py` is the same idea taken further — it is declared as its own `leaf` layer with
`forbid: [leaf, "*"]`, so it stays at the package root rather than living here.
