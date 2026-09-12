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
from typing import NoReturn

from Warehouse.kernel.allocation import partition
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Warehouse.inventory.inventory_common import (
    PutawayItem, UndeclaredStock, is_forward_pick, _equilibrium_qty)


# ── the stock declaration, on the READ side ──────────────────────────────────────────
# A stock level is a RUN's declaration, never a SKU's fact (ADR-0002): the catalogue carries
# none, and `Order.declare_stock` -- the single mutation site -- writes all four level slots
# together at setup.  `Order` uses __slots__, so an order no run has declared on does not
# carry an unset `reorder_point`; it has NO SUCH ATTRIBUTE, and `getattr(order,
# 'reorder_point', <default>)` answers that question with the default.  Both reorder sites
# below used to do exactly that, and both defaults were silent:
#
#   * `None` in `_notify_pick` flags no SKU depleted, so NO REORDER EVER FIRES -- no error,
#     no log line, and a run whose picks, travel and labour all look healthy while
#     replenishment is entirely off.
#   * `0` in `_fire_reorders` makes `position > rp` true for every SKU that still holds a
#     unit anywhere, which is that same silence with a worse tail: a SKU picked to exactly
#     zero falls through to an order-up-to taken from the reorder COPY, whose level slots
#     default to 1 -- a one-unit dribble that reads as a working restock in every table.
#
# Nothing downstream can detect either (the conservation ledger is bin-only; a run that
# never restocks conserves perfectly), so both now raise.  There is deliberately no
# `stock_qty`-style legacy fallback for the threshold the way `_equilibrium_qty` has one for
# the target: nothing has ever duck-typed a reorder point, and inventing one here would be a
# reorder POLICY written inside an error handler.


