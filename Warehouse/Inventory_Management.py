import random
from typing import Callable
from Carton import Carton
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Singleton, Pallet, viable_storage_units

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]


def _uniform_assignment(unit: StorageUnit, available_bins: list[Aisle.Bin]) -> Aisle.Bin | None:
    candidates: list[Aisle.Bin] = [
        b for b in available_bins
        if b.storage_handling_type == unit.carton.storage_type
    ]
    return random.choice(candidates) if candidates else None


class Inventory_Manager:
    def __init__(
        self,
        warehouse: Warehouse,
        assignment_fn: AssignmentFn = _uniform_assignment,
    ) -> None:
        self.warehouse: Warehouse = warehouse
        self.assignment_fn: AssignmentFn = assignment_fn
        self.unassigned: list[StorageUnit] = []

    def place(self, carton: Carton, quantity: int = 1) -> list[Aisle.Bin]:
        units: list[StorageUnit] = viable_storage_units(carton, quantity)
        available: list[Aisle.Bin] = [b for b in self.warehouse.bins if b.storage is None]
        placed: list[Aisle.Bin] = []

        for unit in units:
            bin_ = self.assignment_fn(unit, available)
            if bin_ is None:
                self.unassigned.append(unit)
            else:
                bin_.storage = unit
                available.remove(bin_)
                placed.append(bin_)

        return placed

    def place_all(self, cartons: list[Carton], quantity: int = 1) -> 'Inventory_Manager':
        for carton in cartons:
            self.place(carton, quantity)
        return self

    @property
    def assigned_bins(self) -> list[Aisle.Bin]:
        return [b for b in self.warehouse.bins if b.storage is not None]

    @property
    def empty_bins(self) -> list[Aisle.Bin]:
        return [b for b in self.warehouse.bins if b.storage is None]

    def summary(self) -> None:
        total: int = len(self.warehouse.bins)
        filled: int = len(self.assigned_bins)
        singles: int = sum(1 for b in self.assigned_bins if isinstance(b.storage, Singleton))
        pallets: int = sum(1 for b in self.assigned_bins if isinstance(b.storage, Pallet))
        print(f'Total bins  : {total}')
        print(f'Filled      : {filled}  ({singles} singletons, {pallets} pallets)')
        print(f'Empty       : {total - filled}')
        print(f'Unassigned  : {len(self.unassigned)} units')
