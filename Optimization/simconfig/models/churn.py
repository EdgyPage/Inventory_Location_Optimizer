"""churn.py -- how much of the picking an inbound decision can reach, how long a placement rule
keeps finding good bins, and what any unloading order could move.  The aisle-churn study's
sessions S08 and S09 (`.scratch/aisle-churn/whiteboard/`), as one module.

THE FRESH-BIN LAW (S08).  On the floor every line fires a lot of exactly what it took, and the
simulator drains a SKU's bins smallest-on-hand first (ADR-0003), so a SKU's next line is served
from the fresh packs while the declared bulk waits.  A pick is served from a bin an inbound
decision filled iff an earlier line of the same SKU fell at least one order-to-shelf lead l
before it.  With lines at Poisson rate lambda over H days, x = lambda (H - l)^+:

    E[served] = x - (1 - e^-x)              phi = sum_s E[served]_s / sum_s lambda_s H

and, CONDITIONAL on the window holding n lines of the SKU (a retrodiction):

    E[served | n] = sum_{j=2}^{n} (1 - I_{l/H}(j - 1, n - j + 2)).

THE FRONTIER LAW (S09).  `_TravelBalancedPool.take` (Rank_labor / Rank_cartlabor) puts a unit in
the head of the height bracket b minimising M_b h_s + D_b, so each bracket is a queue consumed
from its cheap end.  Frees return at costs distributed like the bracket's occupied bins; a free
in front of the front is retaken at once.  The front D_b(t) solves

    N_b(D_b) + R_b(t) Q_b(D_b) = C_b(t),    s_b(t) = Pr_h[argmin_b' (M_b' h + D_b'(t)) = b],

with dC_b/dt = m_a s_b, and the per-aisle rate m_a WATER-FILLED: the aisle is chosen by LPT on
the ledger's expected labour, so sum_a (lambda - L_a)^+ = W(t) and each aisle takes its rise.

THE STEADY STATE.  The free pool is at a flow equilibrium (frees = placements, S09 section 1),
so once the good free bins are spent a bracket hands out exactly what it frees:

    s*_b = n_b nu_b / sum_b' n_b' nu_b'      (= sigma_b, the occupancy share, when turnover
                                               nu is equal across brackets)

-- a velocity-blind rule cannot beat its own occupancy.  The time it can, the BREATHING ROOM, is

    B = G_free / (m (s - sigma_G)).

THE GAP CHAIN.  The placement rule's pick gap is earned on fresh picks:

    dT / T ~ phi (U h / T) (M_rank - M_free).

THE ORDER BOUND.  An unloading order only permutes which of a day's packs takes which of the
day's bins; by the rearrangement inequality every order lies in [C_min, C_max], and two
velocity-blind orders are exchangeable -- expected gap exactly 0, random-permutation spread
Var = (1/(n-1)) sum (w - w_bar)^2 sum (M - M_bar)^2.
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict

from Warehouse.kernel.closed_form import Call, Equation, Model, Sym, exp, fmax


# ── the fresh-bin law ────────────────────────────────────────────────────────────────────────

def beta_inc_int(x: float, a: int, b: int) -> float:
    """I_x(a, b) for positive integers: the binomial tail
    sum_{j=a}^{a+b-1} C(a+b-1, j) x^j (1-x)^(a+b-1-j)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    n = a + b - 1
    return sum(math.comb(n, j) * x ** j * (1.0 - x) ** (n - j) for j in range(a, n + 1))


def served_given_count(n: int, H: float, ell: float) -> float:
    """E[served lines | the window holds n lines of the SKU]: the j-th of n uniform points on
    [0, H] is served iff its gap to the first, H Beta(j - 1, n - j + 2), is at least ell."""
    if n < 2 or ell >= H:
        return 0.0
    x = ell / H
    return float(sum(1.0 - beta_inc_int(x, j - 1, n - j + 2) for j in range(2, n + 1)))


LAM, H, ELL = Sym('lam', r'\lambda_s'), Sym('H'), Sym('ell', r'\ell')
X = Sym('x', 'x_s')
X_EQ = Equation('x', 'x_s', LAM * fmax(0, H - ELL), unit='lines',
                doc='expected lines of the SKU after the first lead of the window')
