"""coverage.py — stock coverage as a RUNTIME rescaling under declared days, never a
generator knob.

The catalogue authors every SKU's order-up-to quantity in GENERATION batches
(`generate_inventory`: `equilibrium_qty = round(coverage_batches x freq x qty)`), and a
generation batch is not a unit of time -- it is "each SKU appears with probability `freq`",
which is worth however many site days the run's pickers make it worth.  Under the calibrated
era the demand a SKU sees per DAY is known at setup (.scratch/department-calibration, "Derive
the expected-travel closed form", decision 2; the derivation note's section 5):

    d_s = n · π_s · E[q_s]                    units of SKU s per day

`n` is the pair's fixed point (lines per day the declared crew fills its capacity at), `π_s`
the SKU's line share within its channel section (`freq / Σ freq`) and `E[q_s]` the mean of
the SKU's STAMPED line law (`Demand.line.mean()`; today `max(1, Poisson(λ_s))`, so
`λ_s + e^{-λ_s}`) -- read off the SKU, never re-derived here ("Stamp the line distribution
on the SKU").  This module re-derives every
SKU's stock levels by the GENERATOR'S OWN FORMULA with the day as the unit:

    equilibrium_qty = max(1, round(coverage_days · d_s))
    reorder_point   = max(1, min(Q - 1, round(d_s · (lead_days + safety_days))))   (Q > 1)
                    = 1                                                            (Q = 1)

The rounding floors are the generator's too, and they are what the caller must measure and
state: `Q >= 1` and `r >= 1` make a SKU whose `coverage_days · d_s` rounds below 1.5 hold a
single unit and reorder on its first pick, whatever the coverage says.  Coverage is nominal
AND real only for SKUs with `Q >= 2`; `rescale_section` returns the shares so the record can
say how much of the section, and of its demand, sits on the floor.

THIS MODULE IS PURE: orders, a line count and two declared scalars in; the orders' `equilibrium_qty`,
`reorder_point` and `stock_plan` mutated and a stats dict out.  It imports no CONFIG and touches
no file.  The harness seam that drives it -- the pair-level fixed point Q(n) -> plan_warehouse
-> geometry -> n -- is `Optimization/simdriver/era_coverage.py`.
"""
from __future__ import annotations

#: Relative change of a channel's fixed-point `n` below which the coverage loop has converged.
DEFAULT_TOL: float = 0.01
#: Rounds the loop may take before it stops and records the residual it stopped at.  The
#: bracketed secant (`era_coverage.next_guess`) needs ~2 rounds to bracket and ~4-6 to close
#: to 1% on the reference pair; each full-scale round costs about a minute.
DEFAULT_MAX_ROUNDS: int = 12


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


def stock_levels(d_s: float, lead_days: float, coverage_days: float,
                 safety_days: float) -> tuple[int, int]:
    """`(equilibrium_qty, reorder_point)` for one SKU by the generator's formula in days.

    `build_inventory_with_profile`'s pair, verbatim but denominated in days: the order-up-to
    is `coverage x demand`, the reorder point is demand over the lead plus the safety buffer,
    capped one below the equilibrium so the trigger always fires before the target, and
    both floored at 1.  A SKU at `Q = 1` takes the generator's `rp = 1` branch.
    """
    if coverage_days <= 0.0:
        raise ValueError(f'coverage_days must be positive days; got {coverage_days!r}')
    if safety_days < 0.0:
        raise ValueError(f'safety_days must be non-negative days; got {safety_days!r}')
    q = max(1, int(round(float(coverage_days) * float(d_s))))
    if q <= 1:
        return 1, 1
    raw = int(round(float(d_s) * (max(0.0, float(lead_days)) + float(safety_days))))
    return q, max(1, min(q - 1, raw))


