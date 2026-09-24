"""coverage.py — stock coverage as a RUNTIME declaration in days, never a generator knob.

The catalogue carries NO stock level (ADR-0002, department-calibration "Field the floor",
decision 5): a level is a RUN's declaration, and this module is where every run makes it.  It
used to be authored at generation in GENERATION batches (`equilibrium_qty = round(
coverage_batches x freq x qty)`), and a generation batch is not a unit of time -- it is "each
SKU appears with probability `freq`", which is worth however many site days the run's pickers
make it worth (1,771 of them on the reference catalogue's store section).  The demand a SKU
sees per DAY is known at setup (.scratch/department-calibration, "Derive the expected-travel
closed form", decision 2; the derivation note's section 5):

    d_s = n · π_s · E[q_s]                    units of SKU s per day

`n` is the pair's fixed point (lines per day the declared crew fills its capacity at), `π_s`
the SKU's line share within its channel section (`freq / Σ freq`) and `E[q_s]` the mean of
the SKU's STAMPED line law (`Demand.line.mean()`; today `max(1, Poisson(λ_s))`, so
`λ_s + e^{-λ_s}`) -- read off the SKU, never re-derived here ("Stamp the line distribution
on the SKU").  This module re-derives every
SKU's stock levels by the GENERATOR'S OWN FORMULA with the day as the unit, floored at ONE
LINE of the SKU's own demand ("Choose the coverage floor", decisions 1-2):

    L_s             = ceil(floor_lines · E[q_s])                  the LINE FLOOR, >= 1
    equilibrium_qty = max(round(coverage_days · d_s), L_s)
    reorder_point   = min(Q - 1, max(round(d_s · (lead_days + safety_days)), L_s))   (Q > 1)
                    = 1                                                              (Q = 1)
    pipeline_qty    = round(d_s · lead_days)                      the lead PIPELINE, stamped

The floor is a physical number per SKU -- a pick's worth of itself, so no SKU stocks out on a
single line -- and it is what replaced the generator's unit floor (`Q >= 1`), under which a
slow mover held one unit against ten-unit lines and the store section of the reference pair
was 100% floored ("Rescale stock coverage at setup", the finding).  A SKU whose coverage is
shorter than the interval between its lines lands on the floor with `rp = Q - 1`: every pick
fires an order-up-to for exactly what it took -- the textbook BASE-STOCK (S-1, S) policy,
which `inventory_reorder._fire_reorders` already implements on inventory position.  Coverage
is nominal AND real only for SKUs above the floor; `rescale_section` returns the shares so the
record can say how much of the section, and of its demand, sits on it.

The reorder point's lead-time content is stamped, not inferred: `pipeline_qty` is the
in-transit allowance `_fire_reorders` adds to the order-up-to (decision 6).  The manager's
`rp × lead ÷ (lead + 1)` heuristic assumed `rp` encodes lead-time demand; under the line floor
`rp` encodes a line, and a floored SKU with a two-day lead would have ordered up to ~1.7 lines.
The stamp rides the SKU (`Order.pipeline_qty`, a `cartons` column of the planned inventory) so
the spawned workers read it; a flag-off run never stamps it and the heuristic stands.

THE LEAD IS TWO ADDITIVE STAGES ("Declare the coverage against the inbound lead", decisions
1-6).  A SKU's `lead_time_mean` is its SUPPLIER lead -- order to ready-to-ship, authored in
BATCHES and read here as days through `lead_unit_days` (`1 / releases_per_day`: one batch is
one day under the era; 1.0 flag-off, where a batch was always read as a day) -- and the
trailer pipeline's transit is the site's, one number per pair, rounded UP to the day grid
because a trailer is observed only at the next drain:

    transit_days = E[ceil(L / D)] = 1 + Σ_{k>=1} (1 - Φ(ln(k·D / m) / σ))        (`transit_day_law`)
    lead_days_s  = lead_time_mean_s · lead_unit_days + transit_days

with `m` the lead median in seconds, `σ` the spread and `D` the site day; exactly 1 at spread
0 over a one-day median, 0 with no pipeline.  The first-time promise holds AT the lead
(decision 5): `fill_rate` prices the shelf a line meets as the order-up-to POSITION
`S = Q + pipeline_qty` less the units of the SKU's own prior lines still in transit, the
prior lines counted over the integer days the observed lead spans, and `solve_floor_lines`
absorbs it unchanged.  At lead zero every term reduces to the lead-free expression EXACTLY --
the same code path, not a tolerance -- so a record with no pipeline is byte-identical to one
declared before the lead existed.

THIS MODULE IS PURE: orders, a line count and three declared scalars in; the orders'
stock DECLARATION written through `Order.declare_stock` (the one mutation site for the four
level slots) and a stats dict out.  It imports no CONFIG and touches no file.  The harness seam
that drives it -- the pair-level fixed point Q(n) -> plan_warehouse -> geometry -> n -- is
`Optimization/simdriver/era_coverage.py`, and it runs in EVERY mode: the calibrated era decides
whether the clock cuts and caps, never whether a run declares its stock (ADR-0002, decision 6).
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
from scipy.special import gammaln

#: The line floor a FLAG-OFF run declares at: one pick's worth of the SKU ("Choose the
#: coverage floor", decision 1).  Under the era the floor is SOLVED from the first-time
#: confidence (`solve_floor_lines`), searching upward from this same value -- a SKU never
#: holds less than one line of itself in either regime.
DEFAULT_FLOOR_LINES: float = 1.0
#: The solve's resolution in lines, and the doubling ceiling past which a catalogue is
#: declared unable to reach the fill asked of it (128 lines of stock per SKU).
FLOOR_SOLVE_TOL: float = 1e-4
FLOOR_SOLVE_MAX: float = 128.0
#: Relative change of a channel's fixed-point `n` below which the coverage loop has converged.
DEFAULT_TOL: float = 0.01
#: Rounds the loop may take before it stops and records the residual it stopped at.  The
#: bracketed secant (`era_coverage.next_guess`) needs ~2 rounds to bracket and ~4-6 to close
#: to 1% on the reference pair; each full-scale round costs about a minute.
DEFAULT_MAX_ROUNDS: int = 12
#: The lognormal's upper tail below which the day-grid sum of `transit_day_law` stops, and
#: the day count past which a law is declared unsummable (a spread so wide the tail never
#: closes is a configuration error, not a lead).
TRANSIT_TAIL_TOL: float = 1e-12
TRANSIT_DAYS_MAX: int = 100_000
#: The transit mass `_served_under_lead` leaves unpriced: grid days are taken in order until
#: the remaining mass is under this, so the pilot law's 138-day tail (115 of them under
#: 1e-6) costs a dozen recursions rather than all of them.  Dropped mass reads as served
#: 0 (the conservative side), never as served.
SERVED_TAIL_TOL: float = 1e-9
#: The Panjer recursion is rescaled row by row past this magnitude so a seed `e^{-λ}` that
#: underflows (λ above ~745) never zeroes a shelf that would in fact serve everything.
_PANJER_RESCALE: float = 1e150


def _phi(x: float) -> float:
    """The standard normal cdf, by `erf`."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def transit_day_law(lead_s: float, lead_sigma: float, day_seconds: float) -> dict:
    """The trailer pipeline's transit on the SITE-DAY GRID: the law of `ceil(L / D)` for a
    lognormal lead `L` (median `lead_s` seconds, spread `lead_sigma`) against a day of
    `day_seconds`, and its expectation ("Declare the coverage against the inbound lead",
    decision 3 -- transit and the grid only, no doors, no queue):

        transit_days = E[ceil(L / D)] = 1 + Σ_{k>=1} (1 - Φ(ln(k·D / m) / σ))
        P(ceil(L / D) = k) = Φ(ln(k·D / m) / σ) - Φ(ln((k-1)·D / m) / σ)          k >= 1

    Returns `{'transit_days', 'pmf': {k: p}, 'lead_s', 'lead_sigma', 'day_seconds'}`.  The
    ceiling is the model, not a rounding choice: a reorder fires and dispatches inside one
    drain, `arrived = dispatched + L`, and the yard is read once per site day, so a lead of
    any positive length lands at the next drain at the earliest (1.766 days at the pilot's
    480 min / 0.7 / 8 h; the continuous mean, 1.28, is the wrong number).  Spread 0 is taken
    literally (`ceil(m / D)` with probability one, exactly 1 for a median of one day) and a
    zero median is no transit at all (`{0: 1.0}`, 0 days): a trailer that arrives inside the
    drain that dispatched it.  Refuses a non-positive day or a negative median or spread.
    """
    m, sig, D = float(lead_s), float(lead_sigma), float(day_seconds)
    if D <= 0.0:
        raise ValueError(f'day_seconds must be positive; got {day_seconds!r}')
    if m < 0.0 or sig < 0.0:
        raise ValueError(f'the lead median and spread are non-negative; got {lead_s!r}, '
                         f'{lead_sigma!r}')
    base = {'lead_s': m, 'lead_sigma': sig, 'day_seconds': D}
    if m <= 0.0:
        return {'transit_days': 0.0, 'pmf': {0: 1.0}, **base}
    if sig <= 0.0:
        k0 = int(math.ceil(m / D - 1e-9))               # an exactly one-day lead reads 1
        return {'transit_days': float(k0), 'pmf': {k0: 1.0}, **base}
    pmf: dict = {}
    expect = 1.0
    prev = 0.0
    k = 1
    while True:
        cdf = _phi(math.log(k * D / m) / sig)
        p = cdf - prev
        if p > 0.0:
            pmf[k] = p
        tail = 1.0 - cdf
        if tail < TRANSIT_TAIL_TOL:
            break
        expect += tail
        if k >= TRANSIT_DAYS_MAX:
            raise ValueError(
                f'the lead law (median {m:g} s, spread {sig:g}) still carries a tail of '
                f'{tail:.3g} past {k:,} site days; the day-grid expectation does not close')
        prev = cdf
        k += 1
    return {'transit_days': float(expect), 'pmf': pmf, **base}


