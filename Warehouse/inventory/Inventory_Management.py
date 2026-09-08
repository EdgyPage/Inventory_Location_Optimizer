import bisect
import logging
import heapq
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

# Matches inventory_optimal.py's `log`; the refill cap in _stock is the only site, and a
# NameError there would fire ONLY in the pathological case it exists to report.
log = logging.getLogger(__name__)

# Shared leaf types/constants/helpers live in inventory_common (no import cycle).
# Re-exported here so `from Inventory_Management import Placement, BinKey, ...` is unchanged.
from Warehouse.inventory.put_policy import key_for as _put_key_for
from Warehouse.inventory.put_queue import (
    HeldItems, PutQueueSet, single_queue, store_and_fulfillment)
from Warehouse.inventory.inventory_common import (
    PutawayItem,
    AssignmentFn, RankedAssignmentFn, Placement, LoadParams, WarehousePlan,
    BinKey, binkey_of, is_forward_pick, _SIZE_RANKS, _SIZES_DESCENDING, tier_ranks_for,
    UNIT_CLASSES,
    _equilibrium_qty, _max_qty_fitting_size, own_bin_room,
    _uniform_assignment, _wp_for, _SortedBins,
)
from Warehouse.inventory.inventory_planning import PlanningMixin
from Warehouse.inventory.inventory_optimal import OptimalLayoutMixin
from Warehouse.inventory.inventory_reorder import ReorderMixin
from Warehouse.inventory.inventory_reorder import BatchTransit as _BatchTransit


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


class _MultiQueueView:
    """A read-only, age-ordered view across split put-away streams.

    `_stock_queue` was a single deque that three external consumers iterate and measure
    (`Diagnostics/bucket_fill.py`, the runner's queue-depth ledger, and the conservation
    check).  Once the streams split there is no one deque to return, and returning a merged
    COPY would let a caller mutate it and lose the write in silence.  This is iterable and
    sizeable and nothing else, so the mutation fails at the attribute rather than at the
    consequence.

    Merged by arrival age, so a consumer asking "what is waiting, oldest first" gets the
    same answer it always did rather than one queue's contents followed by another's.
    """

    __slots__ = ('_qs',)

    def __init__(self, queues):
        self._qs = queues

    def __iter__(self):
        return iter(sorted((it for q in self._qs for it in q.items),
                           key=lambda it: it.age))

    def __len__(self):
        return self._qs.depth

    def __bool__(self):
        return self._qs.depth > 0

    def __repr__(self):
        return f'<put-away: {self._qs.depth} waiting across {len(self._qs)} queues>'


#: Backstop on the admit/drain refill loop in `_stock`.  Not a modelling parameter: the loop
#: already terminates when a pass places nothing, so reaching this means something pathological
#: (a staging limit far below the arrival rate).  It logs loudly rather than spinning, because
#: a hung worker in a 272-arm sweep is the most expensive failure mode there is.
_MAX_REFILL_PASSES = 10_000

#: Default put-away tolerance: unbounded, i.e. the assignment policy's requested order is
#: granted in full.  This is the pre-Phase-2 behaviour and stays the default so that turning
#: the window on is always an explicit act.  See Inventory_Manager._serve_order.
DEFAULT_PUTAWAY_WINDOW: int | None = None


