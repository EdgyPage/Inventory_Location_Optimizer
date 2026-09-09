"""staffing.py — the calibrated era's staffing DERIVATION, as a pure module.

DEMAND per channel is the declared staffing input of the calibrated era (ADR-0004,
.scratch/department-calibration, "Fit the store's window to its own steady state" --
reversing "Define the calibrated era", which declared the pickers): a mean day is a
declared fraction of the section's SKUs drawn as lines, and everything a run fields is
DERIVED from that and ONE more declared scalar, the joint FIRST-TIME CONFIDENCE `c` -- the
probability a pick is completed the first time, reached on its day AND filled from its
shelf -- live at setup:

    lines per day      n = demand × N_skus                     (the declared day, mean)
    day law            N ~ Normal(n, cv·n), cv the declared batch spread   (declared)
    line floor         the smallest floor_lines whose first-pass fill >= sqrt(c)
                                                              (`coverage.solve_floor_lines`)
    pick load          E[W] = demanded units/day × s_pick      (DEMANDED, never served)
    picking crew       the smallest integer K with E[(W - K·S)^+] / E[W] <= 1 - sqrt(c)
                                                              (`solve_pickers`, per channel)
    batch content      one batch = one day's demand          (a BatchConfig mean fraction)
    put load           units × f_put                         (units put per unit picked)
    put crew           ceil(Σ_ch put load_ch × s_put_ch ÷ (S × ρ_put))   (ONE site crew)
    receive load       packs the script's LOTS pack into × f_recv
    receiving crew     ceil(receive seconds ÷ (S × ρ_recv))  (ONE site crew)

NOTHING IS LOST under the era (memory `nothing-is-lost-under-the-era`): a pick the day cut
or the shelf could not fill is re-offered next day and a lead-0 top-up lands before the
re-attempt, so the crew picks ALL of demand and is sized on DEMANDED units.  The previous
derivation priced pickers on SERVED units (demand × fill rate) and under-staffed both
channels by exactly that factor; `test_first_time_guarantee` pins the sabotage.  Put-away
and receiving keep their declared utilization targets: a receiving backlog on a heavy
trailer is a legitimate scenario the inbound campaign exists to study.

`s_pick` and `s_put` are BOTH per channel, and both are seconds per unit: CLOSED-FORM
EXPECTATIONS over the catalogue's demand distribution and the warehouse geometry the run
built (`Optimization/simconfig/expected_travel.py`, "Derive the expected-travel closed form").
There are no calibration simulations and no calibration record.  The harness computes them
at setup and hands them in as resolved constants (a declared `--s-pick-*` / `--s-put`
override replaces the expectation and is recorded `declared`).  `s_recv` is EXACT from the
script: an unload has no travel term, so every pack's seconds are computable from the
catalogue and the cost model alone.

**s_put IS PER CHANNEL** ("Give put-away a per-channel expected travel", 2026-09-08).  It was
one site value until then, and the two sections do not share a geometry: the era re-read
measured 98.5 s/unit put away on the store against 29.2 on fulfillment, a 3.4× spread that a
single site price averaged away.  The site TOTAL stayed right -- so the crew was correctly
SIZED -- while both leaves' expected utilization sat ~0.21 outside the band, in opposite
directions.  Picking never had this defect (its constant was always per channel) and neither
did receiving (its per-channel seconds come straight from the script).  There is now no
site-wide put price anywhere in the derived record: `put.s_put` is a map keyed by channel.

THIS MODULE IS PURE.  Inputs, the resolved constants and the catalogue / script totals go
in; the derived dict comes out.  It imports no CONFIG, reads no settings and
touches no file, so the arithmetic is testable against a hand computation without a run
(`Tests/unit/test_staffing_derivation.py`).  The harness seam that feeds it is
`Optimization/simdriver/workunits._derive_staffing_for_pair`, which runs AFTER batch
precompute because the receiving crew needs the packs the script implies -- and the script
is itself derived from the pickers, which is why the derivation has two stages:

    stage A  (the catalogue and the geometry)  s_pick at the declared day, the picking crew
                               (solved), batch content -- BEFORE precompute
    stage B  (the script)      put load, packs, the two site crews -- AFTER precompute

Every scalar default below is a declared ASSUMPTION of the era, never a measurement, and the
record says so (`PROVENANCE`, "Design the staffing record", decision 5).  The derived block
carries `derived`; a copied constant keeps the provenance it arrived with.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from Inbound.pack import receive as _receive
from Inbound.unload import UnloadCost, unload_cost
from Warehouse.kernel.cost_model import handle_var, per_pick
from Warehouse.kernel.regime import regime_of
from Warehouse.operations.putaway import PutawayCost
from Optimization.simconfig.constants import PROVENANCE

#: The channel names the derivation knows, in the order the harness runs them.
CHANNELS: tuple[str, ...] = ('store', 'fulfillment')

#: The three departments the utilization bands are drawn for ("Declare the equilibrium
#: bands", decision 1): one ρ each, all 0.85 by decision.
DEPARTMENTS: tuple[str, ...] = ('pick', 'put', 'recv')


# ── the pricing config: the coefficients a channel's labour is predicted with ─────────

@dataclass(frozen=True)
class PricingConfig:
    """The handling coefficients one channel's analytic prediction is priced with.

    The subset of a `PickConfig` the script-only prediction needs -- no speeds, no height
    brackets, because the prediction is at GROUND height with no travel term (the travel
    share is what a reference run adds).  `from_pick_config` lifts them off the channel's
    canonical pick config; `put()` / `recv()` derive the other two crews' prices by the
    same chain the runner uses (`PutawayCost.from_pick`, `UnloadCost.from_putaway`).
    """
    name: str
    intercept: float
    per_item: float
    weight_coef: float
    volume_coef: float
    weight_fn: str
    volume_fn: str

    @classmethod
    def from_pick_config(cls, cfg, name: str = '') -> 'PricingConfig':
        return cls(name=name or getattr(cfg, 'name', '') or 'pick',
                   intercept=float(cfg.pick_intercept), per_item=float(cfg.pick_per_item),
                   weight_coef=float(cfg.pick_weight_coef),
                   volume_coef=float(cfg.pick_volume_coef),
                   weight_fn=str(cfg.pick_weight_fn), volume_fn=str(cfg.pick_volume_fn))

    def var(self, order) -> float:
        """The per-unit weight/volume handling term for `order` under these coefficients."""
        return handle_var(order.weight, order.volume(), self.weight_coef, self.volume_coef,
                          self.weight_fn, self.volume_fn)

    def put(self, *, intercept_scale: float, item_ratio: float) -> PutawayCost:
        return PutawayCost(intercept=self.intercept * intercept_scale,
                           per_item=self.per_item * item_ratio,
                           weight_coef=self.weight_coef, volume_coef=self.volume_coef,
                           weight_fn=self.weight_fn, volume_fn=self.volume_fn)

    def recv(self, *, put_intercept_scale: float, put_item_ratio: float,
             recv_intercept_scale: float) -> UnloadCost:
        return UnloadCost.from_putaway(
            self.put(intercept_scale=put_intercept_scale, item_ratio=put_item_ratio),
            intercept_scale=recv_intercept_scale)


# ── stage A: the catalogue alone ────────────────────────────────────────────────────────

def analytic_pick(orders, pricing: PricingConfig) -> dict:
    """The script-only prediction of a channel's picking labour per UNIT, from the catalogue.

    Weighted the way the sampler weights: a SKU enters a batch in proportion to its
    `relative_frequency` (affinity lift ignored -- it redistributes lines, it does not add
    them), and a line demands the MEAN of the SKU's stamped line law (`Demand.line.mean()`).
    Before the stamp this read `max(1, quantity_rate)` -- a drift from the `λ + e^-λ` the
    sampler and the expectation use, corrected by "Stamp the line distribution on the SKU":
    the recorded `analytic` block moves by the `e^-λ` term; the era's batch content never read
    it (it reads `expected_travel`'s `units_per_line`).  One intercept per LINE
    (one bin visit for one SKU) is the line-count floor ("Choose the calibration
    procedure", decision 7); the per-item charge and `qty × handle_var` are per unit; the
    height multiplier is M(0) = 1 (ground) and there is no travel term.  Returns

        {'seconds_per_unit', 'units_per_line', 'n_skus', 'pricing_config', 'line_families'}

    `seconds_per_unit` is what a travel share scales into a seeded `s_pick`.  Empty
    orders yield zeros rather than raising: a store-only catalogue has no fulfillment
    section, and the caller records that as an absent channel.
    """
    w_sum = 0.0
    wq_sum = 0.0
    sec_sum = 0.0
    n = 0
    fams: dict = {}                                     # law family -> SKUs read (provenance)
    for c in orders:
        w = float(c.demand.relative_frequency)
        line = c.demand.line
        q = line.mean()                                 # E[units per line], off the stamped law
        fams[line.family] = fams.get(line.family, 0) + 1
        w_sum += w
        wq_sum += w * q
        sec_sum += w * per_pick(1.0, pricing.intercept, pricing.var(c), q, pricing.per_item)
        n += 1
    if wq_sum <= 0.0:
        return {'seconds_per_unit': 0.0, 'units_per_line': 0.0, 'n_skus': n,
                'pricing_config': pricing.name, 'line_families': fams}
    return {'seconds_per_unit': sec_sum / wq_sum,
            'units_per_line': wq_sum / w_sum,
            'n_skus': n,
            'pricing_config': pricing.name,
            'line_families': fams}


def pick_capacity(pickers: int, day_seconds: float, rho_pick: float) -> float:
    """Seconds of picking one channel's crew is expected to deliver per day: K × S × ρ.

    FLAG-OFF ONLY since ADR-0004: the era declares demand and solves the crew from the
    first-time confidence (`solve_pickers`), and its capacity is the granted day `K × S`
    with the headroom a consequence rather than a target."""
    return float(pickers) * float(day_seconds) * float(rho_pick)


# ── the first-time guarantee: the crew side ────────────────────────────────────────────

def first_time_split(confidence: float) -> float:
    """Each side's share of the joint first-time confidence: `sqrt(c)`, split EQUALLY
    between the shelf (first-pass fill >= sqrt(c)) and the crew (expected cut share of
    units <= 1 - sqrt(c)) -- ADR-0004, the one declared scalar and nothing else authored.
    Refuses a confidence outside (0, 1): 0 promises nothing and 1 is staffing to the
    peak by another name."""
    c = float(confidence)
    if not (0.0 < c < 1.0):
        raise ValueError(f'first_time_confidence must lie in (0, 1); got {confidence!r}')
    return math.sqrt(c)


def line_moments(orders) -> dict:
    """The first two moments of ONE line's units over a section, weighted the way the
    sampler weights (a SKU's line share `π_s = freq / Σ freq`), off each SKU's STAMPED
    line law: `{'m1': E[q], 'm2': E[q²], 'var': Var[q], 'n_skus'}`.

    `E[q²] = Σ_j (2j + 1) P(q > j)` from `line.survival`, summed to the law's 1 - 1e-12
    quantile (the tail past it is below float noise on a Poisson).  Zeros on an empty
    section or a zero weight sum.
    """
    w_sum = 0.0
    m1 = 0.0
    m2 = 0.0
    n = 0
    for c in orders:
        w = float(c.demand.relative_frequency)
        if w <= 0.0:
            n += 1
            continue
        line = c.demand.line
        upto = int(line.quantile(1.0 - 1e-12)) + 1
        tail = line.survival(upto)
        e1 = float(tail.sum())
        e2 = float(((2.0 * np.arange(upto) + 1.0) * tail).sum())
        w_sum += w
        m1 += w * e1
        m2 += w * e2
        n += 1
    if w_sum <= 0.0:
        return {'m1': 0.0, 'm2': 0.0, 'var': 0.0, 'n_skus': n}
    m1 /= w_sum
    m2 /= w_sum
    return {'m1': m1, 'm2': m2, 'var': max(0.0, m2 - m1 * m1), 'n_skus': n}


def units_cv(lines_per_day: float, cv_lines: float, moments: dict) -> float:
    """The coefficient of variation of a DAY's units under the declared law: `N ~ Normal(n,
    cv·n)` lines, each demanding iid units with the section's moments, so

        E[U] = n·m1        Var[U] = n·Var[q] + (cv·n)²·m1²

    ("Fit the store's window to its own steady state", decision 8: the Gaussian line count
    carries all but ~0.001 of the unit cv on the reference pair, so the day's work is one
    Gaussian draw to promise on).  Zero when the day has no units."""
    n = float(lines_per_day)
    m1 = float(moments['m1'])
    if n <= 0.0 or m1 <= 0.0:
        return 0.0
    var = n * float(moments['var']) + (float(cv_lines) * n) ** 2 * m1 * m1
    return math.sqrt(var) / (n * m1)


def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _Phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def partial_expectation(mean: float, sd: float, cap: float) -> float:
    """`E[(W - cap)^+]` for `W ~ Normal(mean, sd)`: `sd·[φ(z) - z·(1 - Φ(z))]` at
    `z = (cap - mean) / sd` -- the expected overflow of a day's work past the crew's cap.
    Degenerates to `max(0, mean - cap)` at `sd = 0`."""
    mean = float(mean); sd = float(sd); cap = float(cap)
    if sd <= 0.0:
        return max(0.0, mean - cap)
    z = (cap - mean) / sd
    return sd * (_phi(z) - z * (1.0 - _Phi(z)))


def cut_share(load_s: float, sd_s: float, pickers: int, day_seconds: float) -> float:
    """The expected share of a day's work the cut leaves standing under a crew of `pickers`:
    `E[(W - K·S)^+] / E[W]`, `W ~ Normal(load_s, sd_s)`.  The per-PICK guarantee: units
    cut over units demanded, in expectation over the declared day law.  Zero when there
    is no load."""
    if load_s <= 0.0:
        return 0.0
    return partial_expectation(load_s, sd_s, float(pickers) * float(day_seconds)) / float(load_s)


def solve_pickers(load_s: float, cv: float, day_seconds: float, cut_share_max: float,
                  *, k_max: int = 100_000) -> dict:
    """The smallest integer crew whose expected cut share is at or under `cut_share_max`.

    `load_s` is E[W], the day's expected picking seconds at the DECLARED demand priced at
    the channel's expected seconds per unit; `cv` the day's unit cv (`units_cv`); the day
    law is `W ~ Normal(load_s, cv·load_s)`.  `cut_share(K)` is decreasing in K, and it is
    never below the plain excess `(E[W] - K·S) / E[W]` (the overflow of a spread-less day),
    so no crew under `(1 - bound)·E[W] / S` can meet the bound: the search walks up from
    that crew and stops at the first K inside it, which monotonicity makes the SMALLEST.
    (Walking up from the crew that fits the MEAN day would skip feasible crews below it
    whenever the spread is small -- under ~0.06 at the 0.95 confidence.)  Returns

        {'pickers', 'cut_share', 'cut_share_max', 'load_s', 'sd_s', 'cv',
         'expected_utilization'}                              (utilization = E[W] / (K·S))

    A section with no load fields nobody (`pickers = 0`); `k_max` guards a runaway (a
    bound of 0 with a positive sd is unreachable and says so).
    """
    S = float(day_seconds)
    load = float(load_s)
    bound = float(cut_share_max)
    if not (0.0 <= bound < 1.0):
        raise ValueError(f'cut_share_max must lie in [0, 1); got {cut_share_max!r}')
    if load <= 0.0:
        return {'pickers': 0, 'cut_share': 0.0, 'cut_share_max': bound, 'load_s': 0.0,
                'sd_s': 0.0, 'cv': float(cv), 'expected_utilization': 0.0}
    sd = float(cv) * load
    K = max(1, math.ceil((1.0 - bound) * load / S - 1e-12))
    while True:
        share = cut_share(load, sd, K, S)
        if share <= bound:
            break
        K += 1
        if K > k_max:
            raise ValueError(
                f'no crew up to {k_max} pickers brings the expected cut share under '
                f'{bound!r} (load {load:,.0f} s/day, sd {sd:,.0f} s): the bound is not '
                f'reachable under this day law')
    return {'pickers': int(K), 'cut_share': float(share), 'cut_share_max': bound,
            'load_s': load, 'sd_s': sd, 'cv': float(cv),
            'expected_utilization': load / (K * S)}


# ── the one crew reader ──────────────────────────────────────────────────────────────

def picker_key(channel: str | None) -> str:
    """The staffing-inputs key holding this channel's DECLARED picker count (flag-off)."""
    return 'ff_pickers' if channel == 'fulfillment' else 'store_pickers'


def channel_crew(record: dict, *, channel: str | None, pair: str | None = None) -> int:
    """This channel's picking crew off a staffing record -- THE ONE READER (ADR-0004).

    Prefers the DERIVED block (`derived.channels[<channel>].pickers`: the solved crew under
    the era, the declared one restated on an older era record), and falls back to the
    declared input (`inputs.<store|ff>_pickers`, flag-off).  Two record shapes are read:
    the run spec / `sim_result` shape, where `derived` is keyed by PAIR (pass `pair`), and
    the worker payload, where it is the pair's block itself; a pre-derivation payload is the
    inputs dict alone.  Raises `KeyError` naming the channel when neither holds a crew,
    because a reader that fell through to a literal here is the store-crew-on-every-
    fulfillment-leaf trap ("Build the picker staffing seam").
    """
    ch = channel or 'store'
    derived = (record or {}).get('derived') or {}
    if pair is not None and pair in derived:
        derived = derived[pair]
    section = (derived.get('channels') or {}).get(ch) if isinstance(derived, dict) else None
    if isinstance(section, dict) and section.get('pickers') is not None:
        return int(section['pickers'])
    inputs = (record or {}).get('inputs', record) or {}
    v = inputs.get(picker_key(ch))
    if v is None:
        raise KeyError(
            f'the staffing record carries no picking crew for channel {ch!r}: neither a '
            f'derived block (under the era) nor the declared {picker_key(ch)!r} (flag-off)')
    return int(v)


def daily_demand(capacity_s: float, s_pick: float) -> float:
    """Units per day the channel's capacity buys at `s_pick` seconds per unit."""
    if s_pick <= 0.0:
        raise ValueError(f's_pick must be positive seconds per unit; got {s_pick!r}')
    return capacity_s / float(s_pick)


def batch_content(demand_units: float, units_per_line: float, n_skus: int,
                  declared_mean: float, declared_std: float) -> dict:
    """One batch = one day's demand, as the sampler's (mean_fraction, std_fraction).

    The sampler draws a LINE count ~ N(mean·N, std·N) and each line demands its stamped
    law's mean units (`units_per_line`), so the mean fraction that delivers `demand_units` is
    `(demand ÷ units_per_line) ÷ N`.  The spread keeps the DECLARED coefficient of
    variation (the flag-off `std ÷ mean`, 1/3 for the store, 1/4 for fulfillment) so a
    derived script has the same relative day-to-day variability the archive's did.  A
    mean above 1.0 -- every SKU every day, and then some -- is CLAMPED and flagged: the
    sampler cannot draw more lines than SKUs, and the declared crew is then oversized for
    the catalogue (the verifier will show the idle capacity; `saturated` says why).
    """
    if n_skus <= 0 or units_per_line <= 0.0:
        return {'mean_lines': 0.0, 'mean_fraction': 0.0, 'std_fraction': 0.0,
                'saturated': False}
    mean_lines = demand_units / units_per_line
    mean_fraction = mean_lines / n_skus
    cv = (declared_std / declared_mean) if declared_mean > 0 else 0.0
    saturated = bool(mean_fraction > 1.0)
    if saturated:
        mean_fraction = 1.0
    return {'mean_lines': mean_lines,
            'mean_fraction': mean_fraction,
            'std_fraction': mean_fraction * cv,
            'saturated': saturated}


# ── stage B: the script ─────────────────────────────────────────────────────────────────

@dataclass
class ScriptTotals:
    """What one channel's precomputed batch script demands, summed over its batches.

    `units` / `lines` are the demand itself; `analytic_pick_s` is the same script priced
    with `analytic_pick`'s formula line by line (so the record can hold the script-only
    prediction NEXT to the measured value, and their ratio is the travel share).  The
    put and receiving totals come from `implied_reorders`: `lots` the reorders the
    script's lines fire, `put_units` the merchandise units put away (= units received),
    `put_s` their put-away seconds, `packs` the storage units the lots pack into, `recv_s`
    their EXACT unload seconds.  `unknown_skus` counts script lines whose SKU the catalogue
    map did not hold -- zero on a healthy run, and reported rather than silently skipped.
    `per_sku_lines` keeps every line's quantity per SKU IN SCRIPT ORDER, because the
    order-up-to rule's lots depend on the sequence (`fired_lots`).
    """
    batches: int = 0
    units: float = 0.0
    lines: float = 0.0
    analytic_pick_s: float = 0.0
    lots: float = 0.0
    put_units: float = 0.0
    put_s: float = 0.0
    packs: float = 0.0
    recv_s: float = 0.0
    unknown_skus: int = 0
    per_sku_lines: dict = field(default_factory=dict)

    def per_day(self, value: float) -> float:
        """A total as a per-DAY quantity under the era's one batch per day."""
        return value / self.batches if self.batches else 0.0


def script_totals(batches, orders_by_sku: dict, pricing: PricingConfig) -> ScriptTotals:
    """Stage B's demand side: units, lines and the analytic pick seconds of a batch list.

    `batches` are `Workload_Builder.Batch` objects (or anything with `.items: {sku: qty}`);
    `orders_by_sku` maps a SKU to its `Order` for the handling term.  Each SKU's line
    quantities are kept, in script order, for `implied_reorders`.
    """
    t = ScriptTotals(batches=len(batches))
    for b in batches:
        for sku, qty in b.items.items():
            c = orders_by_sku.get(sku)
            if c is None:
                t.unknown_skus += 1
                continue
            q = float(qty)
            t.units += q
            t.lines += 1.0
            t.analytic_pick_s += per_pick(1.0, pricing.intercept, pricing.var(c), q,
                                          pricing.per_item)
            t.per_sku_lines.setdefault(sku, []).append(int(qty))
    return t


def _levels(order) -> tuple[int, int, int]:
    """`(Q, rp, P)` -- the order-up-to target, the reorder point and the pipeline allowance
    -- as `inventory_reorder._fire_reorders` reads them off the SKU's DECLARATION.

    The declaration, not a default: `equilibrium_qty -> 1` / `reorder_point -> 0` sent an
    undeclared order down the base-stock branch and returned a one-line lot, so the era's
    put-away and receiving crews would have been sized off a lot no SKU was ever declared to
    order.  The one caller (`implied_reorders`) runs after the fixed point has declared, so an
    undeclared order here is a bug in the caller.  `stock_qty` stays: it is the documented
    duck-typed legacy contract.  The pipeline is `Order.pipeline_allowance()` (the era's
    stamp, else the `round(rp × lead ÷ (lead + 1))` heuristic) -- a duck-typed order without
    one takes the heuristic.
    """
    eq = int(order.equilibrium_qty if hasattr(order, 'equilibrium_qty')
             else getattr(order, 'stock_qty'))
    rp = int(order.reorder_point)
    allowance = getattr(order, 'pipeline_allowance', None)
    if allowance is not None:
        pipeline = int(allowance())
    else:
        lead = max(0.0, float(getattr(order, 'lead_time_mean', 0.0)))
        pipeline = round(rp * lead / (lead + 1.0)) if lead > 0.0 else 0
    return eq, rp, pipeline


def fired_lots(order, quantities) -> dict:
    """The lots the order-up-to rule fires while `quantities` -- one script's lines for this
    SKU, in script order -- are picked against its declared levels: `{lot: expected count}`.

    Replaces the one ROUNDED lot this derivation used to pack ("Close the put closed form's
    three known gaps"): every pack-proportional term -- the whole put travel term, the put
    intercept, the receiving intercept -- is per PACK, and the packer packs whole units, so
    pricing one rounded lot and scaling it by a fractional reorder count under-counted packs
    by 7% on the store and 3% on fulfillment.  Two regimes, exactly as
    `inventory_reorder._fire_reorders` fires them:

      * BASE STOCK (`rp >= Q - 1`; every SKU on the line floor, "Choose the coverage
        floor"): the shelf holds `Q` at the start of every day (a lead-0 top-up lands before
        the next batch), so a line of `q` units SERVES `min(q, Q)`, fires a lot of exactly
        what it took, and re-offers the remainder next day against a full shelf again
        (`unpicked_unstocked`, "Fit the store's window to its own steady state": nothing is
        lost).  A line is therefore `q // Q` lots of `Q` and one of `q % Q` -- the reference
        pair fires 1.3 lots per line.  With a pipeline `P > 0` a lot below `P + 1` does not
        fire alone (the position only drops to the trigger once `P + 1` units are gone), so
        such a lot is priced as `P + 1` at a count that conserves units -- a FLOOR on the
        pack count, exact at `P = 0`, which is every catalogue the generator authors today.
      * ABOVE THE FLOOR: the expected lot is the position at the reorder point,
        `Q + P - rp`, and the reorders are `Σq ÷ lot`, kept FRACTIONAL (a steady-state
        expectation, not this window's transient -- a 40-day script would fire nothing for
        a SKU whose demand never crosses `Q - rp`).  The lot is handed on as a fraction and
        `received_law` prices it between its two integer neighbours, so the rounding bias
        is gone here too.  The crossing line's overshoot (about half a mean line) is not
        modelled; no fielded catalogue has an above-floor SKU to measure it on.

    The lot is what the ledger ORDERS; what arrives is `received_law`'s business.
    """
    Q, rp, P = _levels(order)
    lots: dict = {}
    if rp >= Q - 1:
        floor = P + 1
        for q in quantities:
            q = int(q)
            for m, n in ((Q, q // Q), (q % Q, 1)):
                if n and m:
                    if m < floor:                    # does not fire alone: priced at P + 1
                        lots[floor] = lots.get(floor, 0.0) + n * m / floor
                    else:
                        lots[m] = lots.get(m, 0.0) + n
        return lots
    total = sum(int(q) for q in quantities)
    lot = float(max(1, Q + P - rp))
    return {lot: total / lot} if total else {}


def received_law(lot: float, supply_cv: float) -> dict:
    """What arrives for a lot the ledger ordered: `{quantity: probability}`.

    `_fire_reorders` receives `max(1, round(N(lot, lot × cv)))` when the SKU carries a
    `supply_cv`, the lot itself otherwise.  The Gaussian is discretised on the unit cells
    (`r - ½, r + ½`], everything below 1½ arriving as one; ±5σ covers the tail to 1e-6.
    A FRACTIONAL lot with no jitter (the above-floor expectation) is the mixture of its two
    integer neighbours weighted by its fractional part, so the packer -- which packs whole
    units -- is asked two whole questions instead of one rounded one.  Always sums to one;
    a lot at or below one arrives as one unit.
    """
    m = float(lot)
    if m <= 1.0:
        return {1: 1.0}
    sd = m * float(supply_cv or 0.0)
    if sd <= 1e-9:
        lo = int(math.floor(m))
        frac = m - lo
        if frac < 1e-9:
            return {lo: 1.0}
        return {lo: 1.0 - frac, lo + 1: frac}
    lo = max(1, int(math.floor(m - 5.0 * sd)))
    hi = max(lo, int(math.ceil(m + 5.0 * sd)))
    root2 = sd * math.sqrt(2.0)
    out: dict = {}
    cum = 0.0 if lo == 1 else 0.5 * (1.0 + math.erf((lo - 0.5 - m) / root2))
    out[lo] = cum                                  # the mass below lo's cell folds onto lo
    for r in range(lo, hi + 1):
        upper = 1.0 if r == hi else 0.5 * (1.0 + math.erf((r + 0.5 - m) / root2))
        out[r] = out.get(r, 0.0) + (upper - cum)   # r == hi takes the tail above too
        cum = upper
    return {r: p for r, p in out.items() if p > 0.0}


def implied_reorders(totals: ScriptTotals, orders_by_sku: dict, pricing: PricingConfig, *,
                     f_put: float, f_recv: float, put_intercept_scale: float,
                     put_item_ratio: float, recv_intercept_scale: float,
                     put_site=None) -> ScriptTotals:
    """Stage B's supply side: the put-away and receiving work the script implies.

    Steady state (f = 1.0) replenishes every unit picked.  Each SKU's script lines fire the
    lots `fired_lots` says they do, each lot arrives as `received_law` says it does, and
    each arrived quantity packs into the storage units the sim's own packer would produce
    (`Inbound.pack.receive` -> `viable_storage_units`, honouring a `stock_plan`).  Each
    pack costs

        put-away   travel + M(y) · (put_intercept + qty · put_per_item + qty · var)
        receiving  recv_intercept + recv_per_item + qty · var                (EXACT)

    `put_site`, when given, is `(unit) -> (travel_s, height_mult)` -- the expected travel
    from the aisle mouth and the height multiplier of the pack's destination under the run's
    placement distribution (`expected_travel.put_site_pricer`).  None prices every put at
    the ground with no travel (the script-only prediction).

    The packer is asked once per (SKU, arrived quantity) and the answer cached: a floored
    SKU's lots never exceed its level plus the jitter, so the distinct questions per SKU are
    few.  There is NO cart-swap term: the era runs one uncarted put queue at
    `swap_coef = 0` and the harness REFUSES anything else (`run_simulation._check_era_flags`,
    `workunits.refuse_unpriceable_put`) rather than under-price it here.  `f_put` scales the
    put load and `f_recv` the receiving load ("units put per unit picked" / packs received
    per pack demanded); at 1.0 both are the steady state, any other value models a growing
    or shrinking warehouse, which is exactly why they are knobs.  Mutates and returns
    `totals`.
    """
    put_cost = pricing.put(intercept_scale=put_intercept_scale, item_ratio=put_item_ratio)
    recv_cost = pricing.recv(put_intercept_scale=put_intercept_scale,
                             put_item_ratio=put_item_ratio,
                             recv_intercept_scale=recv_intercept_scale)
    for sku, qs in totals.per_sku_lines.items():
        c = orders_by_sku[sku]
        var = pricing.var(c)
        cv = float(getattr(c, 'supply_cv', 0.0) or 0.0)
        priced: dict = {}                            # arrived qty -> (packs, qty, put_s, recv_s)
        for lot, n in fired_lots(c, qs).items():
            for r, p in received_law(lot, cv).items():
                hit = priced.get(r)
                if hit is None:
                    plan = _receive(c, r)            # the packer packs whole units
                    put_s = 0.0
                    for u in plan.units:
                        travel, mult = put_site(u) if put_site is not None else (0.0, 1.0)
                        put_s += travel + per_pick(mult, put_cost.intercept, var, u.quantity,
                                                   put_cost.per_item)
                    recv_s = sum(unload_cost(c.weight, c.volume(), u.quantity, recv_cost)
                                 for u in plan.units)
                    hit = priced[r] = (plan.unit_count, plan.packed_qty, put_s, recv_s)
                w = n * p
                totals.lots += w
                totals.put_units += w * hit[1] * f_put
                totals.put_s += w * hit[2] * f_put
                totals.packs += w * hit[0] * f_recv
                totals.recv_s += w * hit[3] * f_recv
    return totals


# ── the crews and the bands ─────────────────────────────────────────────────────────────

def crew_size(load_seconds_per_day: float, day_seconds: float, rho: float) -> int:
    """ceil(load ÷ (S × ρ)), floored at 1 when there is load and 0 when there is none.

    A department with work to do fields at least one person even when the arithmetic says
    0.3; a department with NO work (a store-only catalogue's absent channel contributes
    nothing, but the site crew still forms from the other) fields nobody only when the
    whole site has nothing for it.
    """
    if load_seconds_per_day <= 0.0:
        return 0
    return max(1, math.ceil(load_seconds_per_day / (float(day_seconds) * float(rho))))


def expected_utilization(load_seconds_per_day: float, crew: int, day_seconds: float) -> float:
    """worked ÷ granted for one department on one leaf: load × s ÷ (crew × S).

    AFTER `ceil` and AFTER the leaf's share of the site crew ("Declare the equilibrium
    bands", decision 3): integer crews and single-channel leaves undercut ρ by
    construction, so the band is drawn around THIS number, never around ρ.
    """
    if crew <= 0 or day_seconds <= 0:
        return 0.0
    return load_seconds_per_day / (float(crew) * float(day_seconds))


# ── the record ─────────────────────────────────────────────────────────────────────────

def constant(value: float, provenance: str, **extra) -> dict:
    """One recorded constant: `{value, provenance, ...}`.  Provenance must be one of the
    five agreed values; a copied constant keeps the one it arrived with."""
    if provenance not in PROVENANCE:
        raise ValueError(f'provenance must be one of {PROVENANCE}; got {provenance!r}')
    return {'value': value, 'provenance': provenance, **extra}


def derive(*, inputs: dict, constants: dict, day_seconds: float, channels: dict,
           scripts: dict, pricing_names: dict) -> dict:
    """The derived block, from declared inputs + resolved constants + the script.

    `inputs` is the staffing record's INPUTS (`sim_config.staffing_spec()` shape:
    `rho_put/rho_recv`, `f_put/f_recv`, `band_tol`, `put_crew_mode`, plus the three
    crew-cost scales the harness copies in; `rho_pick` is NOT read here since ADR-0004).
    `constants` is `{'s_pick': {channel: constant}, 's_put': {channel: constant}}` as the
    harness resolves them (the expectation, or a declared override).  BOTH are keyed by
    channel: a site-wide put price is what this derivation used to get wrong, and there is
    no longer anywhere to put one.  `channels` maps a channel name to its stage-A dict:
    `pickers` (the crew stage A SOLVED from the first-time confidence -- `solve_pickers`),
    `daily_demand_units` (the declared day's DEMANDED units), `analytic`, `batch`,
    `n_skus`, `expected` (the expected day at the declared line count, recorded beside the
    constant), and optionally `demand` and `guarantee` (the declaration and the solve's
    stamp, carried through verbatim); `scripts` maps it to its `ScriptTotals`.  Channels
    absent from `channels` (a store-only catalogue) are recorded as absent and contribute
    nothing.

    THE PICKING LOAD IS THE SAMPLED SCRIPT'S DEMANDED UNITS at `s_pick` ("Fit the store's
    window to its own steady state", decision 5: the derivation prices the actual script,
    the declared law is what the guarantee was made against, both are stamped and named
    apart).  Its expected utilization is that load against the granted day `K × S` -- a
    DERIVED number near 0.72 on the reference store, never a target.

    The put and receiving crews are SITE totals: one crew each, summing both channels'
    per-day loads.  Each channel leaf's expected utilization for those two departments is
    its own load against the WHOLE site crew, which is why it sits well below ρ.
    """
    S = float(day_seconds)
    # THE ERA IS ONE SITE DAY.  `per_day` divides each channel's totals by that channel's
    # own batch count, and the two site crews are sized from the SUM of those per-day loads
    # -- so two channels running different batch counts would be two different days added
    # together, and every departmental band drawn from the result would be denominated in
    # nothing ("Define the calibrated era": one boundary for the whole site).  The single
    # production caller reads ONE `n_batches` and hands it to both channels, so this holds
    # by construction today; it was unasserted until the per-channel put price made the
    # sum's meaning load-bearing.
    _counts = {n: scripts[n].batches for n in CHANNELS if channels.get(n) is not None}
    if len(set(_counts.values())) > 1:
        raise ValueError(
            f'the channels ran different batch counts ({_counts}); under the era one batch '
            f'is one site day, so their per-day loads cannot be summed into a site crew')
    # Both constant maps are keyed by channel, so a caller that priced one channel and not
    # the other would fail on a bare `KeyError` deep in the loop below.  Say which map and
    # which channel instead -- this is a pure module and its contract is its docstring.
    _missing = [f'{k}[{n}]' for k in ('s_pick', 's_put') for n in channels
                if n not in (constants.get(k) or {})]
    if _missing:
        raise ValueError(f'no constant for {", ".join(sorted(_missing))}; both `s_pick` and '
                         f'`s_put` are keyed by channel and must cover every channel derived')
    out: dict = {
        'provenance': 'derived',
        'day_seconds': S,
        'channels': {},
        'put': {}, 'receiving': {},
    }
    put_load_s = 0.0
    recv_load_s = 0.0
    per_channel_put_s: dict = {}
    per_channel_recv_s: dict = {}
    for name in CHANNELS:
        ch = channels.get(name)
        if ch is None:
            out['channels'][name] = None
            continue
        t: ScriptTotals = scripts[name]
        s_pick = float(constants['s_pick'][name]['value'])
        K = int(ch['pickers'])
        units_day = t.per_day(t.units)
        # The picking LOAD is the script's DEMANDED units per day at `s_pick`.  Nothing is
        # lost under the era -- a cut or unfilled pick is re-offered next day -- so the crew
        # picks all of demand, and the load is never reduced by a fill rate (the previous
        # derivation priced SERVED units, demand × 0.922, and read 0.85 where the crew
        # actually carried 0.92; memory `nothing-is-lost-under-the-era`).  The capacity is
        # the granted day `K × S`; the utilization it implies is a consequence of the
        # first-time confidence the crew was solved for, stamped, and the band is drawn
        # around THIS number.  The declared day's own load (E[W], the guarantee's) sits in
        # `guarantee`, named apart.
        pick_load_s = units_day * s_pick
        rec = {
            'pickers': K,
            'pickers_provenance': str(ch.get('pickers_provenance') or 'derived'),
            'pricing_config': pricing_names[name],
            's_pick': constants['s_pick'][name],
            'pick_capacity_s': float(K) * S,
            'pick_load_s': pick_load_s,
            'daily_demand_units': ch['daily_demand_units'],
            'demand': ch.get('demand'),
            'guarantee': ch.get('guarantee'),
            'analytic': ch['analytic'],
            'expected': ch.get('expected'),
            'batch': ch['batch'],
            'script': {
                'batches': t.batches,
                'units': t.units, 'lines': t.lines,
                'units_per_day': units_day,
                'analytic_pick_s': t.analytic_pick_s,
                # The script's own seconds per unit at ground with no travel -- the
                # handling floor the expectation's travel and swaps sit on top of.
                'analytic_s_pick': (t.analytic_pick_s / t.units) if t.units else 0.0,
                'lots': t.lots, 'put_units': t.put_units, 'put_s': t.put_s,
                'packs': t.packs, 'recv_s': t.recv_s,
                'unknown_skus': t.unknown_skus,
            },
            'expected_utilization': {
                'pick': expected_utilization(pick_load_s, K, S),
            },
        }
        # Site loads, per day.  Put-away is priced at THIS CHANNEL's own seconds per unit
        # -- the expected travel over its own section's class-uniform destination plus the
        # packs' handling -- so the two crews' loads are summed from prices that were never
        # averaged across two geometries.  Note there is no `declared` / `derived` branch
        # here, and deliberately so: the constant is per channel either way, and when it is
        # the expectation its value IS `put_s / put_units`, so `units × price` reproduces
        # the script's own seconds.  One code path means a declared price cannot silently
        # stop reaching the load.  Receiving is EXACT and needs no constant at all.
        ch_put_s = t.per_day(t.put_units) * float(constants['s_put'][name]['value'])
        ch_recv_s = t.per_day(t.recv_s)
        per_channel_put_s[name] = ch_put_s
        per_channel_recv_s[name] = ch_recv_s
        put_load_s += ch_put_s
        recv_load_s += ch_recv_s
        out['channels'][name] = rec

    put_crew = crew_size(put_load_s, S, float(inputs['rho_put']))
    recv_crew = crew_size(recv_load_s, S, float(inputs['rho_recv']))
    out['put'] = {
        'crew': put_crew,
        'mode': inputs.get('put_crew_mode'),
        # PER CHANNEL, and there is no site scalar beside it on purpose: a site-wide
        # `s_put` in the record is a number a future consumer could price something from,
        # and averaging a 3.4× spread is the defect this key was split to end.  A reader
        # who wants the site average divides `load_seconds_per_day` by
        # `load_units_per_day`, both of which are read.
        's_put': {n: constants['s_put'][n] for n in channels},
        'load_seconds_per_day': put_load_s,
        'load_units_per_day': sum(scripts[n].per_day(scripts[n].put_units)
                                  for n in channels),
        'f_put': float(inputs['f_put']),
        'expected_utilization': {n: expected_utilization(per_channel_put_s[n], put_crew, S)
                                 for n in channels},
    }
    total_packs = sum(scripts[n].per_day(scripts[n].packs) for n in channels)
    out['receiving'] = {
        'crew': recv_crew,
        'load_seconds_per_day': recv_load_s,
        'load_packs_per_day': total_packs,
        # Exact seconds per pack, derived from the script.  REPORTED ONLY -- nothing reads
        # it (checked 2026-09-08).  This comment used to claim the throughput audit's
        # receiving self-check compares realized labour against it; that check re-prices
        # every row instead, precisely because an average was 7× off on a real run (memory
        # `equilibrium-check-two-traps`).  Left in place as a site read-out; do NOT make it
        # the precedent for a new price, which is why `s_put` above is a per-channel map
        # and has no site twin.
        's_recv': constant((recv_load_s / total_packs) if total_packs else 0.0, 'derived',
                           note='exact from the script: an unload has no travel term'),
        'f_recv': float(inputs['f_recv']),
        # ADR-0003's rework term.  ASSUMED, and assumed to be ZERO: the own-bin rung and
        # the rescues only fire once the free index is dry, which a warehouse sized to its
        # declared levels never does.  It is in the record rather than left out precisely
        # so a run that DOES repack contradicts something -- the equilibrium audit compares
        # the measured `recv_repacked_packs` against this and reports the gap.  A future
        # coefficient replaces the 0.0 and needs no schema move to do it.
        'f_repack': constant(float(inputs.get('f_repack', 0.0)), 'assumed',
                             note='packs repacked per pack received; a measured repack is '
                                  'a finding about the warehouse sizing, not a cost to '
                                  'absorb into a band'),
        'expected_utilization': {n: expected_utilization(per_channel_recv_s[n], recv_crew, S)
                                 for n in channels},
    }
    return out


def derived_differs(a: dict | None, b: dict | None, *, rel_tol: float = 1e-9) -> list[str]:
    """The paths at which two derived blocks disagree, for the restore drift check.

    Floats compare with a tolerance, never `==` (the repo rule); everything else exactly.
    An empty list means "the same derivation".  Used on resume (raise) and on re-analysis
    (warn and stamp) -- "Design the staffing record", decision 3.
    """
    diffs: list[str] = []

    def _walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    diffs.append(f'{path}/{k}')
                else:
                    _walk(x[k], y[k], f'{path}/{k}')
        elif isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                and not isinstance(x, bool) and not isinstance(y, bool):
            if not math.isclose(float(x), float(y), rel_tol=rel_tol, abs_tol=1e-9):
                diffs.append(path)
        elif x != y:
            diffs.append(path)

    _walk(a or {}, b or {}, '')
    return diffs


def regime_orders(orders, regime: str | None) -> list:
    """The catalogue section one channel picks from: `regime_of(order) == regime`, or the
    whole catalogue when `regime` is None (the store-only path)."""
    if regime is None:
        return list(orders)
    return [c for c in orders if regime_of(c) == regime]
