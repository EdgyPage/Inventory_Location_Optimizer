class Aisle:
    next_aisle_id = 1

    class Bin:
        def __init__(self, aisle, bayX, bayY, storage_size):
            self.aisle = aisle
            self.bayX = bayX
            self.bayY = bayY
            self.storage_size = storage_size
            self.storage_type = aisle.storage_type
            self.quantity = 0

        @property
        def location(self):
            return (self.aisle.aisle_id, self.bayX, self.bayY)

    def __init__(self, storage_size, storage_type, bayXPerAisle, bayYPerAisle):
        self.aisle_id = Aisle.next_aisle_id
        Aisle.next_aisle_id += 1
        self.storage_size = storage_size
        self.storage_type = storage_type
        self.bayXPerAisle = bayXPerAisle
        self.bayYPerAisle = bayYPerAisle

        self.bins = [
            Aisle.Bin(self, x, y, storage_size)
            for x in range(1, bayXPerAisle + 1)
            for y in range(1, bayYPerAisle + 1)
        ]

    @classmethod
    def from_size_distribution(cls, storage_sizes, probabilities, storage_type, bayXPerAisle, bayYPerAisle):
        aisle = object.__new__(cls)
        aisle.aisle_id = cls.next_aisle_id
        cls.next_aisle_id += 1
        aisle.storage_size = None
        aisle.storage_type = storage_type
        aisle.bayXPerAisle = bayXPerAisle
        aisle.bayYPerAisle = bayYPerAisle

        counts = []
        remaining = bayYPerAisle
        for prob in probabilities[:-1]:
            count = round(prob * bayYPerAisle)
            counts.append(count)
            remaining -= count
        counts.append(remaining)

        y_size = {}
        y = 1
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
