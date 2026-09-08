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

THIS MODULE IS PURE: orders, a line count and three declared scalars in; the orders'
stock DECLARATION written through `Order.declare_stock` (the one mutation site for the four
level slots) and a stats dict out.  It imports no CONFIG and touches no file.  The harness seam
that drives it -- the pair-level fixed point Q(n) -> plan_warehouse -> geometry -> n -- is
`Optimization/simdriver/era_coverage.py`, and it runs in EVERY mode: the calibrated era decides
whether the clock cuts and caps, never whether a run declares its stock (ADR-0002, decision 6).
"""
from __future__ import annotations

import math

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


def rescale_section(orders, lines_per_day: float, *, coverage_days: float,
                    safety_days: float, floor_lines: float) -> dict:
    """Re-derive every order's stock levels for one section and return the floor shares.

    MUTATES the orders through `Order.declare_stock`: the order-up-to, the reorder point and
    the stamped lead `pipeline_qty`, with `stock_plan` reset to None -- a plan the warehouse
    planner wrote for the OLD quantity would otherwise be honoured by `viable_storage_units` at
    the new one, and the planner re-packs the line-sized quantity.  Returns

        {'n_skus', 'line_families', 'lines_per_day', 'coverage_days', 'safety_days',
         'floor_lines', 'sum_q', 'units_per_day',
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
        lead = max(0.0, float(getattr(c, 'lead_time_mean', 0.0)))
        L = line_floor(line, floor_lines)
        q, rp = stock_levels(ds, lead, coverage_days, safety_days, L)
        c.declare_stock(q, rp, stock_plan=None, pipeline_qty=pipeline_qty(ds, lead))
        sum_q += q
        units += ds
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


def fill_rate(orders, lines_per_day: float) -> dict:
    """The expected FIRST-PASS fill rate of one section at the levels the orders carry now:
    the units a shelf at `Q_s` serves off a line, over the units the line asks for, weighted
    by each SKU's lines per day ("Choose the coverage floor", decision 5):

        Σ_s  lines_s · E[min(q_s, Q_s)]  ÷  Σ_s  lines_s · E[q_s]        lines_s = n · π_s

    The denominator is the section's daily units; the numerator the units served before a
    restock.  Under base stock with NO pipeline (`pipeline_qty = 0`: a lead of zero, which is
    every catalogue the generator authors today) the shelf sits at `Q_s` when every line
    arrives -- the previous line's order landed -- so this IS the expectation `missed_share`'s
    level is read against: `1 - fill_rate` is the expected missed share.  With a lead the
    shelf a line meets is `Q_s` minus what is still in transit, so the number is an UPPER bound
    on the fill rate there (the record carries the stamped pipeline beside it).  Call it on the
    PLANNED orders -- the planner may grow a level into leftover capacity, and the shelf that
    serves the line is the one the run fields.  Returns `{'fill_rate', 'expected_missed_share',
    'units_per_day', 'served_per_day', 'base_stock_skus', 'base_stock_share', 'n_skus',
    'pipeline_units'}` -- the last is Σ `pipeline_qty` over the section, 0 when the premise
    holds exactly.
    """
    d = daily_demand(orders, lines_per_day)
    served = 0.0
    units = 0.0
    base_stock = 0
    pipeline_units = 0
    for c in orders:
        # Read the DECLARATION directly.  These used to be `getattr(..., 1)` / `getattr(..., 0)`,
        # which on an undeclared order priced the fill rate against a one-unit shelf and called
        # every SKU base stock -- and `throughput.audit` reads `missed_share` against exactly
        # this number.  Every caller prices orders the fixed point has already declared, so an
        # undeclared one here is a bug in the caller, and it says so instead of answering.
        q = int(c.equilibrium_qty)
        if int(c.reorder_point) >= q - 1:
            base_stock += 1
        pipeline_units += int(getattr(c, 'pipeline_qty', None) or 0)
        ds = d[c.sku]
        if ds <= 0.0:
            continue
        line = c.demand.line
        mean = float(line.mean())
        lines_s = ds / mean
        # E[min(q, Q)] <= E[q] exactly; the running-product sum can land a few ulp above it
        # on a shelf far past the tail, and a fill rate of 1.0000000000000009 is not a number.
        served += lines_s * min(mean, float(line.expected_min(q)))
        units += ds
    n = len(orders)
    fr = (served / units) if units > 0.0 else 0.0
    return {'fill_rate': fr, 'expected_missed_share': 1.0 - fr,
            'units_per_day': float(units), 'served_per_day': float(served),
            'base_stock_skus': int(base_stock), 'base_stock_share': (base_stock / n) if n else 0.0,
            'n_skus': n, 'pipeline_units': int(pipeline_units)}



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