def _undeclared_stock(order) -> NoReturn:
    """Raise `UndeclaredStock` for an order no run has declared a stock level on.

    The `inventory_common._equilibrium_qty` precedent, at the other end of the same
    declaration and for the same reason.  A RAISER rather than a `_reorder_point(order)`
    accessor on purpose: `_notify_pick` is the pick loop's O(1) hook -- once per pick
    mutation, hundreds of thousands of times a batch -- and both call sites already had the
    `is None` test they need.  Written this way the guard costs the fast path nothing at all,
    so no future profile can argue it back out of existence.
    """
    raise UndeclaredStock(
        f'SKU {getattr(order, "sku", "?")}: no stock level has been declared for this order, '
        f'so it has no reorder point to trigger on. The catalogue carries no levels '
        f'(ADR-0002): a run declares them at setup from coverage_days / safety_days / '
        f'floor_lines through Optimization/simconfig/coverage.rescale_section, and records '
        f'the fielded levels in its own planned inventory (the stock_levels table). '
        f'Replenishing against a level nobody declared is what this error exists to catch.')


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

        TWO ABSENCES, TWO ANSWERS, and they used to be one test.  A SKU with no TEMPLATE is
        tolerated; a template with no DECLARED level raises.  See the guards below — the
        difference is the whole reason this method is not three lines.
        """
        cur = self._current_quantities.get(sku, 0)
        if cur <= 0:
            return
        new_qty = max(0, cur - qty)
        self._current_quantities[sku] = new_qty
        orig = self._originals.get(sku)
        if orig is None:
            # NO TEMPLATE, NO REPLENISHMENT — a contract, not the ADR-0002 hole below.
            # `_originals` is filled by INTAKE (`enqueue` / `enqueue_all` / `place_optimal`);
            # a manager driven over bins it did not take in — the constructor accepts a
            # warehouse whose bins already hold storage, and `init_lift_state` rebuilds
            # `_current_quantities` from them — can legitimately pick a SKU it has no
            # template for.  It also cannot restock one: `.reorder()` needs that template, so
            # `_fire_reorders` ALREADY skips such a SKU (`if sku not in self._originals:
            # continue`) and a flag set here could only be a flag that phase drops.  The two
            # guards state one contract between them; keep them agreeing.
            return
        rp = getattr(orig, 'reorder_point', None)
        if rp is None:
            # THE SITE THAT ATE REPLENISHMENT WHOLE.  This used to fold into the test above
            # and answer `None`, so an undeclared SKU was never flagged, never reordered, and
            # never complained.  Reachable: setup places through `_equilibrium_qty` (which
            # raises), but `enqueue(order, quantity=N)` takes an explicit quantity and skips
            # that read entirely — so an undeclared order can reach a bin, and from a bin it
            # reaches this line.  Do not restore a default here; a default is the silence.
            _undeclared_stock(orig)
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
    #:     (minutes-authored, default zero) on the same `transit` seam -- and still
    #:     serves the SKU's batch lead in FRONT of the trailer, as its SUPPLIER lead at
    #:     the ordering site (`TrailerTransit.dispatch`), so the two stages add.
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
            rp       = getattr(orig, 'reorder_point', None)
            if rp is None:
                # THE TEMPLATE IS THE ONLY THING THAT CAN RAISE — see the copy below.  The
                # old default was 0, which is not a conservative threshold but an invisible
                # one: `position > 0` holds for every SKU still holding a unit, so an
                # undeclared SKU simply never reordered, and the one that reached zero
                # ordered a single unit off the copy's defaults.
                _undeclared_stock(orig)
            cur_qty  = self._current_quantities.get(sku, 0)
            position = cur_qty + self._queued_qty.get(sku, 0) + self._deferred_qty.get(sku, 0)
            if position > rp:
                continue            # on-hand + on-order already covers the threshold
            rc     = orig.reorder()
            # Order-up-to is lead-aware, scaled to the WAREHOUSE equilibrium (not the full
            # generated demand): the order-up-to is raised by the units expected in transit
            # over the lead so on-hand lands at the equilibrium target without overshooting
            # the sampled-down eq.  `Order.pipeline_allowance` is the ONE definition -- the
            # era's stamped `pipeline_qty` (round(d_s x lead), simconfig/coverage.py) when the
            # SKU carries it, else the heuristic this loop always used: reorder_point encodes
            # ~(lead+1) batches of demand, so the pipeline is rp·lead/(lead+1).  lead==0 ⇒
            # pipeline 0 ⇒ no-op either way.
            # BOTH READS BELOW ARE ON THE COPY, and the guard that protects them is on the
            # TEMPLATE.  `Order.reorder()` copies the declaration only when the template has
            # one, so an undeclared SKU's copy is undeclared too and these reads WOULD raise --
            # but they would raise here, deep in the restock arithmetic, rather than at the
            # `rp is None` test on `orig` above, which is where the message can still name the
            # SKU and say where a level comes from.  (Before ADR-0002 the copy could not raise
            # at all: every slot came from a `getattr` default, so an undeclared SKU took a
            # target of 1, ordered one unit a batch forever, and looked like a working restock
            # in every table.)  So: do not hoist these reads above that test, and do not delete
            # it on the grounds that `_equilibrium_qty` raises -- raising LATE is the failure
            # this comment exists to prevent, not raising at all.
            pipeline = rc.pipeline_allowance()
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
        has still arrived. Below it, `drain_putaway` is the put crew's labour. Receiving is
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
            # THE COORDINATOR OWNS THE DRAIN.  `Warehouse -> Inbound` is forbidden in both
            # directions (`context/architecture.yml:102-103`), so it arrives by INJECTION
            # like the dock, the packer and the transit: whoever builds the standing yard
            # builds the coordinator and binds it.  A loud refusal rather than an inline
            # fallback, because a second implementation of this drain is precisely what
            # the extraction removed -- and a silent one would be a run answering a
            # different question (memory `pool-run-swallows-dead-arms`).
            if self.receiving is None:
                raise RuntimeError(
                    'a standing transit is bound but no receiving coordinator is: the '
                    'standing drain lives on `Inbound.receiving.SiteReceiving` and is '
                    'reached through `mgr.receiving`. Bind one where the Dock and the '
                    'YardTransit are built')
            # ONE LEAF, and the coordinator refuses a partial site drain -- a coupled leaf
            # reaching here would be draining the site's dock for its own channel while the
            # other's arrivals stood on the yard.  A coupled run drives `SiteReceiving.drain`
            # instead, which composes these same seven phases for every leaf at once.
            row = self.receiving.receive((self,), deadline)
            if row is not None:
                self._yard_drains.append(row)
            return
        dock.note_arrivals(arrivals)
        while dock.items and dock.can_start(deadline):
            item = dock.items.popleft()
            unit = item.unit
            order = unit.order
            # `unit=` is read only by a SITE dock, whose price list is keyed by the
            # merchandise's regime (site-dock 27).  Unreachable from here today -- a
            # coupled run takes the STANDING branch above and never reaches this deque
            # drain -- but it is the one charge site that would REFUSE rather than
            # price if a site dock were ever bound to a non-standing transit, and the
            # unit is already in hand one line up.
            dur = dock.unload_seconds(order.weight, order.volume(), unit.quantity,
                                      unit=unit)
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

    # ── the standing yard's two ports (`Inbound.receiving.SiteReceiving` calls these) ──
    #
    # The standing drain itself lives on the COORDINATOR, because one dock serves the
    # whole site and a manager is one channel's.  What stays here is what a manager alone
    # can answer: `_originals` and the packer (plans-at-arrival), and the ledger (the
    # per-unit deferred→queued flip).  The transit holds trailer, yard and door STATE;
    # the coordinator owns every DECISION; these two methods are the only way it reaches
    # merchandise.
    #
    # They are PUBLIC on purpose.  A cross-package caller that reached a private would be
    # a boundary violation dressed as an underscore, and they are separately callable —
    # which is what makes the drain's two merchandise-bearing steps testable without
    # standing up a dock.
    #
    # `owned_skus` below is the coordinator's THIRD port and is not one of these two: it
    # carries no merchandise, is read once at bind time rather than during a drain, and
    # exists so the `{sku: leaf}` owner dict can be built without reaching `_originals`
    # across the package boundary.

    def owned_skus(self):
        """The catalogue partition THIS leaf is responsible for, as a view of its keys.

        The third port, and the only one the coordinator reads outside a drain: it builds
        its `{sku: leaf}` owner dict from every bound leaf's set ONCE, at bind time, and
        refuses an overlap there (`Inbound/receiving.py`).  Public rather than reaching
        `_originals` across the package boundary — a cross-package caller that touched a
        private would be a boundary violation dressed as an underscore.

        `_originals` is the templates the manager was loaded with, which IS the partition:
        a channel leaf loads its own regime-filtered inventory, so this set is that filter's
        result and the two leaves' sets are disjoint by construction.

        IT IS FILLED BY INTAKE (`enqueue` / `enqueue_all` / `place_optimal`), which the
        driver runs to completion BEFORE the batch loop — not by anything a drain does. So
        the set is stable for the life of a coupled run, and the bind-time read is a read
        of the whole partition rather than of however much of it had arrived. A caller that
        broke that would break loudly rather than quietly: a sku the dict never learned
        about refuses at `_owner_of` instead of being delivered somewhere plausible.
        """
        return self._originals.keys()

    def plan_lot(self, sku: int, qty: int, source: str) -> tuple:
        """Plans-at-arrival for ONE contiguous lot: `(plans, items)`.

        Returns both halves because both are consumed and neither derives the other: the
        `LoadPlan`s become `trailer.plans` and the dock's arrival notice, while the
        stamped items become `trailer.pending`.  A plan holds several units, so the
        grouping is not recoverable from the items alone.

        `source` rides in from the transit (`transit.SOURCE`) rather than being read off
        `self.transit`, because the coordinator's transit is the site's and a leaf's may
        be unbound entirely once there are two.
        """
        pack = self.packer if self.packer is not None else _pack_plain
        rc = self._originals[sku].reorder()
        deliveries = (self.inbound_split(sku, qty)
                      if self.inbound_split is not None else None)
        plans: list = []
        items: list = []
        got = 0
        for plan in pack(rc, qty, deliveries):
            plans.append(plan)
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
        return plans, items

    def accept(self, item, dur: float) -> None:
        """The canonical handoff for ONE unloaded unit: ledger flip, labour, queue.

        `position = on_hand + queued + deferred` never wobbles, because the three ledger
        moves and the queue entry happen together here.

        Takes `dur` and not the whole `(t0, dur, w)` record: `t0` and `w` are fields of
        the DOCK's row, and the dock is the coordinator's — handing them to a leaf would
        be two dead parameters plus an invitation to write the row twice.
        """
        unit = item.unit
        sku = unit.order.sku
        self._deferred_qty[sku] = max(
            0, self._deferred_qty.get(sku, 0) - unit.quantity)
        self._queued_sku_counts[sku] = self._queued_sku_counts.get(sku, 0) + 1
        self._queued_qty[sku] = self._queued_qty.get(sku, 0) + unit.quantity
        self._recv_seconds += dur
        self._queue(item)

    def drain_putaway(self, deadline: float | None = None,
                      charge_cut: bool = True) -> None:
        """Place the stock queue into bins (retries prior-batch stragglers too).

        `deadline` is the day's whistle on the put crews' batch-local clocks; what the
        whistle stops stays queued and is drained by the next batch, which is the put side
        of "work that does not complete rolls over to the next shift".

        PUBLIC, like `reclaim_emptied_bins` and for the same reason: a second work stream
        drives this phase without replaying the five above it.  The site put pool
        (`Inbound/putaway_pool.py`) calls it TWICE in one site day — once at this leaf's
        share of the day, once at the whole of it — which is what `charge_cut=False` is
        for: `cut` is a LEVEL, so each drain charging it would inflate it inside one batch
        where no downstream rule can undo it.  The pool charges once itself, through
        `count_put_cut`.  True is every other caller.
        """
        if self._stock_queue:
            self._stock(deadline=deadline, charge_cut=charge_cut)

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
        # THE POOL OWNS PHASE 5 WHEN THERE IS ONE, exactly as `mgr.receiving` owns phase 4
        # under a standing yard: `Warehouse -> Inbound` is forbidden in both directions, so
        # it arrives by INJECTION and is reached through an attribute.  Under one crew of
        # putters serving two channels this phase is not a leaf's to run alone — the day
        # has to be divided before either leaf spends it, and the shared clocks reset once
        # after both.  None is every uncoupled run, and then this line is what it was.
        if self.putaway_pool is None:
            self.drain_putaway(put_deadline)
        else:
            self.putaway_pool.drain(self, put_deadline)
        return triggered