def _windowed(units: list, keys: list, k: int) -> list:
    """Serve `units` best-first, but only ever choosing from the K oldest still waiting.

    Ranked ONCE from a stable descending sort, then windowed on the rank.  The rank detour
    is not indirection for its own sake: the first version negated each key for a min-heap,
    which silently required every key to be a number, and `sku_batched` needs a two-level
    key.  Ranking also puts the tie rule in one place -- `sorted` is stable, so units the
    key cannot separate keep their arrival order, which is what a FIFO queue should do
    anyway.

    Keys are computed once by the caller and reused across every window an item appears in,
    which is what keeps this O(n log n) rather than O(n*k) key evaluations.
    """
    n = len(units)
    rank = [0] * n
    for pos, i in enumerate(sorted(range(n), key=lambda j: keys[j], reverse=True)):
        rank[i] = pos
    heap = [(rank[i], i) for i in range(k)]
    heapq.heapify(heap)
    nxt = k
    out = []
    while heap:
        _r, i = heapq.heappop(heap)
        out.append(units[i])
        if nxt < n:                          # the window slides by exactly one placement
            heapq.heappush(heap, (rank[nxt], nxt))
            nxt += 1
    return out


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
        # How far past the head of the put-away queue the policy is allowed to reach.
        # None = the whole queue, which is what every ranked policy silently assumed before
        # the pool inversion.  See _serve_order.
        self.putaway_window: int | None = DEFAULT_PUTAWAY_WINDOW
        # Monotonic arrival stamp for put-away items. Never reset: it orders the queue
        # across batches, and restarting it would make a fresh arrival look older than
        # something that has been waiting since batch 0.
        self._putaway_seq: int = 0
        # The put-away queues.  Default: ONE queue that takes everything with the policy's
        # full ordering freedom -- byte-identically the manager as it behaved before queues
        # existed.  Swap in `store_and_fulfillment()` (or any PutQueueSet) to split the
        # streams; see Warehouse/inventory/put_queue.py.
        self._put_queues: PutQueueSet = single_queue()
        # Placements made so far in this _stock() call, so the queue loop can charge the
        # shared budget without each drain having to return a count through paths that
        # already have three exits.
        self._placed_this_call: int = 0
        # Items a full queue refused. They are stamped and waiting, just not on the floor
        # yet -- a trailer still loaded, a reorder still on the dock. Retried oldest-first
        # at the top of every drain. See _admit.
        #: Refused items, partitioned by the queue that refused them -- see `HeldItems`.
        #: A plain deque here is what made the retry quadratic.
        self._held: HeldItems = HeldItems()
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
        # _queued_qty   = items reordered and ADMITTED UPSTREAM OF A BIN but not yet binned --
        #                 standing on the dock or waiting in a put queue.  Both, deliberately:
        #                 the credit is added by `_release_to_stock` after its admit loop and
        #                 removed by `_execute_placement` when the unit reaches a bin, so it
        #                 spans everything in between and a unit the receiving crew has not
        #                 got to yet is still on order.  That is what lets the dock intercept
        #                 inside `_admit` without touching this ledger at all.
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
        # THE ORDER PORT'S TRANSIT.  Owns lead-queue TIMING ([sku, qty, remaining_lead]
        # entries, one batch per tick by default); the manager keeps only the scalar
        # deferred ledger.  The trailer pipeline binds its own transit here flag-on —
        # injection, never import, like `packer` below.
        self.transit = _BatchTransit()
        # The arm's absolute epoch as of the current check_reorders call — the trailer
        # transit's clock; None outside a run (bare test managers), which fails safe:
        # a positive lead simply has not arrived yet.
        self._now_s: float | None = None
        # THE INBOUND SEAM.  `inbound_split(sku, qty) -> list[int] | None` decides whether an
        # arrival comes in as one delivery or several; packing is per delivery, so a shipment
        # that would palletize whole can land as singletons when a trailer splits it.  None =
        # every arrival comes whole, which is every run today.  Trailers, docks and load
        # planning live in the CALLER -- this only asks how the shipment showed up.
        self.inbound_split = None
        # THE PACKER SEAM, the receiving half of the broker.  A callable
        # `(order, qty, deliveries) -> [plan]` bound by the driver (`Inbound.pack.packer`);
        # None = the mixin's own `_pack_plain`, which packs the identical unit stream and
        # records no LoadPlans.  Injection, never import: `Warehouse/ -> Inbound/` is a
        # forbidden edge in both directions.
        self.packer = None
        # THE RECEIVING DOCK.  None = no receiving crew, which is every run that does not ask
        # for one: nothing is constructed, so the no-op is structural rather than a flag test.
        # Bound by `enable_receiving`, the `enable_putaway_timing` precedent -- a binder the
        # harness calls, so the domain never reaches into CONFIG for it.
        self._dock = None
        # THE SPACE TIMELINE, the standing yard's space instrument (`Inbound/space.py`).
        # None = never constructed, which is every run without INBOUND_STANDING_YARD: the
        # three hooks that feed it (reclaim-harvest, the fill in _execute_placement, the
        # ctx-freeze in _receive_standing) are `is None` tests on this one attribute, so
        # flag-off byte-identity is by construction.  Attached by the driver
        # (`SpaceTimeline.attach(mgr)`, the BinRecorder rebind precedent): injection,
        # never import.
        self.space_timeline = None
        #: Receiving labour, in seconds. Deliberately NOT folded into `_put_seconds`: that
        #: figure has been published, and widening what it counts would move it silently.
        #: Repack rework (ADR-0003) DOES land here -- it is receiving work, done by the
        #: receiving crew, and hiding it in its own total is how it would stop being noticed.
        self._recv_seconds: float = 0.0
        #: ADR-0003's three per-batch flows, drained by `snapshot_putaway_rework`.
        #: `_put_topups` counts top-ups that landed in a bin ALREADY holding the SKU -- the
        #: own-bin rung firing at all means the free index was dry for that unit.
        #: `_recv_repacks` counts rescue ACTS, `_recv_repacked_packs` the packs they
        #: produced; the two differ whenever one act splits a unit several ways, and the
        #: ratio is the only thing that says how badly.  Counted even with no dock bound,
        #: so a dockless run still reports its rework instead of silently having none.
        self._put_topups: int = 0
        self._recv_repacks: int = 0
        self._recv_repacked_packs: int = 0
        #: One `(yard_start, free_doors_start, yard_end, staged_remainder_end)` per STANDING
        #: drain — the `yard_drains` row, appended by `_receive_standing` and drained per
        #: batch.  Empty on every run without the standing yard, which is what makes the
        #: table have zero rows there rather than a batch's worth of honest-looking zeros:
        #: a v1 or dockless run has no yard, and "the yard was empty" is a different claim
        #: from "there was no yard".
        self._yard_drains: list = []
        # Seed for the reorder-quantity noise.  check_reorders draws qty from a per-reorder
        # random.Random((_seed, sku, _batch_num)) so the quantity is a pure function of the
        # seed (reproducible, off the global stream) rather than global call order.  The
        # runner sets this to seed_world; default 0 keeps standalone managers deterministic.
        self._seed: int = 0

        # Bins emptied by picks, pending return to _index at next check_reorders.
        self._pending_reclaim: list[Aisle.Bin] = []
        # id(bin) -> the ABSOLUTE second it ran dry (the pick sim's carried clock), for
        # the bins in _pending_reclaim.
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
        self._put_clock: float = 0.0                 # slowest stream's FINISH across queues
        self._put_size: int = 1                      # default crew size; per-queue crews win
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
            self._admit(unit, 'intake')
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
                self._admit(unit, 'intake')
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
        """Units waiting for a bin, INCLUDING those a full queue is holding upstream.

        Held items are waiting just as much as queued ones -- the only difference is that
        the floor has no room for them yet.  Reporting only the queued half would make a
        staging limit look like the backlog had vanished, which is the opposite of what a
        backpressure model is for.
        """
        return len(self._stock_queue) + len(self._held)

    @property
    def held_depth(self) -> int:
        """Units refused floor space and waiting upstream. 0 unless a queue sets `staging`."""
        return len(self._held)

    @property
    def lead_queue_depth(self) -> int:
        """In-flight transit ENTRIES: lead-queue records flag-off, trailers flag-on.

        Through the transit's own census, NOT the `_lead_queue` shim below: the shim
        exposes `BatchTransit`'s entry list and a trailer transit has no such list — the
        first e2e run with the trailer flag on found exactly that AttributeError here.
        For `BatchTransit`, `depth` IS `len(entries)`, so flag-off reads are unchanged.
        """
        return self.transit.depth

    @property
    def in_transit_qty(self) -> int:
        """Total pieces on order and not yet released — the transit census's level.
        For `BatchTransit` this is the historical sum of entry quantities, unchanged."""
        return self.transit.merchandise()

    # ── the transit shim ──────────────────────────────────────────────────────────
    @property
    def _lead_queue(self) -> list:
        """The transit's entries, by their historical name.

        A PROPERTY so the many readers (and the tests that build a bare manager and assign
        directly) survive the transit split unchanged: the entries live on `self.transit`,
        timing methods live there too, and this name is a window, not a second copy.
        """
        return self.transit._entries

    @_lead_queue.setter
    def _lead_queue(self, entries) -> None:
        if getattr(self, 'transit', None) is None:      # __new__-built test managers
            self.transit = _BatchTransit()
        self.transit._entries = list(entries)

    def transit_snapshot(self) -> list:
        """(sku, qty, remaining_lead) tuples — the public read the replay viewer uses
        instead of reaching for a private attribute."""
        return self.transit.snapshot()

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
        # The space timeline's FILL event: version bump + clear-stamp expiry for the bin
        # just occupied.  Observer only -- reads nothing back, changes no placement.
        if self.space_timeline is not None:
            self.space_timeline.fill(bin_)
        # Costs the put; changes nothing about it.  See enable_putaway_timing.
        if self._put_speed is not None:
            self._cost_putaway(unit, bin_, source)

    # ── the own-bin rung (ADR-0003) ───────────────────────────────────────────────────
    def _top_up_own_bins(self, unit: StorageUnit, source: str | None = None,
                         queue=None) -> int:
        """Add `unit`'s items to bins ALREADY HOLDING ITS SKU, fullest first.  Returns how
        many items landed (0 when the SKU holds no bin with room).

        THE SECOND RUNG of the put-away chain (ADR-0003): tried only after `place_one` has
        failed to find an empty bin, and ahead of the repack/singleton rescues and
        `pending`.  Empty-first is what keeps the new-bin decision — the moment an
        assignment arm actually optimises anything — happening on every top-up it can; this
        rung is what stops the run falling off a cliff when the free index is dry.

        THE NO-OP CONDITION IS PER-BUCKET, NOT GLOBAL, and the ADR states it loosely.  This
        rung fires exactly when `place_one` returns None, which is a free index empty FOR THE
        UNIT'S OWN BinKey — a run with plenty of free bins elsewhere can still reach it.  So
        "a no-op in any run whose free index never exhausts" is true only read per bucket;
        the checkable claim is "a no-op in any run where every unit finds an empty bin".  The
        same correction applies to the rescues, which this rung now pre-empts whenever the
        SKU has room — that was a reachable path before, so an arm that fires a rescue is NOT
        byte-identical to HEAD even with a deep index.

        FULLEST FIRST, ties by `location`.  Remnants merge into the bin that is already
        nearest full, which leaves the emptiest bin emptiest and lets `drain_sku`'s
        smallest-first pick take it to zero and hand it back to the free index.  The two
        rules are one mechanism and neither works alone.

        IT CROSSES THE FORWARD-PICK SPLIT.  Both `_sku_singleton_bins` and `_sku_pallet_bins`
        are drawn from, so a pallet unit's items can land in a forward-pick singleton bin and
        the reverse.  That is deliberate and safe -- `own_bin_room` measures against the BIN's
        own family, so nothing is asked to hold more than it can -- but it does mean the rung
        consolidates across a split that `_candidates_raw` never crosses.

        THE FIFTH BIN-MUTATION SITE.  Every other write to bin state replaces a whole
        `storage`; this one adds to an existing unit's `quantity`, so it updates the same
        manager dicts as `_execute_placement` MINUS everything that is about a bin becoming
        occupied:

          * `_bin_sku`, `_sku_*_bins`, `_unavailable`, `_index` — unchanged: the bin was
            already occupied by this SKU and already out of the free index.
          * `_aisle_sku_counts` — unchanged: the SKU gained no aisle it was not already in.
          * `_sigma_fd` — unchanged: Σ f·D counts a SKU's bin OCCUPANCY, and no bin changed
            hands.  Adding a delta here would charge the same bin twice.
          * `space_timeline.fill` — NOT called: it means "a free bin became occupied", and
            a bin that was already occupied is not news to the yard's space forecast.

        What DOES move is the merchandise: on-hand up, on-order down, and a put charged per
        BIN TOUCHED, because each is a separate trip for the putter.
        """
        order = unit.order
        sku   = order.sku
        remaining = unit.quantity
        placed    = 0
        # Fullest first; `location` breaks the tie so the choice is a pure function of
        # warehouse state, the same determinism contract `drain_sku` keeps.
        own = sorted(
            (b for b in (*self._sku_singleton_bins.get(sku, ()),
                         *self._sku_pallet_bins.get(sku, ()))
             if b.storage is not None),
            key=lambda b: (-b.storage.quantity, b.location))
        for bin_ in own:
            if remaining <= 0:
                break
            # Same invariant `_execute_placement` asserts, for the same reason: a SKU's own
            # bins are drawn from its own BinKey, so a cross-regime one is a real bug rather
            # than a case to handle.
            if regime_of(bin_) != regime_of(unit):
                raise AssertionError(
                    f'cross-regime top-up: {regime_of(unit)} unit (sku={sku}) into '
                    f'{regime_of(bin_)} bin {getattr(bin_, "location", None)}')
            room = own_bin_room(order, bin_)
            if room <= 0:
                continue
            n = min(remaining, room)
            self._execute_topup(order, bin_, n, source=source, queue=queue)
            remaining -= n
            placed    += n
        return placed

    def _execute_topup(self, order: Order, bin_: Aisle.Bin, n: int,
                       *, source: str | None = None, queue=None) -> None:
        """Commit ONE top-up of `n` items into `bin_`, which already holds `order`'s SKU.

        The own-bin twin of `_execute_placement`, and a separate method for the same reason
        that one exists: it is the single commit point an observer can wrap.  `BinRecorder`
        rebinds it to emit the `bin_placement` row carrying `bin_state='occupied'`, exactly
        as it rebinds `_execute_placement` for the `'empty'` rows -- so the spatial log stays
        complete without `_top_up_own_bins` knowing anything about recording.

        Which bins and how much is `_top_up_own_bins`' decision; this method only commits.
        """
        sku = order.sku
        # OWNERSHIP, asserted here rather than trusted.  `own_bin_room` measures room and
        # explicitly does not check whose merchandise is in the bin, and three observers now
        # rebind this method, so it is effectively a public seam: adding a SKU's items to
        # another SKU's unit would corrupt both without any downstream reader noticing.
        held = bin_.storage.order.sku
        if held != sku:
            raise AssertionError(
                f'top-up of sku {sku} into a bin holding sku {held} '
                f'{getattr(bin_, "location", None)}')
        bin_.storage.quantity += n            # THE MUTATION — allowlisted by name
        # RE-FIT, because this is the only mutation that raises a quantity.  A `StorageUnit`
        # caches `_height`/`_width`/`_length`/`_stack_axis` and `Pallet.storage_size` at
        # construction and nothing recomputes them; picks mutate quantity DOWNWARD, where a
        # stale-large cache is merely conservative.  Upward it lies in the unsafe direction:
        # a 42x12x46 order at qty 1 caches 'small', and at qty 4 is really 'extra_large'.
        # `requeue_bin` re-admits the SAME object, and `_candidates_raw` reads
        # `unit.storage_size` to pick the tier — so an evicted top-up would be offered small
        # bins and `_execute_placement` would put it in one with no fit check, building a
        # warehouse that cannot physically exist.  Cannot raise: `n <= own_bin_room`, which
        # caps on the BIN's own tier, so the new total is constructible in it.
        bin_.storage._fit(order)
        self._current_quantities[sku] = self._current_quantities.get(sku, 0) + n
        # On-order -> on-hand, mirroring `_execute_placement`'s max-0 guard so an
        # intake top-up (never queued-counted) stays harmless.
        if sku in self._queued_qty:
            rem = self._queued_qty[sku] - n
            if rem > 0:
                self._queued_qty[sku] = rem
            else:
                self._queued_qty.pop(sku, None)
        self._reorder_placements += 1
        # Counted HERE, per bin touched, not once per call: one top-up is one unit landing in
        # one occupied bin, which is exactly one `bin_placement` row and one trip.  Counting
        # per call would make the flow disagree with the rows it has to be read against
        # whenever a unit fills two own bins.
        self._put_topups += 1
        if self._put_speed is not None:
            # The putter carried exactly `n` items to this bin; price that, not the whole
            # unit it was cut from.
            self._cost_putaway(type(bin_.storage)(order, n), bin_, source, queue=queue)

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
        self._put_size = size
        self._put_cost = cost if cost is not None else PutawayCost()
        # ONE CLOCK PER WORKER, AND ONE SET OF WORKERS PER QUEUE.  A single serial clock
        # made a crew of N take exactly as long as a crew of one, while
        # `work_events.put_rows` round-robined the records across N workers -- so the rows
        # claimed N people were working and the instants said otherwise.  A single set of
        # clocks across QUEUES has the mirror problem: a forklift crew and a cart crew are
        # not the same people and do not queue behind each other.
        #
        # `speed`/`size` here are the DEFAULT, used by any queue whose spec names no crew
        # of its own -- so a manager with the single default queue is unchanged.
        self._bind_put_crews()

    def enable_receiving(self, dock) -> None:
        """Give the warehouse a receiving crew, and a dock for it to work.

        Follows `enable_putaway_timing`'s precedent: a binder the harness calls, so the domain
        never reaches into CONFIG for its own configuration.  There is deliberately no
        `disable_receiving` -- the off state is "this was never called", which is what makes
        the feature's no-op structural rather than a flag nobody can see.

        NOT ADDITIVE, unlike put-away timing, and that is the point.  A receiving crew changes
        WHEN merchandise reaches a put queue, so it changes which units are binned in which
        batch.  It does not change the pick simulation, the packing, or which bin a given unit
        lands in once it is offered.

        `dock` arrives CONSTRUCTED (`Inbound.dock.Dock`, already priced): the broker holds
        what it is handed and imports nothing -- `Warehouse/ -> Inbound/` is a forbidden
        edge, so construction is the driver's job, like every other injected policy.
        """
        self._dock = dock

    @property
    def dock_depth(self) -> int:
        """Storage units standing on the dock. 0 when there is no receiving crew.

        DISJOINT from `queue_depth`, which counts the put queues and `_held`: an item is in
        one place or the other, never both. A reader wanting the whole unbinned backlog sums
        them, which is why both are reported rather than one merged number.
        """
        return self._dock.depth if self._dock is not None else 0

    @property
    def receiving_seconds(self) -> float:
        """Total receiving labor recorded so far, in seconds. 0.0 when there is no crew."""
        return self._recv_seconds

    def drain_receiving_records(self) -> list:
        """This batch's unload records, and restart the crew's clock. `[]` when no crew.

        A drain is a batch boundary -- see `Dock.drain_records` and, for what happens when
        the reset is missed, `drain_putaway_records`.
        """
        return self._dock.drain_records() if self._dock is not None else []

    def receiving_snapshot(self) -> tuple:
        """`(depth, unloaded, cut, seconds)` for this batch, resetting the three flows.

        `(0, 0, 0, 0.0)` when there is no receiving crew, so the caller writes the same row
        shape either way and a no-dock run records four honest zeros rather than a NULL that
        every consumer then has to special-case.
        """
        return self._dock.snapshot() if self._dock is not None else (0, 0, 0, 0.0)

    # ── put-away rework (ADR-0003) ────────────────────────────────────────────────────
    def _charge_repack(self, order: Order, new_units: list) -> None:
        """Price one rescue as RECEIVING work: an unload-priced act per RESULTING pack.

        A rescue breaks a unit no bin could hold into several that fit.  Somebody does that,
        and it is dock work: the receiving crew already owns "take merchandise apart and
        make it storable", it is priced per pack by `unload_cost`, and there is no travel
        term because the merchandise does not go anywhere to be repacked.  So the rescue
        costs the dock's own per-pack price and NO new coefficient enters the model.

        Counted even when no dock is bound.  A dockless run can still repack, and the
        staffing record expects ZERO of them (provenance `assumed`); a rescue nobody counted
        is exactly the finding the equilibrium audit exists to make loud.
        """
        self._recv_repacks += 1
        self._recv_repacked_packs += len(new_units)
        dock = self._dock
        if dock is None:
            return
        for u in new_units:
            dur = dock.unload_seconds(order.weight, order.volume(), u.quantity)
            t0, w = dock.charge(dur)
            dock.repacks.append((t0, dur, order.sku, u.quantity, w))
            self._recv_seconds += dur

    def drain_repack_records(self) -> list:
        """This batch's repack records, and start the list over. `[]` when no crew.

        A SEPARATE STREAM from `drain_receiving_records`, not a widened one: `put_rows`
        refuses to mix event types in one call (the `role`/`event_type` split it exists to
        keep honest), so `repack` rows have to arrive as their own list or the writer would
        have to re-derive the type per row from a discriminator nothing declares.
        """
        return self._dock.drain_repacks() if self._dock is not None else []

    def snapshot_putaway_rework(self) -> tuple:
        """`(put_topups, recv_repacks, recv_repacked_packs)`, resetting all three.

        Per-batch FLOWS, so they reset -- the `cut`-is-a-level trap in reverse: these really
        are flows and summing them over batches is the right thing to do, which is only true
        because the drain happens exactly once per batch.
        """
        out = (self._put_topups, self._recv_repacks, self._recv_repacked_packs)
        self._put_topups = self._recv_repacks = self._recv_repacked_packs = 0
        return out

    def free_bin_depth(self) -> int:
        """How many bins are in the free index right now -- a LEVEL, read at batch end.

        ADR-0003's other half of the record: the own-bin rung fires because this ran to
        zero, so the share is only readable next to the depth that produced it.  Not reset,
        because a level is not a flow.
        """
        return sum(len(v) for v in self._index.values())

    # ── the yard's raw material (RAW STAMPS ONLY — every span derives at analysis) ──
    # Three accessors, one per row source, and none of them computes a span, a detention
    # day or an overage.  That altitude is the yard-metrics decision itself: the fee
    # threshold is a knob, and a run whose DB held pre-divided overage days could never be
    # re-reported under a different one without re-simulating.

    def drain_yard_drains(self) -> list:
        """This batch's per-drain yard levels, and start the list over.

        `[]` when the standing yard is not bound, which is not the same as a row of zeros —
        see the field's own note.  Drained per batch for `drain_putaway_records`' reason.
        """
        out, self._yard_drains = self._yard_drains, []
        return out

    def drain_yard_trailers(self) -> list:
        """This batch's FINISHED trailer stamps, and start the transit's list over.

        `[]` on any transit without the standing surfaces (`BatchTransit`, `TrailerTransit`)
        — probed the way `_receive` probes for `STANDING`, so neither is edited to satisfy
        a reader neither has anything to say to.
        """
        drain = getattr(self.transit, 'drain_stamps', None)
        return drain() if drain is not None else []

    def standing_yard_trailers(self) -> list:
        """Stamps for trailers STILL ON SITE — read at run end, and never drained.

        The censored tail.  A run that stops with trailers standing has held them for at
        least as long as it ran, and dropping those rows would report the adversarial arm's
        fee as clipped rather than concentrated.
        """
        standing = getattr(self.transit, 'standing_stamps', None)
        return standing() if standing is not None else []

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
        for q in self._put_queues:
            q.reset_clocks()
        self._put_clock = 0.0
        return recs

    def _cost_putaway(self, unit: StorageUnit, bin_: Aisle.Bin, source,
                      queue=None) -> None:
        """Charge one placement to ITS QUEUE's crew and record it.

        Called from `_execute_placement` and `_execute_topup` only -- the TWO put-away commit
        points the bin-mutation allowlist names (ADR-0003 added the second) -- so this adds no
        new bin writer. A top-up passes a unit built for the `n` items that actually moved,
        not the whole unit they were cut from, so the price is of the trip that happened.

        `queue` is the stream the unit came off.  None means the caller does not know,
        which happens on a direct `_execute_placement` from a test or a diagnostic; the
        unit is then routed the same way admission routed it, so the charge still lands on
        the crew that would really have done the work.
        """
        from Warehouse.operations.putaway import put_cost
        q = queue if queue is not None else self._put_queues.route(unit)
        order = unit.order
        dur = put_cost(bin_.x_phys, bin_.y_phys, order.weight, order.volume(),
                       unit.quantity, q.speed, q.cost)
        # THE CART.  A load is bounded by volume, and a unit that does not fit means the
        # putter has emptied this cart and goes back for another -- the same next-fit, the
        # same swap cost and the same meaning as a picker's, because in a store it is the
        # same cart.  Charged BEFORE the put, so the swap precedes the work it enables:
        # identical ordering to both picker loops, where advancing the clock first makes the
        # gap carry the swap seconds.
        if q.load(order.volume() * unit.quantity):
            dur += q.spec.swap_coef
        t0, w = q.charge(dur)
        # The put side's finish across EVERY stream: the crews work in parallel, so the
        # arm waits for the slowest, not for their sum.
        self._put_clock = max(x.finish for x in self._put_queues)
        self._put_seconds += dur
        self._put_records.append(
            (t0, dur, order.sku, unit.quantity, bin_.location[0],
             bin_.x_phys, bin_.y_phys, source or 'intake', w, q.name))

    def _sigma_delta(self, sku: int, bin_: Aisle.Bin) -> float:
        """f_s · D(bin) increment for the incremental Σ f·D tracker.

        The ONE shared expression behind every += / -= on _sigma_fd (placement adds,
        eviction/depletion subtracts) — kept as a method so the formula can't drift
        between the three sites.  Callers guard on ``self._sigma_freq is not None``.
        """
        return (self._sigma_freq.get(sku, 0.0)
                * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))

    def _stock(self, budget: int | None = None, deadline: float | None = None) -> None:
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

        ``deadline`` is the WHISTLE, in seconds on the crews' batch-local clocks: a putter
        already past it starts nothing new, and what is left rolls into the next call.  The
        two constraints are independent and both are checked -- a budget is people, a
        deadline is the clock, and a warehouse can run out of either first.  ``None`` is
        every run that does not ask for a day cut.

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
        # One queue at a time, in spec order.  A shared budget spends down across them:
        # it models a finite crew-hour allowance for the call, and splitting it per queue
        # would make the cap depend on how the streams happen to be configured.
        #
        # The outer pass exists because STAGING BOUNDS THE BACKLOG, NOT THE THROUGHPUT.  A
        # dock with room for eight pallets still moves hundreds in a day: you put one away
        # and another comes off the truck into the space it left.  Admitting the held items
        # once and draining once made `staging` a per-call quota -- measured at exactly
        # `staging` placements per call, for every value of it.  So: admit, drain, and go
        # round again while the floor is still holding work AND the last pass actually
        # moved something.
        #
        # `_admit_held` runs FIRST, before anything is placed, so a held item cannot be
        # overtaken by whatever arrives during the call.
        self._placed_this_call = 0
        _passes = 0
        while True:
            _passes += 1
            self._admit_held()
            _before_pass = self._placed_this_call
            for queue in self.put_queues:
                if budget is not None and budget <= 0:
                    break
                if not queue.items:
                    continue
                # The whistle, checked before the queue is entered as well as inside
                # the drain: a crew already out of day should not be handed a wave only to
                # put it straight back.
                if not queue.can_start(deadline):
                    continue
                before = self._placed_this_call
                if self.placement.is_ranked:
                    self._stock_ranked(budget, queue, deadline)
                else:
                    self._stock_per_unit(budget, queue, deadline)
                spent = self._placed_this_call - before
                queue.placed += spent
                if budget is not None:
                    budget -= spent
            # Stop when the floor is empty of held work, when the budget is gone, or when a
            # whole pass placed nothing -- the last is the termination guarantee: without it
            # a queue whose units no bin can hold would spin forever.
            if not self._held:
                break
            if budget is not None and budget <= 0:
                break
            # Every crew is out of day: another pass would admit held items onto a floor
            # nobody can work, inflating `admitted` for work that cannot start.
            if not any(q.can_start(deadline) for q in self.put_queues):
                break
            if self._placed_this_call == _before_pass:
                break
            if _passes >= _MAX_REFILL_PASSES:
                log.error(
                    'put-away refill hit the %d-pass cap with %d item(s) still held; '
                    'staging is so tight relative to the arrival rate that the drain '
                    'cannot clear it, and this call is giving up rather than spinning',
                    _MAX_REFILL_PASSES, len(self._held))
                break
        # WHAT THE WHISTLE COST, counted once and outside the loop.  The three gates above
        # each defer work without counting it, deliberately: any of them can fire on several
        # refill passes, and a counter incremented inside the loop would report how many
        # passes the drain happened to need rather than how much work the day boundary left
        # standing -- the exact defect `blocked` was fixed for.
        #
        # Safe to compute afterwards because a clock only ever advances: a queue that could
        # not start during the drain still cannot start now, so what remains on it is
        # precisely what the whistle stopped.
        #
        # `_held` is NOT counted.  A held item was refused FLOOR SPACE and never reached a
        # queue; that is `blocked`, a different problem with a different fix, and the two
        # counters are worth having only while they stay disjoint.
        if deadline is not None:
            for queue in self.put_queues:
                if queue.items and not queue.can_start(deadline):
                    queue.cut += len(queue.items)

    def _stock_per_unit(self, budget: int | None = None, queue=None,
                        deadline: float | None = None) -> None:
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

        ``deadline`` is the day's whistle on the crew's batch-local clock; see `_stock`.
        Checked per unit rather than once, because each put advances the clock that decides
        it -- a crew with ten minutes left takes as many units as fit in ten minutes.
        """
        queue = queue if queue is not None else self.put_queues.queues[0]
        waiting = queue.items          # NOT `q`: the repack loop below uses that for a
                                       # quantity, and shadowing it cost a debug cycle
        pending: deque[PutawayItem] = deque()
        placed = 0
        while waiting:
            if budget is not None and placed >= budget:
                # Budget spent.  Everything still queued waits for the next call — the same
                # deferral a unit gets when no bin fits it, so nothing new can be dropped.
                pending.extend(waiting)
                waiting.clear()
                break
            if not queue.can_start(deadline):
                # The whistle.  Identical deferral to the budget above; only the reason
                # differs.  NOT counted here -- `_stock` counts what is left once, after the
                # drain, because this line can run on several refill passes.
                pending.extend(waiting)
                waiting.clear()
                break
            item   = waiting.popleft()
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
                # No EMPTY bin fits this unit.  Attempt the fallback chain in order
                # (ADR-0003 fixes the order; the first rung is the new one):
                #   0. The SKU's OWN bins, fullest first, filled to capacity.
                #   1. Repack into smaller pallet size tier (existing logic).
                #   2. Fall back to singleton bins of the same order type.
                #   3. If all else fails, the unit goes to a local `pending` deque
                #      that becomes the new _stock_queue, and it retries next batch.
                #      There is NO expiry: a unit no bin can ever hold retries forever,
                #      and `mgr.queue_depth` is the only signal it is happening.  (An
                #      earlier version of this comment described a `_MAX_DRAIN_RETRIES`
                #      abandonment cap; no such constant has ever existed in the repo.)
                #
                # 0 AHEAD OF 1 AND 2 IS THE WHOLE POINT: the rescues exist for a pallet
                # meeting a small-bin warehouse, and they must not fire for a carton whose
                # own shelf has room.  A rescue is rework; a top-up is a put.
                repacked = False
                shc = order.storage_handle_config

                # ── rung 0: the SKU's own bins (ADR-0003) ─────────────────────────
                _topups_before = self._put_topups
                took = self._top_up_own_bins(unit, source=item.source, queue=queue)
                if took:
                    # The budget counts PLACEMENTS, and a unit absorbed into three own bins
                    # is three trips, three `bin_placement` rows and three `_reorder_placements`
                    # -- so it must cost three here too, or a crew cap is silently generous
                    # exactly when the warehouse is most fragmented.  Read off the flow rather
                    # than returned, so `_top_up_own_bins` keeps its one-number interface.
                    _bins_touched = self._put_topups - _topups_before
                    if took == unit.quantity:
                        # Fully absorbed: the unit is off the queue for good, so the
                        # queued-unit count drops exactly as `_execute_placement` drops it.
                        n_q = self._queued_sku_counts.get(sku, 0)
                        if n_q <= 1:
                            self._queued_sku_counts.pop(sku, None)
                        else:
                            self._queued_sku_counts[sku] = n_q - 1
                        placed += _bins_touched
                        continue
                    # Partial: the remainder goes back on the queue as ONE unit and takes
                    # the chain from the top -- a smaller unit may now find an empty bin,
                    # which is still empty-first.  One unit in, one unit out, so
                    # `_queued_sku_counts` needs no delta (unlike the rescues, which split).
                    waiting.appendleft(
                        item.respawn(type(unit)(order, unit.quantity - took)))
                    placed += _bins_touched
                    continue

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
                        # The rework is RECEIVING work, priced per resulting pack.
                        self._charge_repack(order, new_units)
                        for u in reversed(new_units):
                            waiting.appendleft(item.respawn(u))
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
                        # Same rework, same price -- a singleton rescue is a repack that
                        # happens to land in the forward-pick family.
                        self._charge_repack(order, new_units)
                        for u in reversed(new_units):
                            waiting.appendleft(item.respawn(u))
                        repacked = True

                # ── no bin available — hold in queue, retry next batch ────────
                if not repacked:
                    pending.append(item)
        queue.items = pending
        self._placed_this_call += placed


    @property
    def put_queues(self) -> PutQueueSet:
        """The put-away streams.  Assigning a new set re-binds the crews, so a manager that
        already had timing enabled keeps it -- swapping in `store_and_fulfillment()` after
        `enable_putaway_timing` is the normal order and must not silently turn timing off."""
        return self._put_queues

    @put_queues.setter
    def put_queues(self, queues: PutQueueSet) -> None:
        """Install a queue set.  REFUSES to discard merchandise doing it.

        The rebind does not migrate items, so swapping a loaded set would delete whatever it
        held -- silently, and invisibly to the conservation ledger, which is a stock ledger
        over BINS and cannot see a unit that never reached one.

        Today nothing loses anything: the runner installs the split set after `enqueue_all`,
        and initial stocking drains the default queue empty before it gets here.  But that is
        a property of the CALL ORDER at one call site, not a guarantee -- move the install one
        line later, or add a second caller, and the hole opens with no error.  So the
        invariant is asserted where it can be, rather than documented where it cannot.
        """
        old = getattr(self, '_put_queues', None)
        if old is not None and old is not queues:
            standing = sum(len(q.items) for q in old)
            if standing:
                raise ValueError(
                    f'refusing to replace a put-queue set still holding {standing} storage '
                    f'unit(s): the rebind does not migrate items, so they would be deleted '
                    f'with nothing raising and no ledger able to see it. Drain the queues '
                    f'first, or install the new set before anything is admitted.')
        self._put_queues = queues
        if self._put_speed is not None:
            self._bind_put_crews()

    def _bind_put_crews(self) -> None:
        """Give every queue a crew: its own `spec.crew` if it has one, else the manager's
        default from `enable_putaway_timing`.

        A crew per STREAM, because that is what a crew is: a forklift crew's clock has
        nothing to do with a cart crew's, and two streams working in parallel must not
        serialise onto one clock -- which is exactly what a single manager-level
        `_put_clocks` did.
        """
        for q in self._put_queues:
            crew = q.spec.crew
            speed = getattr(crew, 'speed', None) or self._put_speed
            size = getattr(crew, 'size', None) or self._put_size
            q.bind_crew(speed, self._put_cost, size)

    @property
    def _stock_queue(self):
        """The put-away queue.

        A live deque while there is ONE queue -- which is the default, and why every
        existing consumer, mutation site and test keeps working unchanged.  With the streams
        split there is no single deque to hand back, so this returns a read-only view and
        any code that tried to mutate it would fail loudly rather than mutate a copy.
        Internal drain code takes a `PutQueue` explicitly and never reaches for this.
        """
        if len(self.put_queues) == 1:
            return self.put_queues.queues[0].items
        return _MultiQueueView(self.put_queues)

    @_stock_queue.setter
    def _stock_queue(self, value):
        # `_stock_per_unit` rebuilds the queue wholesale from its `pending` deque. Legal
        # only in the single-queue case; with the streams split the drain assigns to the
        # PutQueue it was handed instead.
        if len(self.put_queues) != 1:
            raise RuntimeError(
                'cannot replace _stock_queue wholesale while the put-away streams are '
                'split — assign to the individual PutQueue.items instead')
        self.put_queues.queues[0].items = value if isinstance(value, deque) else deque(value)

    def queue_contents(self) -> list[tuple]:
        """What is waiting for a bin, per stream, aggregated for the replay viewer.

        Returns `(kind, sku, unit_type, storage_size, queue, qty)`.  `kind` is 'stock' for
        items on a queue and 'held' for items a full queue refused -- the second is invisible
        in `_stock_queue` by construction, so a reader that only walked the queues would
        under-report the backlog exactly when backpressure is doing something.
        """
        agg: dict = {}
        for q in self.put_queues:
            for it in q.items:
                u = it.unit
                k = ('stock', u.order.sku, u.unit_category, u.storage_size, q.name)
                agg[k] = agg.get(k, 0) + u.quantity
        for it in self._held:
            u = it.unit
            k = ('held', u.order.sku, u.unit_category, u.storage_size,
                 self.put_queues.route(u).name)
            agg[k] = agg.get(k, 0) + u.quantity
        # A fourth `kind`, and a ROW rather than a column: `reorder_queue` already carries a
        # kind discriminator, so the dock's contents cost no schema change.  Guarded, so a
        # run with no receiving crew emits exactly the rows it emitted before.
        if self._dock is not None:
            for it in self._dock.items:
                u = it.unit
                k = ('dock', u.order.sku, u.unit_category, u.storage_size, self._dock.name)
                agg[k] = agg.get(k, 0) + u.quantity
        return [(*k, v) for k, v in agg.items()]

    def carryover_rows(self, batch_id: int) -> list[tuple]:
        """`(batch_id, reason, sku, qty)` for every unit still standing unbinned.

        THREE reasons, spanning TWO classes, and the split is deliberate:

          placement failure   'unplaced' could not reach a bin; 'held' was refused floor
                              space.  Keeping these apart is the whole value of the column:
                              different problems, different fixes, and one carried-over
                              count cannot tell them apart.
          pre-placement       'dock' has not been offered a bin at all -- it is still on a
                              trailer, waiting to be unloaded.

        So sum ALL THREE for "what is standing unbinned", and filter to the first two for
        "what did placement fail to do".  Both sums are over THIS method's output, which is
        entirely levels.  The `carryover` TABLE also carries the pick side's `unpicked_*`
        FLOWS from a different producer, and a level and a flow must never be added: see
        the reason column's own comment.  The dock is here because leaving it out made this
        surface and `queue_contents` disagree about the same question: `queue_contents`
        emits a 'dock' kind, and on a 200-batch run that was 52,479 rows this table denied.
        A consumer summing carryover to size the backlog silently missed all of them.

        The three are disjoint by construction -- a unit is on the dock, or admitted to a
        queue, or held, never two of those -- so no sku can collide across reasons, and the
        primary key stays one row per producer.  See `_insert_carryover`, which now RAISES
        on a duplicate rather than replacing.
        """
        agg: dict = {}
        for q in self.put_queues:
            for it in q.items:
                k = ('unplaced', it.unit.order.sku)
                agg[k] = agg.get(k, 0) + it.unit.quantity
        for it in self._held:
            k = ('held', it.unit.order.sku)
            agg[k] = agg.get(k, 0) + it.unit.quantity
        if self._dock is not None:
            for it in self._dock.items:
                k = ('dock', it.unit.order.sku)
                agg[k] = agg.get(k, 0) + it.unit.quantity
        return [(batch_id, reason, sku, qty) for (reason, sku), qty in agg.items()]

    def queue_state_rows(self, batch_id: int) -> list[dict]:
        """One row per queue: depth and oldest age (LEVELS) plus the flow counters.

        DRAINS the counters, so it must run exactly once per batch or the next batch
        double-counts this one.
        """
        rows = self.put_queues.snapshot()
        for r in rows:
            r['batch_id'] = batch_id
        return rows

    def _admit(self, unit: StorageUnit, source: str) -> 'PutawayItem':
        """Put one unit on the put-away queue, stamped with its arrival age.

        The single admission point.  Every producer -- intake, reorder arrivals, reloader
        evictions, and inbound when it exists -- goes through here, so a new producer cannot
        forget to stamp and quietly enter the queue as age -1.

        A queue at its staging limit REFUSES, and the item goes to `_held` rather than
        back to the caller.  Handling it here rather than at each producer is deliberate:
        there are four producers and the failure mode of forgetting one is a unit that
        silently disappears -- and NOT one the conservation ledger would catch, because that
        ledger is a stock ledger over BINS and this unit never reached one. Nothing would
        raise and nothing would count it. Exactly the bug that takes a day to find.  Callers therefore never see a refusal and none of them changed.

        The stamp is taken on ARRIVAL, not on admission.  A pallet that waited three batches
        on the dock is three batches old when it finally gets floor space, and stamping it
        at admission would make it the youngest thing in the warehouse -- turning
        backpressure into a priority inversion.
        """
        item = self._stamp(unit, source)
        # THE DOCK INTERCEPTS HERE, after the stamp and before the queue.  After the stamp,
        # so a pallet that waits three batches on the dock is three batches old when it
        # finally gets floor space -- the inversion the paragraph above forbids.  And here
        # rather than one level up in `_release_to_stock`, because that function credits
        # `_queued_qty` AFTER its admit loop: intercepting inside `_admit` leaves the credit
        # in place, so `position = on_hand + queued + deferred` is unchanged and the reorder
        # ledger needs no edit.  Diverting upstream would drop the merchandise out of
        # `_deferred_qty` without adding it to `_queued_qty`, and the SKU would re-order
        # every batch for as long as the dock was backed up, with nothing raising.
        if self._dock is not None and self._dock.takes(source):
            self._dock.arrive(item)
            return item
        return self._queue(item)

    def _stamp(self, unit: StorageUnit, source: str) -> 'PutawayItem':
        """Wrap one unit as a stamped `PutawayItem` -- the ONE place the age stamp is
        taken.  Split out of `_admit` for the standing yard, whose units are stamped at
        YARD ARRIVAL (their age is the trailer's, not the unload's) but enter no queue
        until the crew pulls them -- `_admit` routes as well as stamps, and routing a
        deferred unit would put it away for free.  Every stamp still comes from here, so
        no producer can invent its own and quietly enter as age -1.
        """
        item = PutawayItem(unit, source, self._putaway_seq)
        self._putaway_seq += 1
        return item

    def _queue(self, item: 'PutawayItem') -> 'PutawayItem':
        """Route one STAMPED item to its put queue, holding it if the queue is full.

        Split out of `_admit` so the receiving crew has somewhere to hand an item it has
        already taken off a trailer.  `_admit` stamps and decides WHERE work enters; this
        decides which put queue takes it.  A dock unload calls this directly -- the item was
        stamped on arrival and must not be re-stamped.
        """
        q = self.put_queues.route(item.unit)
        if not q.admit(item):
            # Held AGAINST ITS QUEUE.  Routing happens here, once, so the retry never has to
            # route again -- and a full queue can then be skipped whole instead of walked.
            self._held.append(q.name, item)
            return None
        return item

    def _admit_held(self) -> int:
        """Retry the held items, oldest first within each queue.  Returns how many got in.

        Stops at the FIRST refusal for a given queue rather than scanning past it for
        something that happens to fit.  Letting a younger item slip into the gap an older one
        could not use is exactly the inversion the age stamp exists to prevent, and it would
        also make the backpressure unfair in a way no real dock is.

        COST IS O(queues + admitted), and getting there took three attempts:

          1. The original walked every held item on every call, doing a `route()` each time.
             `_stock`'s refill loop calls this once per pass and needs roughly
             `work / staging` passes, so passes and the held list grew together: quadratic.
             Measured at 40 batches, staging=4 -- 27,248,644 `route()` calls at 2,400 SKUs.
          2. An early exit on "every queue has refused" cut the routing 96x at that size, and
             was reported as the fix.  It was not.  It still did `still.extend(rest)` into a
             fresh deque, so every call copied the whole list -- and worse, THE EXIT WAS
             UNREACHABLE.  It fired on `len(blocked) >= len(self.put_queues)`, every queue in
             the set, while a store-only catalogue routes to two of the split's three and
             `fulfillment` stays empty forever.  Touches kept growing at k=1.81 against 1.82
             before the exit existed.
          3. `_held` is now PARTITIONED by queue (`HeldItems`), so a full queue is skipped in
             O(1) and there is no exit condition to get wrong.  Routing already happened when
             the item was held, so this does none.

        A per-queue COUNT beside the single deque was tried between 2 and 3 and rejected:
        derived state that can drift, and a stale one makes this stop instantly and livelock,
        which is worse than the slowness it cures.  The partition needs nothing kept in sync.
        """
        admitted = 0
        for name, items in self._held.loaded():
            q = self.put_queues[name]
            while items:
                # arrival=False: this item was counted as blocked when it first arrived, and
                # counting each retry again would report the refill loop's pass count.
                if not q.admit(items[0], arrival=False):
                    break          # full: the rest of THIS queue cannot go either
                items.popleft()
                admitted += 1
        return admitted

    def _window_for(self, queue) -> int | None:
        """The ordering tolerance for one queue: its own `k_cap`, or the manager's default.

        A spec's None means INHERIT rather than "unbounded", so the manager-wide
        `putaway_window` still governs every queue that has no reason to differ, and the
        default single queue behaves exactly as it did before queues existed.
        """
        k = queue.spec.k_cap if queue is not None else None
        return self.putaway_window if k is None else k

    def _serve_order(self, pool, units: list, k: int | None, put_key=None) -> list:
        """WHO is served first, out of one BinKey group.  The drain's decision.

        `putaway_window` is the tolerance, in units:

          None  the policy's whole request is granted -- pre-Phase-2 behaviour, and still
                the default, so nothing moves until a caller asks for a window.
          1     strict FIFO.  The policy chooses the BIN and has no say in the order.
          K     the policy may pick its favourite from the K OLDEST units still waiting.
                Serve it, slide the window forward by one, repeat.

        A window of K >= len(units) is exactly `pool.order(units)`, including the
        tie-breaking: `order` is a stable sort so equal keys keep queue order, and the heap
        below breaks ties on arrival index for the same reason.  That equivalence is
        asserted in Tests/unit/test_putaway_window.py rather than assumed, because it is
        what makes "K = infinity reproduces today" a fact instead of a hope.

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

        `put_key` is the QUEUE's own precedence, from `put_policy`.  When present it
        replaces the pool's `sort_key` entirely -- the pool still chooses every bin, it just
        stops choosing who goes first.  `k` then bounds the result exactly as it bounds the
        pool's own order, so the two knobs compose rather than override each other.

        `units` arrives in queue order (the group's insertion order into `_stock_queue`), so
        the FIFO answer is already in hand and needs no extra bookkeeping to recover.
        """
        if put_key is not None:
            # The queue has an opinion, so the pool's does not apply. Sorted stably, so
            # units the key cannot separate keep their arrival order.
            if k is None or k >= len(units) or len(units) < 2:
                return sorted(units, key=put_key, reverse=True)
            return _windowed(units, [put_key(u) for u in units], k)
        if k is None:
            return pool.order(units)
        if k < 1:
            raise ValueError(f'putaway_window must be >= 1 or None, got {k!r}')
        n = len(units)
        if k >= n or n < 2:
            return pool.order(units)         # the window admits everything: a plain sort

        keys = [pool.sort_key(u) for u in units]
        if keys[0] is None:                  # no precedence at all: queue order already is
            return units                     # the answer, and the window cannot change it
        return _windowed(units, keys, k)

    def _stock_ranked(self, budget: int | None = None, queue=None,
                      deadline: float | None = None) -> None:
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

        ``deadline`` is spent the same way and for the same reason: a wave the crew cannot
        start before the whistle is requeued untouched rather than half-placed.  The clock
        moves DURING a wave (every `_execute_placement` charges its queue), so a wave that
        begins before the whistle can end after it -- that is the bounded overtime the gate
        is defined to allow, and it is why the check is per group rather than per unit.
        """
        queue = queue if queue is not None else self.put_queues.queues[0]
        waiting = queue.items
        if not waiting:
            return
        window = self._window_for(queue)

        # Snapshot queue and group by BinKey (or (BinKey, velocity band) when zoning is on, so
        # each sub-wave is a single band and the once-per-wave candidate fetch is band-correct).
        groups: dict[tuple, list[PutawayItem]] = defaultdict(list)
        while waiting:
            item = waiting.popleft()
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
                waiting.extend(items)
                continue
            if not queue.can_start(deadline):
                # The whistle, on the same terms as the budget above: the wave is requeued
                # whole and unscored.  Counted by `_stock`, once, after the drain.
                waiting.extend(items)
                continue
            # `place_wave` takes and returns bare units, so the envelope is re-attached by
            # object identity.  Safe HERE and nowhere else: every unit in `by_unit` is alive
            # for the whole call, so an id cannot be recycled underneath the lookup — which
            # is exactly the property `BinRecorder`'s cross-call `id()` set could not rely on.
            units   = [it.unit for it in items]
            by_unit = {id(it.unit): it for it in items}
            # The queue's own precedence, if it has one. Resolved per group because
            # `sku_batched` is scoped to the waiting set; `by_unit` bridges the drain's bare
            # units back to the items the policy reads (`age`, `quantity`).
            _pk = _put_key_for(queue.spec.policy, items)
            put_key = None if _pk is None else (lambda u: _pk(by_unit[id(u)]))
            if self.placement.is_pooled:
                # POOLED: the DRAIN owns the order, the pool owns the choice.  One snapshot
                # per group, exactly as the wave took -- the candidate set never depended on
                # the order, because every unit in a group shares a BinKey.
                pool = self.placement.open_pool(self._candidates(units[0]), units[0])
                taken = []
                for unit in self._serve_order(pool, units, window, put_key):
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
                    waiting.append(by_unit[id(unit)])

        self._placed_this_call += placed
        if waiting:
            # The stragglers this wave shed, on the SAME queue: a unit the group path could
            # not fit must not be re-routed, and its queue's tolerance does not apply here
            # because the per-unit path has no ordering to constrain.
            self._stock_per_unit(None if budget is None else max(0, budget - placed),
                                 queue, deadline)


