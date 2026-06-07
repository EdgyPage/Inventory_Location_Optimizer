import bisect
import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from Carton import Carton
from Warehouse_Builder import Warehouse, Warehouse_Builder, AisleConfig, WarehouseConfig
from Aisle_Storage import Aisle
from Aisle_Dimensions import uniform_aisle_bins
from Storage_Primitive import StorageUnit, Singleton, Pallet, Storage_Size, viable_storage_units, _max_qty_fits as _sq_max
from Affinity_Store import AffinityStore

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]

# Takes a list of units and a candidate-bin callback; returns (unit, bin|None)
# pairs in priority order.  All units share the same BinKey group.
BatchAssignmentFn = Callable[
    [list[StorageUnit], Callable[[StorageUnit], list[Aisle.Bin]]],
    list[tuple[StorageUnit, 'Aisle.Bin | None']],
]

@dataclass
class LoadParams:
    lambda_: float = 1.0   # startup-cost multiplier
    k: float       = 1.0   # pickers per task (normally 1 for single-aisle tasks)
    gamma: float   = 1.5   # congestion exponent


@dataclass
class WarehousePlan:
    """Result of Inventory_Manager.plan_warehouse: a sized warehouse + the
    SKU sample chosen to fill it to target utilization."""
    warehouse_cfg : 'WarehouseConfig'
    sampled       : list                  # cartons to actually stock
    sku_allowlist : set                   # sku ids in `sampled`
    capacity      : dict                  # BinKey -> bins available in warehouse
    aisle_configs : list                  # the per-replica AisleConfig list
    total_aisles  : int
    total_bins    : int
    expected_fill : float

_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(
        sorted(Storage_Size.available_sizes_heights, key=Storage_Size.available_sizes_heights.__getitem__)
    )
}

# Sizes ordered from largest to smallest — used by _candidates for O(1) tier lookup.
_SIZES_DESCENDING: tuple[str, ...] = tuple(
    sorted(_SIZE_RANKS, key=_SIZE_RANKS.__getitem__, reverse=True)
)

BinKey = tuple[str, str, str, str]

def _equilibrium_qty(carton: Carton) -> int:
    """Return the Order-Up-To target for *carton*.

    Reads equilibrium_qty if present (new schema); falls back to the legacy
    stock_qty attribute so old in-memory inventories still work correctly.
    """
    return getattr(carton, 'equilibrium_qty',
                   getattr(carton, 'stock_qty', 1))


def _max_qty_fitting_pallet_size(carton: Carton, target_size: str) -> int:
    """Return the maximum number of *carton* items that stack onto one pallet
    whose storage_size is at most *target_size*.

    Pallet stacking height increases monotonically with quantity, so the
    required storage_size also increases.  We scan from 1 upward until the
    pallet outgrows the target tier and return the last fitting quantity.
    Used by _drain to repack a stranded unit into smaller bins.
    """
    target_rank = _SIZE_RANKS.get(target_size, 0)
    result = 0
    for q in range(1, 10_000):
        try:
            p = Pallet(carton, q)
            if _SIZE_RANKS.get(p.storage_size, 99) <= target_rank:
                result = q
            else:
                break   # size is monotone-increasing — stop early
        except ValueError:
            break
    return result


def _uniform_assignment(unit: StorageUnit, candidates: list[Aisle.Bin]) -> Aisle.Bin | None:
    """Pick uniformly at random from the candidate bin list.

    candidates is pre-filtered by _candidates() to the correct handling type,
    storage category, unit type, and largest available size tier.  Picking
    randomly within that filtered set uniformly distributes placements across
    the matching bin locations.
    """
    return random.choice(candidates) if candidates else None



