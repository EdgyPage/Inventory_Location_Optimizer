"""expected_travel.py — the calibrated era's pick and put-away seconds per unit as a
CLOSED-FORM EXPECTATION over the demand distribution and the built geometry.

There are no calibration simulations (.scratch/department-calibration, "Derive the
expected-travel closed form", user decision 2026-09-06).  Every input is known at setup --
the catalogue's line shares and quantity rates, the warehouse the run just built, the
placement distribution, the travel speeds, the handling coefficients, the cart -- so the
seconds a day of picking costs is an expectation computed here, and the staffing derivation
balances around it (`staffing.py`).  The derivation of every formula, its assumptions and
the development-time residual against the reference passes are in
`.scratch/department-calibration/assets/expected-travel-derivation.md`; this docstring only
restates what the code needs a reader to know.

## What the simulator charges (the rules this reproduces -- `fast_pick`, `putaway`)

A TASK is one aisle visit.  The picker starts at the mouth (0, 0), walks the bins in
`_plan_aisle_path` order (columns ascending in x; inside a column toward the nearer end
first), pays `|Δx|·x_pace + |Δy|·y_pace` per segment -- to EVERY bin on the path, empty
ones included -- a `cart_swap_coef` per next-fit swap, `M(y)·(intercept + q·per_item +
q·var)` per non-empty bin, and on a one-way lane an exit to the far end and down to the
ground.  No travel between tasks.  A put is costed from the mouth with no travel between
placements.

Two identities follow and are what the tests pin: on a two-way lane a task's x travel is
exactly `x_max` of its bins (the path is monotone in x); on a one-way lane it is exactly
the aisle length.

## The expectation, in one paragraph

A day is `N ~ Normal(n, cv·n)` i.i.d. lines over the section's SKUs (share `π_s`), each
demanding `max(1, Poisson(λ_s))` units, drained through the SKU's bins in the sim's drain
order (forward-pick first, then location).  Bin k of a SKU is REACHED when the line's
quantity exceeds the stock before it -- a Poisson tail -- so every bin carries a visit RATE
per line.  Poissonisation makes bins independent given N, and each aisle then yields, in
closed form: its visit probability (a task), `E[x_max]` (a product-sum over columns), the
expected y walk (a Markov chain over height levels, one transition per column) and the
one-way exit.  Swaps are the renewal count of next-fit over per-visit volumes.  Handling is
the catalogue priced line by line at the bins' heights.  Seconds per unit is the ratio of the
day's expected seconds to its expected units, and the fixed point `n·s(n)·… = K·S·ρ` is one
scalar root-find.

## The seam: `PlacementDist`

The same formula takes two placement distributions, because two genuinely exist:
`initial` (an arm's own bin map, after its initial stock -- what the arm's report is
judged against) and `uniform` (each unit spread evenly over the free bins of its class --
what the pair's DEMAND derives from, arm-independent, and the long-run steady state of a
FIFO restock, which reorders onto a uniformly random free bin).  The check that separated
them is in the derivation note, §3.

THIS MODULE IS PURE: geometry, orders, a pick/put config and numbers in; dicts out.  It
imports no CONFIG and touches no file.  `scipy` is already a dependency (the affinity CSR).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq
from scipy.special import gammainc

from Warehouse.inventory.inventory_common import binkey_of, is_forward_pick
from Warehouse.kernel.cost_model import (DEFAULT_HEIGHT_BRACKETS, SpeedProfile, handle_var,
                                         height_multiplier, per_pick)
from Warehouse.layout.Aisle_Dimensions import (FF_TIER_HEIGHTS, SINGLETON_BIN_HEIGHT,
                                               SIZE_HEIGHTS, unit_bin_width,
                                               FULFILLMENT_BIN_WIDTH)

#: Gauss-Hermite nodes for the day-to-day line-count average (7 points is exact for a
#: polynomial of degree 13 in N; the visit count is smooth in N).
_GH_POINTS: int = 7
#: A line count is never below this fraction of its mean (the sampler floors at 1 line).
_N_FLOOR: float = 0.05


# ── geometry ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AisleGeom:
    """One aisle's grid: `C` columns of `x_step`, `R` rows of `y_step`, a `key` (BinKey)."""
    aisle_id: int
    key: tuple
    C: int
    R: int
    x_step: int
    y_step: int

    @property
    def length(self) -> float:
        """The one-way exit's far end (`Aisle.aisle_width`)."""
        return float(self.C * self.x_step)

    def x_of(self, col: int) -> float:
        """Bin centre x of 1-based column `col` -- `Aisle.Bin.x_phys`."""
        return (col - 1) * self.x_step + self.x_step // 2

    def y_of(self, row: int) -> float:
        """Bin centre y of 1-based row `row` -- `Aisle.Bin.y_phys` of a uniform aisle."""
        return (row - 1) * self.y_step + self.y_step // 2


