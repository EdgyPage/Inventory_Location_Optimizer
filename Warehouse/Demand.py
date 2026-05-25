import random
import math


class Demand:
    def __init__(self, min_rate: float = 0.5, max_rate: float = 20.0) -> None:
        self.rate: float = random.uniform(min_rate, max_rate)

    def sample(self) -> int:
        # Knuth's algorithm: Poisson sample with mean = self.rate
        L: float = math.exp(-self.rate)
        k: int = 0
        p: float = 1.0
        while p > L:
            k += 1
            p *= random.random()
        return k - 1
