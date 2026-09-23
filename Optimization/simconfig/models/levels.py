"""levels.py -- where a SKU's equilibrium units come from, as one closed-form model.

The declaration chain `Optimization/simconfig/coverage.py` computes for every SKU, written once
as an expression tree (`Warehouse/kernel/closed_form.py`) so it evaluates, renders and is held
equal to the code it models:

    E[q]  = lambda + e^-lambda                      the line law's mean (q = max(1, Poisson))
    d     = n * pi * E[q]                            daily units at the declared line rate
    L     = max(1, ceil(f * E[q]))                   the line floor, f the SOLVED floor lines
    l     = supplier lead * unit + E[ceil(T/D)]      order-to-shelf lead, days
    P     = max(0, round(d * l))                     the pipeline allowance
    Q     = max(L, round(C * d))                     the order-up-to (1 when that is <= 1)
    rp    = min(Q - 1, max(L, round(d (l + s))))     the reorder point (1 when Q is 1)

`pi` here is the LINE SHARE f_s / sum(f) -- what the declaration prices with.  What the sampler
actually delivers differs per SKU (S01: the affinity lift steepens the store's frequency spread
and flattens fulfillment's), which matters for the dynamics, not for the levels.

`f` (the floor lines) is an INPUT: it is solved per section by `coverage.solve_floor_lines` so the
first-pass fill clears sqrt(c), which is a bisection over the whole section, not a per-SKU law.
The record stamps it (reference catalogue: 1.3078 store, 1.4994 fulfillment).

`on_floor` answers the question the study turns on: a SKU is ON THE FLOOR when its coverage term
does not exceed its floor, `round(C d) <= L`.  Then Q = L -- base stock -- and the SKU's stock is
set by how big one line is, not by how fast it sells.  On the reference catalogue essentially
every SKU is on the floor (memory `coverage-in-days-floors-the-store-section`).
"""
from __future__ import annotations

from Warehouse.kernel.closed_form import (Const, Equation, Mirror, Model, Piecewise, Sym, ceil,
                                          exp, fmax, fmin, register, rnd)

LAM, N, PI, F_LINES = Sym('lam', r'\lambda_s'), Sym('n'), Sym('pi', r'\pi_s'), Sym('f_L', 'f')
C_DAYS, SAFETY = Sym('C', 'C'), Sym('safety', r'\sigma_{\mathrm{safe}}')
SUPPLIER, UNIT, TRANSIT = (Sym('supplier', r'\ell_{\mathrm{sup}}'), Sym('unit', 'u_d'),
                           Sym('transit', r'\mathbb{E}\lceil T/D \rceil'))
EQ, D, L, ELL, P = (Sym('Eq', r'\mathbb{E}[q_s]'), Sym('d', 'd_s'), Sym('L', 'L_s'),
                    Sym('ell', r'\ell_s'), Sym('P', 'P_s'))
QCOV, Q = Sym('Qcov', r'Q^{\mathrm{cov}}_s'), Sym('Q', 'Q_s')


# ── mirrors: adapters from symbols to the record's own functions ─────────────────────────────

def _line(lam):
    from Warehouse.catalog.Demand import LineDistribution
    return LineDistribution('poisson_max1', {'lam': lam})


def mirror_line_mean(lam):
    return _line(lam).mean()


def mirror_line_floor(lam, f_L):
    from Optimization.simconfig.coverage import line_floor
    return line_floor(_line(lam), f_L)


def mirror_lead(supplier, unit, transit):
    from types import SimpleNamespace

    from Optimization.simconfig.coverage import sku_lead_days
    return sku_lead_days(SimpleNamespace(lead_time_mean=supplier), transit, unit)


def mirror_Q(d, ell, C, safety, L):
    from Optimization.simconfig.coverage import stock_levels
    return stock_levels(d, ell, C, safety, int(L))[0]


def mirror_rp(d, ell, C, safety, L):
    from Optimization.simconfig.coverage import stock_levels
    return stock_levels(d, ell, C, safety, int(L))[1]


_HERE = 'Optimization.simconfig.models.levels'

LINE_MEAN = register('levels.line_mean', Equation(
    'Eq', r'\mathbb{E}[q_s]', LAM + exp(-LAM),
    doc='units per line: q = max(1, X), X ~ Poisson(lambda)',
    mirrors=Mirror(f'{_HERE}:mirror_line_mean', args={'lam': 'lam'},
                   domain={'lam': ('int', 1, 20)})))

DAILY = Equation('d', 'd_s', N * PI * EQ, unit='units/day',
                 doc='daily units at the declared line rate and the line share')

FLOOR = Equation(
    'L', 'L_s', fmax(1, ceil(F_LINES * EQ - Const(1e-9, r'\epsilon'))), unit='units',
    doc='the line floor: the least stock a SKU holds, f lines of its mean line')
