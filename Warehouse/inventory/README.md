# inventory — the placement engine and its mixins

`Inventory_Manager` is the engine that turns "here is stock" into "it lives in these bins". The
other four modules are the mixins it is composed from, plus their shared leaf helpers.

| Module | Owns |
|---|---|
| `Inventory_Management.py` | `Inventory_Manager` — assignment-driven bin placement |
| `inventory_planning.py` | warehouse sizing/sampling, before any instance exists (`plan_warehouse`) |
| `inventory_reorder.py` | churn counters, the reload primitive, pick notifications |
| `inventory_optimal.py` | the optimal layout, Σf·D objective, full-labor floor and optimal map |
| `inventory_common.py` | leaf types/constants shared by the above |
| `put_queue.py` | one configurable put-away queue, instantiated N times (spec + queue + set) |
| `put_policy.py` | which waiting item a queue works next — the `PUT_POLICIES` registry |
| `dock.py` | the RECEIVING dock: a crew with its own hours, and what is standing on the floor |

`inventory_optimal` and `inventory_reorder` have **fan-in 1** — only the manager imports them. This
package simply makes an existing composition visible.

**Does NOT belong here:** the assignment functions themselves (→ `placement/`), physical geometry
(→ `layout/`), or the SKU catalogue (→ `catalog/`).