class Inventory_Manager:

    # ── warehouse planning (pre-instantiation) ────────────────────────────────
    # These size a warehouse FROM an inventory, before any manager instance or
    # warehouse exists, so they are static/class methods.  They guarantee every
    # (handling, category, size, unit_type) bucket has at least one aisle, so
    # every SKU is structurally placeable, then add demand-driven replicas and
    # sample SKUs to fill to a target utilization.

    @staticmethod
    def bucket_requirements(cartons: list[Carton]) -> dict[BinKey, int]:
        """Exact bin count per (handling, category, storage_size, unit_type)
        bucket, computed by running each carton through viable_storage_units at
        its equilibrium_qty.  This is the authoritative per-tier demand."""
        req: dict[BinKey, int] = defaultdict(int)
        for c in cartons:
            shc = c.storage_handle_config
            for u in viable_storage_units(c, _equilibrium_qty(c)):
                req[(shc.handling, shc.category, u.storage_size, u.unit_category)] += 1
        return dict(req)

    @classmethod
    def plan_warehouse(
        cls,
        cartons      : list[Carton],
        *,
        categories   : list[str],
        handlings    : list[str],
        aisle_width  : int,
        aisle_height : int,
        target_fill  : float = 0.85,
        min_bins     : int | None = None,
        max_bins     : int | None = None,
        max_aisles   : int | None = None,
        composition  : dict | None = None,
        sample       : bool = True,
        rng          : random.Random | None = None,
        log          : Any = None,
    ) -> 'WarehousePlan':
        """Size a warehouse to fit *cartons* under the given constraints.

        1. Enumerate the full bucket set (every handling×category gets 4 pallet
           size tiers + 1 singleton) — the structural floor that guarantees
           every SKU has a place.
        2. Replica per bucket either from demand (default,
           max(1, ceil(demand/(eff·target_fill)))) or from an explicit
           *composition* basis vector (bins ∝ weight).
        3. Scale up to >= min_bins, then down to <= max_bins/max_aisles (never
           below 1 replica/bucket; min_bins wins if it conflicts with max).
        4. Sample SKUs to fill the resulting capacity to target_fill.

        composition: optional factored basis vector of *bin* ratios — a dict with
        any of the keys 'handling', 'category', 'size', 'unit', each mapping a
        value to a relative weight (missing values default to 1.0).  The per-bucket
        weight is the product of the matching dimension weights; bins are allocated
        proportionally.  Total scale comes from min_bins (or demand if min_bins is
        unset).  Example:
            {'unit': {'pallet': 0.7, 'singleton': 0.3},
             'size': {'small': 0.1, 'medium': 0.2, 'large': 0.3, 'extra_large': 0.4}}
        """
        req = cls.bucket_requirements(cartons)

        # 1: enumerate every bucket with a ≥1 floor.
        bucket_list: list[tuple] = []     # (handling, category, size, unit_type)
        for h in handlings:
            for cat in categories:
                for size in _SIZES_DESCENDING:          # 4 pallet tiers
                    bucket_list.append((h, cat, size, 'pallet'))
                bucket_list.append((h, cat, 'singleton', 'singleton'))

        def _eff(bucket: tuple) -> int:
            _h, _c, size, unit_type = bucket
            return uniform_aisle_bins(unit_type, size, aisle_width, aisle_height)

        def _comp_weight(bucket: tuple) -> float:
            """Factored basis-vector weight for a bucket (product of dimension
            weights; each dimension defaults to 1.0 when unspecified)."""
            h, cat, size, unit_type = bucket
            w  = composition.get('handling', {}).get(h, 1.0)
            w *= composition.get('category', {}).get(cat, 1.0)
            w *= composition.get('unit', {}).get(unit_type, 1.0)
            if unit_type == 'pallet':
                w *= composition.get('size', {}).get(size, 1.0)
            return w

        # 2: base replicas — demand-driven, or proportional to a composition vector.
        replicas: dict[tuple, int] = {}
        if composition is not None:
            weights = {b: _comp_weight(b) for b in bucket_list}
            tw      = sum(weights.values()) or 1.0
            demand_bins = sum(
                (max(1, math.ceil(req.get(b, 0) / (_eff(b) * target_fill))) if _eff(b) else 1) * _eff(b)
                for b in bucket_list)
            target_total = float(min_bins) if min_bins else float(demand_bins)
            for b in bucket_list:
                eff = _eff(b)
                desired = target_total * weights[b] / tw     # desired bins for b
                replicas[b] = max(1, round(desired / eff)) if eff else 1
        else:
            for b in bucket_list:
                eff = _eff(b)
                need = req.get(b, 0)
                replicas[b] = max(1, math.ceil(need / (eff * target_fill))) if eff else 1

        total_aisles = sum(replicas.values())
        total_bins   = sum(r * _eff(b) for b, r in replicas.items())

        # 3a: scale UP to satisfy a minimum bin count.
        if min_bins is not None and total_bins < min_bins:
            factor = min_bins / total_bins
            for b in replicas:
                replicas[b] = max(1, math.ceil(replicas[b] * factor))
            total_aisles = sum(replicas.values())
            total_bins   = sum(r * _eff(b) for b, r in replicas.items())

        # 3b: enforce caps, never trimming a bucket below its floor of 1, and
        #     never below min_bins (a min_bins > max_bins request keeps the min).
        _bins_floor = min_bins if min_bins is not None else 0
        if ((max_aisles is not None and total_aisles > max_aisles) or
            (max_bins   is not None and total_bins   > max_bins and total_bins > _bins_floor)):
            ratios = []
            if max_aisles is not None and total_aisles > max_aisles:
                ratios.append(max_aisles / total_aisles)
            if max_bins is not None and total_bins > max_bins:
                ratios.append(max(max_bins, _bins_floor) / total_bins)
            scale = min(ratios)
            for b in replicas:
                replicas[b] = max(1, round(replicas[b] * scale))
            total_aisles = sum(replicas.values())
            total_bins   = sum(r * _eff(b) for b, r in replicas.items())

            # greedy trim largest-bin trimmable bucket (replicas>1) to hit caps,
            # but never drop total_bins below the min_bins floor.
            while (((max_bins   is not None and total_bins   > max_bins) or
                    (max_aisles is not None and total_aisles > max_aisles))
                   and total_bins > _bins_floor):
                trimmable = [b for b in bucket_list if replicas[b] > 1]
                if not trimmable:
                    break   # every bucket at floor — cannot shrink further
                b = max(trimmable, key=_eff)
                if total_bins - _eff(b) < _bins_floor:
                    break   # one more trim would breach the min_bins floor
                replicas[b] -= 1
                total_aisles -= 1
                total_bins   -= _eff(b)

            if log is not None and (
                (max_bins   is not None and total_bins   > max_bins) or
                (max_aisles is not None and total_aisles > max_aisles)):
                log.warning(
                    f'  max-bins/max-aisles below structural minimum — cap not '
                    f'honored (requested max_bins={max_bins} max_aisles={max_aisles}). '
                    f'Floor is {total_aisles} aisles / {total_bins:,} bins: one aisle '
                    f'per {len(bucket_list)} (handling,category,size,unit_type) buckets '
                    f'so every SKU is placeable. Proceeding with the floor.')

        # Build per-replica AisleConfig list + capacity map.
        aisle_configs: list = []
        capacity: dict[BinKey, int] = {}
        for b in bucket_list:
            h, cat, size, unit_type = b
            eff = _eff(b)
            rep = replicas[b]
            capacity[b] = rep * eff
            sizes_arg = ['singleton'] if unit_type == 'singleton' else [size]
            for _ in range(rep):
                aisle_configs.append(
                    AisleConfig(h, cat, unit_type, aisle_width, aisle_height,
                                sizes_arg, None))

        # 4: sample SKUs to fill capacity to target_fill.  Skipped when sample=
        # False (e.g. analysis only needs the warehouse shape + aisle maps, not
        # a restocked inventory) — this avoids re-stocking the whole inventory.
        if sample:
            sampled, allowlist = cls.sample_to_capacity(
                cartons, capacity, target_fill=target_fill, rng=rng)
            total_units = sum(
                len(viable_storage_units(c, _equilibrium_qty(c))) for c in sampled)
            expected_fill = total_units / total_bins if total_bins else 0.0
        else:
            sampled, allowlist = [], set()
            expected_fill = 0.0

        n = len(aisle_configs)
        splits = [1.0 / n] * n if n else []
        warehouse_cfg = WarehouseConfig(
            total_aisles  = n,
            aisle_splits  = splits,
            aisle_configs = aisle_configs,
        )
        return WarehousePlan(
            warehouse_cfg = warehouse_cfg,
            sampled       = sampled,
            sku_allowlist = allowlist,
            capacity      = capacity,
            aisle_configs = aisle_configs,
            total_aisles  = n,
            total_bins    = total_bins,
            expected_fill = expected_fill,
        )

    @staticmethod
    def sample_to_capacity(
        cartons     : list[Carton],
        capacity    : dict[BinKey, int],
        *,
        target_fill : float = 0.85,
        rng         : random.Random | None = None,
    ) -> tuple[list[Carton], set]:
        """Assign each carton a multi-tier stock_plan that fills bin capacity.

        A carton's units are spread across the EMPTIEST bin tiers it can reach:
        flexible (small-footprint) items can be palletized into any tier their
        geometry permits; rigid (large) items only reach the large tiers they
        genuinely require.  Each plan slot is a (is_singleton, qty_per_unit)
        pair; the slots sum to the carton's (possibly grown) equilibrium_qty.

        Allocation: phase 1 is round-robin so every SKU gets at least one unit
        first (placeability) and reaches its base equilibrium; phase 2 fills the
        leftover capacity per (handling, category) group in BULK — distributing
        each bin tier's free space across the cartons that can reach it in one
        step rather than one bin at a time.  The plan is stored on the carton as
        run-length slots (is_singleton, qty_per_unit, count) so
        viable_storage_units — and therefore every reorder — reproduces the exact
        tier mix.

        Performance: each carton's reachable tiers (and the qty that lands a full
        pallet in each) are computed once via Pallet._fit (O(N) fits).  Full
        pallets reuse that cached tier, so no _fit runs in the fill loops; only a
        capped final slot (phase 1, ≤1 per carton) needs a fit.

        Returns (sampled_cartons, sampled_sku_ids).
        """
        _rng   = rng or random
        free   = {b: int(cap * target_fill) for b, cap in capacity.items()}

        def _reachable(c: Carton) -> list[tuple[BinKey, int, bool]]:
            """(bucket, qty_per_unit, is_singleton) options this carton can fill.
            qty_per_unit is the quantity whose pallet lands exactly in that tier,
            so a full pallet of it never needs a _fit recheck at fill time."""
            shc  = c.storage_handle_config
            opts: list[tuple[BinKey, int, bool]] = []
            for size in _SIZES_DESCENDING:
                q = _max_qty_fitting_pallet_size(c, size)
                if q > 0 and Pallet(c, q).storage_size == size:
                    opts.append(((shc.handling, shc.category, size, 'pallet'), q, False))
            sq = _sq_max(c, Singleton)
            if sq > 0:
                opts.append(((shc.handling, shc.category, 'singleton', 'singleton'), sq, True))
            return opts

        order: list[Carton] = list(cartons)
        _rng.shuffle(order)
        reach   = {id(c): _reachable(c) for c in order}
        plans   : dict[int, list[tuple[bool, int, int]]] = {id(c): [] for c in order}
        qty_sum : dict[int, int] = {id(c): 0 for c in order}
        eq0     = {id(c): _equilibrium_qty(c) for c in order}
        shc_of  = {id(c): c.storage_handle_config for c in order}

        def _add_run(c: Carton, is_single: bool, per: int, count: int, bucket: BinKey) -> None:
            """Append `count` units of `per` items to c's plan, charging `bucket`.
            Merges with the previous run if it is the same (is_single, per)."""
            plan = plans[id(c)]
            if plan and plan[-1][0] == is_single and plan[-1][1] == per:
                last = plan[-1]
                plan[-1] = (is_single, per, last[2] + count)
            else:
                plan.append((is_single, per, count))
            qty_sum[id(c)] += per * count
            free[bucket]    = free.get(bucket, 0) - count

        def _add_one(c: Carton, cap_qty: int | None) -> bool:
            """Add ONE pallet/singleton in c's emptiest reachable bucket with
            budget.  cap_qty caps the slot quantity to land the final slot exactly
            on equilibrium.  Returns False when no reachable bucket has space."""
            opts = [(b, per, isng) for (b, per, isng) in reach[id(c)] if free.get(b, 0) > 0]
            if not opts:
                return False
            b, per, isng = max(opts, key=lambda o: free[o[0]])
            if cap_qty is None or cap_qty >= per:
                # Full pallet — tier is the cached bucket, no _fit needed.
                _add_run(c, isng, per, 1, b)
                return True
            # Capped final slot: a smaller quantity can drop a pallet into a
            # SMALLER tier, so charge the bucket it ACTUALLY lands in.
            per = cap_qty
            if per <= 0:
                return False
            if isng:
                actual_b = b
            else:
                shc = shc_of[id(c)]
                actual_b = (shc.handling, shc.category, Pallet(c, per).storage_size, 'pallet')
            if free.get(actual_b, 0) <= 0:
                return False
            _add_run(c, isng, per, 1, actual_b)
            return True

        # Phase 1 (round-robin): every carton up to its base equilibrium.
        progress = True
        while progress:
            progress = False
            for c in order:
                if qty_sum[id(c)] >= eq0[id(c)]:
                    continue
                gap = eq0[id(c)] - qty_sum[id(c)]
                if _add_one(c, cap_qty=gap):
                    progress = True

        # Phase 2 (bulk): fill leftover space per (handling, category) group.
        # For each bin tier with free budget, distribute it across the cartons in
        # the group that can reach it (full pallets only → exact tier, no _fit).
        groups: dict[tuple, list[Carton]] = defaultdict(list)
        for c in order:
            shc = shc_of[id(c)]
            groups[(shc.handling, shc.category)].append(c)

        for gcartons in groups.values():
            bucket_reachers: dict[BinKey, list[tuple[Carton, int, bool]]] = defaultdict(list)
            for c in gcartons:
                for (b, per, isng) in reach[id(c)]:
                    bucket_reachers[b].append((c, per, isng))
            for b, lst in bucket_reachers.items():
                avail = free.get(b, 0)
                if avail <= 0 or not lst:
                    continue
                n          = len(lst)
                base_share = avail // n
                remainder  = avail - base_share * n
                for i, (c, per, isng) in enumerate(lst):
                    share = base_share + (1 if i < remainder else 0)
                    if share > 0:
                        _add_run(c, isng, per, share, b)

        selected = [c for c in order if plans[id(c)]]
        for c in selected:
            base = eq0[id(c)]
            f    = (c.reorder_point / base) if base else 0.5
            total = qty_sum[id(c)]
            c.stock_plan      = plans[id(c)]
            c.equilibrium_qty = total
            c.reorder_point   = max(1, min(total - 1, round(f * total)))
        return selected, {c.sku for c in selected}

    def __init__(
        self,
        warehouse: Warehouse,
        assignment_fn: AssignmentFn = _uniform_assignment,
        affinity: AffinityStore | None = None,
    ) -> None:
        self.warehouse: Warehouse = warehouse
        self.assignment_fn: AssignmentFn = assignment_fn
        # When set, check_reorders uses _drain_batch() instead of _drain().
        # Batch assignment sorts units by pick-effort priority so high-effort
        # items claim the best bins before lower-priority items are placed.
        self.batch_assignment_fn: BatchAssignmentFn | None = None
        self._affinity: AffinityStore | None = affinity
        self._index: dict[BinKey, list[Aisle.Bin]] = defaultdict(list)
        # id(bin) → position in its _index tier list — O(1) swap-remove support.
        self._bin_index_pos: dict[int, int] = {}
        # Per-aisle sorted secondary index: BinKey -> {aisle_id -> list[Bin] sorted by _W}.
        # Populated by init_travel_costs(); maintained by _index_add/_index_remove thereafter.
        self._aisle_index: dict[BinKey, dict[int, list[Aisle.Bin]]] = defaultdict(lambda: defaultdict(list))
        self._travel_costs_ready: bool = False

        # Keyed by id(bin) for O(1) removal when bins are reclaimed.
        self._unavailable: dict[int, Aisle.Bin] = {}

        # Queue holds pre-palletized StorageUnit objects ready for bin assignment.
        self._queue: deque[StorageUnit] = deque()
        # Count of queued units per SKU — O(1) alternative to rebuilding a set
        # from the full queue on every check_reorders call.
        self._queued_sku_counts: dict[int, int] = {}
        # Product-quantity on-order trackers (parallel to the unit-count dicts):
        # _queued_qty   = items reordered and queued but not yet placed in a bin,
        # _deferred_qty = items reordered and in-transit (lead-time deferral).
        # Reorder thresholds use inventory position = on_hand + queued + deferred
        # so a SKU already reordered (but unbinned) is not reordered again.
        self._queued_qty: dict[int, int]   = {}
        self._deferred_qty: dict[int, int] = {}
        self._originals: dict[int, Carton] = {}
        # equilibrium_qty at initial intake per SKU (not updated on reorders).
        self._initial_quantities: dict[int, int] = {}

        # Incremental inventory count — avoids O(N_bins) scan in check_reorders.
        self._current_quantities: dict[int, int] = {}

        # Deferred reorder support (Order-Up-To with lead times).
        # _batch_num increments on each check_reorders call; deferred reorders
        # are keyed by the batch number when they are due to arrive.
        self._batch_num: int = 0
        self._deferred_reorders: dict[int, list[tuple[int, list[StorageUnit]]]] = defaultdict(list)
        self._deferred_sku_counts: dict[int, int] = {}   # in-flight deferred units per SKU

        # Bins emptied by picks, pending return to _index at next check_reorders.
        self._pending_reclaim: list[Aisle.Bin] = []

        # SKUs whose current quantity has dropped to or below the reorder threshold
        # since the last check_reorders call.  Maintained by _notify_pick so
        # check_reorders scans only depleted SKUs instead of all N_skus.
        self._depleted_skus: set[int] = set()

        # Persistent lift state shared with load-aware assignment functions.
        self._aisle_sku_sets: dict[int, set[int]]         = defaultdict(set)
        self._aisle_lift_sum: dict[int, float]             = defaultdict(float)
        self._aisle_sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        # Pre-translated matrix indices mirror of _aisle_sku_sets — eliminates
        # the O(N_aisle_members) dict lookup set-comprehension in delta_lift_idxs.
        self._aisle_idx_sets: dict[int, set[int]]         = defaultdict(set)
        # id(bin) → sku; needed for lift removal after storage is cleared.
        self._bin_sku: dict[int, int] = {}

        # Demand-based state for trip-cost assignment functions.
        # Populated by init_demand_state(); unused for strategy A.
        self._aisle_demand_sum: dict[int, float]   = defaultdict(float)
        self._sku_demand_product: dict[int, float] = {}   # sku -> f * q

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

    def enqueue(self, carton: Carton, quantity: int | None = None) -> 'Inventory_Manager':
        """Queue one carton for bin placement.

        quantity=None (default) reads equilibrium_qty from the carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override the carton's own stock level (e.g. overstock sampling).
        """
        qty = quantity if quantity is not None else _equilibrium_qty(carton)
        for unit in viable_storage_units(carton, qty):
            self._queue.append(unit)
        # Count intake units as on-order so a reorder fired before they all reach
        # a bin does not over-order (they decrement back as they place).
        self._queued_qty[carton.sku] = self._queued_qty.get(carton.sku, 0) + qty
        if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
            self._originals[carton.sku] = carton
            self._initial_quantities[carton.sku] = qty
        self._drain()
        return self

    def enqueue_all(self, cartons: list[Carton], quantity: int | None = None) -> 'Inventory_Manager':
        """Queue a list of cartons for bin placement.

        quantity=None (default) reads equilibrium_qty from each carton — the normal
        path for inventory intake.  Pass an explicit integer only when you need
        to override every carton's stock level (e.g. overstock sampling).
        """
        for carton in cartons:
            qty = quantity if quantity is not None else _equilibrium_qty(carton)
            for unit in viable_storage_units(carton, qty):
                self._queue.append(unit)
            # Count intake units as on-order so a reorder fired before they all
            # reach a bin does not over-order (decremented back as they place).
            self._queued_qty[carton.sku] = self._queued_qty.get(carton.sku, 0) + qty
            if carton.sku not in self._originals and not getattr(carton, '_is_reorder', False):
                self._originals[carton.sku] = carton
                self._initial_quantities[carton.sku] = qty
        self._drain()
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
        self._bin_sku.clear()
        self._current_quantities.clear()
        self._sku_singleton_bins.clear()
        self._sku_pallet_bins.clear()

        sku_to_idx = affinity._sku_to_idx

        for bin_ in self._unavailable.values():
            if bin_.storage is not None:
                sku = bin_.storage.carton.sku
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
                if bin_.unit_type == 'singleton':
                    self._sku_singleton_bins[sku].add(bin_)
                else:
                    self._sku_pallet_bins[sku].add(bin_)

        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_lift_sum[aid] = affinity.sum_lift(list(sku_set))

    def init_travel_costs(self, wp: Any) -> None:
        """Precompute _W on every bin and build the per-aisle sorted secondary index.

        Must be called after init_lift_state() and before swapping to a
        load-aware assignment_fn built with build_load_*_assignment_fn(...,
        aisle_index=self._aisle_index).  After this call, _index_add and
        _index_remove maintain _aisle_index incrementally.
        """
        x_speed = wp.x_speed
        y_speed = wp.y_speed
        for b in self.warehouse.bins:
            b._W = x_speed * b.x_phys + y_speed * b.y_phys
        self._aisle_index.clear()
        for key, bins in self._index.items():
            by_aisle = self._aisle_index[key]
            for b in bins:
                bisect.insort(by_aisle[b.location[0]], b, key=lambda x: x._W)
        self._travel_costs_ready = True

    def init_demand_state(self, inventory: Any) -> None:
        """Populate demand-product lookup and per-aisle demand sums.

        Must be called after init_lift_state() so _aisle_sku_sets already
        reflects the actual placement.  Call once per strategy worker before
        swapping to a trip-cost assignment function.
        """
        self._sku_demand_product = {
            c.sku: c.demand.frequency * c.demand.quantity_rate
            for c in inventory.cartons
        }
        self._aisle_demand_sum.clear()
        for aid, sku_set in self._aisle_sku_sets.items():
            self._aisle_demand_sum[aid] = sum(
                self._sku_demand_product.get(s, 0.0) for s in sku_set
            )

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
                    counts = aisle_sku_counts[aid]
                    n      = counts.get(sku, 0)
                    if n > 1:
                        counts[sku] = n - 1
                    else:
                        counts.pop(sku, None)
                        aisle_sku_sets[aid].discard(sku)
                        idx = sku_to_idx.get(sku)
                        if idx is not None:
                            aisle_idx_sets[aid].discard(idx)
                        delta = 2.0 * delta_lift_idxs(sku, aisle_idx_sets[aid])
                        aisle_lift_sum[aid] = max(0.0, aisle_lift_sum[aid] - delta)
                        d = sku_demand_prod.get(sku, 0.0)
                        if d:
                            aisle_demand_sum[aid] = max(0.0, aisle_demand_sum[aid] - d)
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

        # Always drain — retries prior-batch stragglers too.
        if self._queue:
            if self.batch_assignment_fn is not None:
                self._drain_batch()
            else:
                self._drain()
        return triggered

    @property
    def available(self) -> list[Aisle.Bin]:
        return [b for bins in self._index.values() for b in bins]

    @property
    def unavailable(self) -> list[Aisle.Bin]:
        return list(self._unavailable.values())

    @property
    def queue_depth(self) -> int:
        return len(self._queue)

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
            bisect.insort(aisle_lst, bin_, key=lambda b: b._W)

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
            i = bisect.bisect_left(aisle_lst, bin_._W, key=lambda b: b._W)
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
        shc       = unit.carton.storage_handle_config
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
        sku = unit.carton.sku
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

    def _drain(self) -> None:
        """Place queued StorageUnit objects into warehouse bins (per-unit path).

        Units are processed one at a time via assignment_fn.  Used for
        immediate enqueue calls and as fallback when batch_assignment_fn is None.

        Placement failures:
          1. Repack into a smaller pallet size tier (retried immediately via appendleft).
          2. Fall back to singleton bins of the same carton type (same).
          3. If no bin is available, the unit stays in the queue (FIFO, no expiry).
        """
        pending: deque[StorageUnit] = deque()
        while self._queue:
            unit   = self._queue.popleft()
            carton = unit.carton
            sku    = carton.sku

            # B/C: aisle_index is active — assign derives BinKey from unit directly.
            # A: uniform assignment needs a real candidates list.
            candidates = (None if self._travel_costs_ready
                          else self._candidates(unit))
            bin_       = self.assignment_fn(unit, candidates)

            if bin_ is not None:
                self._execute_placement(unit, bin_)
            else:
                # No bin fits this unit.  Attempt rescues in priority order:
                #   1. Repack into smaller pallet size tier (existing logic).
                #   2. Fall back to singleton bins of the same carton type.
                #   3. If all else fails, track consecutive failures; after
                #      _MAX_DRAIN_RETRIES the unit is abandoned and the queued-
                #      count is decremented so a fresh reorder can fire next batch.
                repacked = False
                shc = carton.storage_handle_config

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
                        max_q = _max_qty_fitting_pallet_size(carton, size)
                        if max_q <= 0:
                            continue
                        remaining  = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_q)
                            new_units.append(Pallet(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True
                        break

                # ── rescue 2: singleton bins of same carton type ──────────────
                if not repacked:
                    max_sing = _sq_max(carton, Singleton)
                    avail = self._index.get((shc.handling, shc.category, None, 'singleton'))
                    if max_sing > 0 and avail:
                        remaining = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_sing)
                            new_units.append(Singleton(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True

                # ── no bin available — hold in queue, retry next batch ────────
                if not repacked:
                    pending.append(unit)
        self._queue = pending


    def _drain_batch(self) -> None:
        """Batch-optimal placement: sort units by pick-effort priority, then drain.

        Groups the queue by BinKey (handling, category, storage_size, unit_type)
        — the same key used by _candidates() — so units only compete with others
        in the same bin pool.  Within each group, batch_assignment_fn returns
        (unit, bin|None) pairs sorted by pick-effort priority so high-effort
        items claim the best (lowest-W) bins before lower-priority items.

        Units that cannot be placed go through the same rescue logic as _drain()
        and then to the pending queue for retry next batch.
        """
        from collections import defaultdict as _dd

        if not self._queue:
            return

        # Snapshot queue and group by BinKey
        groups: dict[tuple, list[StorageUnit]] = _dd(list)
        while self._queue:
            unit = self._queue.popleft()
            shc  = unit.carton.storage_handle_config
            key  = (shc.handling, shc.category, unit.storage_size, unit.unit_category)
            groups[key].append(unit)

        pending: deque[StorageUnit] = deque()

        for _key, units in groups.items():
            # Get batch assignments — high pick-effort units first
            assignments = self.batch_assignment_fn(units, self._candidates)   # type: ignore[misc]

            assigned_ids = set()
            for unit, bin_ in assignments:
                if bin_ is not None:
                    self._execute_placement(unit, bin_)
                    assigned_ids.add(id(unit))

            # Unassigned units: run through rescue logic then pending
            for unit, bin_ in assignments:
                if bin_ is not None:
                    continue
                carton = unit.carton
                sku    = carton.sku
                shc    = carton.storage_handle_config
                repacked = False

                # rescue 1: smaller pallet tier
                if unit.unit_category == 'pallet' and unit.storage_size is not None:
                    current_rank = _SIZE_RANKS.get(unit.storage_size, 99)
                    for size in _SIZES_DESCENDING:
                        if _SIZE_RANKS[size] >= current_rank:
                            continue
                        avail = self._index.get(
                            (shc.handling, shc.category, size, 'pallet'))
                        if not avail:
                            continue
                        max_q = _max_qty_fitting_pallet_size(carton, size)
                        if max_q <= 0:
                            continue
                        remaining = unit.quantity
                        new_units: list[StorageUnit] = []
                        while remaining > 0:
                            q = min(remaining, max_q)
                            new_units.append(Pallet(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True
                        break

                # rescue 2: singleton bins
                if not repacked:
                    max_sing = _sq_max(carton, Singleton)
                    avail = self._index.get((shc.handling, shc.category, None, 'singleton'))
                    if max_sing > 0 and avail:
                        remaining = unit.quantity
                        new_units = []
                        while remaining > 0:
                            q = min(remaining, max_sing)
                            new_units.append(Singleton(carton, q))
                            remaining -= q
                        delta = len(new_units) - 1
                        if delta:
                            self._queued_sku_counts[sku] = (
                                self._queued_sku_counts.get(sku, 1) + delta
                            )
                        for u in reversed(new_units):
                            self._queue.appendleft(u)
                        repacked = True

                if not repacked:
                    pending.append(unit)

        # Drain any repacked units immediately
        if self._queue:
            self._drain()

        # Merge pending back
        for u in pending:
            self._queue.append(u)


# ── load-aware assignment functions ───────────────────────────────────────────

def _aisle_extremal_bins(
    candidates: list[Any],
    x_speed   : float,
    y_speed   : float,
    minimize  : bool,
) -> tuple[dict[int, float], dict[int, Any]]:
    """Reduce candidates to one bin per aisle — the extremal-W representative.

    Proof of correctness
    --------------------
    For a fixed aisle (fixed ls, dl), the score tuple (delta_l2, old_L) is
    strictly monotone increasing in W = x_speed*x_phys + y_speed*y_phys:
      old_L  = W + λ(W/k)^γ ls           — increasing in W
      new_L  = W + λ(W/k)^γ (ls+dl)      — increasing in W
      delta_l2 = new_L² − old_L²          — product of two positive increasing
                                             functions, so also increasing in W

    Consequence: within a fixed aisle, the minimum-W bin always yields the
    minimum score (best for minimising) and the maximum-W bin always yields the
    maximum score (best for maximising).  Reducing O(N_bins) candidates to one
    representative per aisle is exact — no approximation.
    """
    best_W  : dict[int, float] = {}
    best_bin: dict[int, Any]   = {}
    for b in candidates:
        aid = b.location[0]
        W   = x_speed * b.x_phys + y_speed * b.y_phys
        if aid not in best_W or (W < best_W[aid] if minimize else W > best_W[aid]):
            best_W[aid]   = W
            best_bin[aid] = b
    return best_W, best_bin


def build_load_minimizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
    aisle_index    : dict | None = None,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily minimises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Dual-optimisation algorithm
    ---------------------------
    1. Reduce candidates to one bin per aisle (minimum-W bin) — exact by
       monotonicity of delta_l2 in W within a fixed aisle.
    2. Sort the O(N_aisles) representatives by W ascending.
    3. Evaluate aisles in W order with LAZY CSR queries (delta_lift computed
       only when the aisle is actually reached, not upfront for all aisles).
    4. Early termination: once the best score has delta_l2 = 0 (no affinity
       partners in the winning aisle), any remaining aisle with W ≥ best_old_L
       cannot improve — old_L ≥ W ≥ best_old_L and delta_l2 ≥ 0 = best_delta_l2.
       With sparse top-20 affinity most aisles have delta_lift = 0, so the
       termination typically fires after the first few aisles.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any] | None) -> Any | None:
        sku = unit.carton.sku

        # Step 1: one representative bin per aisle (min-W).
        # Fast path: derive BinKey from unit, read directly from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.carton.storage_handle_config
            unit_type = unit.unit_category
            if unit_type == 'singleton':
                by_aisle = aisle_index.get((shc.handling, shc.category, 'singleton', 'singleton'))
            else:
                min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
                by_aisle = None
                for size in reversed(_SIZES_DESCENDING):
                    if _SIZE_RANKS[size] >= min_rank:
                        by = aisle_index.get((shc.handling, shc.category, size, unit_type))
                        if by and any(by.values()):
                            by_aisle = by
                            break
            best_W: dict[int, float] = {}
            best_bin_map: dict[int, Any] = {}
            if by_aisle:
                for aid, lst in by_aisle.items():
                    if lst:
                        b = lst[0]  # sorted ascending — first is min-W
                        best_W[aid]       = b._W
                        best_bin_map[aid] = b
            if not best_W:
                return None
        else:
            if not candidates:
                return None
            best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)

        # Step 2: sort aisles by ascending min-W — O(N_aisles log N_aisles)
        sorted_aids = sorted(best_W, key=best_W.__getitem__)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('inf'), float('inf'))
        best_delta_lift : float               = 0.0

        # Step 3+4: lazy CSR queries + early termination
        for aid in sorted_aids:
            W = best_W[aid]

            # Early termination: best has delta_l2=0; remaining W ≥ best old_L
            # means score ≥ (0, W) ≥ (0, best_old_L) = best — prune the rest.
            if best_score[0] == 0.0 and W >= best_score[1]:
                break

            ls = aisle_lift_sum[aid]
            # Marginal lift is zero when the SKU already lives in this aisle —
            # it's already counted in aisle_lift_sum and adding a duplicate bin
            # does not create a new unique SKU pair.
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if score < best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        # Only update lift state when this is a genuinely new SKU for the aisle.
        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign


def build_load_maximizing_assignment_fn(
    params         : LoadParams,
    affinity       : AffinityStore,
    wp             : Any,
    aisle_sku_sets : dict[int, set[int]],
    aisle_lift_sum : dict[int, float],
    aisle_idx_sets : dict[int, set[int]],
    aisle_index    : dict | None = None,
) -> AssignmentFn:
    """Build an AssignmentFn that greedily maximises the L2 norm of predicted
    aisle loads  L_a = W_a + λ*(W_a/k)^γ * lift_sum.

    Same dual-optimisation structure as the minimising variant:
    one bin per aisle (min-W) + aisles sorted by W descending (largest
    travel cost first — highest potential delta_l2) + lazy CSR queries.
    No early termination for maximising: a low-W aisle can still win if it
    has very high affinity lift, so the sorted order does not guarantee
    pruning.  The one-bin-per-aisle reduction still eliminates O(N_bins)
    evaluations, leaving O(N_aisles) CSR queries.
    """
    lam    = params.lambda_
    k      = params.k
    gam    = params.gamma
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def _L(W: float, ls: float) -> float:
        return W + lam * (W / k) ** gam * ls

    def assign(unit: Any, candidates: list[Any] | None) -> Any | None:
        sku = unit.carton.sku

        # One representative bin per aisle (max-W) — exact by monotonicity.
        # Fast path: derive BinKey from unit, read from pre-sorted index.
        # Fallback: scan candidates list (used only when aisle_index is None).
        if aisle_index is not None:
            shc       = unit.carton.storage_handle_config
            unit_type = unit.unit_category
            if unit_type == 'singleton':
                by_aisle = aisle_index.get((shc.handling, shc.category, 'singleton', 'singleton'))
            else:
                min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
                by_aisle = None
                for size in reversed(_SIZES_DESCENDING):
                    if _SIZE_RANKS[size] >= min_rank:
                        by = aisle_index.get((shc.handling, shc.category, size, unit_type))
                        if by and any(by.values()):
                            by_aisle = by
                            break
            best_W: dict[int, float] = {}
            best_bin_map: dict[int, Any] = {}
            if by_aisle:
                for aid, lst in by_aisle.items():
                    if lst:
                        b = lst[-1]  # sorted ascending — last is max-W
                        best_W[aid]       = b._W
                        best_bin_map[aid] = b
            if not best_W:
                return None
        else:
            if not candidates:
                return None
            best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=False)

        # Sort descending: high-W aisles have the largest potential delta_l2
        sorted_aids = sorted(best_W, key=best_W.__getitem__, reverse=True)

        best_bin        : Any | None          = None
        best_aid        : int                 = -1
        best_score      : tuple[float, float] = (float('-inf'), float('-inf'))
        best_delta_lift : float               = 0.0

        for aid in sorted_aids:
            W  = best_W[aid]
            ls = aisle_lift_sum[aid]
            dl = (0.0 if sku in aisle_sku_sets[aid]
                  else 2.0 * affinity.delta_lift_idxs(sku, aisle_idx_sets[aid]))
            old_L    = _L(W, ls)
            new_L    = _L(W, ls + dl)
            delta_l2 = new_L * new_L - old_L * old_L
            score    = (delta_l2, old_L)

            if score > best_score:
                best_score      = score
                best_bin        = best_bin_map[aid]
                best_aid        = aid
                best_delta_lift = dl

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_lift_sum[best_aid] += best_delta_lift
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
        return best_bin

    return assign