# The MIRRORED twin reads lambda directly (the record's `line_floor` takes the line law, not
# its mean), so the gate samples the same inputs the record's function takes; the model above
# reads E[q] from LINE_MEAN by symbol.  Same tree below the E[q] node.
register('levels.line_floor', Equation(
    'L', 'L_s', fmax(1, ceil(F_LINES * (LAM + exp(-LAM)) - Const(1e-9, r'\epsilon'))),
    unit='units',
    mirrors=Mirror(f'{_HERE}:mirror_line_floor', args={'lam': 'lam', 'f_L': 'f_L'},
                   domain={'lam': ('int', 1, 20), 'f_L': (0.2, 4.0)})))

LEAD = register('levels.lead', Equation(
    'ell', r'\ell_s', fmax(0.0, SUPPLIER) * UNIT + fmax(0.0, TRANSIT), unit='days',
    doc='order-to-shelf lead: supplier lead (batches x days per batch) plus the transit law',
    mirrors=Mirror(f'{_HERE}:mirror_lead',
                   args={'supplier': 'supplier', 'unit': 'unit', 'transit': 'transit'},
                   domain={'supplier': (0.0, 5.0), 'unit': (0.25, 1.0),
                           'transit': (0.0, 3.0)})))

PIPELINE = Equation('P', 'P_s', fmax(0, rnd(D * fmax(0.0, ELL))), unit='units',
                    doc='the pipeline allowance: units expected in transit over the lead')

COVERAGE = Equation('Qcov', r'Q^{\mathrm{cov}}_s', rnd(C_DAYS * D), unit='units',
                    doc='the coverage term: C days of demand')

ORDER_UP_TO = register('levels.Q', Equation(
    'Q', 'Q_s', Piecewise(((Const(1), fmax(L, rnd(C_DAYS * D)) <= 1),),
                          fmax(L, rnd(C_DAYS * D))), unit='units',
    doc='the order-up-to: coverage days of demand, floored at the line floor',
    mirrors=Mirror(f'{_HERE}:mirror_Q',
                   args={'d': 'd', 'ell': 'ell', 'C': 'C', 'safety': 'safety', 'L': 'L'},
                   domain={'d': (0.0, 40.0), 'ell': (0.0, 4.0), 'C': (1.0, 20.0),
                           'safety': (0.0, 4.0), 'L': ('int', 1, 60)})))

REORDER_POINT = register('levels.rp', Equation(
    'rp', r'r_s', Piecewise(((Const(1), fmax(L, rnd(C_DAYS * D)) <= 1),),
                            fmin(fmax(L, rnd(C_DAYS * D)) - 1,
                                 fmax(L, rnd(D * (fmax(0.0, ELL) + SAFETY))))), unit='units',
    doc='the reorder point: lead-plus-safety demand, floored at L, one below Q',
    mirrors=Mirror(f'{_HERE}:mirror_rp',
                   args={'d': 'd', 'ell': 'ell', 'C': 'C', 'safety': 'safety', 'L': 'L'},
                   domain={'d': (0.0, 40.0), 'ell': (0.0, 4.0), 'C': (1.0, 20.0),
                           'safety': (0.0, 4.0), 'L': ('int', 1, 60)})))

ON_FLOOR = Equation('on_floor', r'\mathbb{1}_{\mathrm{floor}}',
                    Piecewise(((Const(1), QCOV <= L),), Const(0)),
                    doc='1 when the coverage term does not exceed the floor: base stock, Q = L')

POSITION = Equation('S', 'S_s', Q + P, unit='units',
                    doc='the inventory position a fired order restores: order-up-to plus pipeline')

LEVELS = Model('levels', (LINE_MEAN, DAILY, FLOOR, LEAD, PIPELINE, COVERAGE,
                          ORDER_UP_TO, REORDER_POINT, ON_FLOOR, POSITION),
               doc='Where one SKU\'s equilibrium units come from: the declaration '
                   '`Optimization/simconfig/coverage.py` stamps on it.')


def sku_inputs(order, *, lines_per_day: float, line_share: float, floor_lines: float,
               transit_days: float, lead_unit_days: float = 1.0, coverage_days: float = 10.0,
               safety_days: float = 2.0) -> dict:
    """The LEVELS model's inputs for one catalogue order."""
    return {'lam': order.demand.line.params['lam'], 'n': float(lines_per_day),
            'pi': float(line_share), 'f_L': float(floor_lines),
            'supplier': float(getattr(order, 'lead_time_mean', 0.0) or 0.0),
            'unit': float(lead_unit_days), 'transit': float(transit_days),
            'C': float(coverage_days), 'safety': float(safety_days)}


def section(orders, *, lines_per_day: float, floor_lines: float, transit_days: float,
            lead_unit_days: float = 1.0, coverage_days: float = 10.0,
            safety_days: float = 2.0) -> list:
    """The LEVELS model evaluated for every SKU of one channel section: a list of
    `(order, Result)` in the order given."""
    W = sum(float(c.demand.relative_frequency) for c in orders) or 1.0
    out = []
    for c in orders:
        ins = sku_inputs(c, lines_per_day=lines_per_day,
                         line_share=float(c.demand.relative_frequency) / W,
                         floor_lines=floor_lines, transit_days=transit_days,
                         lead_unit_days=lead_unit_days, coverage_days=coverage_days,
                         safety_days=safety_days)
        out.append((c, LEVELS.evaluate(ins)))
    return out
