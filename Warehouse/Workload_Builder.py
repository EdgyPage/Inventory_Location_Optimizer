from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

from Inventory_Builder import Inventory, AffMatrix
from Aisle_Storage import Aisle
from Warehouse_Builder import Warehouse
from Storage_Primitive import StorageCart

_CART_VOLUME: int = StorageCart.max_length * StorageCart.max_width * StorageCart.max_height


@dataclass
class BatchConfig:
    inventory_size: int
    mean_fraction: float = 0.20   # centre of num_skus distribution as fraction of inventory
    std_fraction: float  = 0.05   # spread of num_skus distribution as fraction of inventory


def _lift_weighted_sample(
    candidates: list,
    k: int,
    affinity: AffMatrix,
) -> list:
    """Sample k items from candidates, weighting each by demand.frequency plus
    cumulative lift to already-selected SKUs.  High-lift partners of chosen SKUs
    become progressively more likely to be drawn next."""
    remaining = list(candidates)
    selected: list = []
    selected_skus: set[int] = set()

    while len(selected) < k and remaining:
        weights = [
            c.demand.frequency + sum(affinity.get((s, c.sku), 0.0) for s in selected_skus)
            for c in remaining
        ]
        chosen = random.choices(remaining, weights=weights, k=1)[0]
        selected.append(chosen)
        selected_skus.add(chosen.sku)
        remaining.remove(chosen)

    return selected


class Batch:
    def __init__(
        self,
        config: BatchConfig,
        inventory: Inventory,
        affinity: AffMatrix | None = None,
    ) -> None:
        self.config = config

        mean = config.mean_fraction * config.inventory_size
        std  = config.std_fraction  * config.inventory_size
        self.num_skus: int    = max(1, min(config.inventory_size, round(random.gauss(mean, std))))
        self.threshold: float = random.random()

        candidates = [c for c in inventory.cartons if c.demand.frequency > self.threshold]
        k = min(self.num_skus, len(candidates))

        if affinity and k > 0:
            selected = _lift_weighted_sample(candidates, k, affinity)
        else:
            selected = random.sample(candidates, k) if k > 0 else []

        self.items: dict[int, int] = {c.sku: max(1, c.demand.sample()) for c in selected}


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
        self.x_traversed: int = sum(abs(path[i].bayX - path[i+1].bayX) for i in range(len(path) - 1))
        self.y_traversed: int = sum(abs(path[i].bayY - path[i+1].bayY) for i in range(len(path) - 1))
        sku_to_vol: dict[int, int] = {
            b.storage.carton.sku: b.storage.carton.volume()
            for b in path if b.storage is not None
        }
        total_vol: int = sum(sku_to_vol.get(sku, 0) * qty for sku, qty in items.items())
        self.carts_required: int = math.ceil(total_vol / _CART_VOLUME) if total_vol > 0 else 0

    @staticmethod
    def from_batch(batch: Batch, warehouse: Warehouse) -> list[Task]:
        """Decompose a Batch into one Task per aisle.

        For each SKU in the batch, singleton bins are drained before pallet bins
        so that forward-pick locations are always preferred over reserve locations.
        """
        sku_to_bins: dict[int, list[Aisle.Bin]] = defaultdict(list)
        for bin_ in warehouse.bins:
            if bin_.storage is not None:
                sku_to_bins[bin_.storage.carton.sku].append(bin_)

        # Singleton bins first within each SKU's bin list
        for bins in sku_to_bins.values():
            bins.sort(key=lambda b: 0 if b.unit_type == 'singleton' else 1)

        # Distribute each batch quantity: drain singleton bins before pallet bins
        bin_pick: dict[Aisle.Bin, int] = {}
        for sku, qty in batch.items.items():
            remaining: int = qty
            for bin_ in sku_to_bins.get(sku, []):
                if remaining <= 0:
                    break
                available: int = bin_.storage.quantity if bin_.storage is not None else 0
                take: int = min(remaining, available)
                if take > 0:
                    bin_pick[bin_] = bin_pick.get(bin_, 0) + take
                    remaining -= take

        aisle_bins:  dict[int, list[Aisle.Bin]] = defaultdict(list)
        aisle_items: dict[int, dict[int, int]]  = defaultdict(dict)
        for bin_, take in bin_pick.items():
            aisle_id = bin_.location[0]
            aisle_bins[aisle_id].append(bin_)
            sku = bin_.storage.carton.sku  # type: ignore[union-attr]
            aisle_items[aisle_id][sku] = aisle_items[aisle_id].get(sku, 0) + take

        return [
            Task(aisle_id, _plan_aisle_path(bins), aisle_items[aisle_id])
            for aisle_id, bins in aisle_bins.items()
        ]


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
