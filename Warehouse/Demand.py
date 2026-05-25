import random
import math


class Demand:
    def __init__(self, min_rate=0.5, max_rate=20.0):
        self.rate = random.uniform(min_rate, max_rate)

    def sample(self):
        # Knuth's algorithm: Poisson sample with mean = self.rate
        L = math.exp(-self.rate)
        k, p = 0, 1.0
        while p > L:
            k += 1
            p *= random.random()
        return k - 1