def _steps_for(unit_type: str, storage_size: str) -> tuple[int, int]:
    """(x_step, y_step) of a uniform aisle, by the same rule `Aisle.__init__` applies."""
    if unit_type == 'fulfillment':
        return FULFILLMENT_BIN_WIDTH, FF_TIER_HEIGHTS[storage_size]
    if unit_type == 'singleton':
        return unit_bin_width('singleton'), SINGLETON_BIN_HEIGHT
    return unit_bin_width(unit_type), SIZE_HEIGHTS[storage_size]


class Geometry:
    """The built warehouse as the expectation reads it: aisles grouped by BinKey.

    `from_warehouse` reads a `Warehouse` (the pair-level `warehouse_meta` or a worker's
    own build); `from_layout_rows` reads `warehouse.db`'s `aisle_layout` rows, so an
    analysis can recompute an expectation off the archive without rebuilding.
    """

    def __init__(self, aisles: list[AisleGeom]) -> None:
        self.aisles: list[AisleGeom] = list(aisles)
        self.by_id: dict[int, AisleGeom] = {a.aisle_id: a for a in self.aisles}
        self.by_class: dict[tuple, list[AisleGeom]] = {}
        for a in self.aisles:
            self.by_class.setdefault(a.key, []).append(a)

    @classmethod
    def from_warehouse(cls, warehouse) -> 'Geometry':
        out = []
        for a in warehouse.aisles:
            if a.bins:
                xs, ys = a.bins[0].x_step, a.bins[0].y_step
            else:
                xs, ys = _steps_for(a.unit_type, a.storage_size)
            out.append(AisleGeom(a.aisle_id, binkey_of(a), int(a.bayXPerAisle),
                                 int(a.bayYPerAisle), int(xs), int(ys)))
        return cls(out)

    @classmethod
    def from_layout_rows(cls, rows) -> 'Geometry':
        """`rows`: dicts with aisle_id, handling_type, category, unit_type, storage_size,
        bay_x, bay_y (the `aisle_layout` table / `save_aisle_layout` rows)."""
        out = []
        for r in rows:
            key = (r['handling_type'], r['category'], r['storage_size'], r['unit_type'])
            xs, ys = _steps_for(r['unit_type'], r['storage_size'])
            out.append(AisleGeom(int(r['aisle_id']), key, int(r['bay_x']), int(r['bay_y']),
                                 xs, ys))
        return cls(out)

    def class_bins(self, key: tuple) -> int:
        return sum(a.C * a.R for a in self.by_class.get(key, ()))

    def class_mean_height_mult(self, key: tuple, brackets) -> float:
        """The height multiplier averaged over every bin of the class (uniform placement)."""
        tot = 0.0; n = 0
        for a in self.by_class.get(key, ()):
            tot += a.C * sum(height_multiplier(brackets, a.y_of(r)) for r in range(1, a.R + 1))
            n += a.C * a.R
        return tot / n if n else 1.0

    def class_mean_travel(self, key: tuple, x_pace: float, y_pace: float) -> float:
        """Mouth-to-bin seconds averaged over every bin of the class (a put's travel)."""
        tot = 0.0; n = 0
        for a in self.by_class.get(key, ()):
            sx = sum(a.x_of(c) for c in range(1, a.C + 1)) * a.R
            sy = sum(a.y_of(r) for r in range(1, a.R + 1)) * a.C
            tot += sx * x_pace + sy * y_pace
            n += a.C * a.R
        return tot / n if n else 0.0


