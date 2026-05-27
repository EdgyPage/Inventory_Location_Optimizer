import random
from collections import defaultdict
from dataclasses import dataclass
from Carton import Carton
from Storage_Primitive import Storage_Type

AffMatrix = dict[tuple[int, int], float]


@dataclass
class InventoryConfig:
    num_skus: int
    handling_splits: list[float]
    category_splits: list[float]


class Inventory:
    def __init__(self, cartons: list[Carton]) -> None:
        self.cartons: list[Carton] = cartons

    def affinity_matrix(
        self,
        min_lift: float = 1.5,
        max_lift: float = 5.0,
    ) -> AffMatrix:
        """Return per-pair lift values for every within-group SKU pair.

        Each unordered pair draws its own lift from uniform(min_lift, max_lift),
        so within-group SKUs have varying correlation strength rather than a
        flat value.  Both directions are stored identically (lift is symmetric).
        Absent pairs are treated as 0.0 by callers.
        """
        by_group: dict[int, list[int]] = defaultdict(list)
        for c in self.cartons:
            by_group[c.lift_group].append(c.sku)

        affinity: AffMatrix = {}
        for skus in by_group.values():
            for i, sku_i in enumerate(skus):
                for sku_j in skus[i + 1:]:
                    lift_val: float = random.uniform(min_lift, max_lift)
                    affinity[(sku_i, sku_j)] = lift_val
                    affinity[(sku_j, sku_i)] = lift_val
        return affinity


class Inventory_Builder:
    def __init__(self) -> None:
        self._cartons: list[Carton] = []
        self._storage_type: Storage_Type = Storage_Type()

    def from_config(self, config: InventoryConfig) -> 'Inventory_Builder':
        for _ in range(config.num_skus):
            handling = random.choices(
                self._storage_type.handling_storage_types,
                weights=config.handling_splits,
                k=1
            )[0]
            category = random.choices(
                self._storage_type.category_storage_types,
                weights=config.category_splits,
                k=1
            )[0]
            self._cartons.append(Carton((handling, category)))
        return self

    def build(self) -> Inventory:
        return Inventory(list(self._cartons))
