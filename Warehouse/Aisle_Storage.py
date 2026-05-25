

class Aisle:
    next_aisle_id = 1

    class Bin:
        def __init__(self, aisle, bayX, bayY):
            self.aisle = aisle
            self.bayX = bayX
            self.bayY = bayY
            self.storage_size = aisle.storage_size
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
            Aisle.Bin(self, x, y)
            for x in range(1, bayXPerAisle + 1)
            for y in range(1, bayYPerAisle + 1)
        ]