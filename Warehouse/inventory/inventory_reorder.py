"""inventory_reorder.py — churn counters, reload primitive, pick notifications, and
Order-Up-To reorder logic.

`ReorderMixin` holds the per-batch lifecycle methods.  Mixed into Inventory_Manager so
the public API (`pop_churn`, `requeue_bin`, `check_reorders`, and the O(1) pick
notifications called by PickSimulation) is unchanged.  All instance state and the
collaborators it calls (`self._index_add`, `self._stock`) are provided by
Inventory_Manager.__init__ / its placement methods.
"""
from __future__ import annotations

import random
from collections import deque

from Warehouse.kernel.allocation import partition
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Warehouse.inventory.inventory_common import (
    PutawayItem, is_forward_pick, _equilibrium_qty)




class BatchTransit:
    """The order port's flag-off transit: the legacy lead queue as an object.

    Entries are the same mutable ``[sku, qty, remaining_lead]`` records they always were,
    ticking one BATCH per ``advance()`` — the unit note further down this module is why that is
    a decision.  This is the DEFAULT the manager constructs for itself; the trailer
    pipeline binds its own transit (absolute-clock leads, yard, doors) on the same
    seam, flag-on — injection, never import, like ``packer`` above.

    The manager keeps the SCALAR ledger (``_deferred_qty`` credits and debits) either way:
    transit owns TIMING, the ledger owns POSITION, and that split is what let the dock land
    with zero reorder-ledger edits.
    """

    __slots__ = ('_entries',)

    def __init__(self):
        self._entries: list[list] = []

    #: What admissions from this transit are stamped as — the flag-off provenance.
    SOURCE = 'reorder'

    def dispatch(self, sku: int, qty: int, lead: int,
                 unit_volume: int | None = None, now_s: float | None = None) -> None:
        """Accept one fired reorder from the order port.  `unit_volume` and `now_s`
        are the trailer transit's business, carried on the seam so the two transits
        are indistinguishable to the phase bodies; the batch countdown ignores both."""
        self._entries.append([sku, qty, lead])

    def advance(self) -> None:
        """One batch elapsed: every in-transit order moves one tick closer.

        The tick is one BATCH, not one second — the module's unit note is the decision.
        """
        for entry in self._entries:
            entry[2] -= 1

    def release(self, now_s: float | None = None) -> list[list]:
        """Pop and return every arrived entry (remaining_lead <= 0), in queue order.

        Un-arrived entries keep their order; a negative remainder is an order that arrived
        while nothing drained it — never clamped, because clamping would hide the backlog.
        """
        still: list[list] = []
        released: list[list] = []
        for entry in self._entries:
            (released if entry[2] <= 0 else still).append(entry)
        self._entries = still
        return released

    @property
    def depth(self) -> int:
        return len(self._entries)

    def merchandise(self) -> int:
        """Total pieces in transit — the in_transit_qty level."""
        return sum(e[1] for e in self._entries)

    def snapshot(self) -> list:
        """(sku, qty, remaining_lead) tuples for the replay/reorder_queue rows."""
        return [tuple(e) for e in self._entries]


class _PlainDelivery:
    """The default packer's per-delivery record: the unit stream and nothing else.

    What `_release_to_stock` observes from a plan when no packer is bound is exactly
    `.units` (a dock that would count the plans only exists when the driver also binds the
    rich packer) — so this is the whole flag-off contract, and the unit stream is
    byte-identical to `Inbound.pack.receive`'s by construction: both call
    `viable_storage_units` once per delivery quantity.
    """
    __slots__ = ('units',)

    def __init__(self, units):
        self.units = tuple(units)


def _pack_plain(order, quantity: int, deliveries=None) -> list:
    """Pack an arrival with no Inbound package in sight: same units, no LoadPlan records."""
    qtys = [int(q) for q in deliveries if int(q) > 0] if deliveries else [quantity]
    return [_PlainDelivery(viable_storage_units(order, q)) for q in qtys]