def daily_demand(orders, lines_per_day: float) -> dict:
    """`{sku: d_s}` for one channel section at `lines_per_day` lines a day.

    The line share is `freq / Σ freq` over exactly the orders given -- the section the
    channel picks from (`staffing.regime_orders`), which is also how `expected_travel
    .accumulate` weights them, so the two agree by construction.  An empty section or a
    zero weight sum yields zeros.
    """
    W = sum(float(c.demand.relative_frequency) for c in orders)
    if W <= 0.0 or lines_per_day <= 0.0:
        return {c.sku: 0.0 for c in orders}
    n = float(lines_per_day)
    return {c.sku: n * (float(c.demand.relative_frequency) / W)
                   * c.demand.line.mean()
            for c in orders}


def line_floor(line, floor_lines: float) -> int:
    """`L_s = ceil(floor_lines × E[q_s])`, at least 1: the least stock a SKU ever holds, in
    units, read off its stamped line law.  `floor_lines` is the declared number of lines
    (1.0 by default: one pick's worth); the mean is rounded UP so the shelf is never below
    the expected line.  Refuses a non-positive `floor_lines` -- a zero would be the unit
    floor by the back door."""
    if floor_lines <= 0.0:
        raise ValueError(f'floor_lines must be positive lines; got {floor_lines!r}')
    return max(1, int(math.ceil(float(floor_lines) * float(line.mean()) - 1e-9)))


