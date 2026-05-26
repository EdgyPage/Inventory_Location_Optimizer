import random
import math


class Demand:
    def __init__(
        self,
        min_frequency: float = 0.0,
        max_frequency: float = 1.0,
        min_quantity: float = 0.5,
        max_quantity: float = 20.0,
    ) -> None:
        self.frequency: float = random.uniform(min_frequency, max_frequency)
        self.quantity_rate: float = random.uniform(min_quantity, max_quantity)

    def sample(self) -> int:
        # Knuth's algorithm: Poisson sample with mean = quantity_rate
        L: float = math.exp(-self.quantity_rate)
        k: int = 0
        p: float = 1.0
        while p > L:
            k += 1
            p *= random.random()
        return k - 1