SERVED = Equation('served', r'\mathbb{E}[\mathrm{served}_s]', X - (1 - exp(-X)), unit='lines',
                  doc='lines served from a bin a reorder filled inside the window')
PHI = Equation('phi', r'\varphi_s', Sym('served', r'\mathbb{E}[\mathrm{served}_s]') / (LAM * H),
               doc='the SKU\'s share of its lines an inbound decision can reach')
FRESH = Model('fresh', (X_EQ, SERVED, PHI),
              doc='The fresh-bin law: an inbound decision reaches only lines that follow an '
                  'earlier line of the same SKU by at least the order-to-shelf lead.')


def section_phi(rates, H: float, ell: float) -> float:
    """phi of a section, unconditional: `rates` are the SKUs' line rates per day (the
    SAMPLER's, S01 -- not the line share), those that draw no line included."""
    served = lines = 0.0
    for lam in rates:
        r = FRESH.evaluate({'lam': lam, 'H': H, 'ell': ell})
        served += r['served']
        lines += lam * H
    return served / lines if lines else 0.0


# ── the LPT aisle concentration: water-filling ───────────────────────────────────────────────

def water_level(loads, W: float):
    """(lambda, shares): the level with sum_a (lambda - L_a)^+ = W, and each aisle's share of
    W.  W <= 0 returns the lowest load and all-zero shares."""
    if W <= 0 or not loads:
        return (min(loads) if loads else 0.0), [0.0] * len(loads)
    Ls = sorted(loads)
    acc, lam = 0.0, Ls[-1] + W / len(Ls)
    for i, L in enumerate(Ls):
        nxt = Ls[i + 1] if i + 1 < len(Ls) else math.inf
        room = (i + 1) * (nxt - L)
        if acc + room >= W:
            lam = L + (W - acc) / (i + 1)
            break
        acc += room
    return lam, [max(0.0, lam - L) / W for L in loads]


def effective_count(shares) -> float:
    """1 / sum share^2 -- how many aisles the placements effectively spread over."""
    t = sum(shares)
    return 1.0 / sum((s / t) ** 2 for s in shares if s > 0) if t > 0 else 0.0


# ── the frontier law ─────────────────────────────────────────────────────────────────────────