# ── trip-cost assignment functions ────────────────────────────────────────────


def _demand_weighted_delta_lift(
    affinity       : AffinityStore,
    sku            : int,
    member_idx_set : set[int],
    freq_by_idx    : dict[int, float],
) -> float:
    """Sum of affinity(s, i) * f_i for all affinity partners i in the aisle.

    Uses the same CSR row-slice pattern as delta_lift_idxs but multiplies
    each affinity score by the partner's demand frequency.  This weights the
    co-location benefit by how often the partner actually appears in a batch,
    so rare-but-high-affinity pairs do not dominate over common low-affinity ones.
    """
    if not member_idx_set or affinity._matrix is None or sku not in affinity._sku_to_idx:
        return 0.0
    i     = affinity._sku_to_idx[sku]
    start = int(affinity._matrix.indptr[i])
    end   = int(affinity._matrix.indptr[i + 1])
    if start == end:
        return 0.0
    col_indices = affinity._matrix.indices[start:end]
    data        = affinity._matrix.data[start:end]
    return float(sum(
        d * freq_by_idx.get(int(ci), 0.0)
        for ci, d in zip(col_indices, data)
        if ci in member_idx_set
    ))


def build_trip_minimizing_assignment_fn(
    affinity         : AffinityStore,
    wp               : Any,
    aisle_sku_sets   : dict[int, set[int]],
    aisle_idx_sets   : dict[int, set[int]],
    aisle_demand_sum : dict[int, float],
    freq_by_idx      : dict[int, float],
    freq_by_sku      : dict[int, float],
    qty_by_sku       : dict[int, float],
    beta             : float = 1.0,
) -> AssignmentFn:
    """Build an AssignmentFn that minimises expected marginal aisle-trip cost.

    Objective
    ---------
    score(s, a) = f_s * W  -  beta * co_occur_a   (minimise)

      f_s        = demand frequency of the SKU being placed
      W          = x_speed*x_phys + y_speed*y_phys for the representative bin
      co_occur_a = sum_{i in aisle_a} affinity(s,i) * f_i  (demand-weighted lift)
      beta       = co-location subsidy weight (default 1.0)

    Tie-broken by (aisle_demand_sum + f_s*q_s): prefer less-loaded aisles to
    reduce makespan variance across pickers.

    Placement behaviour
    -------------------
    High-frequency SKUs are pulled toward low-W aisles.  SKUs whose
    high-frequency affinity partners already live in an aisle receive a
    co-location subsidy (beta * co_occur_a) that offsets the travel cost,
    collapsing correlated demand into fewer aisles per batch and reducing
    the dominant makespan driver: n_tasks (aisles visited per batch).
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Minimum-W representative per aisle: monotonicity holds for f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)

        best_score : tuple[float, float] = (float('inf'), float('inf'))
        best_bin   : Any | None = None
        best_aid   : int        = -1

        for aid, W in best_W.items():
            co_occur  = (0.0 if sku in aisle_sku_sets[aid]
                         else _demand_weighted_delta_lift(
                             affinity, sku, aisle_idx_sets[aid], freq_by_idx))
            primary   = f_s * W - beta * co_occur
            secondary = aisle_demand_sum[aid] + f_s * q_s
            score     = (primary, secondary)
            if score < best_score:
                best_score = score
                best_bin   = best_bin_map[aid]
                best_aid   = aid

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        return best_bin

    return assign


def build_trip_maximizing_assignment_fn(
    affinity         : AffinityStore,
    wp               : Any,
    aisle_sku_sets   : dict[int, set[int]],
    aisle_idx_sets   : dict[int, set[int]],
    aisle_demand_sum : dict[int, float],
    freq_by_idx      : dict[int, float],
    freq_by_sku      : dict[int, float],
    qty_by_sku       : dict[int, float],
    beta             : float = 1.0,
) -> AssignmentFn:
    """Build an AssignmentFn that maximises expected marginal aisle-trip cost.

    Mirror of build_trip_minimizing_assignment_fn.  Places high-frequency SKUs
    in distant aisles isolated from their affinity partners, maximising the
    expected number of distinct aisle visits per batch.  Intended as the
    strategy-C upper bound to contrast against B (minimising) and A (uniform).

    Tie-broken by -(aisle_demand_sum + f_s*q_s): prefer already-loaded aisles
    to concentrate demand and maximise picker load imbalance.
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)

        # Maximum-W representative per aisle: farther bins yield higher f_s * W.
        best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=False)

        best_score : tuple[float, float] = (float('-inf'), float('-inf'))
        best_bin   : Any | None = None
        best_aid   : int        = -1

        for aid, W in best_W.items():
            co_occur  = (0.0 if sku in aisle_sku_sets[aid]
                         else _demand_weighted_delta_lift(
                             affinity, sku, aisle_idx_sets[aid], freq_by_idx))
            primary   = f_s * W - beta * co_occur
            secondary = -(aisle_demand_sum[aid] + f_s * q_s)
            score     = (primary, secondary)
            if score > best_score:
                best_score = score
                best_bin   = best_bin_map[aid]
                best_aid   = aid

        if best_bin is None:
            return None

        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        return best_bin

    return assign


