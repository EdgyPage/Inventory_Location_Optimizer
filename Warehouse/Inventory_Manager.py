from Carton import Carton
from Inventory_Builder import Inventory
from Warehouse_Builder import Warehouse
from Aisle_Storage import Aisle
from Storage_Primitive import Singleton, Pallet

_PALLET_SIZES: set[str] = {'large', 'extra_large'}


class Inventory_Manager:
    def __init__(self, inventory: Inventory, warehouse: Warehouse) -> None:
        self.inventory: Inventory = inventory
        self.warehouse: Warehouse = warehouse
        self.unassigned: list[Carton] = []

    def assign(self, quantity: int = 1) -> 'Inventory_Manager':
        cartons_by_handling: dict[str, list[Carton]] = {}
        for carton in self.inventory.cartons:
            handling: str = carton.storage_type[0]
            cartons_by_handling.setdefault(handling, []).append(carton)

        for bin_ in self.warehouse.bins:
            if bin_.storage is not None:
                continue

            candidates: list[Carton] = cartons_by_handling.get(bin_.storage_type, [])

            for i, carton in enumerate(candidates):
                try:
                    if bin_.storage_size in _PALLET_SIZES:
                        bin_.storage = Pallet(carton, quantity)
                    else:
                        bin_.storage = Singleton(carton, quantity)
                    candidates.pop(i)
                    break
                except ValueError:
                    continue

        self.unassigned = [c for pool in cartons_by_handling.values() for c in pool]
        return self

    @property
    def assigned_bins(self) -> list[Aisle.Bin]:
        return [b for b in self.warehouse.bins if b.storage is not None]

    @property
    def empty_bins(self) -> list[Aisle.Bin]:
        return [b for b in self.warehouse.bins if b.storage is None]

    def summary(self) -> None:
        total   = len(self.warehouse.bins)
        filled  = len(self.assigned_bins)
        singles = sum(1 for b in self.assigned_bins if isinstance(b.storage, Singleton))
        pallets = sum(1 for b in self.assigned_bins if isinstance(b.storage, Pallet))
        print(f'Total bins  : {total}')
        print(f'Filled      : {filled}  ({singles} singletons, {pallets} pallets)')
        print(f'Empty       : {total - filled}')
        print(f'Unassigned  : {len(self.unassigned)} cartons')
