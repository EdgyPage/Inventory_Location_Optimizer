"""Demand.py — a SKU's demand rates, and the LAW one line's quantity is drawn from.

Two scalars and one object per SKU:

  * `relative_frequency` — the SKU's pick weight as a [0, 1] RELATIVE share (batch
    selection is weighted by it; it is not an absolute rate);
  * `quantity_rate`      — the rate parameter of the line law (λ for the Poisson family);
    the ranking heuristics (velocity = freq × rate) read this scalar as a weight;
  * `line`               — the `LineDistribution`: the law one LINE's quantity is drawn
    from, stamped on the SKU (.scratch/department-calibration, "Choose the coverage
    floor", decision 10).  Every distribution-dependent number — the sampler's draw, the
    mean units a line demands, the tail past a bin's stock, a fill rate — is READ off this
    object, never re-derived by a consumer.  Before the stamp, `staffing.py` took a line as
    `max(1, λ)` and `coverage.py` as `λ + e^-λ`: two answers for one quantity, the drift
    the stamp ends.

The one family today is `poisson_max1`, `max(1, Poisson(λ))`: its sampler is Knuth's
(`poisson_sample`, unchanged), so a stamped catalogue draws exactly what the unstamped one
did.  Two of the staffing record's five provenance values apply here (`Optimization/
simconfig/constants.py:PROVENANCE`, which this leaf may not import): `declared` — the law
was authored by the generator or read from the inventory file's stamp; `assumed` — the law
was RECONSTRUCTED as Poisson(`demand_qty_rate`) from a pre-stamp catalogue (or by a caller
that passed none), and the staffing record names that reconstruction.
"""
import json
import math
import random

import numpy as np
from scipy.special import gammainc


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


# ── the line law ────────────────────────────────────────────────────────────────────────

#: The provenance values a line law may carry (a subset of the staffing record's enum).
LINE_PROVENANCE: tuple[str, ...] = ('declared', 'assumed')

#: The largest Poisson rate the object accepts.  `e^-lam` underflows to 0.0 past ~746, and
#: the running-product readings (`cdf`, `quantile`, `expected_min`) would then read every
#: mass as zero; a pick line demanding hundreds of units is not a warehouse line either
#: (`Order.MAX_QTY` is 20).  Refused at construction so no reader can hang or lie.
MAX_POISSON_LAM: float = 700.0


class LineDistribution:
    """The law one LINE's quantity `q` is drawn from — family + parameters, and every
    distribution-dependent reading a consumer needs, so no consumer names a family:

        mean()            E[q]
        cdf(k)            P(q <= k)
        quantile(p)       the smallest k with P(q <= k) >= p
        survival(upto)    P(q > j) for j = 0..upto-1, as an ndarray (the tail a bin's stock
                          is reached past; `expected_travel.accumulate` reads it per bin)
        expected_min(S)   E[min(q, S)] = Σ_{j<S} P(q > j)  (the fill a stock level S buys)
        sample(rng)       one draw, from `rng` (a `random.Random`) or the global module

    Families:
        poisson_max1  {'lam': λ}   q = max(1, X), X ~ Poisson(λ).  mean = λ + e^-λ;
                                   P(q > 0) = 1 and P(q > j) = P(X > j) for j >= 1;
                                   sampled by Knuth's `poisson_sample`, floored at one.

    `to_row()` / `from_row()` are the inventory file's two columns (`line_family`,
    `line_params` JSON — the `creation_plan` table's precedent).  `provenance` is metadata
    (see the module docstring), not part of the law.
    """
    __slots__ = ('family', 'params', 'provenance')

    FAMILIES: tuple[str, ...] = ('poisson_max1',)

    def __init__(self, family: str, params: dict, provenance: str = 'declared') -> None:
        if family not in self.FAMILIES:
            raise ValueError(f'unknown line-distribution family {family!r}; '
                             f'known: {self.FAMILIES}')
        if provenance not in LINE_PROVENANCE:
            raise ValueError(f'line-law provenance must be one of {LINE_PROVENANCE}; '
                             f'got {provenance!r}')
        self.family = family
        self.params = {str(k): float(v) for k, v in dict(params or {}).items()}
        self.provenance = provenance
        if family == 'poisson_max1':
            lam = self.params.get('lam')
            if lam is None or not (0.0 <= lam <= MAX_POISSON_LAM):
                raise ValueError(f"poisson_max1 needs 'lam' in [0, {MAX_POISSON_LAM:g}]; "
                                 f"got {params!r}")

    # ── construction ───────────────────────────────────────────────────────────
    @classmethod
    def poisson(cls, lam: float, provenance: str = 'declared') -> 'LineDistribution':
        """`max(1, Poisson(lam))` — the founding family."""
        return cls('poisson_max1', {'lam': float(lam)}, provenance)

    @classmethod
    def from_row(cls, family: str, params_json: str | None,
                 provenance: str = 'declared') -> 'LineDistribution':
        """Rebuild from the inventory file's two columns."""
        params = json.loads(params_json) if params_json else {}
        return cls(family, params, provenance)

    def to_row(self) -> tuple[str, str | None]:
        """`(line_family, line_params)` — the JSON is NULL when the family takes none."""
        return self.family, (json.dumps(self.params, sort_keys=True) if self.params else None)

    def __repr__(self) -> str:
        return f'LineDistribution({self.family}, {self.params}, {self.provenance})'

    # ── the readings ───────────────────────────────────────────────────────────
    def mean(self) -> float:
        lam = self.params['lam']
        return lam + math.exp(-lam)

    def _pmf_x(self, upto: int):
        """P(X = i) for i = 0..upto-1 of the underlying Poisson, by the running product."""
        lam = self.params['lam']
        p = math.exp(-lam)
        for i in range(int(upto)):
            yield p
            p *= lam / (i + 1)

    def cdf(self, k: int) -> float:
        """P(q <= k); zero below one, since a line always demands at least one unit."""
        if k < 1:
            return 0.0
        return min(1.0, math.fsum(self._pmf_x(int(k) + 1)))

    def quantile(self, p: float) -> int:
        """The smallest k with P(q <= k) >= p, for 0 <= p < 1 (p <= 0 is one)."""
        if not (0.0 <= p < 1.0):
            raise ValueError(f'quantile needs 0 <= p < 1; got {p!r}')
        lam = self.params['lam']
        pmf = math.exp(-lam)
        cum = pmf                        # P(X <= 0)
        k = 1
        while True:
            pmf *= lam / k
            cum += pmf                   # P(X <= k) = P(q <= k) for k >= 1
            # A `p` above the sum's float plateau (it can settle at 1 - 2e-16) would
            # otherwise never be reached: once the mass past the mode has underflowed,
            # the cdf is numerically one and k is the quantile.
            if cum >= p or (pmf == 0.0 and k > lam):
                return k
            k += 1

    def survival(self, upto: int) -> np.ndarray:
        """P(q > j) for j = 0..upto-1: `1.0` at j = 0, the Poisson upper tail after.

        The regularised lower gamma `P(j+1, λ)` IS `P(X > j)`; vectorised because the
        expected-travel closed form reads it for every bin of every SKU."""
        upto = int(upto)
        if upto <= 0:
            return np.zeros(0)
        t = gammainc(np.arange(1, upto + 1, dtype=float), self.params['lam'])
        t[0] = 1.0
        return t

    def expected_min(self, S: int) -> float:
        """E[min(q, S)] — the units a stock level `S` is expected to deliver against one
        line (a fill rate's numerator).  Σ_{j<S} P(q > j) by the running product."""
        S = int(S)
        if S <= 0:
            return 0.0
        cum = 0.0                        # P(X <= j - 1)
        total = 1.0                      # j = 0: P(q > 0) = 1
        for j, pmf in enumerate(self._pmf_x(S)):
            cum += pmf                   # now P(X <= j)
            if j >= 1:
                total += max(0.0, 1.0 - cum)
        return total

    def sample(self, rng: random.Random | None = None) -> int:
        """One line's quantity: Knuth's Poisson draw, floored at one."""
        return max(1, poisson_sample(self.params['lam'], rng))


