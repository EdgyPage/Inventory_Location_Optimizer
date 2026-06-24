from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from Inventory_Builder import Inventory, AffMatrix
from Aisle_Storage import Aisle
from Warehouse_Builder import Warehouse
from Storage_Primitive import StorageCart
from Affinity_Store import AffinityStore

_CART_VOLUME: int = StorageCart.max_length * StorageCart.max_width * StorageCart.max_height

# Module-level cache keyed by affinity dict id so the O(|affinity|) partner-map
# build is paid only once per unique affinity object across all batch calls in a run.
_partner_map_cache: dict[int, dict[int, list[tuple[int, float]]]] = {}


def _get_partner_map(affinity) -> dict[int, list[tuple[int, float]]]:
    """Build sku -> [(partner_sku, lift), ...] from either a dict AffMatrix
    {(i, j): lift} or an AffinityStore (CSR matrix).  Cached per affinity-object id
    so the O(|affinity|) build is paid once per run.  `None` ⇒ {} (pure demand weighting)."""
    if affinity is None:
        return {}
    key = id(affinity)
    cached = _partner_map_cache.get(key)
    if cached is not None:
        return cached
    pm: dict[int, list[tuple[int, float]]] = defaultdict(list)
    if isinstance(affinity, dict):
        for (si, sj), v in affinity.items():
            if si < sj:
                pm[si].append((sj, v))
                pm[sj].append((si, v))
    else:
        # AffinityStore: the CSR matrix is symmetric, so each row i already lists
        # all of sku_i's partners — no si<sj dedup needed.
        m = getattr(affinity, '_matrix', None)
        if m is not None:
            sku_to_idx = affinity._sku_to_idx
            idx_to_sku = {i: s for s, i in sku_to_idx.items()}
            indptr, indices, data = m.indptr, m.indices, m.data
            for s, i in sku_to_idx.items():
                start, end = int(indptr[i]), int(indptr[i + 1])
                if end > start:
                    pm[s] = [(idx_to_sku[int(indices[j])], float(data[j]))
                             for j in range(start, end)]
    result = dict(pm)
    _partner_map_cache[key] = result
    return result


@dataclass
class BatchConfig:
    inventory_size: int
    mean_fraction: float = 0.20   # centre of num_skus distribution as fraction of inventory
    std_fraction: float  = 0.05   # spread of num_skus distribution as fraction of inventory


def _lift_weighted_sample(
    candidates: list,
    k: int,
    affinity: 'AffMatrix | AffinityStore | None',
    rng: random.Random | None = None,
) -> list:
    """Sample k distinct items from candidates without replacement.

    Weight of a candidate B is the conditional-demand model
        weight(B) = demand.relative_frequency(B) · Π lift(A, B)
    over the already-selected partners A of B.  Lift enters MULTIPLICATIVELY (its
    natural sense: lift = 1 = independence ⇒ no change), so demand and affinity stay
    commensurable.  affinity=None ⇒ pure demand weighting (empty partner map).

    Uses a numpy cumsum draw per step + the module-level partner-map cache.  Pass `rng`
    (a `random.Random`) to draw from a dedicated stream; default `None` uses the global
    module.
    """
    r = rng or random
    partner_map = _get_partner_map(affinity)

    n = len(candidates)
    sku_to_idx: dict[int, int] = {c.sku: i for i, c in enumerate(candidates)}
    base_weights = np.fromiter(
        (c.demand.relative_frequency for c in candidates), dtype=np.float64, count=n
    )
    lift_mult = np.ones(n, dtype=np.float64)   # Π lift(A,B) over already-selected partners
    active = np.ones(n, dtype=bool)
    w = np.empty(n, dtype=np.float64)
    selected: list = []

    for _ in range(k):
        np.multiply(base_weights, lift_mult, out=w)   # weight(B) = freq(B) · Π lift(A,B)
        w[~active] = 0.0
        total: float = float(w.sum())
        if total <= 0.0:
            break
        cumw = np.cumsum(w)
        idx = int(np.searchsorted(cumw, r.uniform(0.0, total)))
        if idx >= n:
            idx = n - 1
        chosen = candidates[idx]
        selected.append(chosen)
        active[idx] = False
        for partner_sku, lv in partner_map.get(chosen.sku, []):
            j = sku_to_idx.get(partner_sku)
            if j is not None:
                lift_mult[j] *= lv

    return selected


