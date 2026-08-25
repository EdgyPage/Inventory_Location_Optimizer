import bisect
from collections import defaultdict, deque
from typing import Any

from Warehouse.catalog.Order import Order
from Warehouse.layout.Warehouse_Builder import Warehouse
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import (
    StorageUnit, Singleton, Pallet, FulfillmentBin,
    viable_storage_units, _max_qty_fits as _sq_max,
)
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.kernel.cost_model import sec_per_inch
from Warehouse.kernel.regime import FULFILLMENT, regime_of

# Shared leaf types/constants/helpers live in inventory_common (no import cycle).
# Re-exported here so `from Inventory_Management import Placement, BinKey, ...` is unchanged.
from Warehouse.inventory.inventory_common import (
    PutawayItem,
    AssignmentFn, RankedAssignmentFn, Placement, LoadParams, WarehousePlan,
    BinKey, binkey_of, is_forward_pick, _SIZE_RANKS, _SIZES_DESCENDING, tier_ranks_for,
    UNIT_CLASSES,
    _equilibrium_qty, _max_qty_fitting_size,
    _uniform_assignment, _wp_for, _SortedBins,
)
from Warehouse.inventory.inventory_planning import PlanningMixin
from Warehouse.inventory.inventory_optimal import OptimalLayoutMixin
from Warehouse.inventory.inventory_reorder import ReorderMixin


def _apportion(m: int, weights: list, n: int) -> list:
    """Allocate `m` aisles across `n` bands proportional to `weights`, with a floor of 1 per band
    when `m >= n` (so every band is reachable for the zone-filter spill).  When `m < n`, the first
    `m` (hottest) bands get 1 each and the rest 0 (spill covers the empties).  Largest-remainder."""
    if m <= 0:
        return [0] * n
    if m < n:
        return [1 if j < m else 0 for j in range(n)]
    extra = m - n                                   # after giving 1 per band
    wsum  = sum(weights) or 1
    raw   = [w / wsum * extra for w in weights]
    add   = [int(x) for x in raw]
    order = sorted(range(n), key=lambda j: -(raw[j] - int(raw[j])))
    for i in range(extra - sum(add)):
        add[order[i % n]] += 1
    return [1 + add[j] for j in range(n)]