def pipeline_qty(d_s: float, lead_days: float) -> int:
    """`round(d_s × lead)`: the units expected in transit over the lead -- the allowance
    `_fire_reorders` adds to the order-up-to so on-hand returns to the target when the lot
    lands.  Zero at lead 0, as the heuristic it replaces was."""
    return max(0, int(round(float(d_s) * max(0.0, float(lead_days)))))


def stock_levels(d_s: float, lead_days: float, coverage_days: float,
                 safety_days: float, floor_units: int) -> tuple[int, int]:
    """`(equilibrium_qty, reorder_point)` for one SKU: the generator's formula in days,
    floored at `floor_units` (the SKU's line floor, `line_floor`) on BOTH levels.

    The order-up-to is `coverage x demand`, never less than the floor; the reorder point is
    demand over the lead plus the safety buffer, never less than the floor, capped one below
    the equilibrium so the trigger always fires before the target.  A SKU whose coverage
    term falls below its floor therefore lands at `rp = Q - 1` -- base stock.  A SKU at
    `Q = 1` (a floor of one unit) takes the generator's `rp = 1` branch, as `Order.build`
    clamps it.
    """
    if coverage_days <= 0.0:
        raise ValueError(f'coverage_days must be positive days; got {coverage_days!r}')
    if safety_days < 0.0:
        raise ValueError(f'safety_days must be non-negative days; got {safety_days!r}')
    if floor_units < 1:
        raise ValueError(f'floor_units must be at least one unit; got {floor_units!r}')
    L = int(floor_units)
    q = max(L, int(round(float(coverage_days) * float(d_s))))
    if q <= 1:
        return 1, 1
    raw = int(round(float(d_s) * (max(0.0, float(lead_days)) + float(safety_days))))
    return q, min(q - 1, max(L, raw))


