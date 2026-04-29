

class Aisle:
    next_aisle_id = 1
    def __init__(self, storage_size, storage_type, bayXPerAisle, bayYPerAisle):
        self.aisle_id = Aisle.next_aisle_id
        Aisle.next_aisle_id += 1
        self.storage_size = storage_size
        self.storage_type = storage_type
        self.bayXPerAisle = bayXPerAisle
        self.bayYPerAisle = bayYPerAisle

        self.bins = []

        class Bin:
            def __init__(self, quantity, Aisle, bayX, bayY):
                self.aisle = Aisle
                self.quantity = quantity
                self.storage_size = self.aisle.storage_size
                self.storage_type = self.aisle.storage_type
                self.bayX = bayX
                self.bayY = bayY

                @property
                def location(self):
                    return (self.aisle.aisle_id, self.bayX, self.bayY)