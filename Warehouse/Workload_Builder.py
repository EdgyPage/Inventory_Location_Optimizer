from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from Inventory_Builder import Inventory
from Aisle_Storage import Aisle
from Warehouse_Builder import Warehouse


@dataclass
class BatchConfig:
    inventory_size: int
    mean_fraction: float = 0.20   # centre of num_skus distribution as fraction of inventory
    std_fraction: float  = 0.05   # spread of num_skus distribution as fraction of inventory


class Batch:
    def __init__(self, config: BatchConfig, inventory: Inventory) -> None:
        self.config = config

        mean = config.mean_fraction * config.inventory_size
        std  = config.std_fraction  * config.inventory_size
        self.num_skus: int   = max(1, min(config.inventory_size, round(random.gauss(mean, std))))
        self.threshold: float = random.random()

        candidates = [c for c in inventory.cartons if c.demand.frequency > self.threshold]
        k = min(self.num_skus, len(candidates))
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

    @staticmethod
    def from_batch(batch: Batch, warehouse: Warehouse) -> list[Task]:
        """Decompose a Batch into one Task per aisle, each with a planned path."""
        sku_to_bin: dict[int, Aisle.Bin] = {
            bin_.storage.carton.sku: bin_
            for bin_ in warehouse.bins
            if bin_.storage is not None
        }

        aisle_bins:  dict[int, list[Aisle.Bin]] = defaultdict(list)
        aisle_items: dict[int, dict[int, int]]  = defaultdict(dict)

        for sku, qty in batch.items.items():
            bin_ = sku_to_bin.get(sku)
            if bin_ is None:
                continue
            aisle_id = bin_.location[0]
            aisle_bins[aisle_id].append(bin_)
            aisle_items[aisle_id][sku] = qty

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
