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

    @classmethod
    def from_rates(cls, frequency: float, quantity_rate: float) -> 'Demand':
        """Create a Demand with specific, pre-determined rates (used for reorders)."""
        d = cls.__new__(cls)
        d.frequency = frequency
        d.quantity_rate = quantity_rate
        return d

    @property
    def rate(self) -> float:
        """Alias for quantity_rate."""
        return self.quantity_rate

    def sample(self) -> int:
        # Knuth's algorithm: Poisson sample with mean = quantity_rate
        L: float = math.exp(-self.quantity_rate)
        k: int = 0
        p: float = 1.0
        while p > L:
            k += 1
            p *= random.random()
        return k - 1
