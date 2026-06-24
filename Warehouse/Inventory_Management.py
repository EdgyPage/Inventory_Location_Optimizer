import bisect
from collections import defaultdict, deque
from typing import Any

from Order import Order
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, viable_storage_units, _max_qty_fits as _sq_max
from Affinity_Store import AffinityStore
from cost_model import sec_per_inch

# Shared leaf types/constants/helpers live in inventory_common (no import cycle).
# Re-exported here so `from Inventory_Management import Placement, BinKey, ...` is unchanged.
from inventory_common import (
    AssignmentFn, RankedAssignmentFn, Placement, LoadParams, WarehousePlan,
    BinKey, _SIZE_RANKS, _SIZES_DESCENDING,
    _equilibrium_qty, _max_qty_fitting_pallet_size, _uniform_assignment,
)
from inventory_planning import PlanningMixin
from inventory_optimal import OptimalLayoutMixin
from inventory_reorder import ReorderMixin


class Inventory_Manager(PlanningMixin, OptimalLayoutMixin, ReorderMixin):


    def __init__(
        self,
        warehouse: Warehouse,
        assignment_fn: AssignmentFn = _uniform_assignment,
        affinity: AffinityStore | None = None,
    ) -> None:
        self.warehouse: Warehouse = warehouse
        self.name: str = ''        # e.g. inventory_initial_assignment_reslot; for graph titles
        # The single placement policy.  Defaults to per-unit uniform; a strategy's
        # build() swaps in its own (FIFO/cohesion = per-unit; trip/rank = ranked wave
        # + per-unit straggler fallback).  _stock() dispatches on placement.is_ranked.
        self.placement: Placement = Placement('uniform_fifo', assignment_fn)
        self._affinity: AffinityStore | None = affinity
        self._index: dict[BinKey, list[Aisle.Bin]] = defaultdict(list)
        # id(bin) → position in its _index tier list — O(1) swap-remove support.
        self._bin_index_pos: dict[int, int] = {}
        # Per-aisle sorted secondary index: BinKey -> {aisle_id -> list[Bin] sorted by _D}.
        # Populated by init_travel_costs(); maintained by _index_add/_index_remove thereafter.
        self._aisle_index: dict[BinKey, dict[int, list[Aisle.Bin]]] = defaultdict(lambda: defaultdict(list))
        self._travel_costs_ready: bool = False

        # Keyed by id(bin) for O(1) removal when bins are reclaimed.
        self._unavailable: dict[int, Aisle.Bin] = {}

        # Stock (restock) queue: pre-palletized StorageUnit objects ready for bin assignment.
        # Fed by initial intake (enqueue), evictions (requeue_bin), and arrived reorders
        # (released from the lead queue).  Placed into bins by _stock().
        self._stock_queue: deque[StorageUnit] = deque()
        # Count of queued units per SKU — O(1) alternative to rebuilding a set
        # from the full queue on every check_reorders call.
        self._queued_sku_counts: dict[int, int] = {}
        # Product-quantity on-order trackers (parallel to the unit-count dicts):
        # _queued_qty   = items reordered and queued (in the stock queue) but not yet binned,
        # _deferred_qty = items reordered and in-transit in the LEAD queue (lead time not elapsed).
        # Reorder thresholds use inventory position = on_hand + queued + deferred
        # so a SKU already reordered (but unbinned / in-transit) is not reordered again.
        self._queued_qty: dict[int, int]   = {}
        self._deferred_qty: dict[int, int] = {}
        self._originals: dict[int, Order] = {}
        # equilibrium_qty at initial intake per SKU (not updated on reorders).
        self._initial_quantities: dict[int, int] = {}

        # Incremental inventory count — avoids O(N_bins) scan in check_reorders.
        self._current_quantities: dict[int, int] = {}

        # Lead queue (Order-Up-To with deterministic lead times): every reorder enters here as a
        # (sku, qty, remaining_lead) record — even lead 0.  check_reorders decrements remaining_lead
        # by 1 each batch and hands arrivals (remaining_lead <= 0) to the stock queue.
        self._batch_num: int = 0
        self._lead_queue: list[list] = []   # each entry: [sku, qty, remaining_lead]
        # Seed for the reorder-quantity noise.  check_reorders draws qty from a per-reorder
        # random.Random((_seed, sku, _batch_num)) so the quantity is a pure function of the
        # seed (reproducible, off the global stream) rather than global call order.  The
        # runner sets this to seed_world; default 0 keeps standalone managers deterministic.
        self._seed: int = 0

        # Bins emptied by picks, pending return to _index at next check_reorders.
        self._pending_reclaim: list[Aisle.Bin] = []

        # SKUs whose current quantity has dropped to or below the reorder threshold
        # since the last check_reorders call.  Maintained by _notify_pick so
        # check_reorders scans only depleted SKUs instead of all N_skus.
        self._depleted_skus: set[int] = set()

        # Churn counters (read + reset per batch via pop_churn): reload evictions
        # (Capacity_Reloader.requeue_bin) and reorder unit placements this batch
        # (bumped in _execute_placement).
        self._reload_moves: int       = 0
        self._reorder_placements: int = 0

        # Incremental Sigma f*D tracker — avoids a full occupied-bin scan per batch.
        # None until enable_sigma_fd() binds the freq map + speeds; then maintained
        # on every placement (+), pick-empty / eviction (−).
        self._sigma_freq: dict | None = None
        self._sigma_x: float = 0.0   # per-inch PACE (sec_per_inch of the ft/s x_speed), set by enable_sigma_fd
        self._sigma_y: float = 0.0   # per-inch PACE (sec_per_inch of the ft/s y_speed)
        self._sigma_fd: float = 0.0

        # Optimal-map basis (populated by build_optimal_map):
        #   _bin_pref[id(bin)] = quantity-free preferred score of a bin (D + M*v_ref) — a
        #     stable location basis over ALL bins, independent of pick quantity.
        #   _map_target[sku]   = the pref of that SKU's labor-optimal bin (from the exact
        #     full-labor assignment) — the score a reorder of that SKU should match.
        self._bin_pref: dict[int, float] = {}
        self._map_target: dict[int, float] = {}

        # Persistent lift state shared with load-aware assignment functions.
        self._aisle_sku_sets: dict[int, set[int]]         = defaultdict(set)
        self._aisle_lift_sum: dict[int, float]             = defaultdict(float)
        self._aisle_sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        # Pre-translated matrix indices mirror of _aisle_sku_sets — eliminates
        # the O(N_aisle_members) dict lookup set-comprehension in delta_lift_idxs.
        self._aisle_idx_sets: dict[int, set[int]]         = defaultdict(set)
        # Per-aisle placed-member COLUMN positions: aisle → {sku_idx → [x_phys, ...]},
        # one x per LIVE bin.  Pruned on reclaim/eviction right beside _aisle_idx_sets,
        # so it holds only SKUs currently in the aisle (no stale positions, no unbounded
        # growth, and the partner-centroid scan is O(distinct SKUs in aisle)).
        # Lets co-demand compaction/expansion + the labor minimiser score a candidate
        # bin by its column distance to an entering SKU's already-placed affinity partners.
        self._aisle_member_pos: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        # id(bin) → sku; needed for lift removal after storage is cleared.
        self._bin_sku: dict[int, int] = {}

        # Demand-based state for trip-cost assignment functions.
        # Populated by init_demand_state(); unused for strategy A.
        self._aisle_demand_sum: dict[int, float]   = defaultdict(float)
        self._sku_demand_product: dict[int, float] = {}   # sku -> f * q

        # Cost-weighted twin of the demand state: expected picking labor.
        # _sku_pick_load_product[sku] = f * q * cost1 (= order.expected_labor);
        # _aisle_pick_load_sum[aid]   = Σ over the aisle's SKUs.  Maintained in lockstep
        # with the demand_sum state; read by the Rank_labor aisle-balance selector.
        self._aisle_pick_load_sum: dict[int, float]   = defaultdict(float)
        self._sku_pick_load_product: dict[int, float] = {}   # sku -> f * q * cost1

        # SKU → bins split by unit type for Task.from_batch lookups.
        # Sets give O(1) add/discard; Task.from_batch sorts the bins by
        # (bayX, bayY) anyway so insertion order doesn't matter.
        self._sku_singleton_bins: dict[int, set[Aisle.Bin]] = defaultdict(set)
        self._sku_pallet_bins: dict[int, set[Aisle.Bin]]    = defaultdict(set)

        for b in warehouse.bins:
            if b.storage is None:
                self._index_add(b)
            else:
                self._unavailable[id(b)] = b

    # ── public API ──────────────────────────────────────────────────────────

    def enqueue(self, order: Order, quantity: int | None = None) -> 'Inventory_Manager':
        """Queue one order for bin placement.

        quantity=None (default) reads equilibrium_qty from the order — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override the order's own stock level (e.g. overstock sampling).
        """
        qty = quantity if quantity is not None else _equilibrium_qty(order)
        for unit in viable_storage_units(order, qty):
            self._stock_queue.append(unit)
        # Count intake units as on-order so a reorder fired before they all reach
        # a bin does not over-order (they decrement back as they place).
        self._queued_qty[order.sku] = self._queued_qty.get(order.sku, 0) + qty
        if order.sku not in self._originals and not getattr(order, '_is_reorder', False):
            self._originals[order.sku] = order
            self._initial_quantities[order.sku] = qty
        self._stock()
        return self

    def enqueue_all(self, orders: list[Order], quantity: int | None = None) -> 'Inventory_Manager':
        """Queue a list of orders for bin placement.

        quantity=None (default) reads equilibrium_qty from each order — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override every order's stock level (e.g. overstock sampling).
        """
        for order in orders:
            qty = quantity if quantity is not None else _equilibrium_qty(order)
            for unit in viable_storage_units(order, qty):
                self._stock_queue.append(unit)
            # Count intake units as on-order so a reorder fired before they all
            # reach a bin does not over-order (decremented back as they place).
            self._queued_qty[order.sku] = self._queued_qty.get(order.sku, 0) + qty
            if order.sku not in self._originals and not getattr(order, '_is_reorder', False):
                self._originals[order.sku] = order
                self._initial_quantities[order.sku] = qty
        self._stock()
        return self

    def init_lift_state(self, affinity: AffinityStore) -> None:
        """Populate aisle lift state from current warehouse contents.

        Call after uniform stocking, before swapping to a load-aware
        assignment_fn.  Ensures reorder decisions see the actual aisle
        composition rather than starting from zero.  Also rebuilds
        _current_quantities so the incremental counter is consistent with
        the actual bin contents after any bulk stocking operation.
        """
        self._aisle_sku_sets.clear()
        self._aisle_lift_sum.clear()
        self._aisle_sku_counts.clear()
        self._aisle_idx_sets.clear()
        self._aisle_member_pos.clear()
        self._bin_sku.clear()
        self._current_quantities.clear()
        self._sku_singleton_bins.clear()
        self._sku_pallet_bins.clear()

        sku_to_idx = affinity._sku_to_idx

        for bin_ in self._unavailable.values():
            if bin_.storage is not None:
                sku = bin_.storage.order.sku
                aid = bin_.location[0]
                qty = bin_.storage.quantity
                self._aisle_sku_sets[aid].add(sku)
                counts = self._aisle_sku_counts[aid]
                counts[sku] = counts.get(sku, 0) + 1
                self._bin_sku[id(bin_)] = sku
                self._current_quantities[sku] = (
                    self._current_quantities.get(sku, 0) + qty
                )
                idx = sku_to_idx.get(sku)
                if idx is not None:
                    self._aisle_idx_sets[aid].add(idx)
                    self._aisle_member_pos[aid][idx].append(bin_.x_phys)
                if bin_.unit_type == 'singleton':
                    self._sku_singleton_bins[sku].add(bin_)
                else:
                    self._sku_pallet_bins[sku].add(bin_)

        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_lift_sum[aid] = affinity.sum_lift(list(sku_set))

    def init_travel_costs(self, wp: Any) -> None:
        """Precompute _D on every bin and build the per-aisle sorted secondary index.

        Must be called after init_lift_state() and before swapping to a
        load-aware assignment_fn built with build_load_*_assignment_fn(...,
        aisle_index=self._aisle_index).  After this call, _index_add and
        _index_remove maintain _aisle_index incrementally.
        """
        x_pace = sec_per_inch(wp.x_speed)   # ft/s -> s/inch (positions are inches)
        y_pace = sec_per_inch(wp.y_speed)
        for b in self.warehouse.bins:
            b._D = x_pace * b.x_phys + y_pace * b.y_phys
        self._aisle_index.clear()
        for key, bins in self._index.items():
            by_aisle = self._aisle_index[key]
            for b in bins:
                bisect.insort(by_aisle[b.location[0]], b, key=lambda x: x._D)
        self._travel_costs_ready = True

    def init_demand_state(self, inventory: Any, wp: Any = None) -> None:
        """Populate demand-product lookup and per-aisle demand sums.

        Must be called after init_lift_state() so _aisle_sku_sets already
        reflects the actual placement.  Call once per strategy worker before
        swapping to a trip-cost assignment function.

        When *wp* is given, also build the cost-weighted labor twin
        (_sku_pick_load_product = f*q*cost1 = order.expected_labor, and the
        per-aisle _aisle_pick_load_sum) used by the Rank_labor balance selector.
        """
        self._sku_demand_product = {
            c.sku: c.demand.frequency * c.demand.quantity_rate
            for c in inventory.orders
        }
        self._aisle_demand_sum.clear()
        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_demand_sum[aid] = sum(
                self._sku_demand_product.get(s, 0.0) for s in sku_set
            )

        if wp is not None:
            # order.expected_labor reads labor_cost, which the worker sets via
            # compute_labor_cost() before this call.
            self._sku_pick_load_product = {
                c.sku: c.expected_labor for c in inventory.orders
            }
            self._aisle_pick_load_sum.clear()
            for aid, sku_set in self._aisle_sku_sets.items():
                self._aisle_pick_load_sum[aid] = sum(
                    self._sku_pick_load_product.get(s, 0.0) for s in sku_set
                )

    @property
    def available(self) -> list[Aisle.Bin]:
        return [b for bins in self._index.values() for b in bins]

    @property
    def unavailable(self) -> list[Aisle.Bin]:
        return list(self._unavailable.values())

    @property
    def queue_depth(self) -> int:
        return len(self._stock_queue)

    @property
    def lead_queue_depth(self) -> int:
        """Number of in-transit reorders waiting out their lead time (lead queue length)."""
        return len(self._lead_queue)

    @property
    def in_transit_qty(self) -> int:
        """Total units currently in transit (sum of lead-queue order quantities)."""
        return sum(entry[1] for entry in self._lead_queue)

    @property
    def assigned_bins(self) -> list[Aisle.Bin]:
        return list(self._unavailable.values())

    @property
    def empty_bins(self) -> list[Aisle.Bin]:
        return self.available

    def summary(self) -> None:
        total: int     = len(self.warehouse.bins)
        filled: int    = len(self._unavailable)
        singles: int   = sum(1 for b in self._unavailable.values() if b.storage is not None and b.storage.unit_category == 'singleton')
        pallets: int   = sum(1 for b in self._unavailable.values() if b.storage is not None and b.storage.unit_category == 'pallet')
        available: int = sum(len(v) for v in self._index.values())
        print(f'Total bins  : {total}')
        print(f'Filled      : {filled}  ({singles} singletons, {pallets} pallets)')
        print(f'Empty       : {available}')
        print(f'Queued      : {self.queue_depth} items pending')

    # ── index maintenance ────────────────────────────────────────────────────

    def _key(self, bin_: Aisle.Bin) -> BinKey:
        return (bin_.handling_type, bin_.storage_type, bin_.storage_size, bin_.unit_type)

    def _index_add(self, bin_: Aisle.Bin) -> None:
        key = self._key(bin_)
        lst = self._index[key]
        self._bin_index_pos[id(bin_)] = len(lst)
        lst.append(bin_)
        if self._travel_costs_ready:
            aisle_lst = self._aisle_index[key][bin_.location[0]]
            bisect.insort(aisle_lst, bin_, key=lambda b: b._D)

    def _index_remove(self, bin_: Aisle.Bin) -> None:
        """O(1) removal via swap-remove: move last element into the vacated slot."""
        key  = self._key(bin_)
        lst  = self._index[key]
        pos  = self._bin_index_pos.pop(id(bin_))
        last = lst[-1]
        lst[pos] = last
        lst.pop()
        if last is not bin_:
            self._bin_index_pos[id(last)] = pos
        if self._travel_costs_ready:
            aisle_lst = self._aisle_index[key][bin_.location[0]]
            i = bisect.bisect_left(aisle_lst, bin_._D, key=lambda b: b._D)
            while i < len(aisle_lst) and aisle_lst[i] is not bin_:
                i += 1
            if i < len(aisle_lst):
                del aisle_lst[i]

    # ── placement ───────────────────────────────────────────────────────────

    def _candidates(self, unit: StorageUnit) -> list[Aisle.Bin]:
        """Return available bins for *unit*, scoped to the SMALLEST fitting tier.

        A pallet of size S fits in a bin of size S or larger.  We return the
        smallest non-empty tier ≥ S (the unit's own tier first), spilling UP to
        larger tiers only when the exact tier is full.  This is both physically
        sensible (don't waste an extra_large bin on a small pallet) and keeps
        per-tier demand mapped to per-tier capacity, which is how the warehouse
        is sized — preventing small units from starving large-tier bins.

        Returning a single tier keeps the candidate list small (one index
        bucket) regardless of warehouse size.
        """
        shc       = unit.order.storage_handle_config
        unit_type = unit.unit_category                    # 'pallet' or 'singleton'
        if unit_type == 'singleton':
            bins = self._index.get((shc.handling, shc.category, 'singleton', 'singleton'))
            return bins or []
        min_rank  = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
        # Ascending tier order (small → extra_large): smallest fitting tier first.
        for size in reversed(_SIZES_DESCENDING):
            if _SIZE_RANKS[size] >= min_rank:
                bins = self._index.get((shc.handling, shc.category, size, unit_type))
                if bins:
                    return bins
        return []

    def _execute_placement(self, unit: StorageUnit, bin_: Aisle.Bin) -> None:
        """Commit one unit→bin placement and update all manager state dicts."""
        sku = unit.order.sku
        n = self._queued_sku_counts.get(sku, 0)
        if n <= 1:
            self._queued_sku_counts.pop(sku, None)
        else:
            self._queued_sku_counts[sku] = n - 1
        # Unit moves from on-order (queued) to on-hand (binned).  max-0 keeps
        # initial-intake placements (never queued-counted) harmless.
        if sku in self._queued_qty:
            rem = self._queued_qty[sku] - unit.quantity
            if rem > 0:
                self._queued_qty[sku] = rem
            else:
                self._queued_qty.pop(sku, None)
        bin_.storage = unit
        self._index_remove(bin_)
        self._unavailable[id(bin_)] = bin_
        self._bin_sku[id(bin_)] = sku
        self._current_quantities[sku] = (
            self._current_quantities.get(sku, 0) + unit.quantity
        )
        if isinstance(unit, Singleton):
            self._sku_singleton_bins[sku].add(bin_)
        else:
            self._sku_pallet_bins[sku].add(bin_)
        if self._affinity is not None:
            aid    = bin_.location[0]
            counts = self._aisle_sku_counts[aid]
            counts[sku] = counts.get(sku, 0) + 1
        self._reorder_placements += 1
        if self._sigma_freq is not None:
            self._sigma_fd += (self._sigma_freq.get(sku, 0.0)
                               * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))

    def _stock(self) -> None:
        """Dispatch the queued wave to the placement policy: a ranked wave if the
        policy carries a ``place_wave``, otherwise the per-unit path.  Single entry
        used by enqueue/enqueue_all (initial stock) and check_reorders (reorders).

        Runs the coupling guard first — even on an empty queue — so an armed/fn
        mismatch fails loudly before any placement: when travel costs are armed,
        candidates is passed as None and place_one MUST read mgr._aisle_index instead;
        a mismatch (index armed but fn scans, or vice-versa) silently returns None for
        every placement.
        """
        fast = self._travel_costs_ready
        if fast != self.placement.uses_aisle_index:
            raise RuntimeError(
                f'Assignment divergence: _travel_costs_ready={fast} but '
                f'placement.uses_aisle_index={self.placement.uses_aisle_index} '
                f'(policy {self.placement.name!r}).  init_travel_costs() and an '
                'index-consuming placement must be armed together or not at all.')
        if not self._stock_queue:
            return
        if self.placement.is_ranked:
            self._stock_ranked()
        else:
            self._stock_per_unit()

    def _stock_per_unit(self) -> None:
        """Place queued StorageUnit objects one at a time via placement.place_one.

        Used for initial enqueue, FIFO/cohesion reorders, and the stragglers a ranked
        wave leaves behind.  The coupling guard runs in _stock() (the single entry).

        Placement failures:
          1. Repack into a smaller pallet size tier (retried immediately via appendleft).
          2. Fall back to singleton bins of the same order type (same).
          3. If no bin is available, the unit stays in the queue (FIFO, no expiry).
        """
        pending: deque[StorageUnit] = deque()
        while self._stock_queue:
            unit   = self._stock_queue.popleft()
            order = unit.order
            sku    = order.sku

            # B/C: aisle_index is active — assign derives BinKey from unit directly.
            # A: uniform assignment needs a real candidates list.
            candidates = (None if self._travel_costs_ready
                          else self._candidates(unit))
            bin_       = self.placement.place_one(unit, candidates)

            if bin_ is not None:
                self._execute_placement(unit, bin_)
            else:
                # No bin fits this unit.  Attempt rescues in priority order:
                #   1. Repack into smaller pallet size tier (existing logic).
                #   2. Fall back to singleton bins of the same order type.
                #   3. If all else fails, track consecutive failures; after
                #      _MAX_DRAIN_RETRIES the unit is abandoned and the queued-
                #      count is decremented so a fresh reorder can fire next batch.
                repacked = False
                shc = order.storage_handle_config

                # ── rescue 1: smaller pallet tier ────────────────────────────
                if unit.unit_category == 'pallet' and unit.storage_size is not None:
                    current_rank = _SIZE_RANKS.get(unit.storage_size, 99)
                    for size in _SIZES_DESCENDING:
                        if _SIZE_RANKS[size] >= current_rank:
                            continue   # same or larger tier — already failed
                        avail = self._index.get(
                            (shc.handling, shc.category, size, 'pallet'))
                        if not avail:
                            continue
                        max_q = _max_qty_fitting_pallet_size(order, size)
                        if max_q <= 0:
                            continue
                        remaining  = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_q)
                            new_units.append(Pallet(order, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._stock_queue.appendleft(u)
                        repacked = True
                        break

                # ── rescue 2: singleton bins of same order type ──────────────
                if not repacked:
                    max_sing = _sq_max(order, Singleton)
                    avail = self._index.get((shc.handling, shc.category, None, 'singleton'))
                    if max_sing > 0 and avail:
                        remaining = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_sing)
                            new_units.append(Singleton(order, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._stock_queue.appendleft(u)
                        repacked = True

                # ── no bin available — hold in queue, retry next batch ────────
                if not repacked:
                    pending.append(unit)
        self._stock_queue = pending


    def _stock_ranked(self) -> None:
        """Ranked placement: sort units by pick-effort priority, then drain.

        Groups the queue by BinKey (handling, category, storage_size, unit_type)
        — the same key used by _candidates() — so units only compete with others
        in the same bin pool.  Within each group, placement.place_wave returns
        (unit, bin|None) pairs sorted by pick-effort priority so high-effort
        items claim the best (lowest-D) bins before lower-priority items.

        Units the wave cannot place are handed to the per-unit path
        (_stock_per_unit), which re-fetches candidates per unit so they spill into
        other tiers / remaining bins exactly as FIFO does (see below).
        """
        if not self._stock_queue:
            return

        # Snapshot queue and group by BinKey
        groups: dict[tuple, list[StorageUnit]] = defaultdict(list)
        while self._stock_queue:
            unit = self._stock_queue.popleft()
            shc  = unit.order.storage_handle_config
            key  = (shc.handling, shc.category, unit.storage_size, unit.unit_category)
            groups[key].append(unit)

        for _key, units in groups.items():
            # Ranked assignments — high pick-effort units claim the best bins first.
            assignments = self.placement.place_wave(units, self._candidates)   # type: ignore[misc]
            for unit, bin_ in assignments:
                if bin_ is not None:
                    self._execute_placement(unit, bin_)
                else:
                    # The wave couldn't place this unit: place_wave takes a single
                    # candidate snapshot for the whole wave (one tier, fetched once),
                    # so once that snapshot is consumed it sheds the surplus even when
                    # bins remain free.  Hand leftovers to the per-unit path, which
                    # re-fetches _candidates PER unit — using all currently-free bins
                    # in the tier and spilling up to larger tiers — plus the
                    # smaller-tier/singleton rescues.  This is the same path that keeps
                    # FIFO's queue at zero; without it the ranked queue grows unbounded.
                    self._stock_queue.append(unit)

        if self._stock_queue:
            self._stock_per_unit()