def sku_lead_days(order, transit_days: float = 0.0, lead_unit_days: float = 1.0) -> float:
    """`lead_days_s = lead_time_mean_s · lead_unit_days + transit_days`: the SKU's expected
    order-to-shelf lead in days -- its supplier lead (authored in batches, one batch a day
    under the era) plus the pair's day-grid transit ("Declare the coverage against the
    inbound lead", decisions 2 and 4).  Never negative; 0 on a lead-free catalogue with no
    pipeline, which is every catalogue the generator has authored so far."""
    attr = max(0.0, float(getattr(order, 'lead_time_mean', 0.0) or 0.0))
    return attr * float(lead_unit_days) + max(0.0, float(transit_days))


def rescale_section(orders, lines_per_day: float, *, coverage_days: float,
                    safety_days: float, floor_lines: float,
                    transit_days: float = 0.0, lead_unit_days: float = 1.0) -> dict:
    """Re-derive every order's stock levels for one section and return the floor shares.

    MUTATES the orders through `Order.declare_stock`: the order-up-to, the reorder point and
    the stamped lead `pipeline_qty`, with `stock_plan` reset to None -- a plan the warehouse
    planner wrote for the OLD quantity would otherwise be honoured by `viable_storage_units` at
    the new one, and the planner re-packs the line-sized quantity.  Each SKU's lead is
    `sku_lead_days` -- its supplier lead in days plus the pair's `transit_days` (0 with no
    pipeline; `lead_unit_days` 1.0 is the flag-off reading of a batch as a day).  Returns

        {'n_skus', 'line_families', 'lines_per_day', 'coverage_days', 'safety_days',
         'floor_lines', 'transit_days', 'lead_unit_days',
         'lead_days',                      # the units-weighted mean lead over the section
         'sum_q', 'units_per_day',
         'floor_line_skus', 'floor_line_share', 'floor_line_demand_share',   # Q == L_s
         'base_stock_skus', 'base_stock_share',                              # rp == Q - 1
         'floor_rp_skus', 'floor_rp_share',           # above the floor, rp lifted to L_s
         'above_floor_skus', 'realized_coverage_days'}   # Σ Q / Σ d over the SKUs above

    `floor_line_demand_share` is the share of the section's daily UNITS that lands on a SKU
    holding exactly its line floor -- the number that says whether the floor is a tail or the
    section.  `realized_coverage_days` is the coverage the SKUs above the floor actually carry
    (their rounded Q against their demand), which the nominal number only approximates.  The
    expected first-pass fill rate is NOT here: it depends on the levels the planner finally
    fields, so `fill_rate` prices it on the planned orders once (`era_coverage.fixed_point`).
    """
    d = daily_demand(orders, lines_per_day)
    sum_q = 0
    units = 0.0
    lead_units = 0.0                      # Σ d_s · lead_s, the units-weighted lead's numerator
    floored = 0
    floored_units = 0.0
    base_stock = 0
    floor_rp = 0
    above_sum_q = 0
    above_units = 0.0
    fams: dict = {}                       # law family -> SKUs read (the record's provenance)
    for c in orders:
        line = c.demand.line
        fams[line.family] = fams.get(line.family, 0) + 1
        ds = d[c.sku]
        lead = sku_lead_days(c, transit_days, lead_unit_days)
        L = line_floor(line, floor_lines)
        q, rp = stock_levels(ds, lead, coverage_days, safety_days, L)
        c.declare_stock(q, rp, stock_plan=None, pipeline_qty=pipeline_qty(ds, lead))
        sum_q += q
        units += ds
        lead_units += ds * lead
        if rp >= q - 1:
            base_stock += 1
        if q <= L:
            floored += 1
            floored_units += ds
        else:
            above_sum_q += q
            above_units += ds
            if int(round(ds * (lead + float(safety_days)))) < L:
                floor_rp += 1
    n = len(orders)
    return {
        'n_skus': n,
        'line_families': fams,
        'lines_per_day': float(lines_per_day),
        'coverage_days': float(coverage_days),
        'safety_days': float(safety_days),
        'floor_lines': float(floor_lines),
        'transit_days': max(0.0, float(transit_days)),
        'lead_unit_days': float(lead_unit_days),
        'lead_days': (lead_units / units) if units > 0.0 else 0.0,
        'sum_q': int(sum_q),
        'units_per_day': float(units),
        'floor_line_skus': int(floored),
        'floor_line_share': (floored / n) if n else 0.0,
        'floor_line_demand_share': (floored_units / units) if units > 0.0 else 0.0,
        'base_stock_skus': int(base_stock),
        'base_stock_share': (base_stock / n) if n else 0.0,
        'floor_rp_skus': int(floor_rp),
        'floor_rp_share': (floor_rp / n) if n else 0.0,
        'above_floor_skus': int(n - floored),
        'realized_coverage_days': (above_sum_q / above_units) if above_units > 0.0 else 0.0,
    }


