# picking — the pick simulations

Given a stocked warehouse and a stream of batches, simulate pickers walking and picking, and emit
the event log everything downstream measures.

| Module | Owns |
|---|---|
| `fast_pick.py` | `DeferredPickSimulation` — the two-phase deferred-mutation sim used in production |
| `Pick.py` | `PickConfig`, `PickEvent`, `PickSimulation` — the reference implementation |
| `Workload_Builder.py` | samples affinity- and demand-weighted batches of tasks |

`fast_pick` is what a real run executes; `Pick` remains the readable reference the fast path is
checked against. Picker position persists across tasks, which is why travel is path-dependent and
why batch order matters.

**Does NOT belong here:** turning the event log into statistics (→ `Optimization/metrics/`) or
storing them (→ `Optimization/persistence/`).
