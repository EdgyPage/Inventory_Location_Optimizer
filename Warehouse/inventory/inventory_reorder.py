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

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Warehouse.inventory.inventory_common import PutawayItem, _equilibrium_qty


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
        (self._sku_singleton_bins if bin_.unit_type == 'singleton'
         else self._sku_pallet_bins)[sku].discard(bin_)
        self._index_add(bin_)

        self._drop_sku_from_aisle(sku, bin_)

        # On-hand -> on-order (queued); re-enqueue the unit for the ranked drain.
        self._current_quantities[sku] = max(0, self._current_quantities.get(sku, 0) - unit.quantity)
        self._queued_qty[sku]         = self._queued_qty.get(sku, 0) + unit.quantity
        self._queued_sku_counts[sku]  = self._queued_sku_counts.get(sku, 0) + 1
        self._stock_queue.append(PutawayItem(unit, 'reslot'))
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

        ``at`` is the picker-local second the bin ran dry.  Recorded in `_emptied_at` and
        read by nothing yet: the drain still happens once, at the top of the next batch, so
        a slot freed mid-batch is invisible until then.  Knowing WHEN is what a forecast of
        upcoming bin slots needs, and the pick loop is the only place that knows it.
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
                lst = (sku_singleton if bin_.unit_type == 'singleton' else sku_pallet).get(sku)
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

    def _release_to_stock(self, sku: int, qty: int) -> None:
        """Convert an arrived (sku, qty) order into storage units and append them to the
        stock queue, updating the queued-unit / queued-qty trackers.  Shared by every
        lead-queue arrival (including lead-0 orders released the same batch)."""
        rc    = self._originals[sku].reorder()
        units = viable_storage_units(rc, qty)
        if not units:
            return
        for unit in units:
            self._stock_queue.append(PutawayItem(unit, 'reorder'))
        self._queued_sku_counts[sku] = self._queued_sku_counts.get(sku, 0) + len(units)
        self._queued_qty[sku]        = self._queued_qty.get(sku, 0) + sum(u.quantity for u in units)

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
    #:   * The feature that makes it wrong is the trailer/dock work, where an arrival IS a
    #:     scheduled instant and the sorter's whole job is choosing between them.  That
    #:     feature should pick the representation with the trailer model in hand, not
    #:     inherit one chosen here.
    #:
    #: Until then: a `work_events` row for an inbound arrival would sit at a batch
    #: boundary, and anything reasoning about arrival TIMES must know that.
    LEAD_TIME_UNIT = 'batches'

    def _advance_lead_queue(self) -> None:
        """One batch elapsed: decrement pre-existing in-transit orders only.

        Runs BEFORE `_fire_reorders`, which is what stops an order fired this batch from
        being decremented in the same batch it was placed.

        The tick is one BATCH, not one second -- see `LEAD_TIME_UNIT` above for why that
        is a decision and what would change it.
        """
        for entry in self._lead_queue:
            entry[2] -= 1

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
            self._lead_queue.append([sku, qty, lead])
            self._deferred_qty[sku] = self._deferred_qty.get(sku, 0) + qty
            self._units_ordered += qty
            triggered.append(sku)
        self._depleted_skus.clear()
        return triggered

    def _release_arrivals(self) -> None:
        """Release arrived orders (remaining_lead ≤ 0) into the stock queue.

        Catches both decremented-to-0 olds AND fresh lead-0 newcomers in the same batch,
        which is why it runs after `_fire_reorders` rather than before.
        """
        if not self._lead_queue:
            return
        still: list[list] = []
        for sku, qty, rem in self._lead_queue:
            if rem <= 0:
                self._deferred_qty[sku] = max(0, self._deferred_qty.get(sku, 0) - qty)
                self._release_to_stock(sku, qty)
            else:
                still.append([sku, qty, rem])
        self._lead_queue = still

    def _drain_putaway(self) -> None:
        """Place the stock queue into bins (retries prior-batch stragglers too)."""
        if self._stock_queue:
            self._stock()

    def check_reorders(self) -> list[int]:
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
          4. Place the stock queue into bins via _stock().

        Inventory POSITION = on_hand + queued (stock queue) + deferred (lead queue), so a
        SKU with an order already in flight is not reordered again.

        THE ORDER IS THE BEHAVIOUR.  Each step above is a method so a future caller can
        drive them separately, but reordering them changes results: firing before the lead
        tick would decrement an order in the batch it was placed, and releasing before
        firing would delay every lead-0 arrival by a batch.
        """
        self._tick_batch()
        self.reclaim_emptied_bins()
        self._advance_lead_queue()
        triggered = self._fire_reorders()
        self._release_arrivals()
        self._drain_putaway()
        return triggered
