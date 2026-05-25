from dataclasses import dataclass
from typing import Optional
from Aisle_Storage import Aisle


@dataclass
class AisleConfig:
    storage_type: str
    bayXPerAisle: int
    bayYPerAisle: int
    storage_sizes: list[str]
    size_probabilities: Optional[list[float]] = None


@dataclass
class WarehouseConfig:
    total_aisles: int
    aisle_splits: list[float]
    aisle_configs: list[AisleConfig]


class Warehouse:
    def __init__(self, aisles: list[Aisle]) -> None:
        self.aisles: list[Aisle] = aisles

    @property
    def bins(self) -> list[Aisle.Bin]:
        return [bin for aisle in self.aisles for bin in aisle.bins]


class Warehouse_Builder:
    def __init__(self) -> None:
        self._aisles: list[Aisle] = []

    def add_aisle(self, storage_size: str, storage_type: str, bayXPerAisle: int, bayYPerAisle: int) -> 'Warehouse_Builder':
        self._aisles.append(Aisle(storage_size, storage_type, bayXPerAisle, bayYPerAisle))
        return self

    def add_aisle_from_distribution(self, storage_sizes: list[str], probabilities: list[float], storage_type: str, bayXPerAisle: int, bayYPerAisle: int) -> 'Warehouse_Builder':
        self._aisles.append(Aisle.from_size_distribution(storage_sizes, probabilities, storage_type, bayXPerAisle, bayYPerAisle))
        return self

    def from_config(self, config: WarehouseConfig) -> 'Warehouse_Builder':
        counts: list[int] = []
        remaining: int = config.total_aisles
        for split in config.aisle_splits[:-1]:
            count: int = round(split * config.total_aisles)
            counts.append(count)
            remaining -= count
        counts.append(remaining)

        for count, aisle_config in zip(counts, config.aisle_configs):
            for _ in range(count):
                if len(aisle_config.storage_sizes) == 1:
                    self.add_aisle(
                        aisle_config.storage_sizes[0],
                        aisle_config.storage_type,
                        aisle_config.bayXPerAisle,
                        aisle_config.bayYPerAisle,
                    )
                else:
                    self.add_aisle_from_distribution(
                        aisle_config.storage_sizes,
                        aisle_config.size_probabilities,
                        aisle_config.storage_type,
                        aisle_config.bayXPerAisle,
                        aisle_config.bayYPerAisle,
                    )
        return self

    def build(self) -> Warehouse:
        return Warehouse(list(self._aisles))
