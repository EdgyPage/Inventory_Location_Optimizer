from __future__ import annotations


class Aisle:
    next_aisle_id: int = 1

    class Bin:
        def __init__(self, aisle: Aisle, bayX: int, bayY: int, storage_size: str) -> None:
            self.aisle: Aisle = aisle
            self.bayX: int = bayX
            self.bayY: int = bayY
            self.storage_size: str = storage_size
            self.storage_type: str = aisle.storage_type
            self.quantity: int = 0

        @property
        def location(self) -> tuple[int, int, int]:
            return (self.aisle.aisle_id, self.bayX, self.bayY)

    def __init__(self, storage_size: str, storage_type: str, bayXPerAisle: int, bayYPerAisle: int) -> None:
        self.aisle_id: int = Aisle.next_aisle_id
        Aisle.next_aisle_id += 1
        self.storage_size: str | None = storage_size
        self.storage_type: str = storage_type
        self.bayXPerAisle: int = bayXPerAisle
        self.bayYPerAisle: int = bayYPerAisle

        self.bins: list[Aisle.Bin] = [
            Aisle.Bin(self, x, y, storage_size)
            for x in range(1, bayXPerAisle + 1)
            for y in range(1, bayYPerAisle + 1)
        ]

    @classmethod
    def from_size_distribution(cls, storage_sizes: list[str], probabilities: list[float], storage_type: str, bayXPerAisle: int, bayYPerAisle: int) -> Aisle:
        aisle = object.__new__(cls)
        aisle.aisle_id = cls.next_aisle_id
        cls.next_aisle_id += 1
        aisle.storage_size = None
        aisle.storage_type = storage_type
        aisle.bayXPerAisle = bayXPerAisle
        aisle.bayYPerAisle = bayYPerAisle

        counts: list[int] = []
        remaining: int = bayYPerAisle
        for prob in probabilities[:-1]:
            count = round(prob * bayYPerAisle)
            counts.append(count)
            remaining -= count
        counts.append(remaining)

        y_size: dict[int, str] = {}
        y: int = 1
        for size, count in zip(storage_sizes, counts):
            for _ in range(count):
                y_size[y] = size
                y += 1

        aisle.bins = [
            cls.Bin(aisle, x, y, y_size[y])
            for x in range(1, bayXPerAisle + 1)
            for y in range(1, bayYPerAisle + 1)
        ]
        return aisle
