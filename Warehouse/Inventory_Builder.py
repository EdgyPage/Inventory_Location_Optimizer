import random
from dataclasses import dataclass
from Carton import Carton
from Storage_Primitive import Storage_Type


@dataclass
class InventoryConfig:
    num_skus: int
    handling_splits: list[float]
    category_splits: list[float]


class Inventory:
    def __init__(self, cartons: list[Carton]) -> None:
        self.cartons: list[Carton] = cartons


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
