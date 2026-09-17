# 18 - re-judge the two inventory mixins after the ledger lands

Type: decision
Status: needs-triage
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