def line_law_census(orders) -> dict:
    """What laws a catalogue carries and where they came from — the staffing record's
    `line_law` block: `{'n_skus', 'families': {family: n}, 'provenance': {p: n},
    'reconstructed_skus'}` (the last is the `assumed` count: SKUs whose law was rebuilt
    as Poisson(demand_qty_rate) from a pre-stamp file)."""
    fams: dict = {}
    prov: dict = {}
    n = 0
    for c in orders:
        line = c.demand.line
        fams[line.family] = fams.get(line.family, 0) + 1
        prov[line.provenance] = prov.get(line.provenance, 0) + 1
        n += 1
    return {'n_skus': n, 'families': dict(sorted(fams.items())),
            'provenance': dict(sorted(prov.items())),
            'reconstructed_skus': int(prov.get('assumed', 0))}


# ── the SKU's demand ────────────────────────────────────────────────────────────────────

class Demand:
    def __init__(
        self,
        min_frequency: float = 0.0,
        max_frequency: float = 1.0,
        min_quantity: float = 0.5,
        max_quantity: float = 20.0,
    ) -> None:
        # relative_frequency: a SKU's pick weight as a [0,1] RELATIVE share, not an
        # absolute rate — drives weighted selection in batch sampling.
        self.relative_frequency: float = random.uniform(min_frequency, max_frequency)
        self.quantity_rate: float = random.uniform(min_quantity, max_quantity)
        # A randomly drawn SKU has no stamp to read: its law is the founding family at
        # the drawn rate, and says so.
        self.line: LineDistribution = LineDistribution.poisson(self.quantity_rate, 'assumed')

    @classmethod
    def from_rates(cls, relative_frequency: float, quantity_rate: float,
                   line: LineDistribution | None = None) -> 'Demand':
        """Create a Demand with specific, pre-determined rates (used for reorders and every
        DB/generator construction).  `line` is the stamped law; absent, it is reconstructed
        as Poisson(`quantity_rate`) with provenance `assumed`."""
        d = cls.__new__(cls)
        d.relative_frequency = relative_frequency
        d.quantity_rate = quantity_rate
        d.line = line if line is not None else LineDistribution.poisson(quantity_rate, 'assumed')
        return d

    @property
    def rate(self) -> float:
        """Alias for quantity_rate."""
        return self.quantity_rate

    def sample(self, rng: random.Random | None = None) -> int:
        """One LINE's quantity, drawn from the stamped law — never below one.  (Before the
        stamp this returned the bare Poisson draw and the batch sampler floored it; the
        floor is the law's now, and the draws are identical.)"""
        return self.line.sample(rng)
