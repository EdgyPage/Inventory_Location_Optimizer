import math
import random


def poisson_sample(lam: float, rng: random.Random | None = None) -> int:
    """Knuth's algorithm: return a Poisson-distributed integer with mean `lam`.

    Pass `rng` (a `random.Random`) to draw from a dedicated stream; default `None`
    uses the global `random` module (back-compatible)."""
    r = rng or random
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while p > threshold:
        k += 1
        p *= r.random()
    return k - 1


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

    def sample(self, rng: random.Random | None = None) -> int:
        return poisson_sample(self.quantity_rate, rng)
