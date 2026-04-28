

class Carton:
    next_sku = 1

    def __init__(self, length, width, height, weight=5):
        self.length = length
        self.width = width
        self.height = height
        self.weight = weight
        self._sku = Carton.next_sku
        Carton.next_sku += 1
        self.demand = 0


    def volume(self):
        return self.length * self.width * self.height
    @property
    def sku(self):
        return self._sku
    