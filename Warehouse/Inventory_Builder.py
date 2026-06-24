import random
from collections import defaultdict
from dataclasses import dataclass
from Order import Order
from Storage_Primitive import Storage_Type, Singleton

AffMatrix = dict[tuple[int, int], float]

# largest dimension that still fits in a Singleton bin in any orientation
_SINGLETON_MAX_DIM: int = Singleton.max_width   # 16


@dataclass
class InventoryConfig:
    num_skus: int
    handling_splits: list[float]
    category_splits: list[float]
    singleton_fraction: float = 0.0   # fraction of SKUs sized to fit singleton bins


class Inventory:
    def __init__(self, orders: list[Order]) -> None:
        self.orders: list[Order] = orders

    def affinity_matrix(
        self,
        min_lift: float = 1.5,
        max_lift: float = 5.0,
        max_per_group: int = 500,
    ) -> AffMatrix:
        """Return per-pair lift values for within-(handling, category) SKU pairs.

        SKUs are grouped by storage_type — only items that share the same handling
        and category can be co-located, so cross-group lift is always zero.
        Each group is capped at max_per_group SKUs to keep the matrix sparse
        enough for batch sampling at warehouse scale.
        """
        by_group: dict[tuple[str, str], list[int]] = defaultdict(list)
        for c in self.orders:
            by_group[c.lift_group].append(c.sku)

        affinity: AffMatrix = {}
        for skus in by_group.values():
            eligible = skus[:max_per_group]
            for i, sku_i in enumerate(eligible):
                for sku_j in eligible[i + 1:]:
                    lift_val: float = random.uniform(min_lift, max_lift)
                    affinity[(sku_i, sku_j)] = lift_val
                    affinity[(sku_j, sku_i)] = lift_val
        return affinity


class Inventory_Builder:
    def __init__(self) -> None:
        self._cartons: list[Order] = []
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
            max_dim = (
                _SINGLETON_MAX_DIM
                if random.random() < config.singleton_fraction
                else None
            )
            self._cartons.append(
                Order((handling, category)) if max_dim is None
                else Order((handling, category), max_dim=max_dim)
            )
        return self

    def build(self) -> Inventory:
        return Inventory(list(self._cartons))
