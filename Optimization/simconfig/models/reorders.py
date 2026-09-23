"""reorders.py -- when a reorder fires, how often, and what lands, as one closed-form model.

What the simulator does (`Warehouse/inventory/inventory_reorder.py:246-274, 501-569`): after a
pick the SKU's POSITION (on hand + queued + deferred) is compared with its reorder point r; at
or below it, the next `check_reorders` fires an order-up-to lot `Q + P - position`, noised by
the SKU's supply CV.  After a fire the position is restored to S = Q + P.

On the floor (every SKU on both reference catalogues, S02) r = Q - 1, so from a full position a
fire needs the cumulative units taken since the last fire to reach **P + 1**:

  * P = 0 (almost every SKU): EVERY line fires, and its lot is exactly what the line took;
  * P >= 1: lines smaller than P + 1 accumulate, and a fire comes after N lines, where N is the
    first passage of the running line-quantity sum over P + 1 -- a renewal count.

With q ~ max(1, Poisson(lambda)) the renewal function U(j) = sum_n P(S_n = j) satisfies
U(0) = 1, U(j) = sum_{k=1..j} p_k U(j - k), and the expected lines per fire is

    E[N] = sum_{j=0}^{P} U(j)                       (lines until the sum reaches P + 1)

so, by Wald,  E[lot] = E[q] E[N],  fires per day = p_s / E[N],  units ordered per day = p_s E[q].

`p_s` is the REALISED line rate (S01: the sampler's, not the line share), and a line takes
`min(q, on hand)` when stock is short -- the thinning S06 names.

SUPPLY NOISE shifts the threshold (S03): a fire lands `epsilon = qty - ideal` units off, so the
next fire needs P + 1 + epsilon units taken; `renewal_lines_noisy` averages the renewal count
over epsilon's law.  PIPELINE FILL is a one-time transient, not a flow: a run starts every SKU
at Q with nothing on order, so the first fire of a SKU with P >= 1 orders its P extra units.
"""
from __future__ import annotations

import math

from Warehouse.kernel.closed_form import Call, Equation, Model, Sym, exp


def line_pmf(lam: float, upto: int) -> list:
    """P(q = k) for k = 0..upto, q = max(1, Poisson(lam)): P(q = 0) = 0 and P(q = 1) takes the
    Poisson's mass at 0 and 1 together."""
    p = [0.0] * (upto + 1)
    term = math.exp(-lam)                        # P(X = 0)
    for k in range(upto + 1):
        if k == 0:
            x0 = term
        else:
            term = term * lam / k                # P(X = k)
            p[k] = term
    if upto >= 1:
        p[1] += x0
    return p


def renewal_lines(lam: float, threshold: float) -> float:
    """E[N]: the expected number of lines until their running quantity reaches `threshold`
    (an integer >= 1), for q ~ max(1, Poisson(lam)).  1 when the threshold is 1."""
    t = int(round(threshold))
    if t <= 1:
        return 1.0
    pk = line_pmf(lam, t)
    U = [1.0] + [0.0] * (t - 1)
    for j in range(1, t):
        U[j] = sum(pk[k] * U[j - k] for k in range(1, j + 1))
    return sum(U)


def noise_pmf(lot: float, cv: float) -> dict:
    """The supply noise a fire lands with, epsilon = qty - ideal, as `{eps: prob}` over integers:
    `qty = max(1, round(Normal(ideal, ideal * cv)))` (`_fire_reorders`), taken at the typical lot.
    Zero noise (cv = 0) is `{0: 1}`."""
    sd = float(lot) * float(cv)
    if sd <= 1e-9:
        return {0: 1.0}
    half = 0.5
    out = {}
    lo, hi = int(math.floor(-6 * sd)) - 1, int(math.ceil(6 * sd)) + 1
    for e in range(lo, hi + 1):
        a = (e - half) / (sd * math.sqrt(2.0))
        b = (e + half) / (sd * math.sqrt(2.0))
        pr = 0.5 * (math.erf(b) - math.erf(a))
        if pr > 1e-12:
            out[e] = pr
    tot = sum(out.values())
    return {e: pr / tot for e, pr in out.items()}


def renewal_lines_noisy(lam: float, pipeline: float, cv: float) -> float:
    """E[N] with SUPPLY NOISE: a fire restores the position to S + epsilon, so the next fire
    needs the running quantity to reach P + 1 + epsilon (at least 1 -- a position already at
    or below the reorder point fires on the very next line).  Epsilon's law is `noise_pmf` at
    the typical lot E[q] * E[N] of the noise-free cycle."""
    P = int(round(pipeline))
    n0 = renewal_lines(lam, P + 1)
    lot = (lam + math.exp(-lam)) * n0
    return sum(pr * renewal_lines(lam, max(1, P + 1 + e))
               for e, pr in noise_pmf(lot, cv).items())


LAM, PIPE, RATE = Sym('lam', r'\lambda_s'), Sym('P', 'P_s'), Sym('p', 'p_s')
EQ, NCYC = Sym('Eq', r'\mathbb{E}[q_s]'), Sym('N', r'\mathbb{E}[N_s]')

LINE_MEAN = Equation('Eq', r'\mathbb{E}[q_s]', LAM + exp(-LAM),
                     doc='units per line, q = max(1, Poisson)')
CV = Sym('cv', r'\kappa_s')
CYCLE = Equation('N', r'\mathbb{E}[N_s]',
                 Call('renewal', renewal_lines_noisy, {'lam': LAM, 'pipeline': PIPE, 'cv': CV},
                      tex=r'\mathbb{E}_{\epsilon}\,\operatorname{U}_{\Sigma}'),
                 unit='lines',
                 doc='lines per fire: the renewal count of the running quantity over '
                     'P + 1 + epsilon, epsilon the supply noise the last fire landed with '
                     '(1 when P = 0 and there is no noise -- every line fires)')
LOT = Equation('lot', r'\mathbb{E}[\mathrm{lot}_s]', EQ * NCYC, unit='units',
               doc='units per fire, by Wald')
FIRES = Equation('fires', r'\phi_s', RATE / NCYC, unit='fires/day',
                 doc='reorders fired per day at the realised line rate')
UNITS = Equation('ordered', r'o_s', RATE * EQ, unit='units/day',
                 doc='units ordered per day: every unit taken is ordered back')

REORDERS = Model('reorders', (LINE_MEAN, CYCLE, LOT, FIRES, UNITS),
                 doc='When a floor SKU reorders: after the running quantity taken since its '
                     'last fire reaches P + 1, for exactly what was taken.')


def section_flows(skus: list) -> dict:
    """Section totals from `[(lam, P, p, cv), ...]`: fires/day and units ordered/day."""
    fires = units = 0.0
    for lam, P, p, cv in skus:
        r = REORDERS.evaluate({'lam': lam, 'P': P, 'p': p, 'cv': cv})
        fires += r['fires']
        units += r['ordered']
    return {'fires': fires, 'ordered': units}