class Batch:
    def __init__(
        self,
        config: BatchConfig,
        inventory: Inventory,
        affinity: AffMatrix | AffinityStore | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        # Dedicated batch stream: pass `rng=random.Random(seed_batches + i)` so batch
        # i is a pure function of (inventory, affinity, config) and is identical across
        # arms, immune to whatever placement/reorder randomness ran first. Default
        # `None` keeps the global `random` module (back-compatible).
        r = rng or random

        # Batch SIZE (distinct SKUs / order lines) ~ Normal(mean_fraction·N,
        # std_fraction·N), bounded to [1, N].  No eligibility cutoff: the size follows the
        # requested mean/sd directly (a small "low-pick" batch from the left tail is fine),
        # and WHICH SKUs fill it is demand- (relative_frequency) and lift-weighted below.
        mean = config.mean_fraction * config.inventory_size
        std  = config.std_fraction  * config.inventory_size
        self.num_skus: int = max(1, min(config.inventory_size, round(r.gauss(mean, std))))

        candidates = inventory.orders           # every SKU is eligible
        k = min(self.num_skus, len(candidates))

        # Selection is always demand-weighted (by demand.relative_frequency); an
        # AffinityStore/dict additionally multiplies in lift toward partners of already-
        # chosen SKUs.  affinity=None ⇒ pure demand weighting (empty partner map).  An
        # unhandled affinity type raises rather than silently degrading.
        if k <= 0:
            selected = []
        elif affinity is None or isinstance(affinity, (dict, AffinityStore)):
            selected = _lift_weighted_sample(candidates, k, affinity, rng=r)
        else:
            raise TypeError(
                f'Batch affinity must be dict, AffinityStore, or None; got '
                f'{type(affinity).__name__}. Refusing to silently fall back to '
                f'uniform sampling.')

        self.items: dict[int, int] = {c.sku: max(1, c.demand.sample(rng=r)) for c in selected}

        # For a plain dict, store it directly for use in analytics.
        # For AffinityStore, lift_sum is computed on-demand per task in
        # extract_task_stats — pre-loading all batch pairs would fetch O(k²) rows
        # from a potentially huge DB on every batch creation.
        self.aff: AffMatrix = affinity if isinstance(affinity, dict) else {}


class Task:
    """Single-aisle ordered pick sequence derived from a Batch."""

    def __init__(
        self,
        aisle_id: int,
        path: list[Aisle.Bin],
        items: dict[int, int],
    ) -> None:
        self.aisle_id: int          = aisle_id
        self.path: list[Aisle.Bin]  = path         # bins in visit order
        self.items: dict[int, int]  = items         # sku -> quantity for this aisle
        x_trav = 0.0
        y_trav = 0.0
        for i in range(len(path) - 1):
            x_trav += abs(path[i].x_phys - path[i+1].x_phys)
            y_trav += abs(path[i].y_phys - path[i+1].y_phys)
        self.x_traversed: float = x_trav   # physical units
        self.y_traversed: float = y_trav   # physical units
        # Snapshot the (weight, volume, qty, y_phys) pick lines NOW, while the path bins
        # still hold stock.  The sim depletes these bins (storage→None) during run(), so
        # computing them later (in extract_task_stats) would drop emptied bins and zero out
        # the analytical workload W.  Captured here so W reflects the real picks at all heights.
        self.pick_lines: list[tuple[int, int, int, float]] = [
            (b.storage.order.weight, b.storage.order.volume(),
             items[b.storage.order.sku], b.y_phys)
            for b in path
            if b.storage is not None and b.storage.order.sku in items
        ]
        # Build volume lookup from path bins.  A SKU in items may have no bin
        # in this path when all its bins are pending reclaim (emptied last batch).
        # Fall back to the order volume from the first bin found anywhere in the
        # path for that SKU — missing SKUs keep volume=0 which underestimates
        # carts_required, so use a secondary lookup from any path bin.
        sku_to_vol: dict[int, int] = {}
        for b in path:
            if b.storage is not None:
                sku = b.storage.order.sku
                if sku not in sku_to_vol:
                    sku_to_vol[sku] = b.storage.order.volume()
        # For SKUs in items not covered by path bins, approximate with any
        # non-None path bin's order volume (they share the same aisle type,
        # so dimensions are at least in the same order of magnitude).
        fallback_vol = next(
            (b.storage.order.volume() for b in path if b.storage is not None), 1
        )
        total_vol: int = sum(
            sku_to_vol.get(sku, fallback_vol) * qty for sku, qty in items.items()
        )
        self.carts_required: int = math.ceil(total_vol / _CART_VOLUME) if total_vol > 0 else 0

    @staticmethod
    def from_batch(batch: Batch, warehouse: Warehouse, manager=None) -> list[Task]:
        """Decompose a Batch into one Task per aisle.

        For each SKU in the batch, singleton bins are drained before pallet bins
        so that forward-pick locations are always preferred over reserve locations.

        If manager is provided its pre-built _sku_singleton_bins/_sku_pallet_bins
        dicts are used directly, skipping the O(N_all_bins) warehouse scan that
        would otherwise rebuild the index on every batch.
        """
        # Distribute each batch quantity: drain singleton bins before pallet bins
        bin_pick: defaultdict[Aisle.Bin, int] = defaultdict(int)

        if manager is not None:
            # O(N_batch_skus) — uses maintained index, no full warehouse scan
            for sku, qty in batch.items.items():
                remaining: int = qty
                for bin_ in manager._sku_singleton_bins.get(sku, []):
                    if remaining <= 0:
                        break
                    available: int = bin_.storage.quantity if bin_.storage is not None else 0
                    take: int = min(remaining, available)
                    if take > 0:
                        bin_pick[bin_] += take
                        remaining -= take
                for bin_ in manager._sku_pallet_bins.get(sku, []):
                    if remaining <= 0:
                        break
                    available = bin_.storage.quantity if bin_.storage is not None else 0
                    take = min(remaining, available)
                    if take > 0:
                        bin_pick[bin_] += take
                        remaining -= take
        else:
            # Fallback: O(N_all_bins) scan — used when no manager is available
            sku_to_bins: dict[int, list[Aisle.Bin]] = defaultdict(list)
            for bin_ in warehouse.bins:
                if bin_.storage is not None:
                    sku_to_bins[bin_.storage.order.sku].append(bin_)
            for bins in sku_to_bins.values():
                bins.sort(key=lambda b: 0 if b.unit_type == 'singleton' else 1)
            for sku, qty in batch.items.items():
                remaining = qty
                for bin_ in sku_to_bins.get(sku, []):
                    if remaining <= 0:
                        break
                    available = bin_.storage.quantity if bin_.storage is not None else 0
                    take = min(remaining, available)
                    if take > 0:
                        bin_pick[bin_] += take
                        remaining -= take

        aisle_bins:  dict[int, list[Aisle.Bin]] = defaultdict(list)
        aisle_items: dict[int, dict[int, int]]  = defaultdict(dict)
        for bin_, take in bin_pick.items():
            aisle_id = bin_.location[0]
            aisle_bins[aisle_id].append(bin_)
            sku = bin_.storage.order.sku  # type: ignore[union-attr]
            aisle_items[aisle_id][sku] = aisle_items[aisle_id].get(sku, 0) + take

        tasks = []
        for aisle_id, bins in aisle_bins.items():
            path = _plan_aisle_path(bins)
            if path:   # guard: skip tasks with empty paths (all bins emptied mid-build)
                tasks.append(Task(aisle_id, path, aisle_items[aisle_id]))
        return tasks


def _plan_aisle_path(bins: list[Aisle.Bin]) -> list[Aisle.Bin]:
    """Order bins by bayX; within each x-column traverse bayY in whichever
    direction (ascending or descending) minimises entry distance from the
    current position."""
    by_x: dict[int, list[Aisle.Bin]] = defaultdict(list)
    for b in bins:
        by_x[b.location[1]].append(b)

    path: list[Aisle.Bin] = []
    current_y: int = 0

    for x in sorted(by_x.keys()):
        group = sorted(by_x[x], key=lambda b: b.location[2])
        y_low  = group[0].location[2]
        y_high = group[-1].location[2]

        if abs(current_y - y_low) <= abs(current_y - y_high):
            ordered = group                  # ascending y
        else:
            ordered = list(reversed(group))  # descending y

        path.extend(ordered)
        current_y = ordered[-1].location[2]

    return path
