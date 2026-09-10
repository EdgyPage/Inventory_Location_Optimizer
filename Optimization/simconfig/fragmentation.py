"""fragmentation.py -- the stationary bin fragmentation of a replenished SKU, in closed form.

Under the calibrated era every SKU on the line floor runs base stock at a lead of zero
(`coverage.py`): a line of `q` serves `min(q, Q)` off a shelf holding `Q`, the order-up-to
fires exactly what it took, and the lot lands before the next batch.  Three decided rules
then fragment the shelf across bins (ADR-0003; ".scratch/department-calibration",
"Let a base-stock top-up reach the shelf" and "Band the own-bin share and the free-index
depth"):

  * a top-up lands in an EMPTY bin first (`Inventory_Manager._candidates_raw`), so a
    partial line leaves a remnant AND opens a bin for the lot that replaces it;
  * picks drain the SKU's SMALLEST bin first (`Workload_Builder.drain_sku`), which is what
    clears a remnant and hands its bin back;
  * the lot is packed by the planner's `stock_plan` -- its LEADING slots filled in order
    (`Storage_Primitive.viable_storage_units`), so a partial lot of `r` is `r // per`
    units of `per` and one of `r % per`, and a pallet of fewer items can sit in a smaller
    size tier than the full one it replaces.

So each SKU is a Markov chain over the multiset of units on its shelf, driven by its own
stamped line law (`Demand.line`).  This module derives the chain's STATIONARY distribution
and its TRANSIENT from a fresh fielding, and sums the expected units per bucket (BinKey)
over a section -- the closed form the record's `fielded.buckets[].expected_extra` stamps
and the fill headroom is derived from ("Derive the stationary fragmentation closed form",
"Derive the fill headroom from the fragmentation").

THE CHAIN.  A unit is `(q_now, single, n0)`: the items it holds now, and the kind it was
packed as (singleton or pallet, and the quantity it was packed with -- the KIND decides
the size tier, which the bin keeps even as picks thin the unit).  A state is the sorted
tuple of units; sorted ascending it IS the drain order.  With `S` the items on the shelf,
`Q` the order-up-to and `rp` the reorder point, a line of `q`:

  * `q < S`: drain `q` smallest-first; if the shelf is then at or below `rp`, the
    position rule fires `Q - on_hand` and `packing(Q - on_hand)` lands in fresh bins
    (base stock is `rp = Q - 1`: every line fires exactly what it took);
  * `q >= S`: the shelf is emptied and refilled to the fielded state `f`, the remainder
    re-offered next day against a full shelf again (nothing is lost, ADR-0004), so the
    state after the whole line is `f` with the residual `(q - S) mod Q` drained and
    repacked -- `staffing.fired_lots` prices the same line as lots of `Q` and one of the
    residual.

The stationary law is `pi = pi P` over the states reachable from `f` (a sparse power
iteration on the lazy chain); the transient after `j` lines is the forward iterate from `f`.
A SKU that sees `lambda_s` lines a day is at day `t` the Poisson(`lambda_s t`) mixture
over `j`; `lambda_s` is `n * pi_s` with `pi_s` the line share `freq / sum freq` as
`coverage.daily_demand` weights it, unless a caller hands a rate per SKU.

WHAT IS AND IS NOT MODELLED (stated so the check's residual has a name):

  * the supply jitter (`_fire_reorders`' `max(1, round(N(lot, lot * cv)))`) is not: the
    lot is taken as exactly what the position rule ordered;
  * a lead is taken as zero: the lot lands before the next line.  Every catalogue the
    generator authors today has lead 0; with a lead the multiset a lot lands as is the
    same, only its timing against the lines between is not, and the record names the
    SKUs priced this way (`positive_lead_skus`);
  * two lines for one SKU in one batch are aggregated by the sampler into one quantity;
    the chain treats each line alone (on the reference pair a SKU sees a second line in the
    same day on under 1% of its days);
  * a location tie between units of EQUAL quantity and DIFFERENT kind is split uniformly
    over the tied units: `drain_sku` breaks ties by `(aisle_id, bayX, bayY)`, and the built
    layout interleaves every tier's aisles across the whole id range, so the tie carries
    no information;
  * the top-up always finds an empty bin of its own tier -- a spill or an own-bin top-up
    is judged at zero by the equilibrium check, not modelled here.

Checked (2026-09-10, `.scratch/department-calibration/assets/fragmentation-derivation.md`)
against the two finished era runs on the reference pair: scored at each SKU's realized
line count the chain reproduces both leaves' bin change over 25 days to -3%, the one-line
mean to a third of a percent; the store's 39-day drawdown at the declared line share to
-4%; the fulfillment section's transient over-reads because the batch sampler's affinity
lift spreads its lines over fewer SKUs than the line share says (the seam below).

THIS MODULE IS PURE: orders with a declared level and a `stock_plan` in, floats out.  It
imports no CONFIG and touches no file.  The size tier of a kind is read off the packer's
own unit classes (`Pallet`, `Singleton`, `FulfillmentBin`) through `binkey_of`, so the
bucket a unit lands in is the one the sim would build for it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from Warehouse.inventory.inventory_common import BinKey, binkey_of
from Warehouse.kernel.regime import FULFILLMENT, regime_of
from Warehouse.layout.Storage_Primitive import FulfillmentBin, Pallet, Singleton

#: Truncate the line law's upper tail at this quantile mass; the rest is renormalised away.
LINE_TAIL: float = 1e-10
#: Lines of transient kept per class; beyond it the chain is taken at its stationary law.
J_MAX: int = 64
#: The stationary power iteration's L1 step tolerance, the step at which a chain still
#: moving hands over to the direct solve, and the ceiling past which the iterate stands.
POWER_TOL: float = 1e-12
POWER_SWITCH: int = 2_000
POWER_MAX: int = 20_000
#: A chain that reaches this many states raises rather than run on: the reference pair's
#: widest class holds ~9,000, and an above-floor shelf of a thousand items ~1,000.
STATES_MAX: int = 200_000

Kind = tuple[bool, int]              # (single, n0): how a unit was packed
Unit = tuple[int, bool, int]         # (q_now, single, n0): ascending sort = drain order
State = tuple[Unit, ...]


# ── the packer's plan branch ────────────────────────────────────────────────────────────

def plan_slots(order) -> tuple[tuple[bool, int, int], ...]:
    """The order's `stock_plan` as `((single, per, count), ...)`, refusing an order that
    carries none: under the one planner contract every fielded SKU has one ("Field the
    requirement"), and the packer's plan-less branch packs by volume, which this chain
    does not reproduce."""
    plan = getattr(order, 'stock_plan', None)
    if not plan:
        raise ValueError(f'SKU {order.sku}: no stock_plan -- the fragmentation chain packs '
                         f'a lot by the plan the warehouse was sized from, and this order '
                         f'was never planned')
    return tuple((bool(s), int(per), int(count)) for s, per, count in plan)


def packing(plan: tuple, r: int) -> tuple[Kind, ...]:
    """The units a lot of `r` items packs into: the plan's LEADING slots filled in order,
    `take = min(per, remaining)` per unit -- `viable_storage_units`' plan branch, for a
    lot at or below the plan's total (a lot never exceeds the order-up-to).  Returns the
    kinds `(single, n0)` in packing order; `r <= 0` packs nothing."""
    out: list[Kind] = []
    remaining = int(r)
    for single, per, count in plan:
        for _ in range(count):
            if remaining <= 0:
                return tuple(out)
            take = min(per, remaining)
            out.append((single, take))
            remaining -= take
    if remaining > 0:
        raise ValueError(f'a lot of {r} exceeds the plan total {plan_total(plan)}; the '
                         f'packer would fall through to its volume rule here')
    return tuple(out)


def plan_total(plan: tuple) -> int:
    return sum(per * count for _s, per, count in plan)


# ── one SKU's chain ─────────────────────────────────────────────────────────────────────

def _add(state: State, kinds: tuple[Kind, ...]) -> State:
    """The state after the lot's units land, each in its own fresh bin."""
    units = list(state)
    units.extend((n0, single, n0) for single, n0 in kinds)
    units.sort()
    return tuple(units)


def drain_all(state: State, d_max: int) -> dict:
    """`{d: {state: weight}}` for every `d = 1..d_max`: the shelf after `d` items are picked
    smallest-first, in ONE pass (the drain of `d + 1` is the drain of `d` and one more).

    A tie between units of equal quantity and different kind is split uniformly over the
    tied UNITS (the layout's location order is uninformative across tiers -- module
    docstring -- so each tied unit is equally likely to come first); branches are merged
    after every whole unit, so a shelf of many tied units costs its distinct multisets,
    not its orderings.  A `d` no unit can satisfy yields nothing for that `d` (the caller
    only asks below the shelf's total)."""
    res: dict = {d: {} for d in range(1, int(d_max) + 1)}
    level: dict = {(state, 0): 1.0}                  # (state, items removed so far)
    while level:
        nxt: dict = {}
        for (st, removed), w in level.items():
            if removed >= d_max or not st:
                continue
            q_min = st[0][0]
            kinds: dict[Kind, int] = {}
            tied: dict[Kind, int] = {}
            for i, (q, single, n0) in enumerate(st):
                if q != q_min:
                    break
                kinds.setdefault((single, n0), i)     # the first unit of each tied kind
                tied[(single, n0)] = tied.get((single, n0), 0) + 1
            n_tied = sum(tied.values())
            for (single, n0), i in kinds.items():
                # Uniform over the tied UNITS: a kind with two of them is twice as likely
                # to be the one `location` order puts first.
                share = w * tied[(single, n0)] / n_tied
                # A partial take leaves this unit below the rest: re-sort.
                for d in range(removed + 1, min(removed + q_min, d_max + 1)):
                    new = list(st)
                    new[i] = (q_min - (d - removed), single, n0)
                    key = tuple(sorted(new))
                    res[d][key] = res[d].get(key, 0.0) + share
                # A whole take removes it; the order of the rest is unchanged.
                whole = removed + q_min
                if whole <= d_max:
                    new = list(st)
                    del new[i]
                    key = tuple(new)
                    res[whole][key] = res[whole].get(key, 0.0) + share
                    nxt[(key, whole)] = nxt.get((key, whole), 0.0) + share
        level = nxt
    return res


def line_pmf(line, q_max: int | None = None) -> np.ndarray:
    """`P(q = k)` for `k = 0..q_max` off the stamped law's `cdf` (zero at k = 0: a line
    demands at least one unit).  `q_max` defaults to the law's `1 - LINE_TAIL` quantile;
    the truncated tail is renormalised away."""
    if q_max is None:
        q_max = int(line.quantile(1.0 - LINE_TAIL))
    cdf = np.array([float(line.cdf(k)) for k in range(int(q_max) + 1)])
    if cdf[0] > 0.0:
        raise ValueError('a line demands at least one unit (`Demand.line`); this law has '
                         f'mass at zero (cdf(0) = {cdf[0]!r})')
    pmf = np.clip(np.diff(np.concatenate(([0.0], cdf))), 0.0, None)
    total = float(pmf.sum())
    if total <= 0.0:
        raise ValueError('the line law carries no mass at or below its truncation')
    return pmf / total


@dataclass
class SkuChain:
    """The fragmentation chain of one SKU CLASS -- every SKU sharing a line law, a
    `stock_plan` and a reorder point shares it, whatever its size tier.

    `kinds` is the fixed list of unit kinds the plan can ever produce (every `packing(r)`
    for `r = 1..Q`); `stationary_units[k]` the expected units of kind `k` on the shelf
    under the stationary law, `trajectory_units[j, k]` the same after exactly `j` lines
    from the fielded state, `fielded_units[k]` what the plan fields.  `stationary_bins`
    is the expected bin count, `bins_law` its distribution `{bins: p}`, `rho` the share
    of lines that empty a full shelf (`P(q >= Q)`), `states` the reachable state count.
    """
    line: object
    plan: tuple
    rp: int | None = None            # None = base stock, `Q - 1`
    j_max: int = J_MAX
    Q: int = field(init=False)
    rho: float = field(init=False)
    f: State = field(init=False)
    kinds: list = field(init=False)
    fielded_units: np.ndarray = field(init=False)
    stationary: dict = field(init=False)
    stationary_units: np.ndarray = field(init=False)
    stationary_bins: float = field(init=False)
    bins_law: dict = field(init=False)
    trajectory_units: np.ndarray = field(init=False)
    states: int = field(init=False)
    solver: str = field(init=False)
    iterations: int = field(init=False)

    def __post_init__(self) -> None:
        plan = tuple(self.plan)
        Q = plan_total(plan)
        if Q < 1:
            raise ValueError('the plan fields no unit')
        self.Q = Q
        self.rp = Q - 1 if self.rp is None else int(self.rp)
        self.f = _add((), packing(plan, Q))
        kinds: set = set()
        for r in range(1, Q + 1):
            kinds.update(packing(plan, r))
        self.kinds = sorted(kinds)
        self._kidx = {k: i for i, k in enumerate(self.kinds)}
        self.fielded_units = self._units(self.f)
        self._pmf = line_pmf(self.line)
        self.rho = float(self._pmf[Q:].sum())
        self._pack = {r: packing(plan, r) for r in range(1, Q + 1)}
        # Where a line that empties the shelf lands, by the residual it re-offers --
        # computed on demand: a residual is below the law's tail, never anywhere near `Q`
        # on a shelf that holds days of demand, and a table for every `m < Q` was O(Q^2).
        self._land: dict = {0: {self.f: 1.0}}
        self._solve()

    def _landing(self, m: int) -> dict:
        hit = self._land.get(m)
        if hit is None:
            hit = self._land[m] = self._partial(self.f, m)
        return hit

    # -- helpers ------------------------------------------------------------------------
    def _partial(self, st: State, d: int) -> dict:
        """`{state: weight}` after a line of `d` items that leaves stock on the shelf."""
        S = sum(u[0] for u in st)
        out: dict = {}
        on_hand = S - d
        lot = self._pack[self.Q - on_hand] if on_hand <= self.rp else None
        for s2, w in drain_all(st, d)[d].items():
            s3 = _add(s2, lot) if lot is not None else s2
            out[s3] = out.get(s3, 0.0) + w
        return out

    def _kernel(self, st: State) -> dict:
        """`{state: prob}` after one line at `st`, over the whole line law."""
        S = sum(u[0] for u in st)
        pmf = self._pmf
        acc: dict = {}
        if S > 1:
            drains = drain_all(st, min(S - 1, len(pmf) - 1))
            for d, states in drains.items():
                p = float(pmf[d])
                if p <= 0.0 or not states:
                    continue
                on_hand = S - d
                lot = self._pack[self.Q - on_hand] if on_hand <= self.rp else None
                for s2, w in states.items():
                    s3 = _add(s2, lot) if lot is not None else s2
                    acc[s3] = acc.get(s3, 0.0) + p * w
        # Lines at or above the shelf: emptied, refilled, the residual re-offered.
        for q in range(S, len(pmf)):
            p = float(pmf[q])
            if p <= 0.0:
                continue
            for s2, w in self._landing((q - S) % self.Q).items():
                acc[s2] = acc.get(s2, 0.0) + p * w
        return acc

    def _units(self, st: State) -> np.ndarray:
        v = np.zeros(len(self.kinds))
        for _q, single, n0 in st:
            v[self._kidx[(single, n0)]] += 1.0
        return v

    def _solve(self) -> None:
        # Enumerate every state reachable from the fielded one; the kernel is one sparse
        # matrix (row = from, col = to) and everything after is a mat-vec.
        index: dict = {self.f: 0}
        order: list = [self.f]
        rows: list = []; cols: list = []; vals: list = []
        i = 0
        while i < len(order):
            for s2, p in self._kernel(order[i]).items():
                j = index.get(s2)
                if j is None:
                    j = index[s2] = len(order)
                    order.append(s2)
                    if j >= STATES_MAX:
                        raise ValueError(
                            f'the fragmentation chain of a SKU with Q = {self.Q}, rp = '
                            f'{self.rp} and plan {self.plan} reaches more than {STATES_MAX:,} '
                            f'states; this catalogue is outside what the closed form has '
                            f'been sized for (raise STATES_MAX or coarsen the plan)')
                rows.append(i); cols.append(j); vals.append(p)
            i += 1
        n = len(order)
        P = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
        PT = P.T.tocsr()
        U = np.array([self._units(st) for st in order])          # (n, kinds)
        L = np.array([len(st) for st in order], dtype=float)     # bins per state
        pi = self._stationary(PT, n)
        self.stationary = {order[k]: float(pi[k]) for k in np.nonzero(pi)[0]}
        self.stationary_units = U.T @ pi
        self.stationary_bins = float(L @ pi)
        law = np.bincount(L.astype(int), weights=pi)
        self.bins_law = {int(k): float(law[k]) for k in np.nonzero(law > 0.0)[0]}
        # Transient: the full kernel from the fielded state, `j_max` lines deep.
        traj = np.zeros((self.j_max + 1, len(self.kinds)))
        mu = np.zeros(n); mu[0] = 1.0
        traj[0] = U.T @ mu
        for j in range(1, self.j_max + 1):
            mu = PT @ mu
            traj[j] = U.T @ mu
        self.trajectory_units = traj
        self.states = n

    def _stationary(self, PT, n: int) -> np.ndarray:
        """`pi = pi P` by power iteration on the LAZY chain `(P + I) / 2` from the fielded
        state -- the same stationary law, and aperiodic whatever cycle the drain runs in.
        The chain regenerates whenever a line empties the shelf, so the reachable set holds
        one recurrent class and the iterate converges geometrically; it stops when the
        L1 step is below `POWER_TOL`.  A chain still moving at `POWER_SWITCH` steps is a
        slow mixer and goes to the pinned direct solve (`_direct`); `solver` records which
        path answered, `iterations` how many steps it took.

        A direct solve with a normalisation ROW was tried first and dropped: the dense row
        fills the LU factors in, and a 9,000-state class cost four seconds against a
        twentieth of that by iteration; the pinned form keeps the system sparse."""
        if n == 1:
            self.solver = 'trivial'
            self.iterations = 0
            return np.ones(1)
        mu = np.zeros(n); mu[0] = 1.0
        for k in range(1, POWER_MAX + 1):
            nxt = 0.5 * (mu + PT @ mu)
            step = float(np.abs(nxt - mu).sum())
            mu = nxt
            if step < POWER_TOL:
                self.solver = 'power'
                self.iterations = k
                return mu / mu.sum()
            if k == POWER_SWITCH:
                # A slow mixer (a partial line that is rare next to the resets, or a
                # long drain cycle above the floor): solve the balance equations directly,
                # pinned at the state the iterate has settled most mass on -- a recurrent
                # state by then -- so the system stays sparse.
                pi = self._direct(PT, n, int(mu.argmax()))
                if pi is not None:
                    self.solver = 'direct'
                    self.iterations = k
                    return pi
        self.solver = 'power_capped'
        self.iterations = POWER_MAX
        return mu / mu.sum()

    @staticmethod
    def _direct(PT, n: int, pin: int) -> np.ndarray | None:
        """`(I - P^T) pi = 0` with `pi[pin] = 1`, the pinned row and column dropped, then
        normalised; None when the solve fails or does not return a distribution."""
        M = (sp.identity(n, format='csr') - PT).tocsr()
        keep = np.array([i for i in range(n) if i != pin])
        A = M[keep][:, keep].tocsc()
        rhs = -M[keep][:, [pin]].toarray().ravel()
        try:
            x = spla.spsolve(A, rhs)
        except Exception:
            return None
        pi = np.zeros(n); pi[pin] = 1.0; pi[keep] = x
        if not np.all(np.isfinite(pi)) or pi.min() < -1e-9 * pi.max():
            return None
        pi = np.clip(pi, 0.0, None); pi /= pi.sum()
        if float(np.abs(PT @ pi - pi).sum()) > 1e-9:
            return None
        return pi

    # -- readings -----------------------------------------------------------------------
    @property
    def stationary_extra(self) -> float:
        """Expected bins beyond the fielding, in steady state."""
        return self.stationary_bins - float(len(self.f))

    def units_at_lines(self, j: int) -> np.ndarray:
        """Expected units by kind after exactly `j` lines (stationary past `j_max`)."""
        return self.trajectory_units[int(j)] if j <= self.j_max else self.stationary_units

    def units_at_days(self, lines_per_day: float, days) -> np.ndarray:
        """Expected units by kind at each of `days` for a SKU seeing `lines_per_day`
        lines a day: the Poisson mixture over the lines count, the tail past `j_max`
        at the stationary law.  Returns `(len(days), n_kinds)`."""
        days = np.asarray(days, dtype=float)
        W = poisson_weights(float(lines_per_day) * days, self.j_max)   # (T, j_max + 1)
        head = W[:, :self.j_max] @ self.trajectory_units[:self.j_max]
        tail = W[:, self.j_max:self.j_max + 1] * self.stationary_units[None, :]
        return head + tail


_LGAMMA = np.array([math.lgamma(j + 1.0) for j in range(J_MAX + 1)])


def poisson_weights(mean, j_max: int) -> np.ndarray:
    """`P(J = j)` for `j < j_max` and `P(J >= j_max)` in the last column, for each Poisson
    mean in `mean` (shape `(len(mean), j_max + 1)`)."""
    m = np.atleast_1d(np.asarray(mean, dtype=float))
    js = np.arange(j_max, dtype=float)
    lg = _LGAMMA[:j_max] if j_max <= J_MAX else \
        np.array([math.lgamma(j + 1.0) for j in range(j_max)])
    with np.errstate(divide='ignore'):
        logp = -m[:, None] + js[None, :] * np.log(np.where(m > 0.0, m, 1.0))[:, None] - lg[None, :]
    W = np.exp(logp)
    W[m <= 0.0, :] = 0.0
    W[m <= 0.0, 0] = 1.0
    tail = np.clip(1.0 - W.sum(axis=1), 0.0, 1.0)
    return np.concatenate((W, tail[:, None]), axis=1)


# ── the section ─────────────────────────────────────────────────────────────────────────

def class_key(order) -> tuple:
    """What a SKU's chain depends on: regime, line law, plan and reorder point.  Two SKUs
    with the same key share one chain; their size tiers may still differ (that is a
    per-SKU lookup)."""
    line = order.demand.line
    plan = plan_slots(order)
    Q = int(order.equilibrium_qty)
    if plan_total(plan) != Q:
        # The ledger orders up to `equilibrium_qty`; the chain packs by the plan.  They
        # agree only when the plan packed the whole declaration -- `field_requirement`
        # raises on an EMPTY plan, not a short one, so a remainder the packer could not
        # place would leave the sim ordering past the plan while the chain priced less.
        raise ValueError(f'SKU {order.sku}: the stock_plan packs {plan_total(plan)} units '
                         f'against an order-up-to of {Q}; the chain prices the plan, the '
                         f'ledger orders the level, and they must agree')
    return (regime_of(order) == FULFILLMENT, line.family,
            tuple(sorted(line.params.items())), plan, int(order.reorder_point))


def unit_for(order, single: bool, n0: int):
    """The storage unit the packer builds for `n0` items of this SKU packed as `single`:
    its own classes, so the size tier is the sim's."""
    if regime_of(order) == FULFILLMENT:
        return FulfillmentBin(order, n0)
    return Singleton(order, n0) if single else Pallet(order, n0)


def tier_prefix(order) -> tuple:
    """What decides the bucket of every kind of this SKU: regime, handling, category and
    dimensions -- the cache key `bucket_of` completes with the kind."""
    shc = order.storage_handle_config
    return (regime_of(order), shc.handling, shc.category, order.length, order.width,
            order.height)


def bucket_of(order, kind: Kind, cache: dict | None = None, prefix: tuple | None = None) -> BinKey:
    """The BinKey a unit of `kind` lands in for this SKU, cached on `tier_prefix` + kind
    (pass `prefix` when calling for several kinds of one order)."""
    key = ((tier_prefix(order) if prefix is None else prefix), kind)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    b = binkey_of(unit_for(order, *kind))
    if cache is not None:
        cache[key] = b
    return b


def section_fragmentation(orders, *, lines_per_day: float | None = None, days=(),
                          lines_per_day_by_sku: dict | None = None,
                          j_max: int = J_MAX) -> dict:
    """The expected extra units per bucket over one channel section: the closed form the
    record stamps.

        {'buckets': {BinKey: {'fielded': int, 'expected_units': float,
                              'expected_extra': float,
                              'expected_extra_at': [per day in `days`]}},
         'expected_extra': float,          # the section sum, stationary
         'expected_extra_at': [...],       # the section sum per day in `days`
         'n_skus', 'n_classes', 'capped_classes', 'positive_lead_skus',
         'stationary_bins_per_sku', 'fielded_bins_per_sku',
         'chains': {class_key: SkuChain}}

    `expected_extra` is `expected_units - fielded` and can be NEGATIVE for a bucket: a
    partial pallet packs into a smaller tier than the full one it replaces, so a plan's
    singleton or small-tier remainder migrates down a tier under churn (the reference
    store's `singleton` bucket empties toward `small`).  The section sum is what the
    shelf needs beyond its declaration.

    `days` (with `lines_per_day`, the section's declared line count) adds the transient:
    the expected extra at each day after a fresh fielding, the line share per SKU being
    `freq / sum freq` as `coverage.daily_demand` weights it -- or, when
    `lines_per_day_by_sku` is given, that map's rate per SKU (a SKU absent from it sees
    no line).  The seam exists because the stationary law needs no line rate while the
    transient is only as good as its rate: the batch sampler's affinity lift spreads a
    section's lines over SKUs differently from the line share (the fulfillment section
    of the reference pair touches about a quarter fewer SKUs by day 25 than the share
    predicts), and a trajectory band would read the sampler's own rate through this
    seam rather than the share.
    """
    orders = list(orders)
    days = np.asarray(list(days), dtype=float)
    n_days = int(days.size)
    if n_days and lines_per_day is None and lines_per_day_by_sku is None:
        raise ValueError("the transient needs the section's lines_per_day")
    W = sum(float(c.demand.relative_frequency) for c in orders)
    by_class: dict = {}
    for c in orders:
        by_class.setdefault(class_key(c), []).append(c)
    chains: dict = {}
    tier_cache: dict = {}
    bucket_index: dict = {}
    stat_bins = fielded_bins = 0.0
    f_arr = np.zeros(0); s_arr = np.zeros(0); at_arr = np.zeros((0, n_days))

    def grow(arrs, n):
        for i, a in enumerate(arrs):
            if a.shape[0] < n:
                arrs[i] = np.concatenate((a, np.zeros((n - a.shape[0],) + a.shape[1:])))
        return arrs

    for key, members in by_class.items():
        rep = members[0]
        chain = chains[key] = SkuChain(rep.demand.line, plan_slots(rep), rp=key[4], j_max=j_max)
        M, K = len(members), len(chain.kinds)
        stat_bins += chain.stationary_bins * M
        fielded_bins += float(len(chain.f)) * M
        bidx = np.empty((M, K), dtype=np.int64)
        for mi, c in enumerate(members):
            prefix = tier_prefix(c)
            for ki, kind in enumerate(chain.kinds):
                b = bucket_of(c, kind, tier_cache, prefix)
                bi = bucket_index.get(b)
                if bi is None:
                    bi = bucket_index[b] = len(bucket_index)
                bidx[mi, ki] = bi
        nb = len(bucket_index)
        f_arr, s_arr, at_arr = grow([f_arr, s_arr, at_arr], nb)
        flat = bidx.ravel()
        f_arr += np.bincount(flat, weights=np.tile(chain.fielded_units, M), minlength=nb)
        s_arr += np.bincount(flat, weights=np.tile(chain.stationary_units, M), minlength=nb)
        if n_days:
            if lines_per_day_by_sku is not None:
                lam = np.array([float(lines_per_day_by_sku.get(c.sku, 0.0)) for c in members])
            else:
                lam = np.array([lines_per_day * float(c.demand.relative_frequency) / W
                                if W > 0.0 else 0.0 for c in members])
            for ti, t in enumerate(days):
                Wj = poisson_weights(lam * t, chain.j_max)            # (M, j_max + 1)
                E = Wj[:, :chain.j_max] @ chain.trajectory_units[:chain.j_max] \
                    + Wj[:, chain.j_max:] * chain.stationary_units[None, :]
                at_arr[:, ti] += np.bincount(flat, weights=(E - chain.fielded_units[None, :]).ravel(),
                                             minlength=nb)
    buckets: dict = {}
    for b, bi in bucket_index.items():
        buckets[b] = {'fielded': int(round(f_arr[bi])), 'expected_units': float(s_arr[bi]),
                      'expected_extra': float(s_arr[bi] - f_arr[bi]),
                      'expected_extra_at': [float(x) for x in at_arr[bi]]}
    n = len(orders)
    return {'buckets': buckets, 'expected_extra': float((s_arr - f_arr).sum()),
            'expected_extra_at': [float(x) for x in at_arr.sum(axis=0)],
            'n_skus': n, 'n_classes': len(chains),
            'capped_classes': sum(1 for ch in chains.values() if ch.solver == 'power_capped'),
            # The ledger's own lead: `_fire_reorders` runs `max(0, round(lead_time_mean))`.
            'positive_lead_skus': sum(
                1 for c in orders
                if round(float(getattr(c, 'lead_time_mean', 0.0) or 0.0)) > 0),
            'stationary_bins_per_sku': (stat_bins / n) if n else 0.0,
            'fielded_bins_per_sku': (fielded_bins / n) if n else 0.0,
            'chains': chains}
