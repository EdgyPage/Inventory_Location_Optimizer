# inventory — the placement engine and its mixins

`Inventory_Manager` is the engine that turns "here is stock" into "it lives in these bins". It is
composed from **four mixins**, each in its own module, plus their shared leaf helpers.

| Module | Owns |
|---|---|
| `Inventory_Management.py` | `Inventory_Manager` — assignment-driven bin placement |
| `inventory_planning.py` | `PlanningMixin` — warehouse sizing/sampling, before any instance exists (`plan_warehouse`) |
| `inventory_reorder.py` | `ReorderMixin` — churn counters, the reload primitive, pick notifications |
| `inventory_optimal.py` | `OptimalLayoutMixin` — the optimal layout, Σf·D objective, full-labor floor and optimal map |
| `inventory_zoning.py` | `ZoningMixin` — velocity zoning: the band maps, the per-band free-bin sub-index, the spill order, the two filters and the ranked-wave group key |
| `inventory_common.py` | leaf types/constants shared by the above |
| `put_queue.py` | one configurable put-away queue, instantiated N times (spec + queue + set) |
| `put_policy.py` | which waiting item a queue works next — the `PUT_POLICIES` registry |

**Two of the four mixins have fan-in 1; two do not.** `inventory_reorder` and `inventory_zoning`
are imported only by `Inventory_Management.py` — for those, this package simply makes an existing
composition visible. The other two are also read from outside, and a change to either is wider
than it looks:

| Module | Imported outside the manager by |
|---|---|
| `inventory_planning.py` | `Optimization/run_simulation.py:65` — module-level `structural_bin_floor`, used by the `--s-max-bins` structural-floor check |
| `inventory_optimal.py` | `Optimization/run_map_precompute.py:141` — imports the module and reaches `OptimalLayoutMixin._assign_probe` |

This table is here because the sentence it replaces claimed all four were manager-only, which
would have led a maintainer to scope a change to `inventory_planning` as internal and break the
CLI at import.

**Zoning is a mixin and not a collaborator object, deliberately.** `Inventory_Manager._index_add` /
`._index_remove` mirror every bin mutation into the per-band sub-index and are the hottest path in
the system; a mixin method resolves exactly as a method defined on the class does, while a
`self._zoning.` hop would cost an extra attribute lookup per bin moved. Those two mirror sites
therefore stay inline on the manager, and `Tests/unit/test_velocity_zoning.py` asserts they stay
there.

**The receiving dock is NOT here.** It used to be (`dock.py`), and this table said so long after it
moved; it lives at `Inbound/dock.py` with the rest of the receiving side, because a dock that serves
two channels has to sit above two managers and `wh_inventory -> inbound` is forbidden in both
directions.

**Does NOT belong here:** the assignment functions themselves (→ `placement/`), physical geometry
(→ `layout/`), or the SKU catalogue (→ `catalog/`).