# ── the placement distribution (the seam) ───────────────────────────────────────────────

@dataclass(frozen=True)
class Site:
    """One storage unit of a SKU, in drain order: its quantity and WHERE it is -- a concrete
    bin (`initial`: aisle_id, col, row set) or only its class (`uniform`)."""
    key: tuple
    qty: int
    aisle_id: int | None = None
    col: int | None = None
    row: int | None = None


class PlacementDist:
    """`sites[sku] -> [Site, ...]` in the sim's drain order, plus which kind it is.

    `initial(bin_map, geometry)`: `bin_map[sku] = [(aisle_id, bayX, bayY, qty), ...]` as an
    arm's manager holds them after the initial stock; sorted forward-pick first, then
    location -- `Task.from_batch`'s order.
    `uniform(units_by_sku)`: `units_by_sku[sku] = [StorageUnit, ...]` (the packs
    `viable_storage_units` produces for the SKU's stock); each unit is spread over its
    class.  Forward-pick units first, then the given order.
    """

    def __init__(self, kind: str, sites: dict) -> None:
        if kind not in ('initial', 'uniform'):
            raise ValueError(f"placement kind must be 'initial' or 'uniform'; got {kind!r}")
        self.kind = kind
        self.sites = sites

    @classmethod
    def initial(cls, bin_map: dict, geometry: Geometry) -> 'PlacementDist':
        sites: dict = {}
        for sku, bins in bin_map.items():
            rows = []
            for aid, bx, by, q in bins:
                a = geometry.by_id[aid]
                rows.append((0 if a.key[3] == 'singleton' else 1, aid, bx, by, q, a.key))
            rows.sort(key=lambda t: t[:4])
            sites[sku] = [Site(key, int(q), aid, int(bx), int(by)) for _, aid, bx, by, q, key in rows]
        return cls('initial', sites)

    @classmethod
    def uniform(cls, units_by_sku: dict) -> 'PlacementDist':
        sites: dict = {}
        for sku, units in units_by_sku.items():
            fwd = [u for u in units if is_forward_pick(u)]
            rest = [u for u in units if not is_forward_pick(u)]
            sites[sku] = [Site(binkey_of(u), int(u.quantity)) for u in (*fwd, *rest)]
        return cls('uniform', sites)


# ── stage 1: the section's per-line rates (independent of n) ────────────────────────────

@dataclass
class SectionRates:
    """Everything a day of `n` lines scales linearly from.  Per LINE: expected units,
    bin visits, handling seconds, per-visit volume moments; and the visit rates -- per
    aisle (C, R) matrices for `initial`, one scalar per class for `uniform`."""
    kind: str
    n_skus: int = 0
    units_per_line: float = 0.0
    visits_per_line: float = 0.0
    handling_s_per_line: float = 0.0
    vol_per_line: float = 0.0
    vol2_per_line: float = 0.0
    unplaced_skus: int = 0
    aisle_rates: dict = field(default_factory=dict)     # aisle_id -> ndarray (C, R)
    class_rates: dict = field(default_factory=dict)     # key -> float (per bin)


def _poisson_tail(upto: int, lam: float) -> np.ndarray:
    """`P(X > j)` for j = 0..upto-1, X ~ Poisson(lam); `P(q > 0) = 1` for q = max(1, X)."""
    t = gammainc(np.arange(1, upto + 1, dtype=float), lam)     # regularised lower gamma
    if upto > 0:
        t[0] = 1.0
    return t