def _ranked_by_score(taken: list, prefers_low: bool) -> list:
    """Attach each placement's 0-based rank within its group, best first.

    `prefers_low` comes from the pool, so rank 0 always means "the best choice available"
    rather than "the smallest number" -- three arms (tmax, rank_maxlabor, expn) optimise
    upward on purpose, and a consumer should not have to know which.

    Unscored placements (a policy with nothing to report for that unit, or a `None` bin)
    rank NULL rather than last: they were not worse, they were not measured.  Equal scores
    share the LOWEST rank, competition-style, so a tie never implies an ordering the policy
    did not make -- the tie was broken by candidate order, not by the objective.
    """
    scored = sorted((t[2] for t in taken if t[1] is not None and t[2] is not None),
                    reverse=not prefers_low)
    rank_of: dict = {}
    for i, sc in enumerate(scored):
        rank_of.setdefault(sc, i)
    return [(u, b, sc, (rank_of.get(sc) if (b is not None and sc is not None) else None))
            for u, b, sc in taken]


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

        # ── velocity zoning (ABC "like-with-like") — a composable candidate-layer toggle ──
        # When enabled, _candidates restricts a unit's viable bins to its VELOCITY BAND and
        # _stock_ranked sub-groups the wave by band, so every arm places within the band.
        # OFF (default) = identity pass-through ⇒ byte-identical (protects FIFO's random.choice).
        self._zoning_enabled: bool = False
        self._zoning_bands: int = 3
        self._sku_band: dict[int, int] = {}     # sku -> velocity band (0 = hottest)
        self._aisle_band: dict[int, int] = {}   # aisle_id -> geometry band (0 = shallowest/nearest)
        # Per-(tier BinKey, band) free-bin sub-index: the O(#bands) fast path _stock_per_unit uses
        # under zoning so FIFO's candidate fetch is O(#bands) instead of O(free bins in the tier).
        # Built in configure_zoning, maintained in _index_add/_index_remove; empty when zoning is off.
        self._band_index: dict[BinKey, list[list[Aisle.Bin]]] = {}
        self._band_pos: dict[int, int] = {}     # id(bin) -> position in its band bucket

        # Keyed by id(bin) for O(1) removal when bins are reclaimed.
        self._unavailable: dict[int, Aisle.Bin] = {}

        # Stock (restock) queue: pre-palletized StorageUnit objects ready for bin assignment.
        # Fed by initial intake (enqueue), evictions (requeue_bin), and arrived reorders
        # (released from the lead queue).  Placed into bins by _stock().
        self._stock_queue: deque[PutawayItem] = deque()
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
        # id(bin) -> the picker-local second it ran dry, for the bins in _pending_reclaim.
        # Keyed by id because a Bin is owned by the Warehouse for the whole run and is never
        # collected, so the key is stable — the property a StorageUnit does NOT have, which
        # is why put-away provenance rides in a PutawayItem instead of a map like this.
        # Cleared wholesale by _reclaim_empty_bins, which drains _pending_reclaim wholesale.
        self._emptied_at: dict[int, float] = {}

        # SKUs whose current quantity has dropped to or below the reorder threshold
        # since the last check_reorders call.  Maintained by _notify_pick so
        # check_reorders scans only depleted SKUs instead of all N_skus.
        self._depleted_skus: set[int] = set()

        # Churn counters (read + reset per batch via pop_churn): reload evictions
        # (Capacity_Reloader.requeue_bin) and reorder unit placements this batch
        # (bumped in _execute_placement).
        self._reload_moves: int       = 0
        self._reorder_placements: int = 0
        # Units ORDERED this batch (Σ reorder qty entering the lead queue in check_reorders);
        # reset + accumulated there each batch, read via the units_ordered property.
        self._units_ordered: int      = 0

        # Incremental Sigma f*D tracker — avoids a full occupied-bin scan per batch.
        # None until enable_sigma_fd() binds the freq map + speeds; then maintained
        # on every placement (+), pick-empty / eviction (−).
        self._sigma_freq: dict | None = None
        self._sigma_x: float = 0.0   # per-inch PACE (sec_per_inch of the ft/s x_speed), set by enable_sigma_fd
        self._sigma_y: float = 0.0   # per-inch PACE (sec_per_inch of the ft/s y_speed)
        self._sigma_fd: float = 0.0

        # Put-away timing.  None until enable_putaway_timing() binds a crew speed + cost
        # model; then every _execute_placement costs seconds and appends a record.
        # OFF by default so a test, a Diagnostics probe or any direct caller that has no
        # crew is unaffected -- and because put-away was a zero-duration phase for the whole
        # life of the project, so nothing downstream expects the field to exist.
        self._put_speed = None                       # SpeedProfile | None
        self._put_cost = None                        # PutawayCost | None
        self._put_clock: float = 0.0                 # the crew's FINISH = max(_put_clocks)
        self._put_clocks: list[float] = [0.0]        # one per worker; sized by the binder
        self._put_seconds: float = 0.0               # total put-away labor this run
        self._put_records: list = []                 # (t_start, dur, sku, qty, aisle, x, y, source)

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

        # Expected-volume twin (for the cart-swap-aware Rank_cartlabor selector):
        # _sku_vol_product[sku] = f * q * volume (raw expected picked volume mass);
        # _aisle_vol_sum[aid]   = Σ over the aisle's SKUs.  Compared to a per-cart
        # threshold to estimate expected cart swaps.  Maintained like the pick-load twin.
        self._aisle_vol_sum: dict[int, float]   = defaultdict(float)
        self._sku_vol_product: dict[int, float] = {}   # sku -> f * q * volume

        # SKU → bins split by unit type for Task.from_batch lookups.
        # _SortedBins keeps each SKU's bins PERMANENTLY in `location` order — the same
        # order `Task.from_batch` used to impose with two `sorted()` calls per batch SKU
        # (its deep-ladder t_task cost grew ~quadratically: batch-SKU count and per-SKU bin
        # multiplicity both scale with the catalogue).  Identity membership and
        # discard-absent-is-noop semantics match the sets these replaced; the maintenance
        # cost moves to the five add/discard sites (O(log k) insort on an immutable key).
        # Anything NEW that iterates these must not re-introduce an order dependency —
        # iteration order is location order, byte-identical to the old sorted() drains.
        self._sku_singleton_bins: dict[int, _SortedBins] = defaultdict(_SortedBins)
        self._sku_pallet_bins: dict[int, _SortedBins]    = defaultdict(_SortedBins)

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
            self._stock_queue.append(PutawayItem(unit, 'intake'))
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
                self._stock_queue.append(PutawayItem(unit, 'intake'))
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
                if is_forward_pick(bin_):
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
        # Each bin's _D uses ITS regime's travel speeds in a mixed warehouse (wp.by_regime);
        # a single regime collapses to one (x_pace, y_pace) for every bin — byte-identical.
        by_regime = getattr(wp, 'by_regime', None)
        if by_regime:
            paces = {r: (sec_per_inch(w.x_speed), sec_per_inch(w.y_speed))
                     for r, w in by_regime.items()}
            default_pace = (sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed))
            for b in self.warehouse.bins:
                x_pace, y_pace = paces.get(regime_of(b), default_pace)
                b._D = x_pace * b.x_phys + y_pace * b.y_phys
        else:
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
            c.sku: c.demand.relative_frequency * c.demand.quantity_rate
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

            # Expected picked-volume mass (f * q * volume) per SKU and per aisle, seeded
            # from the current placement — read by the Rank_cartlabor cart-swap term.
            self._sku_vol_product = {
                c.sku: c.demand.relative_frequency * c.demand.quantity_rate * c.volume()
                for c in inventory.orders
            }
            self._aisle_vol_sum.clear()
            for aid, sku_set in self._aisle_sku_sets.items():
                self._aisle_vol_sum[aid] = sum(
                    self._sku_vol_product.get(s, 0.0) for s in sku_set
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
    def units_ordered(self) -> int:
        """Units ORDERED in the most recent check_reorders() batch (Σ reorder qty).  Distinct from
        reorder_placements (units PLACED) and in_transit_qty (units still on order)."""
        return getattr(self, '_units_ordered', 0)

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
        return binkey_of(bin_)

    def _index_add(self, bin_: Aisle.Bin) -> None:
        key = self._key(bin_)
        lst = self._index[key]
        self._bin_index_pos[id(bin_)] = len(lst)
        lst.append(bin_)
        if self._zoning_enabled:
            # Mirror the add into the per-band sub-index (see _band_index / _band_pick).
            bucket = self._band_index[key][self._aisle_band.get(bin_.location[0], 0)]
            self._band_pos[id(bin_)] = len(bucket)
            bucket.append(bin_)
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
        if self._zoning_enabled:
            # Same swap-remove on the per-band sub-index; the moved element shares the band bucket.
            bucket = self._band_index[key][self._aisle_band.get(bin_.location[0], 0)]
            bpos   = self._band_pos.pop(id(bin_))
            blast  = bucket[-1]
            bucket[bpos] = blast
            bucket.pop()
            if blast is not bin_:
                self._band_pos[id(blast)] = bpos
        if self._travel_costs_ready:
            aisle_lst = self._aisle_index[key][bin_.location[0]]
            i = bisect.bisect_left(aisle_lst, bin_._D, key=lambda b: b._D)
            while i < len(aisle_lst) and aisle_lst[i] is not bin_:
                i += 1
            if i < len(aisle_lst):
                del aisle_lst[i]

    # ── velocity zoning setup ────────────────────────────────────────────────

    def configure_zoning(self, enabled: bool, n_bands: int = 3, orders: Any = None,
                         *, mode: str = 'equal', abc: dict | None = None) -> None:
        """Enable/disable velocity zoning and precompute the band maps (once, before stocking).

        mode='equal' (default): SKUs and aisles sliced into n_bands EQUAL-COUNT groups (hottest /
        shallowest = band 0).  mode='abc': SKUs banded by cumulative demand-MASS thresholds
        (abc['mass_thresholds'], manual A/B/C), and aisles allocated per BinKey PROPORTIONAL to each
        band's SKU footprint — so the hot A-band is a SMALL aisle fraction and the cold C-band is
        many rarely-visited aisles (batches skip them).  Disabled ⇒ maps cleared (byte-identical).
        """
        self._zoning_enabled = bool(enabled)
        self._zoning_bands = max(1, int(n_bands))
        self._sku_band = {}
        self._aisle_band = {}
        self._band_index = {}      # rebuilt below when enabled; empty (unused) when off
        self._band_pos = {}
        if not self._zoning_enabled:
            return
        n = self._zoning_bands
        if mode == 'abc' and orders is not None:
            self._build_abc_bands(orders, n, abc)
            self._build_band_index()
            return
        # ── equal-count (default; byte-identical) ─────────────────────────────
        # SKU velocity band (global; hottest = band 0).
        if orders is not None:
            vel = {c.sku: (c.demand.relative_frequency * c.demand.quantity_rate) for c in orders}
            skus = sorted(vel, key=lambda s: -vel[s])
            m = len(skus)
            for i, s in enumerate(skus):
                self._sku_band[s] = min(n - 1, i * n // m) if m else 0
        # Aisle geometry band per BinKey (shallowest/nearest = band 0).
        by_key: dict = defaultdict(list)
        for aisle in self.warehouse.aisles:
            if not aisle.bins:
                continue
            by_key[binkey_of(aisle.bins[0])].append(aisle)
        for _key, aisles in by_key.items():
            aisles.sort(key=lambda a: (getattr(a, 'aisle_width', 0), a.aisle_id))
            m = len(aisles)
            for i, a in enumerate(aisles):
                self._aisle_band[a.aisle_id] = min(n - 1, i * n // m) if m else 0
        self._build_band_index()

    def _build_band_index(self) -> None:
        """Partition each free-bin tier list into per-band buckets, once, after the band maps are
        set.  Anchors the O(#bands) `_band_pick` fast path; maintained incrementally thereafter by
        `_index_add`/`_index_remove`.  Re-derives `_band_pos` from scratch, so it is correct
        regardless of whether bins were registered before or after zoning was enabled."""
        n = self._zoning_bands
        self._band_index = defaultdict(lambda: [[] for _ in range(n)])
        self._band_pos = {}
        for key, lst in self._index.items():
            buckets = self._band_index[key]
            for b in lst:
                band = self._aisle_band.get(b.location[0], 0)
                self._band_pos[id(b)] = len(buckets[band])
                buckets[band].append(b)

    def _build_abc_bands(self, orders: Any, n: int, abc: dict | None) -> None:
        """Manual A/B/C bands: SKUs cut at cumulative demand-mass thresholds; aisles allocated per
        BinKey by each band's SKU footprint (few hot SKUs ⇒ few hot aisles)."""
        cuts = list((abc or {}).get('mass_thresholds') or [])
        # SKU band = # of mass thresholds the cumulative (hottest-first) mass has passed.
        vel = {c.sku: (c.demand.relative_frequency * c.demand.quantity_rate) for c in orders}
        total = sum(vel.values()) or 1.0
        cum = 0.0
        for s in sorted(vel, key=lambda k: -vel[k]):
            cum += vel[s] / total
            self._sku_band[s] = min(n - 1, bisect.bisect_left(cuts, cum))
        # Per-band footprint = global SKU count (hot band has few SKUs → few aisles).
        band_counts: dict = {}
        for band in self._sku_band.values():
            band_counts[band] = band_counts.get(band, 0) + 1
        weights = [band_counts.get(j, 0) for j in range(n)]
        # Aisle allocation per BinKey (shallowest = band 0), floor of 1 per band when m ≥ n so the
        # _zone_filter spill contract holds; when m < n some bands are empty and spill covers them.
        by_key: dict = defaultdict(list)
        for aisle in self.warehouse.aisles:
            if not aisle.bins:
                continue
            by_key[binkey_of(aisle.bins[0])].append(aisle)
        for _key, aisles in by_key.items():
            aisles.sort(key=lambda a: (getattr(a, 'aisle_width', 0), a.aisle_id))
            counts = _apportion(len(aisles), weights, n)
            idx = 0
            for band, cnt in enumerate(counts):
                for _ in range(cnt):
                    self._aisle_band[aisles[idx].aisle_id] = band
                    idx += 1

    def _band_of_unit(self, unit: StorageUnit) -> int:
        return self._sku_band.get(unit.order.sku, 0)

    def _spill_bands(self, target: int):
        """Band visit order for zoning spill: the unit's band, then COLDER (downgrade to the next
        hotness), then HOTTER (upgrade) — the single source shared by _zone_filter and _band_pick."""
        yield from range(target, self._zoning_bands)      # colder-first
        yield from range(target - 1, -1, -1)              # then hotter

    def _zone_filter(self, unit: StorageUnit, bins: list) -> list:
        """Restrict *bins* to the unit's velocity band, spilling COLDER-first then HOTTER-fallback:
        a hot item whose band is full drops to the next available (colder) aisle; a cold item whose
        band is full UPGRADES toward hotter aisles until it is placed.  Never returns empty.

        The O(len(bins)) partition path used by the ranked wave (once per wave) and the direct unit
        tests; the per-unit path uses the O(#bands) _band_pick over the maintained sub-index."""
        target = self._band_of_unit(unit)
        by_band: dict = defaultdict(list)
        for b in bins:
            by_band[self._aisle_band.get(b.location[0], 0)].append(b)
        for band in self._spill_bands(target):
            if by_band.get(band):
                return by_band[band]
        return bins

    def _band_pick(self, unit: StorageUnit, key) -> list:
        """O(#bands) equivalent of _zone_filter via the maintained per-band sub-index: the first
        non-empty band bucket in spill order.  `key` is the tier BinKey from _candidates_raw; the
        caller guarantees the tier is non-empty, so some bucket is non-empty (the fallback to the
        raw tier list is unreachable defensive cover)."""
        buckets = self._band_index[key]
        for band in self._spill_bands(self._band_of_unit(unit)):
            if buckets[band]:
                return buckets[band]
        return self._index.get(key, [])

    def _group_key(self, unit: StorageUnit):
        """Ranked-wave grouping key.  With zoning, sub-group by (BinKey, band) so each sub-wave
        is a single band (the once-per-wave candidate fetch is then band-correct); OFF ⇒ BinKey
        only (byte-identical)."""
        if self._zoning_enabled:
            return (binkey_of(unit), self._band_of_unit(unit))
        return binkey_of(unit)

    # ── placement ───────────────────────────────────────────────────────────

    def _candidates(self, unit: StorageUnit) -> list[Aisle.Bin]:
        """Viable bins for *unit* (smallest fitting tier), optionally restricted to the unit's
        velocity band when zoning is on.  OFF ⇒ returns the raw list object unchanged.  Used by the
        ranked wave (once per wave); the per-unit path uses _band_pick directly for O(#bands)."""
        _key, bins = self._candidates_raw(unit)
        if self._zoning_enabled and bins:
            return self._zone_filter(unit, bins)
        return bins

    def _candidates_raw(self, unit: StorageUnit) -> tuple:
        """Return ``(tier_key, bins)``: the SMALLEST fitting tier's BinKey and its free-bin list
        (the live ``self._index`` object), or ``(None, [])`` when no fitting tier has a free bin.
        The key lets the per-unit path index the per-band sub-index without re-deriving the tier.

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
        unit_type = unit.unit_category                    # 'pallet' | 'singleton' | 'fulfillment'
        if unit_type == 'singleton':
            key  = binkey_of(unit)                         # (h, c, 'singleton', 'singleton')
            bins = self._index.get(key)
            return (key, bins) if bins else (None, [])
        # Pallet and fulfillment are both size-tiered: return the smallest non-empty tier
        # >= the unit's own tier, spilling up.  tier_ranks_for() selects the pallet vs
        # fulfillment tier table so one code path serves both bin families.
        ranks, sizes_desc = tier_ranks_for(unit_type)
        min_rank  = ranks.get(unit.storage_size, 0) if unit.storage_size else 0
        # Ascending tier order (smallest → largest): smallest fitting tier first.
        for size in reversed(sizes_desc):
            if ranks[size] >= min_rank:
                key  = (shc.handling, shc.category, size, unit_type)
                bins = self._index.get(key)
                if bins:
                    return (key, bins)
        return (None, [])

    def _execute_placement(self, unit: StorageUnit, bin_: Aisle.Bin,
                           *, source: str | None = None,
                           score: float | None = None,
                           score_rank: int | None = None,
                           policy: str | None = None) -> None:
        """Commit one unit→bin placement and update all manager state dicts.

        `score` / `score_rank` / `policy` are what the assignment policy chose on, when
        there was a policy that computed anything.  Like `source`, the manager does not act
        on them; they pass through to `BinRecorder`, because this is the one call every
        put-away funnels through and there is nowhere later to recover them from -- the
        pools return the number as they decide, and nothing recomputes it.

        `source` is the `PutawayItem` origin the drain popped this unit from
        (`intake` / `reorder` / `reslot`), passed through untouched: the manager does not
        act on it, but this is the one call every put-away funnels through, so it is the
        only place an observer can learn where a placement came from.  Optional and
        ignored here, so a direct caller that does not know about the queue — a test, a
        future inbound writer — is unaffected.
        """
        # Invariant guard: a unit never lands in a bin of a different regime.  This holds
        # structurally today (bins are drawn by the unit's own BinKey), but asserting it here
        # turns the "store and fulfillment items never intersect each other's bins" contract
        # into a fail-fast check — cheap insurance for the mixed-catalog / channel work.
        if regime_of(bin_) != regime_of(unit):
            raise AssertionError(
                f'cross-regime placement: {regime_of(unit)} unit (sku={unit.order.sku}) '
                f'into {regime_of(bin_)} bin {getattr(bin_, "location", None)}')
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
        if is_forward_pick(unit):
            self._sku_singleton_bins[sku].add(bin_)
        else:
            self._sku_pallet_bins[sku].add(bin_)
        if self._affinity is not None:
            aid    = bin_.location[0]
            counts = self._aisle_sku_counts[aid]
            counts[sku] = counts.get(sku, 0) + 1
        self._reorder_placements += 1
        if self._sigma_freq is not None:
            self._sigma_fd += self._sigma_delta(sku, bin_)
        # Costs the put; changes nothing about it.  See enable_putaway_timing.
        if self._put_speed is not None:
            self._cost_putaway(unit, bin_, source)

    def enable_putaway_timing(self, speed, cost=None, size: int = 1) -> None:
        """Bind a put crew's travel speed + cost model, so every placement costs seconds.

        Follows `enable_sigma_fd`'s precedent: a binder the harness calls, so the domain
        never reaches into CONFIG for it.  `speed` is a `cost_model.SpeedProfile` (a put
        crew's own, which is why a Mode exists); `cost` defaults to `PutawayCost()`.

        ADDITIVE BY CONSTRUCTION.  Put-away time does not move the pick clock, does not
        contend for an aisle, and does not change which unit lands in which bin -- the same
        items are picked, in the same order, at the same instants.  It records a duration
        and a row.  Contention is the inbound feature, not this seam.
        """
        from Warehouse.operations.putaway import PutawayCost
        if size < 1:
            raise ValueError(f'a put crew of {size} does no work; size must be >= 1')
        self._put_speed = speed
        self._put_cost = cost if cost is not None else PutawayCost()
        # ONE CLOCK PER WORKER.  A single serial clock made a crew of N take exactly as
        # long as a crew of one, while `work_events.put_rows` round-robined the records
        # across N workers -- so the rows claimed N people were working and the instants
        # said otherwise.
        self._put_clocks = [0.0] * size

    @property
    def putaway_seconds(self) -> float:
        """Total put-away labor recorded so far, in seconds.  0.0 when timing is off."""
        return self._put_seconds

    def drain_putaway_records(self) -> list:
        """Hand over this batch's put-away records, and start the crew's clock over.

        A DRAIN IS A BATCH BOUNDARY.  The records carry `t_start` on the crew's own clock
        measured from the start of the batch, because the consumer
        (`Optimization.metrics.work_events.put_rows`) offsets them onto the arm's absolute
        axis by adding the batch epoch -- and the batch epoch is not known until after the
        batch's picks have been simulated, so the manager cannot stamp absolute times
        itself.

        Resetting here is therefore load-bearing, not tidiness.  Without it `_put_clock`
        accumulates across the whole arm while `put_rows` still adds the epoch, so every
        put row after batch 0 is stamped too late by the total put-away seconds of every
        preceding batch, and the error grows without bound: measured on an eight-batch
        store arm, batch 7's puts landed at `t_local` 53,769-64,152 s against a 17,906 s
        batch, and batch 6's puts overran batch 7's picks so the merged view interleaved
        batches.

        Drained rather than read so the caller takes ownership once per batch and the
        manager never holds a run's worth of rows.
        """
        recs, self._put_records = self._put_records, []
        self._put_clocks = [0.0] * len(self._put_clocks)
        self._put_clock = 0.0
        return recs

    def _cost_putaway(self, unit: StorageUnit, bin_: Aisle.Bin, source) -> None:
        """Charge one placement to the put crew's clock and record it.

        Called from `_execute_placement` only, which the bin-mutation allowlist already
        names as the single put-away commit point -- so this adds no new bin writer.
        """
        from Warehouse.operations.putaway import put_cost
        order = unit.order
        dur = put_cost(bin_.x_phys, bin_.y_phys, order.weight, order.volume(),
                       unit.quantity, self._put_speed, self._put_cost)
        # Greedy list scheduling: the next put goes to whoever is free earliest.  Ties break
        # to the lowest worker index, so a crew of one is exactly the old serial clock.
        w = min(range(len(self._put_clocks)), key=lambda i: (self._put_clocks[i], i))
        t0 = self._put_clocks[w]
        self._put_clocks[w] = t0 + dur
        self._put_clock = max(self._put_clocks)   # the crew's finish, for the caller
        self._put_seconds += dur
        self._put_records.append(
            (t0, dur, order.sku, unit.quantity, bin_.location[0],
             bin_.x_phys, bin_.y_phys, source or 'intake', w))

    def _sigma_delta(self, sku: int, bin_: Aisle.Bin) -> float:
        """f_s · D(bin) increment for the incremental Σ f·D tracker.

        The ONE shared expression behind every += / -= on _sigma_fd (placement adds,
        eviction/depletion subtracts) — kept as a method so the formula can't drift
        between the three sites.  Callers guard on ``self._sigma_freq is not None``.
        """
        return (self._sigma_freq.get(sku, 0.0)
                * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))

    def _stock(self, budget: int | None = None) -> None:
        """Dispatch the queued wave to the placement policy: a ranked wave if the
        policy carries a ``place_wave``, otherwise the per-unit path.  Single entry
        used by enqueue/enqueue_all (initial stock) and check_reorders (reorders).

        ``budget`` caps how many units may be PLACED in this call; whatever is left stays
        queued for the next one, which is the deferral the queue already does for a unit
        no bin can hold.  ``None`` (the default, and every caller today) means place
        everything, exactly as before.

        A budget is what a finite crew and a finite number of dock doors impose: the
        parameter exists so that constraint has somewhere to go without the drain being
        rewritten around it.

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
            self._stock_ranked(budget)
        else:
            self._stock_per_unit(budget)

    def _stock_per_unit(self, budget: int | None = None) -> None:
        """Place queued StorageUnit objects one at a time via placement.place_one.

        Used for initial enqueue, FIFO/cohesion reorders, and the stragglers a ranked
        wave leaves behind.  The coupling guard runs in _stock() (the single entry).

        Placement failures:
          1. Repack into a smaller pallet size tier (retried immediately via appendleft).
          2. Fall back to singleton bins of the same order type (same).
          3. If no bin is available, the unit stays in the queue (FIFO, no expiry).

        ``budget`` caps PLACEMENTS, not pops: a repack splits one unit into several and
        pushes them back, and charging a budget for that would make the cap depend on how
        badly the warehouse is packed rather than on how much the crew can move.
        """
        pending: deque[PutawayItem] = deque()
        placed = 0
        while self._stock_queue:
            if budget is not None and placed >= budget:
                # Budget spent.  Everything still queued waits for the next call — the same
                # deferral a unit gets when no bin fits it, so nothing new can be dropped.
                pending.extend(self._stock_queue)
                self._stock_queue.clear()
                break
            item   = self._stock_queue.popleft()
            unit   = item.unit
            order = unit.order
            sku    = order.sku

            # B/C: aisle_index is active — assign derives BinKey from unit directly.
            # A: uniform assignment needs a real candidates list.  Under zoning, take the O(#bands)
            # _band_pick fast path (same in-band candidate SET as _zone_filter, off the maintained
            # sub-index) instead of the O(free-bins) partition — this per-unit path is what made
            # zoned FIFO O(placements x free-bins).  OFF ⇒ unchanged (_candidates) ⇒ byte-identical.
            if self._travel_costs_ready:
                candidates = None
            elif self._zoning_enabled:
                key, bins  = self._candidates_raw(unit)
                candidates = self._band_pick(unit, key) if bins else bins
            else:
                candidates = self._candidates(unit)
            bin_       = self.placement.place_one(unit, candidates)

            if bin_ is not None:
                self._execute_placement(unit, bin_, source=item.source)
                placed += 1
            else:
                # No bin fits this unit.  Attempt rescues in priority order:
                #   1. Repack into smaller pallet size tier (existing logic).
                #   2. Fall back to singleton bins of the same order type.
                #   3. If all else fails, the unit goes to a local `pending` deque
                #      that becomes the new _stock_queue, and it retries next batch.
                #      There is NO expiry: a unit no bin can ever hold retries forever,
                #      and `mgr.queue_depth` is the only signal it is happening.  (An
                #      earlier version of this comment described a `_MAX_DRAIN_RETRIES`
                #      abandonment cap; no such constant has ever existed in the repo.)
                repacked = False
                shc = order.storage_handle_config

                # ── rescue 1: repack into a smaller size tier (pallet OR fulfillment) ──
                # Both are size-tiered; tier_ranks_for() + the unit class select the family.
                if unit.unit_category in UNIT_CLASSES and unit.storage_size is not None:
                    utype        = unit.unit_category
                    unit_cls, ranks, sizes_desc = UNIT_CLASSES[utype]
                    current_rank = ranks.get(unit.storage_size, 99)
                    for size in sizes_desc:
                        if ranks[size] >= current_rank:
                            continue   # same or larger tier — already failed
                        avail = self._index.get(
                            (shc.handling, shc.category, size, utype))
                        if not avail:
                            continue
                        max_q = _max_qty_fitting_size(order, size, utype)
                        if max_q <= 0:
                            continue
                        remaining  = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_q)
                            new_units.append(unit_cls(order, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._stock_queue.appendleft(item.respawn(u))
                        repacked = True
                        break

                # ── rescue 2: singleton bins of same order type (store only; a
                #    fulfillment unit has no singleton fallback — it stays ff) ──
                if not repacked and unit.unit_category != FULFILLMENT:
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
                            self._stock_queue.appendleft(item.respawn(u))
                        repacked = True

                # ── no bin available — hold in queue, retry next batch ────────
                if not repacked:
                    pending.append(item)
        self._stock_queue = pending


    def _serve_order(self, pool, units: list) -> list:
        """WHO is served first, out of one BinKey group.  The drain's decision.

        This method is the whole point of Phase 1.  Until the pool inversion, an assignment
        function returned `[(unit, bin)] in priority order` and the drain iterated it, so
        every ranked policy was choosing the ORDER as well as the bin -- 14 of the 17
        restock rules, none of them asked to.  Put-away is supposed to be FIFO with a little
        tolerance; instead the queue was freely re-sorted by whatever score maximised the
        assignment function.

        The pool split that in two.  `pool.order(units)` is now a REQUEST: the policy states
        the precedence it would like and this method decides what to grant.  Today it grants
        all of it, which is why everything is still byte-identical -- the seam is real but
        not yet load-bearing.  A finite K-oldest window lands here, and only here.

        `units` arrives in queue order (the group's insertion order into `_stock_queue`), so
        the FIFO answer is already in hand and needs no extra bookkeeping to recover.
        """
        return pool.order(units)

    def _stock_ranked(self, budget: int | None = None) -> None:
        """Ranked placement: sort units by pick-effort priority, then drain.

        Groups the queue by BinKey (handling, category, storage_size, unit_type)
        — the same key used by _candidates() — so units only compete with others
        in the same bin pool.  Within each group, placement.place_wave returns
        (unit, bin|None) pairs sorted by pick-effort priority so high-effort
        items claim the best (lowest-D) bins before lower-priority items.

        Units the wave cannot place are handed to the per-unit path
        (_stock_per_unit), which re-fetches candidates per unit so they spill into
        other tiers / remaining bins exactly as FIFO does (see below).

        ``budget`` is spent per GROUP here, not per unit, and that is not a simplification.
        `place_wave` scores a whole group at once and mutates the manager's aisle running
        balances as it decides, so each unit's bin depends on where the earlier units in
        the same wave went.  Truncating a wave mid-way would place units under a balance
        that assumed the rest landed too.  So the check happens BEFORE the wave is called:
        a group that cannot be afforded is requeued untouched, having never influenced an
        aisle balance.  A wave is the atom.
        """
        if not self._stock_queue:
            return

        # Snapshot queue and group by BinKey (or (BinKey, velocity band) when zoning is on, so
        # each sub-wave is a single band and the once-per-wave candidate fetch is band-correct).
        groups: dict[tuple, list[PutawayItem]] = defaultdict(list)
        while self._stock_queue:
            item = self._stock_queue.popleft()
            groups[self._group_key(item.unit)].append(item)

        # With zoning ON, process band sub-groups HOTTEST-FIRST (band 0 before 1 …) so hot items
        # claim their aisles before colder items can upgrade-spill into them (priority: place hot
        # first).  Group key is then (BinKey, band); OFF ⇒ insertion order (byte-identical).
        group_items = (sorted(groups.items(), key=lambda kv: kv[0][1])
                       if self._zoning_enabled else groups.items())
        placed = 0
        for _key, items in group_items:
            if budget is not None and placed >= budget:
                # Cannot afford this wave: requeue it whole, WITHOUT scoring it, so no
                # aisle balance moves for units that are not going to be placed.
                self._stock_queue.extend(items)
                continue
            # `place_wave` takes and returns bare units, so the envelope is re-attached by
            # object identity.  Safe HERE and nowhere else: every unit in `by_unit` is alive
            # for the whole call, so an id cannot be recycled underneath the lookup — which
            # is exactly the property `BinRecorder`'s cross-call `id()` set could not rely on.
            units   = [it.unit for it in items]
            by_unit = {id(it.unit): it for it in items}
            if self.placement.is_pooled:
                # POOLED: the DRAIN owns the order, the pool owns the choice.  One snapshot
                # per group, exactly as the wave took -- the candidate set never depended on
                # the order, because every unit in a group shares a BinKey.
                pool = self.placement.open_pool(self._candidates(units[0]), units[0])
                taken = []
                for unit in self._serve_order(pool, units):
                    bin_, score = pool.take(unit)
                    taken.append((unit, bin_, score))
                assignments = _ranked_by_score(taken, pool.prefers_low)
            else:
                # LEGACY.  No shipped restock rule reaches here any more: 14 are pooled and
                # the other 3 (fifo, cmax, cmin) have no group path at all.  Kept because
                # `place_wave` is still how the frozen oracles are driven in the equivalence
                # suites, and deleting it would delete the thing the ports are checked
                # against.  A wave has no score to report -- it returns bins, and there is
                # no "score this bin for this unit" step to call afterwards, which is the
                # whole reason a pool returns one.
                assignments = [(u, b, None, None) for u, b in
                               self.placement.place_wave(units, self._candidates)]  # type: ignore[misc]
            for unit, bin_, score, rank in assignments:
                if bin_ is not None:
                    self._execute_placement(unit, bin_,
                                            source=by_unit[id(unit)].source,
                                            score=score, score_rank=rank,
                                            policy=self.placement.name)
                    placed += 1
                else:
                    # The wave couldn't place this unit: place_wave takes a single
                    # candidate snapshot for the whole wave (one tier, fetched once),
                    # so once that snapshot is consumed it sheds the surplus even when
                    # bins remain free.  Hand leftovers to the per-unit path, which
                    # re-fetches _candidates PER unit — using all currently-free bins
                    # in the tier and spilling up to larger tiers — plus the
                    # smaller-tier/singleton rescues.  This is the same path that keeps
                    # FIFO's queue at zero; without it the ranked queue grows unbounded.
                    self._stock_queue.append(by_unit[id(unit)])

        if self._stock_queue:
            self._stock_per_unit(None if budget is None else max(0, budget - placed))


