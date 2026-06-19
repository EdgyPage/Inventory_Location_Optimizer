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
RankedAssignmentFn = Callable[
    [list[StorageUnit], Callable[[StorageUnit], list[Aisle.Bin]]],
    list[tuple[StorageUnit, 'Aisle.Bin | None']],
]


class Placement:
    """One named placement policy — the single object a strategy hands the manager.

    ``place_one`` (per-unit: ``(unit, candidates) -> bin|None``) is ALWAYS present; it
    drives the per-unit drain (initial stock, FIFO/cohesion reorders) and places the
    stragglers a ranked wave leaves behind.  ``place_wave``
    (``(units, candidates_fn) -> [(unit, bin|None)]``), when present, makes this a
    RANKED policy: a whole BinKey group is placed at once in pick-effort order.

    Because every strategy sets exactly one ``mgr.placement`` (never None), the ranked
    drain is just a policy that also carries a ``place_wave`` — no special-casing, and
    a future ranked-cohesion policy is expressible the same way.
    """
    __slots__ = ('name', 'place_one', 'place_wave', 'uses_aisle_index', 'order_score')

    def __init__(self, name: str, place_one: AssignmentFn,
                 place_wave: 'RankedAssignmentFn | None' = None,
                 order_score: 'Callable[[Any], float] | None' = None) -> None:
        self.name             = name
        self.place_one        = place_one
        self.place_wave       = place_wave
        # the per-unit fn declares whether it reads mgr._aisle_index (coupling guard)
        self.uses_aisle_index = bool(getattr(place_one, 'uses_aisle_index', False))
        # Per-policy enqueue ordering: (unit)->float, sorted DESCENDING before placement.
        # Decouples queue order from the placement impl so a policy is never forced into
        # an ordering that fights it.  None ⇒ the ranked wave's default pick-effort order.
        self.order_score      = order_score

    @property
    def is_ranked(self) -> bool:
        return self.place_wave is not None

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
        self.name: str = ''        # e.g. inventory_initial_assignment_reslot; for graph titles
        # The single placement policy.  Defaults to per-unit uniform; a strategy's
        # build() swaps in its own (FIFO/cohesion = per-unit; trip/rank = ranked wave
        # + per-unit straggler fallback).  _drain() dispatches on placement.is_ranked.
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

        # Churn counters (read + reset per batch via pop_churn): reload evictions
        # (Capacity_Reloader.requeue_bin) and reorder unit placements this batch
        # (bumped in _execute_placement).
        self._reload_moves: int       = 0
        self._reorder_placements: int = 0

        # Incremental Sigma f*D tracker — avoids a full occupied-bin scan per batch.
        # None until enable_sigma_fd() binds the freq map + speeds; then maintained
        # on every placement (+), pick-empty / eviction (−).
        self._sigma_freq: dict | None = None
        self._sigma_x: float = 0.0
        self._sigma_y: float = 0.0
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
        # Per-aisle placed-member COLUMN positions: aisle → list of (x_phys, sku_idx).
        # Lets co-demand compaction/expansion score a candidate bin by its column
        # distance to an entering SKU's already-placed affinity partners.
        self._aisle_member_pos: dict[int, list[tuple[float, int]]] = defaultdict(list)
        # id(bin) → sku; needed for lift removal after storage is cleared.
        self._bin_sku: dict[int, int] = {}

        # Demand-based state for trip-cost assignment functions.
        # Populated by init_demand_state(); unused for strategy A.
        self._aisle_demand_sum: dict[int, float]   = defaultdict(float)
        self._sku_demand_product: dict[int, float] = {}   # sku -> f * q

        # Cost-weighted twin of the demand state: expected picking labor.
        # _sku_pick_load_product[sku] = f * q * cost1 (= carton.expected_labor);
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
        self._aisle_member_pos.clear()
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
                    self._aisle_member_pos[aid].append((bin_.x_phys, idx))
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
        x_speed = wp.x_speed
        y_speed = wp.y_speed
        for b in self.warehouse.bins:
            b._D = x_speed * b.x_phys + y_speed * b.y_phys
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
        (_sku_pick_load_product = f*q*cost1 = carton.expected_labor, and the
        per-aisle _aisle_pick_load_sum) used by the Rank_labor balance selector.
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

        if wp is not None:
            # carton.expected_labor reads labor_cost, which the worker sets via
            # compute_labor_cost() before this call.
            self._sku_pick_load_product = {
                c.sku: c.expected_labor for c in inventory.cartons
            }
            self._aisle_pick_load_sum.clear()
            for aid, sku_set in self._aisle_sku_sets.items():
                self._aisle_pick_load_sum[aid] = sum(
                    self._sku_pick_load_product.get(s, 0.0) for s in sku_set
                )

    # ── optimal layout (pure global-D) + Sigma f*D objective ─────────────────

    def _take_optimal_bin(self, bins_by_key: dict, handling: str, category: str,
                          unit_type: str, unit: StorageUnit) -> 'Aisle.Bin | None':
        """Pop the lowest-D available bin for *unit*, smallest fitting tier first
        (same tier logic as _candidates, spilling UP only when a tier is empty).
        bins_by_key maps BinKey -> deque of bins pre-sorted by D ascending."""
        if unit_type == 'singleton':
            dq = bins_by_key.get((handling, category, 'singleton', 'singleton'))
            return dq.popleft() if dq else None
        min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
        for size in reversed(_SIZES_DESCENDING):          # small -> large
            if _SIZE_RANKS[size] >= min_rank:
                dq = bins_by_key.get((handling, category, size, unit_type))
                if dq:
                    return dq.popleft()
        return None

    def _optimal_assign(self, cartons: list[Carton], freq_of: dict,
                        x_speed: float, y_speed: float, place: bool) -> float:
        """Pure-global-D optimal layout: per BinKey class, assign the highest
        pick-frequency units to the lowest-D bins (rearrangement-inequality optimum
        for within-aisle travel).  Returns the minimal Sigma f*D.  When place=True,
        commits each unit to its bin and registers originals so reorders work."""
        D = lambda b: x_speed * b.x_phys + y_speed * b.y_phys
        bins_by_key: dict = defaultdict(deque)
        for b in sorted(self.warehouse.bins, key=D):       # D ascending => low-D heads
            bins_by_key[self._key(b)].append(b)

        if place:
            for c in cartons:
                if c.sku not in self._originals and not getattr(c, '_is_reorder', False):
                    self._originals[c.sku] = c
                    self._initial_quantities[c.sku] = _equilibrium_qty(c)

        # Group units by (handling, category, unit_type); place hottest first so
        # that within each size tier the hottest unit claims the lowest-D bin.
        groups: dict = defaultdict(list)
        for c in cartons:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                shc = unit.carton.storage_handle_config
                groups[(shc.handling, shc.category, unit.unit_category)].append(unit)

        sigma = 0.0
        for (handling, category, utype), units in groups.items():
            units.sort(key=lambda u: freq_of.get(u.carton.sku, 0.0), reverse=True)
            for unit in units:
                b = self._take_optimal_bin(bins_by_key, handling, category, utype, unit)
                if b is None:
                    continue                               # warehouse full for this tier
                sigma += freq_of.get(unit.carton.sku, 0.0) * D(b)
                if place:
                    self._execute_placement(unit, b)
        return sigma

    def place_optimal(self, cartons: list[Carton], freq_of: dict,
                      x_speed: float, y_speed: float) -> float:
        """Stock the warehouse at the pure-global-D optimal layout.  Returns the
        optimal Sigma f*D.  Bumps _reorder_placements per unit (the worker discards
        the initial-stock churn with a pop_churn() before the batch loop)."""
        return self._optimal_assign(cartons, freq_of, x_speed, y_speed, place=True)

    def optimal_sigma_fd(self, cartons: list[Carton], freq_of: dict,
                         x_speed: float, y_speed: float) -> float:
        """The minimal achievable Sigma f*D for this warehouse + inventory (the
        yardstick).  Pure computation — does not mutate manager state."""
        return self._optimal_assign(cartons, freq_of, x_speed, y_speed, place=False)

    # ── full-labor optimum (travel + height handling) + optimal map ──────────

    @staticmethod
    def _handle_var(carton, wp) -> float:
        """Per-unit weight/volume handling term v_s (no intercept, no quantity) — the
        height-scalable part of pick effort.  Mirrors Carton.compute_labor_cost."""
        return (wp.pick_weight_coef * math.log(max(carton.weight, 1))
                + wp.pick_volume_coef * math.log(max(carton.volume(), 1)))

    def _optimal_work_assign(self, cartons: list[Carton], freq_of: dict,
                             qty_of: dict, wp) -> tuple[float, dict]:
        """Exact minimal expected WORK (travel + height-weighted handling) for this
        warehouse + inventory, and each SKU's optimal preferred score.

        Per BinKey class solves the assignment  min Σ f_s·D_b + (f_s·q_s·v_s)·M(y_b)
        exactly (scipy LAP) — the rearrangement/transportation optimum that drives the
        highest height-sensitivity (f·q·v) units to the lowest-M bins and the highest
        frequency to the lowest-D bins.  Returns:
          W*          = Σ (assigned bins) f·(intercept + D_b) + (f·q·v)·M_b
                        (per occupied-bin convention, matching current_sigma_fd)
          sku_target  = {sku → pref(b*)} where b* is the SKU's assigned bin and
                        pref(b) = D_b + M(y_b)·V_REF is the quantity-free bin basis.
        Pure computation — does not mutate manager state.
        """
        brackets  = getattr(wp, 'height_brackets', ())
        xs, ys    = wp.x_speed, wp.y_speed
        intercept = wp.pick_intercept

        def _D(b):  return xs * b.x_phys + ys * b.y_phys
        def _M(b):
            y = b.y_phys
            for thr, mult in brackets:
                if y < thr:
                    return mult
            return brackets[-1][1] if brackets else 1.0

        # quantity-free reference handling so the bin basis pref carries a realistic
        # height-penalty magnitude (V_REF ~ a typical v_s); falls back to 1.0.
        vs = [self._handle_var(c, wp) for c in cartons]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0
        v_by_sku = {c.sku: v for c, v in zip(cartons, vs)}

        bins_by_key: dict = defaultdict(list)
        for b in self.warehouse.bins:
            bins_by_key[self._key(b)].append(b)

        units_by_key: dict = defaultdict(list)
        for c in cartons:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                if unit.unit_category == 'singleton':
                    key = (c.storage_handle_config.handling,
                           c.storage_handle_config.category, 'singleton', 'singleton')
                else:
                    key = (c.storage_handle_config.handling,
                           c.storage_handle_config.category,
                           unit.storage_size, unit.unit_category)
                units_by_key[key].append(unit)

        W_var = 0.0
        sku_target: dict[int, list] = defaultdict(list)
        _LAP_CAP = 1200            # exact LAP up to this many units/class; else greedy

        for key, units in units_by_key.items():
            bins = bins_by_key.get(key)
            if not bins:
                continue
            n = len(units)
            a = [freq_of.get(u.carton.sku, 0.0) for u in units]                  # α_s = f
            b_ = [freq_of.get(u.carton.sku, 0.0) * qty_of.get(u.carton.sku, 0.0)
                  * v_by_sku.get(u.carton.sku, 0.0) for u in units]              # β_s = f·q·v
            # candidate bins: lowest-D per height bracket, capped at n (others dominated)
            by_m: dict = defaultdict(list)
            for bn in bins:
                by_m[_M(bn)].append(bn)
            cand: list = []
            for m, lst in by_m.items():
                lst.sort(key=_D)
                cand.extend(lst[:n])
            m_cnt = len(cand)
            if m_cnt == 0:
                continue
            Dc = [_D(bn) for bn in cand]
            Mc = [_M(bn) for bn in cand]

            assigned: list[tuple[int, int]] = []     # (unit_idx, cand_idx)
            if n <= _LAP_CAP and n * m_cnt <= 4_000_000:
                try:
                    import numpy as _np
                    from scipy.optimize import linear_sum_assignment
                    C = (_np.asarray(a)[:, None] * _np.asarray(Dc)[None, :]
                         + _np.asarray(b_)[:, None] * _np.asarray(Mc)[None, :])
                    ri, ci = linear_sum_assignment(C)
                    assigned = list(zip(ri.tolist(), ci.tolist()))
                except Exception:
                    assigned = []
            if not assigned:                          # greedy fallback (feasible, near-opt)
                order = sorted(range(n), key=lambda i: b_[i] + a[i], reverse=True)
                pools: dict = defaultdict(deque)
                for j, bn in enumerate(cand):
                    pools[Mc[j]].append(j)
                for m in pools:
                    pools[m] = deque(sorted(pools[m], key=lambda j: Dc[j]))
                for i in order:
                    best = None
                    for m, dq in pools.items():
                        if not dq:
                            continue
                        j = dq[0]
                        cost = a[i] * Dc[j] + b_[i] * Mc[j]
                        if best is None or cost < best[0]:
                            best = (cost, m, j)
                    if best is None:
                        continue
                    _, m, j = best
                    pools[m].popleft()
                    assigned.append((i, j))

            for i, j in assigned:
                # per occupied-bin convention (matches current_sigma_fd): f·(intercept+D)
                # + f·q·v·M.  intercept·f is bin-independent so it doesn't affect the argmin,
                # but is included so W* is comparable to a realised layout's work.
                W_var += a[i] * (intercept + Dc[j]) + b_[i] * Mc[j]
                pref = Dc[j] + Mc[j] * v_ref
                sku_target[units[i].carton.sku].append(pref)

        target = {sku: sum(p) / len(p) for sku, p in sku_target.items() if p}
        return W_var, target

    def optimal_work(self, cartons: list[Carton], freq_of: dict,
                     qty_of: dict, wp) -> float:
        """Minimal achievable expected work W* (travel + height handling) — the floor
        yardstick.  Pure computation; does not mutate manager state."""
        return self._optimal_work_assign(cartons, freq_of, qty_of, wp)[0]

    def build_optimal_map(self, cartons: list[Carton], freq_of: dict,
                          qty_of: dict, wp) -> float:
        """Build the optimal map (the score-match basis) on this manager and return W*.
        Sets `_bin_pref` (quantity-free location score for EVERY bin) and `_map_target`
        (each SKU's optimal preferred score).  Call once at warehouse build, after the
        inventory is assigned."""
        brackets = getattr(wp, 'height_brackets', ())
        xs, ys = wp.x_speed, wp.y_speed

        def _M(b):
            y = b.y_phys
            for thr, mult in brackets:
                if y < thr:
                    return mult
            return brackets[-1][1] if brackets else 1.0

        vs = [self._handle_var(c, wp) for c in cartons]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0
        self._bin_pref = {
            id(b): (xs * b.x_phys + ys * b.y_phys) + _M(b) * v_ref
            for b in self.warehouse.bins
        }
        w_star, self._map_target = self._optimal_work_assign(cartons, freq_of, qty_of, wp)
        return w_star

    def current_sigma_fd(self, freq_of: dict, x_speed: float, y_speed: float) -> float:
        """Realised demand-weighted within-aisle travel = sum over occupied bins of
        freq[sku] * D(bin).  The primary convergence metric."""
        s = 0.0
        for b in self._unavailable.values():
            st = b.storage
            if st is not None:
                s += (freq_of.get(st.carton.sku, 0.0)
                      * (x_speed * b.x_phys + y_speed * b.y_phys))
        return s

    def enable_sigma_fd(self, freq_of: dict, x_speed: float, y_speed: float) -> None:
        """Bind the freq map + speeds and seed the incremental Sigma f*D from a
        single full scan.  Afterwards tracked_sigma_fd() is O(1): the running sum is
        maintained on every placement / pick-empty / eviction."""
        self._sigma_freq = freq_of
        self._sigma_x = x_speed
        self._sigma_y = y_speed
        self._sigma_fd = self.current_sigma_fd(freq_of, x_speed, y_speed)

    def tracked_sigma_fd(self) -> float:
        """The incrementally-maintained Sigma f*D (see enable_sigma_fd).  O(1)."""
        return self._sigma_fd

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
            counts = self._aisle_sku_counts[aid]
            n      = counts.get(sku, 0)
            if n > 1:
                counts[sku] = n - 1
            elif n == 1:
                counts.pop(sku, None)
                self._aisle_sku_sets[aid].discard(sku)
                idx = self._affinity._sku_to_idx.get(sku)
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
        self._reorder_placements += 1
        if self._sigma_freq is not None:
            self._sigma_fd += (self._sigma_freq.get(sku, 0.0)
                               * (self._sigma_x * bin_.x_phys + self._sigma_y * bin_.y_phys))

    def _drain(self) -> None:
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
        if not self._queue:
            return
        if self.placement.is_ranked:
            self._drain_ranked()
        else:
            self._drain_per_unit()

    def _drain_per_unit(self) -> None:
        """Place queued StorageUnit objects one at a time via placement.place_one.

        Used for initial enqueue, FIFO/cohesion reorders, and the stragglers a ranked
        wave leaves behind.  The coupling guard runs in _drain() (the single entry).

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
            bin_       = self.placement.place_one(unit, candidates)

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


    def _drain_ranked(self) -> None:
        """Ranked placement: sort units by pick-effort priority, then drain.

        Groups the queue by BinKey (handling, category, storage_size, unit_type)
        — the same key used by _candidates() — so units only compete with others
        in the same bin pool.  Within each group, placement.place_wave returns
        (unit, bin|None) pairs sorted by pick-effort priority so high-effort
        items claim the best (lowest-D) bins before lower-priority items.

        Units that cannot be placed go through the same rescue logic as
        _drain_per_unit() and then to the pending queue for retry next batch.
        """
        if not self._queue:
            return

        # Snapshot queue and group by BinKey
        groups: dict[tuple, list[StorageUnit]] = defaultdict(list)
        while self._queue:
            unit = self._queue.popleft()
            shc  = unit.carton.storage_handle_config
            key  = (shc.handling, shc.category, unit.storage_size, unit.unit_category)
            groups[key].append(unit)

        pending: deque[StorageUnit] = deque()

        for _key, units in groups.items():
            # Get ranked assignments — high pick-effort units first
            assignments = self.placement.place_wave(units, self._candidates)   # type: ignore[misc]

            for unit, bin_ in assignments:
                if bin_ is not None:
                    self._execute_placement(unit, bin_)

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

        # Drain any repacked stragglers immediately via the per-unit path
        # (NOT self._drain(), which would re-dispatch back into this ranked wave).
        if self._queue:
            self._drain_per_unit()

        # Merge pending back
        for u in pending:
            self._queue.append(u)