def frontier_shares(free: dict, occ: dict, m_day: dict, r_day: float, hs: list, days: int,
                    psi: float = 0.0, steps: int = 20) -> dict:
    """The bracket shares a ranked rule's placements take, day by day, in ONE aisle (or a pool
    of homogeneous aisles fed as one).

    free / occ : {M: sorted D list}  free and occupied bins of each height multiplier at t = 0
    m_day      : {day: placements}   r_day: frees per day (flow equilibrium: r = m)
    hs         : the placed units' h (labor_cost) -- the argmin is taken over their law
    psi        : the share of placements picked out again inside the window (S08), which frees
                 where it was PUT and is retaken at once
    Returns {day: {M: share}}."""
    Ms = sorted(set(free) | set(occ))
    free = {M: free.get(M, []) for M in Ms}
    occ = {M: occ.get(M, []) for M in Ms}
    taken = {M: 0.0 for M in Ms}
    freed = {M: 0.0 for M in Ms}
    n_occ = sum(len(v) for v in occ.values()) or 1
    sigma = {M: len(occ[M]) / n_occ for M in Ms}
    grid = {M: sorted(set(free[M][::max(1, len(free[M]) // 400)] + free[M][-1:]
                          + occ[M][::max(1, len(occ[M]) // 400)] + occ[M][-1:])) for M in Ms}

    def front(M):
        f, o, C, R, cand = free[M], occ[M], taken[M], freed[M], grid[M]
        if not cand:
            return math.inf

        def avail(D):
            return bisect.bisect_right(f, D) + (R * bisect.bisect_right(o, D) / len(o)
                                                if o else 0.0)
        if avail(cand[-1]) < C + 1:
            return math.inf
        lo, hi = 0, len(cand) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if avail(cand[mid]) >= C + 1:
                hi = mid
            else:
                lo = mid + 1
        return cand[lo]

    out = {}
    for day in range(days):
        m, r = m_day.get(day, 0) / steps, r_day / steps
        acc = defaultdict(float)
        for _ in range(steps):
            Dh = {M: front(M) for M in Ms}
            win = defaultdict(int)
            for h in hs:
                win[min(Ms, key=lambda M: M * h + Dh[M])] += 1
            for M in Ms:
                s = win[M] / len(hs)
                taken[M] += m * s - r * psi * s
                freed[M] += r * (1 - psi) * sigma[M]
                acc[M] += s / steps
        out[day] = dict(acc)
    return out


# ── the steady state, the breathing room, the gap chain ─────────────────────────────────────

N_B, NU_B, NU_TOT = Sym('n_b', 'n_b'), Sym('nu_b', r'\nu_b'), Sym('turn', r'\sum_{b\'} n_{b\'}\nu_{b\'}')
S_STAR = Equation('s_star', r's^*_b', N_B * NU_B / NU_TOT,
                  doc='the long-run bracket share of a velocity-blind ranked rule: a bracket hands '
                      'out what it frees')
G_FREE, M_RATE = Sym('G_free', r'G_{\mathrm{free}}'), Sym('m', 'm')
S_NOW, SIG_G = Sym('s', 's'), Sym('sigma_G', r'\sigma_G')
BREATH = Equation('B', 'B', G_FREE / (M_RATE * (S_NOW - SIG_G)), unit='days',
                  doc='breathing room: days until the good free pool is spent at the net rate '
                      'placed-good minus freed-good')
STEADY = Model('steady', (S_STAR, BREATH),
               doc='Where a ranked rule settles once its good free bins are spent.')

PHI_S, U, HBAR, T = Sym('phi', r'\varphi'), Sym('U'), Sym('hbar', r'\bar h'), Sym('T')
M_RANK, M_FREE = Sym('M_rank', r'\bar M_{\mathrm{rank}}'), Sym('M_free', r'\bar M_{\mathrm{free}}')
GAP = Equation('gap', r'\Delta T / T', PHI_S * U * HBAR * (M_RANK - M_FREE) / T,
               doc='the placement rule\'s pick gap, earned on the picks fresh bins serve: '
                   'U the window\'s picked units, h the mean handling per unit at M = 1')
GAP_CHAIN = Model('gap', (GAP,),
                  doc='The height mechanism of a ranked rule\'s pick gap (S09 section 3).')


# ── the unloading-order bound ────────────────────────────────────────────────────────────────

def rearrangement(w, M):
    """(C_actual, C_min, C_max, sd_random) for packs `w` paired in order with bins `M`: the
    rearrangement bounds every order lies between, and the spread of a uniformly random
    pairing, whose mean is C_actual's expectation under any velocity-blind order."""
    n = len(w)
    act = sum(a * b for a, b in zip(w, M))
    ws, Ms = sorted(w, reverse=True), sorted(M)
    cmin = sum(a * b for a, b in zip(ws, Ms))
    cmax = sum(a * b for a, b in zip(ws, reversed(Ms)))
    if n < 2:
        return act, cmin, cmax, 0.0
    wb, Mb = sum(w) / n, sum(M) / n
    var = sum((a - wb) ** 2 for a in w) * sum((b - Mb) ** 2 for b in M) / (n - 1)
    return act, cmin, cmax, math.sqrt(var)


ORDER_SD = Equation('order_sd', r'\sigma_{\pi}',
                    Call('rearrangement_sd', lambda w, M: rearrangement(w, M)[3],
                         {'w': Sym('w', 'w'), 'M': Sym('M', 'M')},
                         tex=r'\operatorname{sd}_{\pi}'),
                    unit='s', doc='the spread of a velocity-blind order\'s fresh-pick cost: '
                                  'sqrt(sum (w - w_bar)^2 sum (M - M_bar)^2 / (n - 1))')


# ── the capture curve, the prize, the noise floor, the thresholds ───────────────────────────

def capture(rates, H: float, ell: float) -> float:
    """Phi(H): the share of a window's picks served from stock a rule placed in it -- the
    fresh-bin law at horizon H (`section_phi`).  THE TIME-DENSITY COLLAPSE: scaling every rate
    by k and the horizon by 1/k leaves x = lambda (H - l) unchanged except through the lead,
    so Phi_k(H) = Phi_1(k H) exactly when l << H (`collapse_gap` measures the departure)."""
    return section_phi(rates, H, ell)


def collapse_gap(rates, k: float, H: float, ell: float) -> float:
    """Phi_k(H) - Phi_1(k H): zero at lead 0; the lead's share of the window otherwise."""
    return capture([k * r for r in rates], H, ell) - capture(rates, k * H, ell)


T_BLIND, T_AWARE, PHI_CAP = (Sym('T_blind', r'T_{\mathrm{blind}}'),
                             Sym('T_aware', r'T_{\mathrm{aware}}'), Sym('Phi', r'\Phi(H)'))
PRIZE = Equation('prize', r'\Pi', T_BLIND - T_AWARE, unit='s/day',
                 doc='what a velocity-aware placement of the WHOLE stock saves over a '
                     'velocity-blind one (the opt-vs-uni layout value)')
CAPTURED = Equation('captured', r'\Pi(H)', PHI_CAP * Sym('prize', r'\Pi'), unit='s/day',
                    doc='the share of the prize a restock rule can earn in a window: it only '
                        'touches the picks its own placements serve')
PRIZE_MODEL = Model('prize', (PRIZE, CAPTURED),
                    doc='The most a placement decision can be worth over a window.')


def integrated_autocorr(x, max_lag: int | None = None) -> float:
    """tau = 1 + 2 sum_k rho_k, summed while rho_k stays positive (Geyer's initial positive
    sequence, truncated at n/4): the variance inflation of a correlated series' mean."""
    n = len(x)
    if n < 3:
        return 1.0
    m = sum(x) / n
    d = [v - m for v in x]
    c0 = sum(v * v for v in d) / n
    if c0 <= 0.0:
        return 1.0
    tau = 1.0
    for k in range(1, (max_lag or n // 4) + 1):
        rho = sum(d[i] * d[i + k] for i in range(n - k)) / n / c0
        if rho <= 0.0:
            break
        tau += 2.0 * rho
    return tau


SIG, TAU, NB, Z = Sym('sigma', r'\sigma'), Sym('tau', r'\tau'), Sym('n', 'n'), Sym('z', 'z')
FLOOR = Equation('floor', r'\delta_{95}', Z * SIG * (TAU / NB) ** 0.5, unit='%',
                 doc='half-width of a paired per-batch gap\'s interval: sd of the per-batch '
                     'relative difference, inflated by its integrated autocorrelation')
NOISE = Model('noise floor', (FLOOR,),
              doc='The smallest gap two cells of one run can tell apart.')


def noise_floor(a: dict, b: dict, z: float = 1.96) -> dict:
    """{'sigma', 'tau', 'n', 'floor'} for per-batch series `a` against reference `b`
    (`{batch: value}`), in percent -- the closed form `run_unload_ranking.measured_floor`
    bootstraps."""
    shared = sorted(k for k in a if k in b and b[k])
    d = [(a[k] - b[k]) / b[k] * 100.0 for k in shared]
    n = len(d)
    if n < 3:
        return {'sigma': None, 'tau': None, 'n': n, 'floor': None}
    m = sum(d) / n
    sd = (sum((v - m) ** 2 for v in d) / (n - 1)) ** 0.5
    tau = integrated_autocorr(d)
    return {'sigma': sd, 'tau': tau, 'n': n,
            'floor': NOISE.evaluate({'z': z, 'sigma': sd, 'tau': tau, 'n': n})['floor']}


def threshold(xs, gap, floor, *, mult: float = 2.0):
    """The first x (in the sweep's order) at which |gap(x)| exceeds mult x floor(x), linearly
    interpolated between grid points; None when no point does."""
    prev = None
    for x in xs:
        g, f = abs(gap(x)), mult * floor(x)
        if g > f:
            if prev is None:
                return x
            px, pg, pf = prev
            t = (pf - pg) / ((g - pg) - (f - pf)) if (g - pg) != (f - pf) else 1.0
            return px + max(0.0, min(1.0, t)) * (x - px)
        prev = (x, g, f)
    return None
