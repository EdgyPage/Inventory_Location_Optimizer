"""dock.py -- the two throughput gates behind the aisle-churn thresholds (S04 of the study,
`.scratch/aisle-churn/whiteboard/S04-dock-and-aisle-gates.md`).

THE DOCK.  A trailer holds one door for its whole unload, and a door is worked by at most a
door team, so a door is occupied for the trailer's work divided by the team, plus the time
lost to indivisible packs (an overhead measured at ~8%).  The dock's utilisation is

    rho_door = lambda_T * E[occ] / (doors * S),      E[occ] = W_T / team * (1 + overhead)

where lambda_T is trailers per day, W_T the unload seconds in one trailer and S the shift.  A
yard queue that is stable below rho = 1 grows without bound above it (the S12 bisection:
0.93 held at ~8 h waits, 1.01 climbed 7 -> 25 h over 40 days), and the unloading ORDER moves pick
labour only above it.  The queueing wait below the gate is Allen-Cunneen's M/G/c form,
`yard_wait`.  NOTE lambda_T is the REALISED inbound flow: it is units picked, not units
declared (below).

THE SITE GATE (S16, the 400k confirmation).  The doors bind only when the RECEIVING CREW can
fill them.  The staffing derivation sizes the crew to its utilisation target (ceil(load /
(S rho_recv)), ~0.85), so while that crew is smaller than doors x door team every door works
short-handed, a trailer takes longer (1.55 h at the 400k declared demand with 23 people on 4
doors, 1.20 h with 30), and the realised utilisation sits at the crew's target whatever the
demand: 0.854 and 0.846 measured at k = 1 and 1.35.  The capacity is min(crew, doors x team)
x S, so the yard can only go unstable once the load exceeds doors x team x S:

    rho_site = W / (min(K_recv, n_doors n_team) S),     k_gate = n_doors n_team S / (r W_1)

with W_1 the declared receiving load at k = 1 and r the realised-to-declared ratio (~1.02).  The
per-trailer form above (`DOCK`) is the same quantity read off the yard; its occupancy already
carries the short-handed doors.

THE AISLE CEILING.  The simulator hands each aisle's day of picking to one picker, so a day's
work is divisible only across aisles.  With W_a the aisle's daily task seconds at the declared
demand, the day-cut backlog of the busiest aisles grows once k W_a > S:

    k* = S / max_a W_a,      overflow(k) = sum_a (k W_a - S)^+ / sum_a k W_a

Past k*, picking falls behind demand whatever the crew; reorders -- and so trailers -- follow
the picks, which is why the dock saw less than the declaration at high density.
"""
from __future__ import annotations

import math

from Warehouse.kernel.closed_form import Equation, Model, Sym, fmin

LAM_T, W_T, TEAM, OVER = (Sym('lam_T', r'\lambda_T'), Sym('W_T', 'W_T'), Sym('team', 'n_{team}'),
                          Sym('overhead', r'\omega'))
DOORS, SHIFT, OCC = Sym('doors', 'n_{doors}'), Sym('S', 'S'), Sym('occ', r'\mathbb{E}[o]')
OCC_EQ = Equation('occ', r'\mathbb{E}[o]', W_T / TEAM * (1 + OVER), unit='s',
                  doc='door occupancy per trailer: its unload work over the door team, plus the '
                      'loss to packs one worker must carry alone')
RHO = Equation('rho_door', r'\rho_{\mathrm{door}}', LAM_T * OCC / (DOORS * SHIFT),
               doc='door utilisation; the yard is unstable at or above 1')
DOCK = Model('dock', (OCC_EQ, RHO),
             doc='The site dock as doors held for whole trailers.')

LOAD, CREW, LOAD1, RATIO = (Sym('W', 'W'), Sym('crew', r'K_{\mathrm{recv}}'), Sym('W_1', 'W_1'),
                            Sym('r', 'r'))
CAP = Equation('capacity', r'C_{\mathrm{site}}', fmin(CREW, DOORS * TEAM) * SHIFT, unit='s',
               doc='receiving seconds the site can work a day: the crew, or the door slots when '
                   'the crew outnumbers them')
RHO_SITE = Equation('rho_site', r'\rho_{\mathrm{site}}', LOAD / Sym('capacity', r'C_{\mathrm{site}}'),
                    doc='site receiving utilisation; the yard is unstable at or above 1, which '
                        'needs the load past the door slots')
K_GATE = Equation('k_gate', r'k_{\mathrm{gate}}', DOORS * TEAM * SHIFT / (RATIO * LOAD1),
                  doc='the demand multiple at which the realised load fills every door slot')
SITE = Model('site gate', (CAP, RHO_SITE, K_GATE),
             doc='Where the dock can saturate at all: only once the load outgrows the doors.')


def erlang_c(c: int, a: float) -> float:
    """P(wait) in M/M/c with offered load a = lambda / mu (< c)."""
    if a >= c:
        return 1.0
    s = sum(a ** k / math.factorial(k) for k in range(c))
    t = a ** c / math.factorial(c) * c / (c - a)
    return t / (s + t)


def yard_wait(lam_per_day: float, occ_s: float, doors: int, shift_s: float,
              cv_arrival: float = 1.0, cv_service: float = 0.45) -> float:
    """Allen-Cunneen mean queueing wait in seconds of site time; inf at or past the gate.
    `cv_service` defaults to the measured door-occupancy spread (0.43-0.48)."""
    mu = shift_s / occ_s                       # trailers per door per day
    a = lam_per_day / mu
    if a >= doors:
        return math.inf
    wq_days = erlang_c(doors, a) / (doors * mu - lam_per_day)
    return wq_days * shift_s * (cv_arrival ** 2 + cv_service ** 2) / 2.0


W_MAX = Sym('W_max', r'\max_a W_a')
K_STAR = Equation('k_star', 'k^*', SHIFT / W_MAX,
                  doc='the demand density at which the busiest aisle\'s day of picking '
                      'outlasts the shift (one picker per aisle per day)')
AISLE = Model('aisle ceiling', (K_STAR,),
              doc='Where picking stops keeping up with demand whatever the crew.')


def overflow(loads, k: float, shift_s: float) -> float:
    """Share of a day's picking work (at density k) in aisles past the shift."""
    tot = sum(k * w for w in loads)
    return sum(max(0.0, k * w - shift_s) for w in loads) / tot if tot else 0.0