def _line_pmf(lines: list, upto: int) -> np.ndarray:
    """`P(q_s = i)` for `i = 0 .. upto-1`, one row per line law -- 0 at `i = 0` (a line
    always demands at least one unit).  The founding family is evaluated in one vectorised
    log-space pass (`max(1, Poisson(λ))`: `P(q = 1) = e^{-λ}(1 + λ)`, the Poisson mass above);
    any other family falls back to the law's own `pmf`, row by row, so a second family
    prices correctly the day it is stamped and merely loses the fast path."""
    upto = int(upto)
    n = len(lines)
    out = np.zeros((n, max(upto, 1)))
    if upto <= 1:
        return out[:, :upto]
    if all(l.family == 'poisson_max1' for l in lines):
        lam = np.array([float(l.params['lam']) for l in lines])
        i = np.arange(2, upto, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            logf = (-lam[:, None] + i[None, :] * np.log(lam)[:, None]
                    - gammaln(i + 1.0)[None, :])
        out[:, 1] = np.exp(-lam) * (1.0 + lam)
        if upto > 2:
            # `log(0) = -inf` at λ = 0 makes every row above `i = 1` `exp(-inf) = 0`, which
            # is the law (a zero-rate line is always one unit); `nan_to_num` guards the
            # `0 · inf` that no term here produces but a future column might.
            out[:, 2:] = np.nan_to_num(np.exp(logf), nan=0.0, posinf=0.0)
        return out
    for r, l in enumerate(lines):
        out[r, :] = l.pmf(upto)
    return out


def _served_under_lead(lines: list, S: np.ndarray, rate: np.ndarray, a: np.ndarray,
                       pmf_k: dict, memo: dict | None = None) -> np.ndarray:
    """`E[min(q, (S - D_K)^+)]` per SKU: the units a line is expected to take off a shelf
    whose order-up-to POSITION is `S` while the SKU's own prior lines are still in transit
    ("Declare the coverage against the inbound lead", decision 5).

    The observed lead is `K = a + k` whole days on the site grid: `a` the SKU's supplier lead
    rounded to the grid (the ledger's own `round(lead_time_mean)`) and `k` the trailer's
    transit under `pmf_k` (`transit_day_law`; `{0: 1}` with no pipeline).  The prior lines
    inside those `K` days number `N ~ Poisson(K · rate_s)` at the SKU's line rate `n · π_s`
    (the batch is one Poisson draw a day under the era -- the same convention the
    fragmentation chain prices with), each of the stamped line law, so `D_K` is compound
    Poisson and its mass on `0 .. S-1` follows Panjer's recursion

        g(0) = e^{-λ},   g(j) = (λ / j) · Σ_{i=1..j} i · f(i) · g(j - i)         λ = K · rate_s

    Then, `q` and `D_K` being independent,
    `E[min(q, (S - D)^+)] = Σ_{u<S} P(q > u) · P(D <= S - 1 - u)`.  Vectorised over SKUs in
    groups by shelf size (the recursion is quadratic in `S`, and a floored section is almost
    entirely small shelves), exact to the truncation at `S` -- nothing above the position
    is ever needed -- and to the transit tail `SERVED_TAIL_TOL` leaves unpriced.  The
    recursion is linear in `g`, so a row whose mass outgrows `_PANJER_RESCALE` is divided
    down and its log-scale carried, which keeps a seed that underflows (λ above ~745) from
    zeroing a shelf that serves everything.  At `K = 0` the mass sits at zero and the sum
    is `E[min(q, S)]`.

    `a` is the supplier lead ROUNDED TO GRID DAYS after the batch-to-day conversion; at one
    release a day (the era, which pins it) that is the ledger's own `round(lead_time_mean)`
    in batches, and at any other release schedule the two roundings differ -- a limitation
    stated, not solved, because nothing declares another schedule.

    `memo` (a dict the caller owns, or None) shares per-(group, grid day) results between
    calls that price the SAME groups under DIFFERENT transit laws -- the fill curve's twelve
    points (`era_coverage.fill_curve`), which differ only in the weights `pk` over the grid
    days `k`.  A group's per-`k` sum depends on its SKUs' shelves, rates, supplier leads and
    line laws and on `k`, never on `pk`, so it is computed once per `(group, k)` and each law
    re-weights it in its own `k` order -- the same expression and the same accumulation as the
    unmemoised path, so every float is identical.  The group is keyed by a digest of exactly
    those inputs (and the line laws by identity), so a caller that changed any of them misses
    rather than reusing a stale sum.
    """
    n = len(lines)
    out = np.zeros(n)
    if n == 0:
        return out
    # The grid days in order of weight until the tail is under tolerance: the pilot law
    # carries mass on 138 days, and pricing the ones under 1e-6 costs as much as the rest.
    ks: list = []
    left = 1.0
    for k, pk in sorted(pmf_k.items(), key=lambda kv: -kv[1]):
        if left < SERVED_TAIL_TOL:
            break
        ks.append((int(k), float(pk)))
        left -= float(pk)
    S = np.asarray(S, dtype=int)
    rate = np.asarray(rate, dtype=float)
    a = np.asarray(a, dtype=int)
    order = np.argsort(S, kind='stable')
    # Groups by the power of two above the shelf, so no SKU pays for a shelf far above its own.
    bounds = sorted({1 << max(0, int(math.ceil(math.log2(max(1, int(s)))))) for s in S})
    start = 0
    for S_g in bounds:
        end = start
        while end < n and S[order[end]] <= S_g:
            end += 1
        idx = order[start:end]
        start = end
        if len(idx) == 0:
            continue
        S_i = S[idx]
        gkey = None
        if memo is not None:
            h = hashlib.blake2b(digest_size=16)
            h.update(np.int64(S_g).tobytes())
            h.update(np.ascontiguousarray(S_i, dtype=np.int64).tobytes())
            h.update(np.ascontiguousarray(rate[idx], dtype=float).tobytes())
            h.update(np.ascontiguousarray(a[idx], dtype=np.int64).tobytes())
            h.update(np.fromiter((id(lines[j]) for j in idx), dtype=np.int64,
                                 count=len(idx)).tobytes())
            gkey = h.digest()
            if all((gkey, int(k)) in memo for k, _pk in ks):
                for k, pk in ks:
                    out[idx] += float(pk) * memo[(gkey, int(k))]
                continue
        f = _line_pmf([lines[j] for j in idx], S_g)              # P(q = i), i < S_g
        surv = np.clip(1.0 - np.cumsum(f, axis=1), 0.0, 1.0)      # P(q > u), u < S_g
        wf = f * np.arange(S_g, dtype=float)[None, :]             # i · f(i)
        u = np.arange(S_g)
        pos = S_i[:, None] - 1 - u[None, :]                       # S - 1 - u
        mask = pos >= 0
        gather = np.clip(pos, 0, None)
        for k, pk in ks:
            if gkey is not None:
                val = memo.get((gkey, int(k)))
                if val is not None:
                    out[idx] += float(pk) * val
                    continue
            lam = (a[idx] + int(k)) * rate[idx]
            # Seed 1 with the log-scale `-λ` carried beside it, so the recursion never starts
            # from an underflowed zero; rows are rescaled as they grow.
            g = np.zeros((len(idx), S_g))
            g[:, 0] = 1.0
            logscale = -lam
            for j in range(1, S_g):
                # Σ_{i=1..j} i·f(i)·g(j-i): the severity weights against the mass reversed.
                g[:, j] = (lam / j) * np.einsum('mi,mi->m', wf[:, 1:j + 1], g[:, :j][:, ::-1])
                big = g[:, j] > _PANJER_RESCALE
                if big.any():
                    g[big, :j + 1] /= _PANJER_RESCALE
                    logscale = logscale + big * math.log(_PANJER_RESCALE)
            G = np.cumsum(g, axis=1) * np.exp(logscale)[:, None]  # P(D <= j)
            R = np.take_along_axis(G, gather, axis=1) * mask
            val = np.einsum('mu,mu->m', surv, R)
            if gkey is not None:
                memo[(gkey, int(k))] = val
            out[idx] += float(pk) * val
    return out


def fill_rate(orders, lines_per_day: float, *, transit: dict | None = None,
              lead_unit_days: float = 1.0, memo: dict | None = None) -> dict:
    """The expected FIRST-PASS fill rate of one section at the levels the orders carry now:
    the units a shelf serves off a line, over the units the line asks for, weighted by each
    SKU's lines per day ("Choose the coverage floor", decision 5):

        Σ_s  lines_s · E[min(q_s, (S_s - D_s)^+)]  ÷  Σ_s  lines_s · E[q_s]     lines_s = n · π_s

    `S_s = Q_s + pipeline_qty_s` is the order-up-to POSITION and `D_s` the units of the SKU's
    own prior lines still in transit when this one arrives -- zero at lead zero, where the
    previous line's order has landed and the shelf sits at `Q_s`, and priced by
    `_served_under_lead` otherwise ("Declare the coverage against the inbound lead", decision
    5: the promise holds AT the lead).  `transit` is `transit_day_law`'s result for the pair
    (None = no pipeline) and `lead_unit_days` reads the SKU's supplier lead as days.  The
    denominator is the section's daily units; the numerator the units served before a
    restock, so `1 - fill_rate` is the expected missed share the audit's `supply` clause
    reads.  A SKU with no lead and no pipeline stamp takes the lead-free expression through
    ITS OWN code path (`expected_min`), so a record with no pipeline is byte-identical to one
    priced before the lead existed.  Call it on the PLANNED orders: the shelf that serves the
    line is the one the run fields, which since "Field the requirement" is the declared one
    exactly, so pre-plan and post-plan price the same number.  Returns
    `{'fill_rate', 'expected_missed_share', 'units_per_day', 'served_per_day',
    'base_stock_skus', 'base_stock_share', 'n_skus', 'pipeline_units', 'transit_days',
    'lead_days'}` -- `pipeline_units` is Σ `pipeline_qty` over the section and `lead_days`
    the units-weighted mean lead, both 0 when the lead-free premise holds exactly.
    """
    d = daily_demand(orders, lines_per_day)
    pmf_k = dict(transit['pmf']) if transit else {0: 1.0}
    transit_days = float(transit['transit_days']) if transit else 0.0
    no_transit = (pmf_k == {0: 1.0})
    served = 0.0
    units = 0.0
    lead_units = 0.0
    base_stock = 0
    pipeline_units = 0
    # The SKUs a lead applies to, gathered for one vectorised pass: (index, line, S, rate, a).
    pending: list = []
    weights: list = []
    for c in orders:
        # Read the DECLARATION directly.  These used to be `getattr(..., 1)` / `getattr(..., 0)`,
        # which on an undeclared order priced the fill rate against a one-unit shelf and called
        # every SKU base stock -- and `throughput.audit` reads `missed_share` against exactly
        # this number.  Every caller prices orders the fixed point has already declared, so an
        # undeclared one here is a bug in the caller, and it says so instead of answering.
        q = int(c.equilibrium_qty)
        if int(c.reorder_point) >= q - 1:
            base_stock += 1
        pipeline = int(getattr(c, 'pipeline_qty', None) or 0)
        pipeline_units += pipeline
        ds = d[c.sku]
        if ds <= 0.0:
            continue
        line = c.demand.line
        mean = float(line.mean())
        lines_s = ds / mean
        units += ds
        lead_units += ds * sku_lead_days(c, transit_days, lead_unit_days)
        attr = max(0.0, float(getattr(c, 'lead_time_mean', 0.0) or 0.0)) * float(lead_unit_days)
        a = int(round(attr))
        if a == 0 and no_transit and pipeline == 0:
            # THE LEAD-FREE EXPRESSION, unchanged: E[min(q, Q)] <= E[q] exactly; the
            # running-product sum can land a few ulp above it on a shelf far past the tail,
            # and a fill rate of 1.0000000000000009 is not a number.
            served += lines_s * min(mean, float(line.expected_min(q)))
            continue
        pending.append((line, q + pipeline, lines_s, a))
        weights.append((lines_s, mean))
    if pending:
        got = _served_under_lead([p[0] for p in pending],
                                 np.array([p[1] for p in pending]),
                                 np.array([p[2] for p in pending]),
                                 np.array([p[3] for p in pending]), pmf_k, memo=memo)
        served += sum(w * min(m, float(v)) for (w, m), v in zip(weights, got))
    n = len(orders)
    fr = (served / units) if units > 0.0 else 0.0
    return {'fill_rate': fr, 'expected_missed_share': 1.0 - fr,
            'units_per_day': float(units), 'served_per_day': float(served),
            'base_stock_skus': int(base_stock), 'base_stock_share': (base_stock / n) if n else 0.0,
            'n_skus': n, 'pipeline_units': int(pipeline_units),
            'transit_days': transit_days,
            'lead_days': (lead_units / units) if units > 0.0 else 0.0}



def solve_floor_lines(orders, lines_per_day: float, *, coverage_days: float,
                      safety_days: float, fill_min: float,
                      transit: dict | None = None, lead_unit_days: float = 1.0,
                      lo: float = DEFAULT_FLOOR_LINES, tol: float = FLOOR_SOLVE_TOL,
                      hi_max: float = FLOOR_SOLVE_MAX) -> dict:
    """The shelf side of the first-time guarantee (ADR-0004): the smallest `floor_lines`
    at which the section's expected first-pass fill clears `fill_min` (`sqrt(c)`), with
    the line count DECLARED.

    `fill(f)` is `fill_rate` over the levels `rescale_section(f)` declares -- a pure
    catalogue closed form while every SKU sits on the floor, and the same root when a
    section is not fully floored (a SKU above its floor takes `coverage_days × d_s`, which
    the formula already reads, so `coverage_days` enters the fill through the levels and
    nothing here changes).  It is a non-decreasing STEP function of `f` (`L_s = ceil(f ×
    E[q_s])` moves one SKU at a time), so the root is bracketed by doubling from `lo` and
    bisected to `tol` lines, and the answer is the bracket's UPPER end rounded up to the
    tolerance -- always at or above the true threshold, never below it.  `lo` is the
    one-line floor: a SKU never holds less than a pick's worth of itself, so a confidence
    one line already clears solves to exactly `lo` and says so (`at_lower_bound`).

    THE FLOOR IS SOLVED AT THE LEAD ("Declare the coverage against the inbound lead",
    decision 5): `transit` (the pair's `transit_day_law`, None = no pipeline) and
    `lead_unit_days` reach both the levels and the fill, so the same declared confidence
    buys a higher floor under a pipeline -- the shelf that clears `sqrt(c)` while the
    previous line's lot is still on the road.  The fill stays a non-decreasing step
    function of `f` (the position rises with the floor, the in-transit law does not move).

    MUTATES the orders (every evaluation re-declares through `rescale_section`) and leaves
    them declared at the SOLVED floor.  Refuses a `fill_min` outside [0, 1) and a section
    that cannot reach it by `hi_max` lines.  Returns

        {'floor_lines', 'fill_rate', 'fill_min', 'evaluations', 'at_lower_bound',
         'bracket': (lo, hi)}
    """
    target = float(fill_min)
    if not (0.0 <= target < 1.0):
        raise ValueError(f'fill_min must lie in [0, 1); got {fill_min!r}')
    n = float(lines_per_day)
    evals = 0
    transit_days = float(transit['transit_days']) if transit else 0.0

    def fill(f: float) -> float:
        nonlocal evals
        evals += 1
        rescale_section(orders, n, coverage_days=coverage_days, safety_days=safety_days,
                        floor_lines=f, transit_days=transit_days,
                        lead_unit_days=lead_unit_days)
        return float(fill_rate(orders, n, transit=transit,
                               lead_unit_days=lead_unit_days)['fill_rate'])

    lo = float(lo)
    f_lo = fill(lo)
    if f_lo >= target or n <= 0.0:
        return {'floor_lines': lo, 'fill_rate': f_lo, 'fill_min': target,
                'evaluations': evals, 'at_lower_bound': True, 'bracket': (lo, lo)}
    hi = lo
    f_hi = f_lo
    while f_hi < target:
        hi *= 2.0
        if hi > float(hi_max):
            raise ValueError(
                f'the section cannot reach a first-pass fill of {target:.4f} by '
                f'{hi_max:g} lines of stock per SKU (fill {f_hi:.4f} at {hi / 2.0:g} '
                f'lines); the first-time confidence asks more of this catalogue than a '
                f'line floor can promise')
        f_hi = fill(hi)
    while hi - lo > float(tol):
        mid = 0.5 * (lo + hi)
        if fill(mid) >= target:
            hi = mid
        else:
            lo = mid
    solved = math.ceil(hi / float(tol) - 1e-9) * float(tol)
    f_solved = fill(solved)                      # the orders are left declared HERE
    return {'floor_lines': float(solved), 'fill_rate': f_solved, 'fill_min': target,
            'evaluations': evals, 'at_lower_bound': False, 'bracket': (lo, hi)}


def converged(prev: dict, cur: dict, tol: float = DEFAULT_TOL) -> bool:
    """Every channel's fixed-point `n` moved by less than `tol` (relative) since the last
    round.  A channel absent from either side, or a zero previous value, is not converged."""
    if not prev or set(prev) != set(cur):
        return False
    for k, p in prev.items():
        p = float(p)
        c = float(cur[k])
        if p <= 0.0:
            return False
        if abs(c - p) / p >= tol:
            return False
    return True