def build_uniform_aisle_trip_min_assignment_fn(wp, rng: random.Random | None = None) -> AssignmentFn:
    """Pick an aisle UNIFORMLY at random among the candidate aisles, then place in
    that aisle's minimum-travel-cost bin.

    Ablation control with no affinity, no demand, no priority — the candidate set
    from _candidates is already scoped to the unit's (handling, category, size,
    unit_type), so the random aisle is always a legal one.  Per-unit; pair with
    batch_assignment_fn = None for a FIFO drain.
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    _rng    = rng or random

    def assign(unit: Any, candidates: list[Any]) -> Any | None:
        if not candidates:
            return None
        # min-W bin per aisle; pick a random aisle, return its min-W bin.
        _best_W, best_bin_map = _aisle_extremal_bins(candidates, x_speed, y_speed, minimize=True)
        if not best_bin_map:
            return None
        return best_bin_map[_rng.choice(list(best_bin_map.keys()))]

    return assign


# ── batch assignment functions ────────────────────────────────────────────────

def _batch_assign_impl(
    units        : list,
    candidates_fn,
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta         : float,
    minimize     : bool,
    aisle_selector = None,
) -> list:
    """Shared core for batch-minimizing and batch-maximizing assignment.

    Priority formula (pick-effort x frequency + co-occurrence):
      priority = f_i x (pick_intercept + pick_weight_coef x log(weight)
                                        + pick_volume_coef x log(volume))
                 + beta x co_occur

    Sorted descending by priority; highest-priority unit claims the extremal-W
    bin first within each same-BinKey group.  minimize=True -> lowest-W bin
    (easiest access); minimize=False -> highest-W bin (hardest access).

    W_a (task workload) remains a measurement metric only; this formula
    drives bin placement at reorder time.
    """
    x_speed = wp.x_speed
    y_speed = wp.y_speed
    pi      = wp.pick_intercept
    pw      = wp.pick_weight_coef
    pv      = wp.pick_volume_coef

    # Fix 1: the co-occurrence term ranks each SKU against ALL currently-placed
    # SKU indices.  That union is identical for every unit in the wave (placement
    # is deferred to the caller, so aisle_idx_sets is static here), so build it
    # ONCE — not once per unit inside the sort key (which was O(U·Σ) per wave).
    all_idx = set().union(*aisle_idx_sets.values()) if aisle_idx_sets else set()

    def pick_effort_priority(unit) -> float:
        c    = unit.carton
        f_i  = c.demand.frequency
        w    = max(1, c.weight)
        v    = max(1, c.volume())
        effort = pi + pw * math.log(w) + pv * math.log(v)
        co_occur = beta * _demand_weighted_delta_lift(affinity, c.sku, all_idx, freq_by_idx)
        return f_i * effort + co_occur

    sorted_units = sorted(units, key=pick_effort_priority, reverse=True)
    result: list = []
    if not sorted_units:
        return result

    # Fix 2: the candidate pool is constant for this whole call (placement is
    # deferred) and every unit shares one BinKey, so compute it ONCE instead of
    # re-copying / re-scanning it per unit (was O(U·bucket_bins) per wave).
    # Pre-sort each aisle's bins by travel cost W (extremal-W first) and hand them
    # out by popping the head — equivalent to picking the extremal-W available bin
    # per aisle each step, but O(bucket log bucket + U·n_aisles) overall.
    cands = candidates_fn(sorted_units[0])
    W_of  = {id(b): x_speed * b.x_phys + y_speed * b.y_phys for b in cands}
    by_aisle: dict[int, deque] = {}
    for b in cands:
        by_aisle.setdefault(b.location[0], []).append(b)
    for aid, lst in by_aisle.items():
        lst.sort(key=lambda bb: W_of[id(bb)], reverse=not minimize)   # head = extremal-W
        by_aisle[aid] = deque(lst)
    head_bin = {aid: dq[0]          for aid, dq in by_aisle.items() if dq}
    head_W   = {aid: W_of[id(dq[0])] for aid, dq in by_aisle.items() if dq}

    for unit in sorted_units:
        if not head_W:
            result.append((unit, None))
            continue
        if aisle_selector is not None:
            best_aid = aisle_selector(head_W, head_bin)
        else:
            best_aid = (min if minimize else max)(head_W, key=head_W.__getitem__)
        chosen = head_bin[best_aid]

        sku = unit.carton.sku
        f_s = freq_by_sku.get(sku, 0.0)
        q_s = qty_by_sku.get(sku, 0.0)
        if sku not in aisle_sku_sets[best_aid]:
            aisle_sku_sets[best_aid].add(sku)
            idx = affinity._sku_to_idx.get(sku)
            if idx is not None:
                aisle_idx_sets[best_aid].add(idx)
            aisle_demand_sum[best_aid] += f_s * q_s

        # Advance the chosen aisle's head; drop it when exhausted.
        dq = by_aisle[best_aid]
        dq.popleft()
        if dq:
            head_bin[best_aid] = dq[0]
            head_W[best_aid]   = W_of[id(dq[0])]
        else:
            del head_bin[best_aid]
            del head_W[best_aid]

        result.append((unit, chosen))

    return result


def build_batch_minimizing_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Batch assignment: high pick-effort items get lowest-W (easiest) bins.

    Same parameter signature as build_trip_minimizing_assignment_fn.
    Assign manager.batch_assignment_fn = build_batch_minimizing_assignment_fn(...)
    """
    def batch_assign(units: list, candidates_fn) -> list:
        return _batch_assign_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
        )
    return batch_assign


