# 18 - re-judge the two inventory mixins after the ledger lands

Type: decision
Status: resolved
Blocked by: 02

**This ticket is allowed to end in "closed with a reason", and is marked as likely to.**

## Context

`Inventory_Manager` is built from four mixins. Two of the four are not modules in any useful sense,
and they fail in opposite directions.

**`PlanningMixin` (`Warehouse/inventory/inventory_planning.py:291-740`) touches ZERO `self._*`.**
Every method is `@staticmethod` or `@classmethod` (`:300`, `:329`, `:337`, `:347`, `:704`). It is a
namespace wearing a mixin's clothes. The module-level functions above it -- `structural_bin_floor`
(`:116`), `_aisle_split` (`:230`) -- are the honest shape, and
`Optimization/run_simulation.py:65` already imports one of those directly. Making the class methods
module functions would change nothing about behaviour and would stop the file claiming a dependency
on `Inventory_Manager` it does not have.

**`ReorderMixin` (`inventory_reorder.py:150-893`) is the opposite.** It reaches ~50 distinct manager
attributes -- the aisle ledger, `_queued_qty`, `_held`, `_dock`, `transit`, `space_timeline`,
`packer`, `putaway_pool`, `_sigma_fd`, `_seed`, `_now_s`, `_stock`, `_admit`, `_queue`,
`_index_add`. Its interface IS `Inventory_Manager`. It is a file split, not a module.

**`inventory_zoning` is a mixin deliberately and should stay** -- the `Warehouse/inventory/README.md`
records a measured per-bin attribute-lookup reason, and `Tests/unit/test_velocity_zoning.py` pins
the two mirror sites. That argument is sound for zoning and does not transfer to reorder.

## Why this is blocked on 02, and why that matters

Ticket 02 removes roughly **12 of `ReorderMixin`'s ~50** manager-attribute reaches in one move, by
replacing eleven loose dicts with one `self.ledger`. That is the cheapest available step toward
making the split mean something, and it changes the arithmetic this ticket is judged on. Do not
judge before it lands.

## The question to answer

After 02: is `ReorderMixin`'s remaining attribute surface small enough that it could take an
explicit collaborator (a protocol, or a handful of injected values) rather than `self`? Or is the
honest answer that reorder is intrinsically the manager's own behaviour and the file split is just
a file split -- in which case say so, in the README, and stop.

For `PlanningMixin` the question is narrower and probably a yes: move the static methods to module
level beside `structural_bin_floor` and `_aisle_split`, and delete the class.

## Verification

- `PlanningMixin`: a pure move. `run_simulation.py:65` already imports from module level, so check
  what else does. Byte-identical by construction.
- **`.scratch/architecture-drift/issues/01` names `inventory_planning.py::PlanningMixin.plan_warehouse`**
  as the callee of the missing backbone edge. Moving or renaming it will interact with that ticket.
  Coordinate; do not silently change the symbol the drift ticket is written against.
- Memory anchors point into `inventory_common.py` and neighbours -- gate 7
  (`context/memory/verify_memory.py`) is the one that rots unattended, and the 2026-07 restructure
  staled 8 anchors across 4 of 8 memories. Run it.
- Gates 1, 2, 7, 10.

## Answer -- RESOLVED as CLOSED WITH A REASON (2026-09-16)

Judged after ticket 02 stage A, as this ticket required. All three mixins stay. The numbers,
measured by walking each class's AST for `self.X` where X is not defined on the class:

| mixin | methods | static/class | distinct manager attributes reached |
|---|---|---|---|
| `ReorderMixin` | 19 | 0 | **38** |
| `PlanningMixin` | 5 | **5** | **0** |
| `ZoningMixin` | 8 | 0 | 8 |

### `ReorderMixin` -- closed, it is the manager's own behaviour

Stage A removed roughly 12 reaches, exactly as ticket 02 predicted (the aisle-ledger dicts
became `self.ledger`). **38 remain.** That is not a surface an explicit collaborator could take:
it spans `_queued_qty`, `_held`, `_dock`, `transit`, `space_timeline`, `packer`,
`putaway_pool`, `_sigma_fd`, `_seed`, `_now_s`, `_stock`, `_admit`, `_queue`, `_index_add` and
two dozen more. Passing those as a protocol would reproduce `Inventory_Manager` under a second
name.

The honest answer is the one this ticket said to say if it was true: reorder IS the manager's
own behaviour, the file split is a file split, and that is fine. Recorded here so a future
review does not re-suggest it.

### `PlanningMixin` -- the move FAILS the deletion test, which this ticket did not anticipate

Zero `self.X`, all five methods static or class -- so the "namespace wearing a mixin's clothes"
reading is confirmed, and this ticket proposed moving them to module level and deleting the
class.

**That does not survive the deletion test.** Every caller reaches them through the MANAGER, not
through the mixin: `Inventory_Manager.plan_warehouse(...)` (`sim_assets.py:131`),
`Inventory_Manager.declared_packing(...)` (`era_coverage.py:578`), and the same shape elsewhere.
That spelling is the documented planning entry point -- `sim_config.py:88`, `sim_assets.py:4`,
`simconfig/coverage.py:67` and `era_coverage.py:23-26` all describe the pair-level fixed point
as `Q(n) -> plan_warehouse -> geometry -> n` in those terms.

So deleting the class would not make complexity vanish. It would MOVE it: six call-site renames
across four modules, a documented entry point lost, and either a broken public spelling or a set
of thin delegates on the manager -- which is strictly worse than the mixin, because a delegate
is a wrapper and the mixin is not. The class earns its keep as the manager's planning namespace
even though it holds no instance state.

The one thing that IS true and cheap: `structural_bin_floor` and `_aisle_split` already live at
module level and `run_simulation.py:67` imports one of them directly. That is the right shape
for a helper with no manager involvement, and nothing needs to change for it.

### `ZoningMixin` -- stays, by a prior decision with a measurement behind it

`Warehouse/inventory/README.md` records a measured per-bin attribute-lookup reason, and
`Tests/unit/test_velocity_zoning.py` pins the two mirror sites. Not reopened.

### A note for whoever owns `.scratch/architecture-drift/issues/01`

While checking whether a `PlanningMixin` refactor would collide with that ticket, I diagnosed
it: the backbone edge is NOT missing. `graph.json` carries it with the source
`sim_assets.py::build_shared_assets._plan` -- the nested closure -- while the assertion names
`build_shared_assets`. Written up in that ticket, not acted on here.
