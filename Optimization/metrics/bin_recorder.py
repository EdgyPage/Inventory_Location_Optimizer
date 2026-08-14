"""bin_recorder.py — capture every bin mutation the simulation makes.

Closes the gap that made spatial reconstruction lossy. `bin_inventory` records picks and NEVER
restocks — `check_reorders()` runs before the pre-batch snapshot, so a restocked bin is already in
it at its post-restock quantity and the `post_qty == pre_qty` skip drops it. Measured on a
production arm: **0 rows** with `post_qty > pre_qty` against 20k-42k `reorder_placements` per
batch, so replaying the delta stream only ever decays — losing 59% of the warehouse within five
batches of a keyframe.

What makes this complete
------------------------
Bin state is `Aisle.Bin.storage` / `.storage.quantity`, and a repo-wide search for writes to
either finds six lines across five sites, one of them dead code:

    Inventory_Management._execute_placement   `bin_.storage = unit`    -> PLACE  (recorded here)
    inventory_reorder.requeue_bin             `bin_.storage = None`    -> EVICT  (recorded here)
    fast_pick.py / Pick.py                    qty -= / storage = None  -> PICK   (already `picks`)
    Storage_Primitive.StorageCart             zero callers repo-wide   -> n/a

So PLACE + EVICT + PICK is complete **by construction**, and picks are already fully recorded —
`SUM(pre_qty - post_qty)` equals `SUM(picks.quantity)` exactly on every arm tested.
`Tests/integration/test_bin_log_replay.py` checks that construction argument against a live
simulation: folding the log reproduces the warehouse's own bins at every batch and at every
sim-time within a batch. `Tests/architecture/test_bin_mutation_sites.py` fails the build if a
sixth mutation site ever appears.

How it attaches
---------------
By rebinding two bound methods on the manager **instance** — no edits to `Warehouse/`, and a
strategy swapping `mgr.placement` cannot detach it, because `_execute_placement` is the chokepoint
all three placement call sites reach through `self.`. Same technique as
`Diagnostics/trace_lifecycle.py`.

Time resolution
---------------
`(batch_id, seq)`, and that is the TRUE resolution rather than a compromise: `check_reorders()`
runs entirely between one batch's picks and the next batch's simulation, so no finer ordering
exists to lose. Picks keep their `sim_time`, so intra-batch animation stays exact.
"""
from __future__ import annotations

from Optimization.persistence.Picking_Data import BinEvictionRecord, BinPlacementRecord


class BinRecorder:
    """Accumulates PLACE/EVICT records for one arm, drained by the checkpoint flush.

    Rows are appended to plain lists and handed to the existing per-checkpoint flush, so this
    adds no new write path and no new connection.
    """

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.placements: list[BinPlacementRecord] = []
        self.evictions: list[BinEvictionRecord] = []
        self._batch = 0
        self._place_seq = 0
        self._evict_seq = 0
        self._evicted_units: set[int] = set()
        # Initial stocking happens BEFORE the batch loop starts (enqueue_all in the runner), so
        # "has the loop begun" is what separates the initial fill from a reorder — not "have we
        # picked yet", which mislabels batch 0's reorders as initial.
        self._in_batch_loop = False

    # ── driver hooks ──
    def begin_batch(self, batch_id: int) -> None:
        """Roll onto a new batch and reset the intra-batch sequence counters."""
        self._in_batch_loop = True
        self._batch = batch_id
        self._place_seq = 0
        self._evict_seq = 0

    def drain(self) -> tuple[list, list]:
        """Hand over the accumulated rows and clear, mirroring the other checkpoint lists."""
        placements, evictions = self.placements, self.evictions
        self.placements, self.evictions = [], []
        return placements, evictions

    # ── attachment ──
    def attach(self, mgr) -> None:
        """Wrap `_execute_placement` and `requeue_bin` on this manager instance."""
        orig_place = mgr._execute_placement
        orig_evict = mgr.requeue_bin

        def _execute_placement(unit, bin_):
            # requeue_bin re-queues the IDENTICAL unit object, so identity is what separates a
            # reslot re-placement from a fresh reorder arrival.
            tag = id(unit)
            if tag in self._evicted_units:
                cause = 'reslot'
                self._evicted_units.discard(tag)
            else:
                cause = 'reorder' if self._in_batch_loop else 'initial'
            # Read before delegating: the call mutates the bin and the manager's queue counters.
            aisle_id, bay_x, bay_y = bin_.location
            sku, qty = unit.order.sku, unit.quantity
            orig_place(unit, bin_)
            self.placements.append(BinPlacementRecord(
                run_id=self.run_id, batch_id=self._batch, seq=self._place_seq,
                aisle_id=aisle_id, bayX=bay_x, bayY=bay_y, sku=sku, qty=qty, cause=cause))
            self._place_seq += 1

        def requeue_bin(bin_, *args, **kwargs):
            unit = bin_.storage
            rec = None
            if unit is not None:
                aisle_id, bay_x, bay_y = bin_.location
                rec = BinEvictionRecord(
                    run_id=self.run_id, batch_id=self._batch, seq=self._evict_seq,
                    aisle_id=aisle_id, bayX=bay_x, bayY=bay_y,
                    sku=unit.order.sku, qty=unit.quantity)
                tag = id(unit)
            out = orig_evict(bin_, *args, **kwargs)
            if rec is not None:
                self.evictions.append(rec)
                self._evict_seq += 1
                self._evicted_units.add(tag)
            return out

        mgr._execute_placement = _execute_placement
        mgr.requeue_bin = requeue_bin