def accumulate(orders, pick_cfg, dist: PlacementDist, geometry: Geometry) -> SectionRates:
    """Stage 1: walk the section once and accumulate the per-line quantities.

    `orders` is the channel section (`staffing.regime_orders`); `pick_cfg` is the channel's
    `PickConfig` (duck-typed: `pick_intercept`, `pick_per_item`, the four handling
    coefficients, `height_brackets`).  A SKU with no sites (nothing placed) contributes its
    line share to nothing and is counted in `unplaced_skus`.
    """
    brackets = tuple(getattr(pick_cfg, 'height_brackets', DEFAULT_HEIGHT_BRACKETS))
    intercept = float(pick_cfg.pick_intercept)
    per_item = float(pick_cfg.pick_per_item)
    w = np.array([float(c.demand.relative_frequency) for c in orders])
    W = float(w.sum())
    out = SectionRates(kind=dist.kind, n_skus=len(orders))
    if W <= 0.0:
        return out
    class_M: dict = {}
    for c, wi in zip(orders, w):
        sites = dist.sites.get(c.sku)
        if not sites:
            out.unplaced_skus += 1
            continue
        pi = float(wi / W)               # Python floats from here on: the record is JSON
        lam = float(c.demand.quantity_rate)
        var = handle_var(c.weight, c.volume(), pick_cfg.pick_weight_coef,
                         pick_cfg.pick_volume_coef, pick_cfg.pick_weight_fn,
                         pick_cfg.pick_volume_fn)
        vol = float(c.volume())
        total = sum(s.qty for s in sites)
        tail = _poisson_tail(total, lam)            # P(q > j), j = 0..total-1
        cum = 0
        for s in sites:
            reach = 1.0 if cum == 0 else float(tail[cum]) if cum < total else 0.0
            if reach < 1e-12:
                break
            seg = tail[cum:cum + s.qty]
            eu = float(seg.sum())                                    # E[units from this bin]
            eu2 = float(((2 * np.arange(s.qty) + 1) * seg).sum())    # E[units²]
            if dist.kind == 'initial':
                a = geometry.by_id[s.aisle_id]
                M = height_multiplier(brackets, a.y_of(s.row))
                m = out.aisle_rates.get(s.aisle_id)
                if m is None:
                    m = out.aisle_rates[s.aisle_id] = np.zeros((a.C, a.R))
                m[s.col - 1, s.row - 1] += pi * reach
            else:
                M = class_M.get(s.key)
                if M is None:
                    M = class_M[s.key] = geometry.class_mean_height_mult(s.key, brackets)
                nb = geometry.class_bins(s.key)
                if nb:
                    out.class_rates[s.key] = out.class_rates.get(s.key, 0.0) + pi * reach / nb
            out.handling_s_per_line += pi * reach * M * (intercept + eu * (per_item + var))
            out.units_per_line += pi * eu
            out.visits_per_line += pi * reach
            out.vol_per_line += pi * reach * eu * vol
            out.vol2_per_line += pi * reach * eu2 * vol * vol
            cum += s.qty
    return out


# ── stage 2: the routing chain per aisle ────────────────────────────────────────────────

def _aisle_routing(m: np.ndarray, a: AisleGeom, one_way: bool) -> tuple[float, float, float]:
    """(P(aisle visited), E[x travel], E[y travel]) in INCHES for visit-rate matrix `m`."""
    C, R = a.C, a.R
    v = 1.0 - np.exp(-m)                              # per-bin visit probability
    u = 1.0 - np.prod(1.0 - v, axis=1)                # per-column
    surv = np.cumprod((1.0 - u)[::-1])[::-1]          # prod_{c' >= c} (1 - u)
    p_visit = 1.0 - surv[0]
    if p_visit < 1e-15:
        return 0.0, 0.0, 0.0
    tail_above = np.append(surv[1:], 1.0)             # prod_{c' > c} (1 - u)
    xs = np.array([a.x_of(c) for c in range(1, C + 1)], float)
    ex = p_visit * a.length if one_way else float((xs * u * tail_above).sum())
    ys = np.array([0.0] + [a.y_of(r) for r in range(1, R + 1)])
    D = np.abs(ys[:, None] - ys[None, :])             # |y_i - y_j| over the R+1 levels
    state = np.zeros(R + 1); state[0] = 1.0           # the picker starts at the ground
    ey = 0.0
    for c in range(C):
        uc = u[c]
        if uc < 1e-15:
            continue
        vr = v[c]
        q = vr / vr.sum()                             # single-visit row marginal
        above = np.cumprod((1.0 - vr)[::-1])[::-1]; above = np.append(above[1:], 1.0)
        below = np.cumprod(1.0 - vr); below = np.insert(below[:-1], 0, 1.0)
        e_high = float((ys[1:] * vr * above).sum()) / uc
        e_low = float((ys[1:] * vr * below).sum()) / uc
        entry = float(state @ D[:, 1:] @ q)
        ey += uc * (entry + (e_high - e_low))         # entry + the exact expected span
        state = (1.0 - uc) * state + uc * np.concatenate(([0.0], q))
    if one_way:
        ey += float(state @ ys)                       # the exit descends from the last height
    return float(p_visit), float(ex), float(ey)