def build_batch_maximizing_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
):
    """Batch assignment: high pick-effort items get highest-W (hardest) bins.

    Mirror of build_batch_minimizing_assignment_fn — strategy-C upper bound.
    """
    def batch_assign(units: list, candidates_fn) -> list:
        return _batch_assign_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=False,
        )
    return batch_assign


def build_batch_uniform_ranked_assignment_fn(
    affinity,
    wp,
    aisle_sku_sets   : dict,
    aisle_idx_sets   : dict,
    aisle_demand_sum : dict,
    freq_by_idx      : dict,
    freq_by_sku      : dict,
    qty_by_sku       : dict,
    beta             : float = 1.0,
    rng              : random.Random | None = None,
):
    """Batch assignment: rank units by pick-effort priority (same as
    batch-minimizing), but place each into a UNIFORM-RANDOM aisle's
    minimum-travel-cost bin instead of the globally min-W aisle.

    Ablation control: keeps the ranking (incl. demand-weighted lift) so the only
    difference from batch-minimizing is random vs W-optimal aisle selection —
    isolating whether the trip-min aisle choice is necessary.
    """
    _rng = rng or random

    def batch_assign(units: list, candidates_fn) -> list:
        return _batch_assign_impl(
            units, candidates_fn, affinity, wp,
            aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta, minimize=True,
            aisle_selector=lambda bw, bb: _rng.choice(list(bb.keys())),
        )
    return batch_assign
