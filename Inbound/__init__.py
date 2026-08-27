"""Inbound — the receiving side of the site: trailers, the dock, unloading and packing.

A top-level package beside `Warehouse/`, split out so the two domains meet only at the
Inventory Manager's broker seam (see root `CONTEXT.md` for the vocabulary and
`.scratch/inbound-groundwork/` while the effort is in flight).

# ── the import law ────────────────────────────────────────────────────────────────

Two rules, both enforced by `context/architecture.yml` boundaries:

  * This package imports Warehouse VALUE layers only — `wh_kernel`, `wh_layout`,
    `wh_catalog`, and `wh_operations` (for the `PutawayCost` anti-drift coupling in
    `unload.py`).  Never `wh_inventory`, `wh_picking`, `wh_placement`, or the harness.
  * NO Warehouse module imports this package — not even the Inventory Manager.  The driver
    (`Optimization/`) constructs Inbound's objects from CONFIG specs and binds them on the
    manager (`enable_receiving`, `mgr.packer`), the way observers and placement functions
    are already injected.  Hub-ness is injection, not imports.

# ── what lives here ───────────────────────────────────────────────────────────────

    dock.py     the Dock: spec, crew clocks, and the merchandise standing on the floor
    unload.py   what it costs to take one storage unit off a trailer
    pack.py     packing one delivery into storage units, and the LoadPlan record

The trailer model (TrailerType/Trailer, load pallets, priorities.py) lands here next — the
decisions are on the wayfinder map; the code is not yet written.
"""
from Inbound.dock import Dock, DockSpec
from Inbound.pack import LoadPlan, packer, receive, receive_all, shipment_penalty
from Inbound.unload import UnloadCost, unload_cost

__all__ = ['Dock', 'DockSpec', 'LoadPlan', 'packer', 'receive', 'receive_all',
           'shipment_penalty', 'UnloadCost', 'unload_cost']