def _nodes(cv: float) -> list[tuple[float, float]]:
    """(scale, weight) pairs averaging over N ~ Normal(n, cv·n) -- one pair when cv = 0."""
    if cv <= 0.0:
        return [(1.0, 1.0)]
    x, w = np.polynomial.hermite_e.hermegauss(_GH_POINTS)
    w = w / w.sum()
    return [(max(_N_FLOOR, 1.0 + cv * xi), float(wi)) for xi, wi in zip(x, w)]


def routing(rates: SectionRates, geometry: Geometry, n: float, cv: float,
            one_way: bool) -> dict:
    """Stage 2 at `n` lines a day: `{tasks, x_inches, y_inches}` per day, averaged over the
    day-to-day line count.  `initial` walks every aisle with rates; `uniform` walks each
    class once and multiplies by its replica count."""
    tasks = ex = ey = 0.0
    for scale, wt in _nodes(cv):
        k = n * scale
        if rates.kind == 'initial':
            for aid, m in rates.aisle_rates.items():
                p, x, y = _aisle_routing(m * k, geometry.by_id[aid], one_way)
                tasks += wt * p; ex += wt * x; ey += wt * y
        else:
            for key, r in rates.class_rates.items():
                # Every aisle of a class carries the same constant rate, so the chain is
                # computed ONCE per aisle SHAPE and multiplied by the replica count -- a
                # class of 2,700 fulfillment aisles is one evaluation, not 2,700.
                shapes: dict = {}
                for a in geometry.by_class.get(key, ()):
                    shapes.setdefault((a.C, a.R, a.x_step, a.y_step), [0, a])
                    shapes[(a.C, a.R, a.x_step, a.y_step)][0] += 1
                for count, a in shapes.values():
                    p, x, y = _aisle_routing(np.full((a.C, a.R), r * k), a, one_way)
                    tasks += wt * count * p; ex += wt * count * x; ey += wt * count * y
    return {'tasks': tasks, 'x_inches': ex, 'y_inches': ey}


# ── the day, and the fixed point ────────────────────────────────────────────────────────

def expected_swaps(vol_per_day: float, e_v: float, e_v2: float, cart_cap: float) -> float:
    """Next-fit swaps per day as a renewal count over per-visit volumes: the cart holds
    `(cap - E[v] + E[v²]/2E[v]) / E[v]` visits in expectation (Wald + the residual)."""
    if vol_per_day <= 0.0 or e_v <= 0.0:
        return 0.0
    denom = cart_cap - e_v + e_v2 / (2.0 * e_v)
    return vol_per_day / max(denom, e_v)


