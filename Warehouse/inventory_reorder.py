"""inventory_reorder.py — churn counters, reload primitive, pick notifications, and
Order-Up-To reorder logic.

`ReorderMixin` holds the per-batch lifecycle methods.  Mixed into Inventory_Manager so
the public API (`pop_churn`, `requeue_bin`, `check_reorders`, and the O(1) pick
notifications called by PickSimulation) is unchanged.  All instance state and the
collaborators it calls (`self._index_add`, `self._drain`) are provided by
Inventory_Manager.__init__ / its placement methods.
"""
from __future__ import annotations

import random

from Aisle_Storage import Aisle
from Storage_Primitive import viable_storage_units
from inventory_common import _equilibrium_qty


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
        sku = unit.carton.sku

        if self._sigma_freq is not None:           # incremental Sigma f*D: eviction (−)
            self._sigma_fd -= (self._sigma_freq.get(sku, 0.0)
                               * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))

        # Free the bin and return it to the available index.
        bin_.storage = None
        self._unavailable.pop(id(bin_), None)
        self._bin_sku.pop(id(bin_), None)
        (self._sku_singleton_bins if bin_.unit_type == 'singleton'
         else self._sku_pallet_bins)[sku].discard(bin_)
        self._index_add(bin_)

        # Mirror _reclaim_empty_bins' per-SKU aisle-state removal (affinity on).
        if self._affinity is not None:
            aid    = bin_.location[0]
            idx    = self._affinity._sku_to_idx.get(sku)
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

        # On-hand -> on-order (queued); re-enqueue the unit for the ranked drain.
        self._current_quantities[sku] = max(0, self._current_quantities.get(sku, 0) - unit.quantity)
        self._queued_qty[sku]         = self._queued_qty.get(sku, 0) + unit.quantity
        self._queued_sku_counts[sku]  = self._queued_sku_counts.get(sku, 0) + 1
        self._queue.append(unit)
        self._reload_moves += 1

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

    def _notify_bin_emptied(self, bin_: Aisle.Bin) -> None:
        """Queue an emptied bin for reclaim at the next check_reorders call.

        Called by PickSimulation immediately after bin_.storage is set to
        None — must be O(1).  The bin stays in _unavailable until
        _reclaim_empty_bins processes _pending_reclaim.
        """
        if self._sigma_freq is not None:
            sku = self._bin_sku.get(id(bin_))      # still set until reclaim pops it
            if sku is not None:
                self._sigma_fd -= (self._sigma_freq.get(sku, 0.0)
                                   * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))
        self._pending_reclaim.append(bin_)

    def _apply_picks_batch(
        self,
        picks: list[tuple[int, int]],
        empties: list[Aisle.Bin],
    ) -> None:
        """Apply all pick notifications accumulated during one simulation run.

        Aggregates quantity by SKU before calling _notify_pick so the body
        executes once per unique SKU rather than once per pick event,
        cutting ~430k individual function calls down to ~5k.
        """
        agg: dict[int, int] = {}
        for sku, qty in picks:
            agg[sku] = agg.get(sku, 0) + qty
        for sku, qty in agg.items():
            self._notify_pick(sku, qty)
        self._pending_reclaim.extend(empties)

    # ── reorder logic ────────────────────────────────────────────────────────

    def _reclaim_empty_bins(self) -> None:
        """Return bins in _pending_reclaim to the available index.

        With _unavailable as a dict and _pending_reclaim as a targeted list,
        this is O(pending_bins) — typically a handful per batch — instead of
        the previous O(total_bins) full scan.  Attribute refs are hoisted
        outside the loop to avoid repeated self. lookups across ~7k iterations.
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

    def check_reorders(self) -> list[int]:
        """Order-Up-To replenishment with optional per-SKU lead-time deferral.

        Each call:
          1. Increments the internal batch counter.
          2. Releases any deferred reorders whose lead time has elapsed.
          3. For each SKU flagged by _notify_pick as below its reorder_point,
             computes qty = equilibrium_qty − current_qty (OUP fill-back) and
             either schedules immediately or defers by lead_time_mean batches.

        Guard: a SKU with units already queued OR in-flight deferred is skipped
        so only one replenishment wave is ever in-flight per SKU at a time.

        Lead-time sampling: if carton.lead_time_mean > 0, samples
        max(1, round(N(mean, mean))) batches.  Zero mean = immediate placement.
        """
        self._batch_num += 1
        self._reclaim_empty_bins()

        # ── 1. Release deferred reorders that have arrived ───────────────────
        due = self._deferred_reorders.pop(self._batch_num, None)
        if due:
            for sku, units in due:
                self._deferred_sku_counts[sku] = max(
                    0, self._deferred_sku_counts.get(sku, 0) - len(units)
                )
                moved_qty = sum(u.quantity for u in units)
                self._deferred_qty[sku] = max(0, self._deferred_qty.get(sku, 0) - moved_qty)
                self._queued_qty[sku]   = self._queued_qty.get(sku, 0) + moved_qty
                for unit in units:
                    self._queue.append(unit)
                self._queued_sku_counts[sku] = (
                    self._queued_sku_counts.get(sku, 0) + len(units)
                )

        # Fast exit when there is nothing to do.
        if not self._depleted_skus and not self._queue:
            return []

        # ── 2. Fire OUP reorders for depleted SKUs ───────────────────────────
        # Reorder decisions use INVENTORY POSITION = on-hand + on-order (queued +
        # deferred), not on-hand alone.  A SKU is only genuinely depleted when its
        # position is at/below reorder_point; if an in-flight wave already lifts
        # position above the threshold, it is skipped — no duplicate reorder while
        # earlier units still await a bin.  Order up to equilibrium: eq − position.
        triggered: list[int] = []
        for sku in self._depleted_skus:
            if sku not in self._originals:
                continue
            orig      = self._originals[sku]
            rp        = getattr(orig, 'reorder_point', 0)
            cur_qty   = self._current_quantities.get(sku, 0)
            on_order  = self._queued_qty.get(sku, 0) + self._deferred_qty.get(sku, 0)
            position  = cur_qty + on_order
            if position > rp:
                continue            # on-hand + on-order already covers the threshold
            rc        = orig.reorder()
            eq_qty    = _equilibrium_qty(rc)
            ideal     = eq_qty - position               # OUP fill-back vs position
            if ideal <= 0:
                continue
            cv        = getattr(rc, 'supply_cv', 0.0)
            # Sample received quantity: N(ideal, ideal × cv), floor at 1.
            # cv=0 → always receive exactly what was ordered.
            qty       = max(1, round(random.gauss(ideal, ideal * cv))) if cv > 0.0 else ideal
            units     = viable_storage_units(rc, qty)
            if not units:
                continue
            ordered_qty = sum(u.quantity for u in units)

            lt_mean = getattr(rc, 'lead_time_mean', 0.0)
            if lt_mean > 0.0:
                # Sample lead time; floor at 1 so deferred ≠ immediate
                lead = max(1, round(random.gauss(lt_mean, lt_mean)))
                self._deferred_reorders[self._batch_num + lead].append((sku, units))
                self._deferred_sku_counts[sku] = (
                    self._deferred_sku_counts.get(sku, 0) + len(units)
                )
                self._deferred_qty[sku] = self._deferred_qty.get(sku, 0) + ordered_qty
            else:
                for unit in units:
                    self._queue.append(unit)
                self._queued_sku_counts[sku] = (
                    self._queued_sku_counts.get(sku, 0) + len(units)
                )
                self._queued_qty[sku] = self._queued_qty.get(sku, 0) + ordered_qty
            triggered.append(sku)
        self._depleted_skus.clear()

        # Always drain — retries prior-batch stragglers too.  _drain() dispatches
        # to the ranked wave or the per-unit path based on the placement policy.
        if self._queue:
            self._drain()
        return triggered
