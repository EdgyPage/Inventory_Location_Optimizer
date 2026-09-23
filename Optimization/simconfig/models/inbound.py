"""inbound.py -- the inbound flow as one closed-form model: packs, trailers, the dock, crews.

The supply side the staffing record already derives (`staffing.implied_reorders`: every fired
lot, arrived by `received_law`, packed by the sim's own packer, each pack priced by the
`PutawayCost` / `UnloadCost` laws) is the per-day LOAD; this module composes it with what the
record does not carry -- trailers and the dock -- into one model, so the whole chain renders
and evaluates together:

    V_d            shipped volume per day (units reordered x unit volume; steady state = picked)
    pallets/day  = V_d / (P - E[v] + E[v^2] / 2E[v])             next-fit renewal on 48^3 pallets
    trailers/day = r E[ceil(X / r)],  X ~ N(x, (cv x)^2),  x = pallets/day / 26
                                                                 53-ft trailer; each of the r
                                                                 releases ships its partial last
                                                                 trailer: ceil(x) at cv 0, x + r/2
                                                                 once a release fills many
    rho_door     = trailers/day x E[o] / (doors x S)              `models.dock`
    recv crew    = ceil(recv load / (S rho_recv)),  put crew = ceil(put load / (S rho_put))

`expected_swaps` (the cart's next-fit renewal, `simconfig/expected_travel.py`) IS the pallet
count: a load pallet is a cart that holds 48^3 of loose items (`Inbound/trailer.py`).
"""
from __future__ import annotations

import math

from Optimization.simconfig.expected_travel import expected_swaps
from Optimization.simconfig.staffing import crew_size
from Warehouse.kernel.closed_form import Call, Const, Equation, Model, Sym

PALLET_VOLUME = 48 ** 3          # Inbound.trailer.POSITION_VOLUME
POSITIONS_53 = 26                # Inbound.trailer.Trailer53.pallet_positions

V, EV, EV2 = Sym('V', 'V_d'), Sym('e_v', r'\mathbb{E}[v]'), Sym('e_v2', r'\mathbb{E}[v^2]')
POS, REL = Sym('positions', 'n_{pos}'), Sym('releases', 'r')
PALLETS = Equation('pallets', r'n_{\mathrm{pal}}',
                   Call('next_fit', expected_swaps,
                        {'vol_per_day': V, 'e_v': EV, 'e_v2': EV2,
                         'cart_cap': Const(PALLET_VOLUME)},
                        tex=r'\operatorname{nextfit}_{48^3}'),
                   unit='pallets/day',
                   doc='load pallets per day: next-fit of the day\'s items onto 48-inch cubes')


def expected_trailers(x: float, cv: float, releases: float = 1.0) -> float:
    """r E[ceil(X / r)] for a day's full-trailer equivalent X ~ N(x, (cv x)^2): every release
    ships its open trailer, so a release that carries X / r trailers of volume ships its
    ceiling.  E[ceil(Y)] = sum_{k>=0} P(Y > k) for Y >= 0; exact ceil(x / r) at cv = 0."""
    r = max(1.0, float(releases))
    y = x / r
    if y <= 0.0:
        return 0.0
    sd = cv * y
    if sd <= 1e-12:
        return r * math.ceil(y - 1e-12)
    tot, k = 0.0, 0
    while True:
        p = 0.5 * math.erfc((k - y) / (sd * math.sqrt(2.0)))
        if p < 1e-12 and k > y:
            break
        tot += p
        k += 1
    return r * tot


CV = Sym('cv', r'c_v')
TRAILERS = Equation('trailers', r'\lambda_T',
                    Call('ceil_mean', expected_trailers,
                         {'x': Sym('pallets', r'n_{\mathrm{pal}}') / POS, 'cv': CV,
                          'releases': REL},
                         tex=r'r\,\mathbb{E}\lceil X/r \rceil'),
                    unit='trailers/day',
                    doc='each release ships its open trailer, full or not: the expected ceiling '
                        'of the day\'s full-trailer equivalent, spread by the day-to-day cv')
RECV_LOAD, PUT_LOAD = Sym('recv_load', r'W_{\mathrm{recv}}'), Sym('put_load', r'W_{\mathrm{put}}')
S, RHO_R, RHO_P = Sym('S', 'S'), Sym('rho_recv', r'\rho_{\mathrm{recv}}'), Sym('rho_put', r'\rho_{\mathrm{put}}')
RECV_CREW = Equation('recv_crew', r'K_{\mathrm{recv}}',
                     Call('crew_size', crew_size, {'load_seconds_per_day': RECV_LOAD,
                                                   'day_seconds': S, 'rho': RHO_R},
                          tex=r'\operatorname{crew}'),
                     unit='workers', doc='ceil(load / (S rho)), floored at one when there is load',
                     )
PUT_CREW = Equation('put_crew', r'K_{\mathrm{put}}',
                    Call('crew_size', crew_size, {'load_seconds_per_day': PUT_LOAD,
                                                  'day_seconds': S, 'rho': RHO_P},
                         tex=r'\operatorname{crew}'),
                    unit='workers')
INBOUND = Model('inbound', (PALLETS, TRAILERS, RECV_CREW, PUT_CREW),
                doc='From the day\'s shipped items to trailers, and from the day\'s loads to '
                    'the crews.  Compose with models.dock for the gate.')


def item_moments(units) -> tuple:
    """(V, E[v], E[v^2]) over `[(qty, unit_volume), ...]`, unit-weighted."""
    n = sum(q for q, _v in units)
    if not n:
        return 0.0, 0.0, 0.0
    V = sum(q * v for q, v in units)
    return V, V / n, sum(q * v * v for q, v in units) / n


def trailers_per_day(units, *, days: float, releases_per_day: float = 1.0,
                     positions: int = POSITIONS_53, cv: float = 0.34) -> dict:
    """The trailer law over a window's shipped items `[(qty, unit_volume), ...]` (inches^3).
    `cv` is the day-to-day spread of shipped volume (the record's `day_law.cv_units`, 0.34 on
    the reference pair)."""
    V, ev, ev2 = item_moments(units)
    r = INBOUND.evaluate({'V': V / days, 'e_v': ev, 'e_v2': ev2, 'positions': positions,
                          'releases': releases_per_day, 'cv': cv,
                          'recv_load': 0.0, 'put_load': 0.0,
                          'S': 28_800.0, 'rho_recv': 1.0, 'rho_put': 1.0})
    return {'volume_per_day': V / days, 'e_v': ev, 'pallets_per_day': r['pallets'],
            'trailers_per_day': r['trailers']}