def expected_pick(rates: SectionRates, geometry: Geometry, pick_cfg, n: float,
                  cv: float) -> dict:
    """The expected day at `n` lines: every component per day, and `s_pick`.

    `pick_cfg` supplies `x_speed`/`y_speed` (ft/s), `one_way`, `cart_swap_coef` and
    `cart` (its `capacity()`).  `units == 0` yields `s_pick = 0.0` (an empty section)."""
    _speed = SpeedProfile(pick_cfg.x_speed, pick_cfg.y_speed)
    x_pace, y_pace = _speed.x_pace, _speed.y_pace
    r = routing(rates, geometry, n, cv, bool(getattr(pick_cfg, 'one_way', False)))
    units = n * rates.units_per_line
    visits = n * rates.visits_per_line
    vol = n * rates.vol_per_line
    e_v = rates.vol_per_line / rates.visits_per_line if rates.visits_per_line else 0.0
    e_v2 = rates.vol2_per_line / rates.visits_per_line if rates.visits_per_line else 0.0
    swaps = expected_swaps(vol, e_v, e_v2, float(pick_cfg.cart.capacity()))
    travel_x = r['x_inches'] * x_pace
    travel_y = r['y_inches'] * y_pace
    swap_s = swaps * float(pick_cfg.cart_swap_coef)
    handling = n * rates.handling_s_per_line
    total = travel_x + travel_y + swap_s + handling
    return {
        'lines': float(n), 'cv': float(cv), 'units': float(units), 'visits': float(visits),
        'tasks': float(r['tasks']),
        'travel_x_s': float(travel_x), 'travel_y_s': float(travel_y), 'swaps': float(swaps),
        'swap_s': float(swap_s), 'handling_s': float(handling), 'total_s': float(total),
        's_pick': float(total / units) if units > 0 else 0.0,
        'units_per_line': float(rates.units_per_line), 'placement': rates.kind,
        'unplaced_skus': int(rates.unplaced_skus),
    }


def solve_n(rates: SectionRates, geometry: Geometry, pick_cfg, cv: float, *,
            capacity_s: float, n_max: float) -> dict:
    """The fixed point: the `n` lines a day at which the expected day exactly fills the
    crew's capacity `K·S·ρ`.  `total_s(n)` is increasing in n, so the root is unique;
    Brent's method finds it to 1e-6 relative in ~10 evaluations.  A capacity the section
    cannot absorb even at `n_max` lines (every SKU every day) returns the day at `n_max`
    with `saturated = True` -- the same clamp `staffing.batch_content` applies."""
    if rates.units_per_line <= 0.0 or capacity_s <= 0.0:
        return {**expected_pick(rates, geometry, pick_cfg, 0.0, cv), 'saturated': False,
                'capacity_s': capacity_s}
    def g(n):
        return expected_pick(rates, geometry, pick_cfg, n, cv)['total_s'] - capacity_s
    hi = max(1.0, float(n_max))
    if g(hi) <= 0.0:
        return {**expected_pick(rates, geometry, pick_cfg, hi, cv), 'saturated': True,
                'capacity_s': capacity_s}
    lo = 1e-6
    n = float(brentq(g, lo, hi, rtol=1e-6, maxiter=100))
    return {**expected_pick(rates, geometry, pick_cfg, n, cv), 'saturated': False,
            'capacity_s': capacity_s}


# ── put-away ────────────────────────────────────────────────────────────────────────────

def put_site_pricer(geometry: Geometry, dist: PlacementDist, put_cost, put_speed,
                    brackets=None):
    """A `(unit) -> (travel_s, height_mult)` for `staffing.implied_reorders`: the put's
    travel from the mouth and its height multiplier under the placement distribution --
    the class-uniform mean for `uniform`; for `initial`, the mean over the SKU's own bins
    (a reorder refills the SKU's locations)."""
    brackets = tuple(brackets or getattr(put_cost, 'height_brackets', DEFAULT_HEIGHT_BRACKETS))
    x_pace, y_pace = put_speed.x_pace, put_speed.y_pace
    cache: dict = {}

    def price(unit):
        key = binkey_of(unit)
        if dist.kind == 'uniform':
            hit = cache.get(key)
            if hit is None:
                hit = cache[key] = (geometry.class_mean_travel(key, x_pace, y_pace),
                                    geometry.class_mean_height_mult(key, brackets))
            return hit
        sites = [s for s in dist.sites.get(unit.order.sku, ()) if s.key == key]
        if not sites:
            return (geometry.class_mean_travel(key, x_pace, y_pace),
                    geometry.class_mean_height_mult(key, brackets))
        tr = 0.0; M = 0.0
        for s in sites:
            a = geometry.by_id[s.aisle_id]
            tr += a.x_of(s.col) * x_pace + a.y_of(s.row) * y_pace
            M += height_multiplier(brackets, a.y_of(s.row))
        return tr / len(sites), M / len(sites)
    return price