class ReorderMixin:

    def pop_churn(self) -> tuple[int, int]:
        """Return (reload_moves, reorder_placements) since the last call and reset."""
        r, p = self._reload_moves, self._reorder_placements
        self._reload_moves = 0
        self._reorder_placements = 0
        return r, p

    # ── reload primitive (used by Capacity_Reloader) ─────────────────────────

    def requeue_bin(self, bin_: 'Aisle.Bin') -> None:
        """Evict a placed unit back into the reorder queue and reclaim its bin —
        the inverse of _execute_placement.

        The unit's quantity moves from on-hand to on-order (queued), so inventory
        POSITION is unchanged (no spurious reorder fires).  The freed bin returns to
        the available index and the ranked drain re-places the queued unit in its
        proper priority slot.  Bumps the reload churn counter.  No-op on an empty bin.
        """
        unit = bin_.storage
        if unit is None:
            return
        sku = unit.order.sku

        if self._sigma_freq is not None:           # incremental Sigma f*D: eviction (−)
            self._sigma_fd -= self._sigma_delta(sku, bin_)

        # Free the bin and return it to the available index.
        bin_.storage = None
        self._unavailable.pop(id(bin_), None)
        self._bin_sku.pop(id(bin_), None)
        (self._sku_singleton_bins if is_forward_pick(bin_)
         else self._sku_pallet_bins)[sku].discard(bin_)
        self._index_add(bin_)
        # THE EVICTION EVENT: the second door into the free index (the reclaim-harvest
        # in _reclaim_empty_bins is the first), folded into the SAME reclaim_v so the
        # space timeline's version vector describes _index completely.  Observer only.
        # Unlike the harvest and fill hooks this one is reachable with the standing yard
        # OFF -- the reloader gates on reslot_frac alone -- so the guard is load-bearing.
        if self.space_timeline is not None:
            self.space_timeline.evict(bin_)

        self._drop_sku_from_aisle(sku, bin_)

        # On-hand -> on-order (queued); re-enqueue the unit for the ranked drain.
        self._current_quantities[sku] = max(0, self._current_quantities.get(sku, 0) - unit.quantity)
        self._queued_qty[sku]         = self._queued_qty.get(sku, 0) + unit.quantity
        self._queued_sku_counts[sku]  = self._queued_sku_counts.get(sku, 0) + 1
        self._admit(unit, 'reslot')
        self._reload_moves += 1

    def _drop_sku_from_aisle(self, sku: int, bin_: 'Aisle.Bin') -> None:
        """Remove one bin's contribution to the per-SKU aisle state (affinity on).

        The CANONICAL per-SKU aisle teardown: drop the bin's column position from
        _aisle_member_pos, decrement the aisle's SKU count, and — when this was the
        SKU's LAST bin in the aisle — retire it from the sku/idx sets and subtract
        its lift/demand/pick-load contributions (clamped at 0).

        _reclaim_empty_bins carries a hoisted-locals INLINE TWIN of this logic for
        its per-batch hot loop (deliberate micro-optimization over ~7k bins; it also
        treats a defensive n==0 like n==1, which this cold path never reaches) —
        KEEP THE TWO IN SYNC when editing either.
        """
        if self._affinity is None:
            return
        aid = bin_.location[0]
        idx = self._affinity._sku_to_idx.get(sku)
        # Drop the evicted bin's column position from _aisle_member_pos (live-bin only).
        if idx is not None:
            mp = self._aisle_member_pos.get(aid)
            if mp is not None:
                xs = mp.get(idx)
                if xs:
                    try:
                        xs.remove(bin_.x_phys)
                    except ValueError:
                        pass
                    if not xs:
                        del mp[idx]
        counts = self._aisle_sku_counts[aid]
        n      = counts.get(sku, 0)
        if n > 1:
            counts[sku] = n - 1
        elif n == 1:
            counts.pop(sku, None)
            self._aisle_sku_sets[aid].discard(sku)
            if idx is not None:
                self._aisle_idx_sets[aid].discard(idx)
            delta = 2.0 * self._affinity.delta_lift_idxs(sku, self._aisle_idx_sets[aid])
            self._aisle_lift_sum[aid] = max(0.0, self._aisle_lift_sum[aid] - delta)
            d = self._sku_demand_product.get(sku, 0.0)
            if d:
                self._aisle_demand_sum[aid] = max(0.0, self._aisle_demand_sum[aid] - d)
            dl = self._sku_pick_load_product.get(sku, 0.0)
            if dl:
                self._aisle_pick_load_sum[aid] = max(0.0, self._aisle_pick_load_sum[aid] - dl)

    # ── pick notifications (called by PickSimulation, O(1) each) ────────────

    def _notify_pick(self, sku: int, qty: int) -> None:
        """Decrement the incremental quantity counter and flag the SKU if it
        crosses its reorder_point.

        Called by PickSimulation after each pick event — must be O(1).
        Adds the SKU to _depleted_skus so check_reorders only iterates SKUs
        that actually need attention rather than all N_skus.

        The depletion flag compares reorder_point against the SKU's INVENTORY
        POSITION (on-hand in bins + on-order queued + on-order deferred), not
        on-hand alone.  A SKU that has already been reordered but whose units are
        still waiting for a bin is therefore not flagged again — preventing
        duplicate reorders every batch for unbinned items.
        """
        cur = self._current_quantities.get(sku, 0)
        if cur <= 0:
            return
        new_qty = max(0, cur - qty)
        self._current_quantities[sku] = new_qty
        orig = self._originals.get(sku)
        rp = getattr(orig, 'reorder_point', None) if orig is not None else None
        if rp is not None:
            on_order = self._queued_qty.get(sku, 0) + self._deferred_qty.get(sku, 0)
            if new_qty + on_order <= rp:
                self._depleted_skus.add(sku)

    def _notify_bin_emptied(self, bin_: Aisle.Bin, at: float | None = None) -> None:
        """Queue an emptied bin for reclaim at the next check_reorders call.

        Called by PickSimulation immediately after bin_.storage is set to
        None — must be O(1).  The bin stays in _unavailable until
        _reclaim_empty_bins processes _pending_reclaim.

        ``at`` is the ABSOLUTE second the bin ran dry -- the pick sim's carried clock, on
        the same axis as every other event stamp.  Recorded in `_emptied_at` and read at
        exactly one place: the space timeline's reclaim-harvest in `_reclaim_empty_bins`,
        the one moment the stamps and the bins meet before both are wiped (pinned by
        `Tests/unit/test_bin_empty_timing.py`).  The reclaim itself still happens once,
        at the top of the next batch, so a slot freed mid-batch is invisible to PLACEMENT
        until then -- what the stamp feeds is the standing dock's space view, not the
        reclaim cadence.
        ``None`` from a caller that has no clock (a test, the legacy notification path).
        """
        if self._sigma_freq is not None:
            sku = self._bin_sku.get(id(bin_))      # still set until reclaim pops it
            if sku is not None:
                self._sigma_fd -= self._sigma_delta(sku, bin_)
        self._pending_reclaim.append(bin_)
        if at is not None:
            self._emptied_at[id(bin_)] = at

    def _apply_picks_batch(
        self,
        picks: list[tuple[int, int]],
        empties: 'list[tuple[Aisle.Bin, float]] | list[Aisle.Bin]',
    ) -> None:
        """Apply all pick notifications accumulated during one simulation run.

        Aggregates quantity by SKU before calling _notify_pick so the body
        executes once per unique SKU rather than once per pick event,
        cutting ~430k individual function calls down to ~5k.

        `empties` is `(bin, when)` from `PickSimulation`.  Bare bins are still accepted
        because this is a public-ish notification hook and a caller with no clock is a
        legitimate one — the stamp is metadata, not a precondition.
        """
        agg: dict[int, int] = {}
        for sku, qty in picks:
            agg[sku] = agg.get(sku, 0) + qty
        for sku, qty in agg.items():
            self._notify_pick(sku, qty)
        for e in empties:
            if isinstance(e, tuple):
                bin_, at = e
                self._pending_reclaim.append(bin_)
                self._emptied_at[id(bin_)] = at
            else:
                self._pending_reclaim.append(e)

    # ── reorder logic ────────────────────────────────────────────────────────

    def _reclaim_empty_bins(self) -> None:
        """Return bins in _pending_reclaim to the available index.

        With _unavailable as a dict and _pending_reclaim as a targeted list,
        this is O(pending_bins) — typically a handful per batch — instead of
        the previous O(total_bins) full scan.  Attribute refs are hoisted
        outside the loop to avoid repeated self. lookups across ~7k iterations.

        The per-SKU aisle teardown inside the loop is the hoisted INLINE TWIN of
        _drop_sku_from_aisle (the canonical cold-path version used by requeue_bin)
        — KEEP THE TWO IN SYNC when editing either.
        """
        if not self._pending_reclaim:
            return

        # THE RECLAIM-HARVEST: hand the space timeline the actual clear stamps before
        # this drain wipes them -- the one moment the stamps and the bins meet.  The ONE
        # legal reader of `_emptied_at` (pinned by test_bin_empty_timing); `is None` =
        # no standing yard = a structural no-op.
        if self.space_timeline is not None:
            self.space_timeline.harvest(self._pending_reclaim, self._emptied_at)

        has_affinity = self._affinity is not None
        bin_sku          = self._bin_sku
        sku_singleton    = self._sku_singleton_bins
        sku_pallet       = self._sku_pallet_bins
        unavailable      = self._unavailable
        aisle_sku_counts = self._aisle_sku_counts
        aisle_sku_sets   = self._aisle_sku_sets
        aisle_idx_sets   = self._aisle_idx_sets
        aisle_lift_sum   = self._aisle_lift_sum
        aisle_demand_sum = self._aisle_demand_sum
        sku_demand_prod  = self._sku_demand_product
        aisle_pick_load  = self._aisle_pick_load_sum
        sku_pick_load    = self._sku_pick_load_product
        aisle_member_pos = self._aisle_member_pos
        if has_affinity:
            sku_to_idx      = self._affinity._sku_to_idx
            delta_lift_idxs = self._affinity.delta_lift_idxs

        for bin_ in self._pending_reclaim:
            bin_id = id(bin_)
            sku    = bin_sku.pop(bin_id, None)
            if sku is not None:
                lst = (sku_singleton if is_forward_pick(bin_) else sku_pallet).get(sku)
                if lst:
                    lst.discard(bin_)
                if has_affinity:
                    aid    = bin_.location[0]
                    idx    = sku_to_idx.get(sku)
                    # Drop THIS bin's column position so _aisle_member_pos tracks only
                    # live bins (every reclaimed bin, not just a SKU's last one).
                    if idx is not None:
                        mp = aisle_member_pos.get(aid)
                        if mp is not None:
                            xs = mp.get(idx)
                            if xs:
                                try:
                                    xs.remove(bin_.x_phys)
                                except ValueError:
                                    pass
                                if not xs:
                                    del mp[idx]
                    counts = aisle_sku_counts[aid]
                    n      = counts.get(sku, 0)
                    if n > 1:
                        counts[sku] = n - 1
                    else:
                        counts.pop(sku, None)
                        aisle_sku_sets[aid].discard(sku)
                        if idx is not None:
                            aisle_idx_sets[aid].discard(idx)
                        delta = 2.0 * delta_lift_idxs(sku, aisle_idx_sets[aid])
                        aisle_lift_sum[aid] = max(0.0, aisle_lift_sum[aid] - delta)
                        d = sku_demand_prod.get(sku, 0.0)
                        if d:
                            aisle_demand_sum[aid] = max(0.0, aisle_demand_sum[aid] - d)
                        dl = sku_pick_load.get(sku, 0.0)
                        if dl:
                            aisle_pick_load[aid] = max(0.0, aisle_pick_load[aid] - dl)
            self._index_add(bin_)
            unavailable.pop(bin_id, None)

        self._pending_reclaim.clear()
        # The stamps describe exactly the bins just reclaimed, so they expire with them —
        # otherwise this grows by one entry per emptied bin for the length of the run.
        self._emptied_at.clear()

    def _release_to_stock(self, sku: int, qty: int, deliveries=None) -> list:
        """Convert an arrived (sku, qty) order into storage units and append them to the
        stock queue, updating the queued-unit / queued-qty trackers.  Shared by every
        lead-queue arrival (including lead-0 orders released the same batch).

        RETURNS the `LoadPlan`s it built, rather than stashing them.  An earlier version kept
        them on the manager behind a `drain_inbound()` -- which was write-only state with an
        extra method, since nothing ever drained it, and it pinned a tuple of live
        `StorageUnit`s plus a cloned `Order` for every arrival of the whole run.  There is
        exactly one caller, so the return value is the honest shape: nothing to leak, and a
        receiving crew takes the plans from here rather than from a buffer it must remember
        to empty.

        THE INBOUND SEAM.  Packing has always been a function of how much arrives AT ONCE --
        `viable_storage_units` takes a quantity -- but nothing named that, so nothing could
        express an order that would be palletized arriving whole and lands as two singleton
        packs when a trailer splits it.  `deliveries` is that: a list of quantities summing
        to `qty`, each packed on its own.  None means one delivery of the whole amount,
        which is what every caller does today and is byte-identical.

        The split is the CALLER's to decide.  Trailers, docks and load planning belong
        upstream of here; this only says that when a shipment arrives in pieces, each piece
        packs as the piece it is.  When the driver binds `mgr.packer`
        (`Inbound.pack.packer`), the plans are `LoadPlan`s carrying the counterfactual, so
        the cost of the split is answerable (`Inbound.pack.shipment_penalty`); unbound, the
        default packs the identical unit stream and records nothing.

        A caller that does not want to thread `deliveries` through every arrival sets
        `inbound_split` instead -- consulted here, so the split is reachable from the
        ordinary lead-queue release without `_release_arrivals` knowing about it.
        """
        rc = self._originals[sku].reorder()
        if deliveries is None and self.inbound_split is not None:
            deliveries = self.inbound_split(sku, qty)
        pack = self.packer if self.packer is not None else _pack_plain
        plans = pack(rc, qty, deliveries)
        units = [u for p in plans for u in p.units]
        if not units:
            return plans
        source = getattr(self.transit, 'SOURCE', 'reorder')
        for unit in units:
            self._admit(unit, source)
        self._queued_sku_counts[sku] = self._queued_sku_counts.get(sku, 0) + len(units)
        self._queued_qty[sku]        = self._queued_qty.get(sku, 0) + sum(u.quantity for u in units)
        return plans

    # ── the six phases of a completed batch ──────────────────────────────────────
    # `check_reorders` used to be all six inline, which meant there was no way to reclaim
    # bins without also ordering, or to place the stock queue without also advancing the
    # calendar.  They are separated so a caller can drive them independently — a second
    # work stream (inbound put-away against the same clock) needs to interleave these, not
    # replay them as a block.  The composition below is the ONLY caller today and runs them
    # in exactly the order they always ran in.

    def _tick_batch(self) -> None:
        """Advance the replenishment calendar by one batch.

        This is the simulation's only calendar: lead time is denominated in BATCHES, not
        seconds, and `_batch_num` is also the per-reorder RNG key, so calling this twice
        would silently redraw every reorder quantity.
        """
        self._batch_num += 1

    def reclaim_emptied_bins(self) -> None:
        """Return bins the pick loop emptied to the free index.

        Public because the pick side FILLS `_pending_reclaim` (`_notify_bin_emptied`) while
        the reorder side DRAINS it, and the drain currently happens once, at the top of the
        next batch — so a slot freed mid-batch is invisible until then.  Naming it is the
        first step to letting that change.
        """
        self._reclaim_empty_bins()

    #: Lead time's unit, stated because the rest of the simulator now counts in seconds.
    #:
    #: THE INBOUND PIPELINE IS QUANTIZED TO BATCHES.  `lead_time_mean` is a count of
    #: BATCHES, not seconds, and `_advance_lead_queue` ticks it once per `check_reorders`.
    #: Everything else -- the pick clock, put-away, `work_events.t_abs` -- is in seconds on
    #: a continuous axis, so an arrival lands on a batch boundary rather than at an instant.
    #:
    #: DELIBERATE, and this is the decision rather than an oversight:
    #:
    #:   * Converting to seconds moves EVERY restock result on EVERY arm -- a reorder that
    #:     currently arrives at the top of batch N would arrive part-way through it -- and
    #:     nothing consumes a finer-grained arrival today.  Paying for a whole-archive
    #:     re-run to gain precision no reader uses is the wrong trade now.
    #:   * Batch quantization is HONEST for a wave-picking model: replenishment lands
    #:     between waves, which is when a real warehouse restocks a pick face.
    #:   * The feature that makes it wrong is the trailer/dock work, where an arrival IS
    #:     a scheduled instant — and that feature has now PICKED, with the trailer model
    #:     in hand: flag-off keeps this batch countdown (`BatchTransit`, byte-identical);
    #:     flag-on binds a trailer transit whose leads are absolute-clock seconds
    #:     (minutes-authored, default zero) on the same `transit` seam.
    #:
    #: Until then: a `work_events` row for an inbound arrival would sit at a batch
    #: boundary, and anything reasoning about arrival TIMES must know that.
    LEAD_TIME_UNIT = 'batches'

    def _advance_lead_queue(self) -> None:
        """One batch elapsed: decrement pre-existing in-transit orders only.

        Runs BEFORE `_fire_reorders`, which is what stops an order fired this batch from
        being decremented in the same batch it was placed.

        The tick is one BATCH, not one second -- see `LEAD_TIME_UNIT` above for why that
        is a decision and what would change it.  The tick itself lives on the TRANSIT
        object (`BatchTransit.advance`): this phase is the wrapper the phase ratchet pins,
        and the trailer transit replaces the body's target, never this call site.
        """
        self.transit.advance()

    def _fire_reorders(self) -> list[int]:
        """Fire Order-Up-To reorders for depleted SKUs into the lead queue.

        Returns the SKUs triggered and sets `_units_ordered` for the batch.  Inventory
        POSITION = on_hand + queued (stock queue) + deferred (lead queue), so a SKU with an
        order already in flight is not reordered again.
        """
        triggered: list[int] = []
        self._units_ordered = 0          # units ordered THIS batch (Σ reorder qty below)
        for sku in self._depleted_skus:
            if sku not in self._originals:
                continue
            orig     = self._originals[sku]
            rp       = getattr(orig, 'reorder_point', 0)
            cur_qty  = self._current_quantities.get(sku, 0)
            position = cur_qty + self._queued_qty.get(sku, 0) + self._deferred_qty.get(sku, 0)
            if position > rp:
                continue            # on-hand + on-order already covers the threshold
            rc     = orig.reorder()
            # Order-up-to is lead-aware, scaled to the WAREHOUSE equilibrium (not the full
            # generated demand).  reorder_point already encodes ~(lead+1) batches of
            # warehouse-scale demand, so one batch is rp/(lead+1) and the expected in-transit
            # pipeline over the lead is rp·lead/(lead+1).  Raising the order-up-to by this
            # keeps on-hand at the equilibrium target without overshooting the sampled-down
            # eq.  lead==0 ⇒ pipeline 0 ⇒ no-op; general for all lead times.
            lead_f   = max(0.0, getattr(rc, 'lead_time_mean', 0.0))
            pipeline = round(rp * lead_f / (lead_f + 1.0)) if lead_f > 0.0 else 0
            ideal    = _equilibrium_qty(rc) + pipeline - position   # OUP fill-back vs position
            if ideal <= 0:
                continue
            # Received quantity ~ Normal(ideal, ideal·supply_cv), floor 1 (cv set at generation):
            # slight variation, centred on the equilibrium fill, so restocks can split across units.
            # Drawn from a per-reorder RNG keyed on (seed, sku, batch) so the quantity is a pure
            # function of the seed — reproducible and independent of global-stream call order.
            cv   = getattr(rc, 'supply_cv', 0.0)
            if cv > 0.0:
                # String key (tuple seeds are unsupported; str seeding is deterministic
                # across processes, unlike hash() under randomized PYTHONHASHSEED).
                rng = random.Random(f'{self._seed}:{sku}:{self._batch_num}')
                qty = max(1, round(rng.gauss(ideal, ideal * cv)))
            else:
                qty = ideal
            lead = max(0, int(round(getattr(rc, 'lead_time_mean', 0.0))))   # deterministic lead
            self.transit.dispatch(sku, qty, lead,
                                  unit_volume=rc.volume(), now_s=self._now_s)
            self._deferred_qty[sku] = self._deferred_qty.get(sku, 0) + qty
            self._units_ordered += qty
            triggered.append(sku)
        self._depleted_skus.clear()
        return triggered

    def _release_arrivals(self) -> list:
        """Release arrived orders (remaining_lead ≤ 0) into the stock queue.

        Catches both decremented-to-0 olds AND fresh lead-0 newcomers in the same batch,
        which is why it runs after `_fire_reorders` rather than before.

        Returns this batch's `LoadPlan`s -- what came off the trucks, in arrival order.  The
        phase is a producer with no consumer today; the return exists so a receiving crew is
        a caller of an existing phase rather than a rewrite of one.
        """
        plans: list = []
        for sku, qty, _rem in self.transit.release(self._now_s):
            self._deferred_qty[sku] = max(0, self._deferred_qty.get(sku, 0) - qty)
            plans.extend(self._release_to_stock(sku, qty))
        return plans

    def _receive(self, arrivals=(), deadline: float | None = None) -> None:
        """Unload what is standing on the dock, until the crew runs out of day.

        Returns immediately when there is no receiving crew, which is every run that does not
        ask for one -- the dock is not constructed at all, so this is one `is None` test and
        not a flag.

        WHERE THIS SITS IS THE DESIGN. Above it, steps 0-3 are the CALENDAR: a lead time
        elapses whether or not anyone is at work, and a trailer that arrives at four o'clock
        has still arrived. Below it, `_drain_putaway` is the put crew's labour. Receiving is
        labour too, and it must run BEFORE the put drain, or a unit unloaded at nine in the
        morning would wait a whole batch for a bin -- a latency that would be an artifact of
        where the hook sits rather than anything about a warehouse.

        THE WHISTLE IS A START GATE, exactly as put-away's is: a receiver already past
        `deadline` begins nothing new, and the unload in progress when it blows finishes. So
        overtime is bounded by one unload per worker, which is what someone carrying a pallet
        off a tail lift actually does. `deadline` is a REMAINDER on the crew's batch-local
        clock, not an instant -- see `crew_clock.can_start`.

        What the whistle stops stays on the dock and is the first thing tomorrow's crew
        touches: the deque is in arrival order, so the rollover is FIFO and no unit can be
        overtaken by merchandise that arrived after it.

        THE STANDING YARD REROUTES THIS PHASE WHOLE.  When the bound transit carries the
        standing surfaces (`STANDING` — `YardTransit`; the other transits satisfy them
        trivially by not having them), every door and crew decision happens here instead:
        plans-at-arrival, the whistle-independent door fill, the budget-gated unload off
        staged trailers, mid-drain refills, and the canonical handoff.  The dock-floor
        deque below stays EMPTY in that mode — the standing buffer is the trailer itself.
        """
        dock = self._dock
        if dock is None:
            return
        if getattr(self.transit, 'STANDING', False):
            self._receive_standing(dock, self.transit, deadline)
            return
        dock.note_arrivals(arrivals)
        while dock.items and dock.can_start(deadline):
            item = dock.items.popleft()
            unit = item.unit
            order = unit.order
            dur = dock.unload_seconds(order.weight, order.volume(), unit.quantity)
            t0, w = dock.charge(dur)
            dock.records.append((t0, dur, order.sku, unit.quantity, w))
            dock.unloaded += 1
            self._recv_seconds += dur
            # The item was stamped on ARRIVAL and must not be re-stamped, so this goes to
            # `_queue` rather than back through `_admit` -- which would also divert it
            # straight back onto the dock and spin forever.
            self._queue(item)
        # WHAT THE WHISTLE COST, counted once and after the loop. Same discipline as the put
        # side: a counter incremented inside a loop that can run many times reports how many
        # passes were needed rather than how much work the boundary left standing.
        if deadline is not None and dock.items:
            dock.cut += len(dock.items)

    # ── the standing yard (INBOUND_STANDING_YARD; `_receive` reroutes here) ───────────
    #
    # Doors are real: at most `doors` trailers staged, a trailer holds its door across
    # drains until fully unloaded, the yard-pull fires when a door frees.  Decisions are
    # drain-quantized over FROZEN rankings (the `put_policy` purity contract); the data is
    # event-stamped.  The division of labour with `YardTransit`: the transit holds trailer,
    # yard and door STATE; this side owns every DECISION, because it owns what a decision
    # needs — `_originals` and the packer (plans-at-arrival), the ledger (the per-unit
    # deferred→queued flip), and the crew (via the injected dock's clock operations).

    def _receive_standing(self, dock, transit, deadline: float | None) -> None:
        """One drain of the standing dock: plan arrivals, fill doors, unload, hand off.

        Four steps, and their order is the design:

        1. PLANS-AT-ARRIVAL.  Every trailer that joined the yard gets its pack plan NOW,
           per contiguous lot — the same portions the v1 drain packs — and its units are
           stamped immediately, so a pallet that stands three batches in the yard is three
           batches old when it finally reaches floor space.  The merchandise stays in
           `_deferred_qty`: nothing is queued until a crew actually pulls it.
        2. THE DOOR FILL, NOT budget-gated.  Staging is yard-jockey work, not receiving
           labour, so even a zero-budget drain fills every free door from the drain-frozen
           yard ranking.
        3. THE UNLOAD, budget-gated, per the crew-allocation mode ('split' door teams or
           the 'merged' pooled gang).  The whistle is a START gate, one unload of overtime
           per worker, exactly as the v1 path's.  Same-drain refills consume the frozen
           rankings; no mid-drain re-scoring.
        4. THE CANONICAL HANDOFF.  Whatever the allocation, unloaded units reach `_queue`
           in merged order — trailers by dock rank, units by local rank, filtered to what
           actually unloaded — never in labor-completion order.  That is the containment
           property: crew allocation moves labor stamps and makespans ONLY, never
           placement physics.  The deferred→queued flip rides the handoff, per unit, so
           `position = on_hand + queued + deferred` never wobbles.
        """
        epoch = self._now_s if self._now_s is not None else 0.0
        source = getattr(transit, 'SOURCE', 'reorder')
        pack = self.packer if self.packer is not None else _pack_plain
        ctx = transit.freeze_ctx()
        # CTX-FREEZE IS VIEW-FREEZE: one space projection per drain serves every decision
        # in it (no per-decision rescans).  `ctx.space` is the named-view arrival point
        # the priority seams reserved; every seeded 'fifo' key ignores it, so with both
        # policies 'fifo' the view is pure data -- neutrality rides the degenerate
        # lockstep (test_space_timeline).
        if self.space_timeline is not None:
            ctx.space = self.space_timeline.freeze(self, epoch)

        # 1. plans-at-arrival (manager-side: the transit can reach neither _originals nor
        #    the packer).  Stamped in yard order, so ages are monotone with arrival.
        plans_new: list = []
        for trailer in transit.unplanned():
            items: list = []
            tplans: list = []
            for sku, qty in transit.planned_lots(trailer, ctx):
                rc = self._originals[sku].reorder()
                deliveries = (self.inbound_split(sku, qty)
                              if self.inbound_split is not None else None)
                got = 0
                for plan in pack(rc, qty, deliveries):
                    tplans.append(plan)
                    for unit in plan.units:
                        items.append(self._stamp(unit, source))
                        got += unit.quantity
                if got < qty:
                    # The packer placed less than arrived.  v1's net effect exactly: the
                    # release debits the full delivery, the queue credits what packed —
                    # here the shortfall debits at arrival so the remainder ledger stays
                    # the PLANNED quantities the census also counts.
                    self._deferred_qty[sku] = max(
                        0, self._deferred_qty.get(sku, 0) - (qty - got))
            trailer.plans = tplans
            trailer.pending = items
            trailer.taken = 0
            plans_new.extend(tplans)
            if not items:
                # Nothing packed at all: no work to hold a door open for.
                transit.discard(trailer, epoch)
        dock.note_arrivals(plans_new)

        # 2. the door fill — NOT budget-gated (yard-jockey work, not crew labour).
        yard_next = deque(transit.yard_order(ctx))
        while transit.free_doors > 0 and yard_next:
            transit.stage(yard_next.popleft(), epoch)
        # The drain-frozen DOCK ranking, over everything now staged (carried remainders
        # and fresh stagings alike): the allocation preference and the handoff order.
        work_order = transit.dock_order(ctx)

        # 3. the unload, per the allocation mode.
        if getattr(transit, 'allocation', 'merged') == 'split':
            done = self._unload_split(dock, transit, deadline, epoch,
                                      work_order, yard_next)
        else:
            done = self._unload_merged(dock, transit, deadline, epoch,
                                       work_order, yard_next)

        # 4. the canonical handoff, with the per-unit ledger flip.  `dock.seconds`
        #    accrues HERE, in canonical order, for both allocation modes: summed in
        #    charge order instead, split's float association differs from merged's by
        #    an ulp, and "identical except labor stamps" stops being byte-true.
        for trailer, recs in done:
            for item, t0, dur, w in recs:
                unit = item.unit
                sku = unit.order.sku
                self._deferred_qty[sku] = max(
                    0, self._deferred_qty.get(sku, 0) - unit.quantity)
                self._queued_sku_counts[sku] = self._queued_sku_counts.get(sku, 0) + 1
                self._queued_qty[sku] = self._queued_qty.get(sku, 0) + unit.quantity
                dock.records.append((t0, dur, sku, unit.quantity, w))
                dock.unloaded += 1
                dock.seconds += dur
                self._recv_seconds += dur
                self._queue(item)

        # What the whistle cost: the remainders standing on STAGED trailers, in storage
        # units, counted once.  The yard is never cut — waiting there is calendar, the
        # fee proxy's domain, not a labour boundary's.
        left = sum(len(t.pending) - t.taken for t in transit.staged()
                   if t.pending is not None)
        if deadline is not None and left:
            dock.cut += left

        # THE DRAIN'S ROW.  Two pairs, and they answer two different questions.  The START
        # pair is CONTENTION — standing trailers against free doors at freeze, which is
        # what "did the yard bind" means before anything was served.  The END pair is the
        # BINDING CUT — trailers this drain never reached and units it left on a door.
        # Both are LEVELS: they are re-measured every drain and summing either across
        # drains restates the same standing trailers once per batch (the `recv_cut` scar).
        # `left` is computed above the whistle test, not inside it: a drain that ran out of
        # WORK leaves the same remainder as one that ran out of DAY, and only one of those
        # is a cut — the level says what was standing either way.
        self._yard_drains.append(
            (ctx.yard_depth, ctx.free_doors, transit.yard_depth, left))

    def _unload_merged(self, dock, transit, deadline, epoch, work_order, yard_next):
        """The 'merged' pooled gang: v1's physics kept as the verification bridge.

        One crew works trailers strictly in dock-rank order, completing the top-ranked
        first; a freed door pulls the frozen-ranking-next trailer, which joins the END of
        the work list.  In the degenerate configuration (FIFO, doors >= every trailer, no
        cap) the charge sequence is exactly the v1 drain's — the lockstep pin.
        Returns [(trailer, [(item, t0, dur, worker), ...])] in canonical drain order.
        """
        done: list = [(t, []) for t in work_order]
        # A team of everybody IS the pooled gang; charge_team so `seconds` accrues at
        # the handoff (canonical order) rather than here — see that loop's comment.
        gang = list(range(dock.crew_size))
        idx = 0
        while idx < len(done):
            trailer, recs = done[idx]
            pend = trailer.pending or []
            gated = False
            while trailer.taken < len(pend):
                if not dock.can_start(deadline):
                    gated = True
                    break
                item = pend[trailer.taken]
                order = item.unit.order
                dur = dock.unload_seconds(order.weight, order.volume(),
                                          item.unit.quantity)
                t0, w = dock.charge_team(gang, dur)
                recs.append((item, t0, dur, w))
                trailer.taken += 1
            if gated:
                break
            at = epoch + (recs[-1][1] + recs[-1][2] if recs else 0.0)
            transit.door_freed(trailer, at)
            if yard_next:
                nxt = yard_next.popleft()
                transit.stage(nxt, at)
                done.append((nxt, []))
            idx += 1
        return done

    def _unload_split(self, dock, transit, deadline, epoch, work_order, yard_next):
        """The 'split' door teams: the standing model's own physics.

        At ctx-freeze the workers are DEALT across staged trailers in dock-priority
        order, cycling, so the top ranks take the extras when the division is uneven
        (`allocation.partition`, round-robin).  Each team charges earliest-free WITHIN
        the team.  Doors therefore free STAGGERED — the realistic dynamic the fee and
        space signals need — and when one does, the yard-pull stages the frozen-next
        trailer and the freed team reassigns: (1) to the top-ranked staged trailer with
        NO workers — the one-worker-many-doors inversion's guard — (2) else to its own
        door's replacement, (3) else to the top-ranked trailer with the fewest workers.
        A worker idles only when nothing staged has units.  Dock priority is thereby a
        worker-ALLOCATION preference: decisive when workers < staged trailers, graded
        otherwise.

        The loop advances whichever team can start soonest, so charges interleave in
        true clock order and a reassignment always sees every earlier emptying's effect.
        Returns the same shape as `_unload_merged`, in the same canonical order.
        """
        done: list = [(t, []) for t in work_order]
        recs_of = {id(t): recs for t, recs in done}
        alive: list = list(work_order)
        teams: dict = {}
        if alive:
            crew = list(range(dock.crew_size))
            for trailer, team in zip(alive, partition(crew, len(alive))):
                teams[id(trailer)] = team
        while True:
            best = None
            best_ns = 0.0
            for trailer in alive:
                team = teams.get(id(trailer))
                if not team or trailer.taken >= len(trailer.pending or []):
                    continue
                ns = dock.team_next_free(team)
                if best is None or ns < best_ns:
                    best, best_ns = trailer, ns
            if best is None:
                break                              # nothing workable anywhere
            if deadline is not None and best_ns >= deadline:
                break                              # the START gate, globally: best_ns is
                                                   # the min over teams, so nobody can
            item = best.pending[best.taken]
            order = item.unit.order
            dur = dock.unload_seconds(order.weight, order.volume(), item.unit.quantity)
            t0, w = dock.charge_team(teams[id(best)], dur)
            recs_of[id(best)].append((item, t0, dur, w))
            best.taken += 1
            if best.taken < len(best.pending):
                continue
            # The trailer came up empty: the door frees AT THAT INSTANT (staggered, not
            # at the drain boundary), the yard-pull fires, and the freed team reassigns.
            at = epoch + t0 + dur
            transit.door_freed(best, at)
            freed = teams.pop(id(best))
            alive.remove(best)
            nxt = None
            if yard_next:
                nxt = yard_next.popleft()
                transit.stage(nxt, at)
                teams[id(nxt)] = []
                alive.append(nxt)
                recs: list = []
                done.append((nxt, recs))
                recs_of[id(nxt)] = recs
            live = [t for t in alive
                    if t.pending is not None and t.taken < len(t.pending)]
            target = next((t for t in live if not teams.get(id(t))), None)      # (1)
            if target is None and nxt is not None and nxt in live:              # (2)
                target = nxt
            if target is None and live:                                         # (3)
                pos = {id(t): i for i, t in enumerate(alive)}
                target = min(live, key=lambda t: (len(teams.get(id(t), ())),
                                                  pos[id(t)]))
            if target is not None:
                teams[id(target)].extend(freed)
        return done

    def _drain_putaway(self, deadline: float | None = None) -> None:
        """Place the stock queue into bins (retries prior-batch stragglers too).

        `deadline` is the day's whistle on the put crews' batch-local clocks; what the
        whistle stops stays queued and is drained by the next batch, which is the put side
        of "work that does not complete rolls over to the next shift".
        """
        if self._stock_queue:
            self._stock(deadline=deadline)

    def check_reorders(self, put_deadline: float | None = None,
                       recv_deadline: float | None = None,
                       now_s: float | None = None) -> list[int]:
        """Order-Up-To replenishment through an explicit, deterministic lead queue.

        Every reorder enters `_lead_queue` as a [sku, qty, remaining_lead] record — even
        lead 0.  Per call (one completed batch), in this order and no other:
          0. Advance the batch calendar and reclaim bins the picks emptied.
          1. Advance every pre-existing in-transit order: remaining_lead -= 1.
          2. Fire OUP reorders for depleted SKUs → append new records (remaining_lead =
             round(lead_time_mean) ≥ 0); reorder qty ~ Normal(ideal, ideal·supply_cv)
             centred on the equilibrium fill (supply_cv set at generation).
          3. Release every arrived order (remaining_lead ≤ 0) into the stock queue — this
             catches both decremented-to-0 olds AND fresh lead-0 newcomers (same batch).
          4. Unload the dock, if there is a receiving crew, until its day runs out.
          5. Place the stock queue into bins via _stock().

        Inventory POSITION = on_hand + queued (stock queue) + deferred (lead queue), so a
        SKU with an order already in flight is not reordered again.

        `put_deadline` and `recv_deadline` are the two crews' whistles, each in seconds on
        its OWN batch-local clock, and each reaching exactly one phase.  They are separate
        parameters rather than one shared day because the crews are separate: handing
        receiving the put crew's remaining day would be arithmetically well-formed and wrong
        by an unrelated crew's overrun, and a cut count of zero reads as "the boundary cost
        nothing".

        Neither reaches anything above step 4 on purpose: steps 0-3 are the CALENDAR
        advancing — a lead time elapses whether or not anyone is at work, and a trailer that
        arrives at four o'clock has still arrived.  What a day bounds is the LABOUR, and the
        labour is steps 4 and 5.

        THE ORDER IS THE BEHAVIOUR.  Each step above is a method so a future caller can
        drive them separately, but reordering them changes results: firing before the lead
        tick would decrement an order in the batch it was placed, and releasing before
        firing would delay every lead-0 arrival by a batch.
        """
        # The arm's absolute epoch, stowed for the transit (trailer leads are seconds
        # on this clock; the batch countdown ignores it).  A field rather than a phase
        # parameter so every phase stays callable with no arguments — the ratchet.
        self._now_s = now_s
        self._tick_batch()
        self.reclaim_emptied_bins()
        self._advance_lead_queue()
        triggered = self._fire_reorders()
        arrivals = self._release_arrivals()
        self._receive(arrivals, recv_deadline)
        self._drain_putaway(put_deadline)
        return triggered