def rescale_section(orders, lines_per_day: float, *, coverage_days: float,
                    safety_days: float) -> dict:
    """Re-derive every order's stock levels for one section and return the floor shares.

    MUTATES the orders: `equilibrium_qty`, `reorder_point` and `stock_plan` (reset to None,
    because a plan the warehouse planner wrote for the OLD quantity would otherwise be
    honoured by `viable_storage_units` at the new one).  Returns

        {'n_skus', 'line_families', 'lines_per_day', 'coverage_days', 'safety_days',
         'sum_q', 'units_per_day',
         'floor_q_skus', 'floor_q_share', 'floor_q_demand_share',     # Q == 1
         'floor_rp_skus', 'floor_rp_share',                            # rp formula < 1, floored
         'q_ge2_skus', 'realized_coverage_days'}                       # Σ Q / Σ d over Q >= 2

    `floor_q_demand_share` is the share of the section's daily UNITS that lands on a SKU
    holding a single unit -- the number that says whether the floor is a tail or the section.
    `realized_coverage_days` is the coverage the SKUs above the floor actually carry (their
    rounded Q against their demand), which the nominal number only approximates.
    """
    d = daily_demand(orders, lines_per_day)
    sum_q = 0
    units = 0.0
    floor_q = 0
    floor_q_units = 0.0
    floor_rp = 0
    q2_sum_q = 0
    q2_units = 0.0
    fams: dict = {}                       # law family -> SKUs read (the record's provenance)
    for c in orders:
        fams[c.demand.line.family] = fams.get(c.demand.line.family, 0) + 1
        ds = d[c.sku]
        lead = max(0.0, float(getattr(c, 'lead_time_mean', 0.0)))
        q, rp = stock_levels(ds, lead, coverage_days, safety_days)
        c.equilibrium_qty = q
        c.reorder_point = rp
        c.stock_plan = None
        sum_q += q
        units += ds
        if q == 1:
            floor_q += 1
            floor_q_units += ds
        else:
            q2_sum_q += q
            q2_units += ds
            if int(round(ds * (lead + float(safety_days)))) < 1:
                floor_rp += 1
    n = len(orders)
    return {
        'n_skus': n,
        'line_families': fams,
        'lines_per_day': float(lines_per_day),
        'coverage_days': float(coverage_days),
        'safety_days': float(safety_days),
        'sum_q': int(sum_q),
        'units_per_day': float(units),
        'floor_q_skus': int(floor_q),
        'floor_q_share': (floor_q / n) if n else 0.0,
        'floor_q_demand_share': (floor_q_units / units) if units > 0.0 else 0.0,
        'floor_rp_skus': int(floor_rp),
        'floor_rp_share': (floor_rp / n) if n else 0.0,
        'q_ge2_skus': int(n - floor_q),
        'realized_coverage_days': (q2_sum_q / q2_units) if q2_units > 0.0 else 0.0,
    }


def implied_coverage(orders, lines_per_day: float) -> dict:
    """What the section's CURRENT stock levels are worth in days at `lines_per_day` -- the
    catalogue's implicit coverage, recorded before the first rescaling so the record can say
    what the generation-batch denomination amounted to on this pair.  Demand-weighted mean
    and the plain median of `Q_s / d_s`; `sum_q` for the size before/after."""
    d = daily_demand(orders, lines_per_day)
    covs = []
    weighted = 0.0
    units = 0.0
    sum_q = 0
    for c in orders:
        ds = d[c.sku]
        q = int(getattr(c, 'equilibrium_qty', getattr(c, 'stock_qty', 1)))
        sum_q += q
        if ds > 0.0:
            covs.append(q / ds)
            weighted += q
            units += ds
    covs.sort()
    median = covs[len(covs) // 2] if covs else 0.0
    return {'n_skus': len(orders), 'lines_per_day': float(lines_per_day), 'sum_q': int(sum_q),
            'demand_weighted_days': (weighted / units) if units > 0.0 else 0.0,
            'median_days': float(median)}


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
