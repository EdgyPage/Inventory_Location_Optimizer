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

    dock.py        the Dock: spec, crew clocks, and the merchandise standing on the floor
    unload.py      what it costs to take one storage unit off a trailer
    pack.py        packing one delivery into storage units, and the LoadPlan record
    trailer.py     TrailerType (53/28), stateful Trailers, and the load pallets they carry
    priorities.py  the policy registries (global/local + yard/dock), frozen DockContext,
                   ordering bound
    transit.py     TrailerTransit: order port -> trailers -> yard -> dock doors;
                   YardTransit: the standing yard, where doors become real
    space.py       the space timeline: per-drain frozen SpaceView (empties + untimed
                   predicted clears) the standing dock's decisions read via ctx.space
    gain.py        the unload-plan evaluator (expected future work, faithful-to-arm)
                   and the gain-family ordering entries it registers
"""
from Inbound.dock import Dock, DockSpec
# Imported for its registration side effect too: gain.py lands the gain-family
# entries in the yard/dock registries at package import.
from Inbound.gain import GAIN_POLICIES, GainBundle
from Inbound.pack import LoadPlan, packer, receive, receive_all, shipment_penalty
from Inbound.space import SpaceTimeline, SpaceView
from Inbound.trailer import LoadPallet, Trailer, Trailer28, Trailer53, TRAILER_TYPES
from Inbound.transit import TrailerTransit, YardTransit
from Inbound.unload import UnloadCost, unload_cost

__all__ = ['Dock', 'DockSpec', 'GAIN_POLICIES', 'GainBundle', 'LoadPlan', 'LoadPallet',
           'packer', 'receive', 'receive_all', 'shipment_penalty', 'SpaceTimeline',
           'SpaceView', 'Trailer', 'Trailer28', 'Trailer53', 'TRAILER_TYPES',
           'TrailerTransit', 'UnloadCost', 'unload_cost', 'YardTransit']
