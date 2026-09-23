"""pick.py -- a day's picking in closed form, and what one location is worth to it (S06, S07).

THE DAY.  `expected_travel`'s chain (accumulate -> routing -> expected_swaps -> expected_pick)
prices a day at a placement; this module runs it under either DRAIN ORDER:

  'recorded'   forward-pick first, then location -- `PlacementDist.initial`, what the record
               and `strategy_runner.expected_pick_over` use today;
  'smallest'   smallest on hand first, then location -- what the simulator actually drains
               (ADR-0003; S06 variant b).  The research variant: making it the production
               default is a comparability break for `pick_owed_exact_s`, proposed to the
               user and NOT made here.

TASKS -- the aisles a day opens.  Two forms (S07):

  independent  E[tasks] = sum_a (1 - exp(-Lambda_a))                (what the chain assumes)
  script       mean over the script's days of |U_{s in day} aisle(s)|  (carries the sampler's
               affinity co-draws; on the 40k fill root it sees rank_minlabor open 17-20% FEWER
               fulfillment aisles than fifo where the independent form says 3-13% MORE)

THE VALUE OF A LOCATION (the Mecke / Palm form).  Adding SKU s's lines to bin b in aisle a costs

    g_b = lambda_s [ h_b + P0_a(s) T_new(b) + (1 - P0_a(s)) E[dT_b | a open] ]

with h_b the at-location handling (`PickConfig.closed_form`, M(y_b) scaled), P0_a(s) the
probability that NO other line of a day that draws s lands in aisle a (Palm: conditioned on s
being drawn -- which is where affinity enters), T_new(b) the cost of a task opened just for b,
and E[dT_b | a open] the extra travel b adds to a task already walking a (zero on a one-way
lane; twice the overshoot past the farthest column on a two-way one).  `palm_open` and
`colocation_delta` compute the script-conditional pieces; the difference in `script` tasks
from moving s between aisles is exactly the P0 difference (the test holds them equal).
"""
from __future__ import annotations

import math
from collections import defaultdict

from Warehouse.kernel.closed_form import Call, Equation, Model, Sym


# ── the day, under either drain order ────────────────────────────────────────────────────────

def placement(bin_map: dict, geometry, drain: str = 'recorded'):
    """A `PlacementDist` for `bin_map[sku] = [(aisle_id, bayX, bayY, qty), ...]`."""
    from Optimization.simconfig import expected_travel as et
    if drain == 'recorded':
        return et.PlacementDist.initial(bin_map, geometry)
    if drain != 'smallest':
        raise ValueError(f'drain must be recorded or smallest, got {drain!r}')
    sites = {}
    for sku, bins in bin_map.items():
        rows = sorted(bins, key=lambda b: (b[3], b[0], b[1], b[2]))
        sites[sku] = [et.Site(geometry.by_id[a].key, int(q), a, int(bx), int(by))
                      for a, bx, by, q in rows]
    return et.PlacementDist('initial', sites)


def pick_day(orders, pick_cfg, bin_map: dict, geometry, lines: float, cv: float = 0.0,
             drain: str = 'recorded') -> dict:
    """`expected_pick`'s day (every component, and `s_pick`) at `lines` a day."""
    from Optimization.simconfig import expected_travel as et
    rates = et.accumulate(orders, pick_cfg, placement(bin_map, geometry, drain), geometry)
    return et.expected_pick(rates, geometry, pick_cfg, lines, cv)


# ── tasks: the aisles a day opens ────────────────────────────────────────────────────────────

def tasks_independent(lam_by_aisle) -> float:
    return sum(1.0 - math.exp(-lam) for lam in lam_by_aisle)


LAMS = Sym('Lambda', r'\Lambda_a')
TASKS = Equation('tasks', r'\mathbb{E}[\mathrm{tasks}]',
                 Call('open_aisles', tasks_independent, {'lam_by_aisle': LAMS},
                      tex=r'\sum_a\left(1-e^{-\Lambda_a}\right)'),
                 unit='tasks/day',
                 doc='aisles a day opens when every line is independent (the chain\'s form)')
TASK_MODEL = Model('tasks', (TASKS,),
                   doc='How many aisles a day opens, if lines were drawn independently.')


def tasks_script(days, where: dict) -> float:
    """Mean distinct aisles over `days` (each an iterable of SKUs), `where[sku] = aisle`."""
    days = [d for d in days]
    if not days:
        return 0.0
    return sum(len({where[s] for s in d if s in where}) for d in days) / len(days)


def palm_open(sku, aisle, days, where: dict) -> float:
    """P0_a(s): over the days that draw `sku`, the share on which NO other drawn SKU sits in
    `aisle` -- the Palm probability that s's line would open the aisle by itself.  None when
    the window never draws s."""
    n = alone = 0
    for d in days:
        d = set(d)
        if sku not in d:
            continue
        n += 1
        if not any(where.get(o) == aisle for o in d if o != sku):
            alone += 1
    return alone / n if n else None


def colocation_delta(sku, new_aisle, days, where: dict) -> float:
    """Tasks per day saved (negative) or added by moving `sku` to `new_aisle`: the
    script-conditional marginal, lambda_s [P0_new - P0_old]."""
    days = [set(d) for d in days]
    if not days:
        return 0.0
    old = where.get(sku)
    lam = sum(1 for d in days if sku in d) / len(days)
    if lam == 0.0 or old == new_aisle:
        return 0.0
    p_new = palm_open(sku, new_aisle, days, where)
    p_old = palm_open(sku, old, days, where) if old is not None else 0.0
    return lam * (p_new - p_old)


# ── the value of one location ────────────────────────────────────────────────────────────────

LAM, H_B, P0, T_NEW, DT = (Sym('lam', r'\lambda_s'), Sym('h_b', 'h_b'), Sym('P0', 'P^0_a(s)'),
                           Sym('T_new', r'T_{\mathrm{new}}(b)'),
                           Sym('dT', r'\mathbb{E}[\Delta T_b \mid a\ \mathrm{open}]'))
G_B = Equation('g_b', 'g_b', LAM * (H_B + P0 * T_NEW + (1 - P0) * DT), unit='s/day',
               doc='the daily labour a location adds for SKU s: handling, the task it opens '
                   'when nothing else would, the travel it adds when something else does')
LOCATION = Model('location value', (G_B,),
                 doc='What one location is worth to a day of picking (Mecke / Palm form).')


def overshoot_travel(x_b: float, others_x, x_pace: float, one_way: bool) -> float:
    """E[dT_b | a open] on one day: 0 on a one-way lane; on a two-way one, out-and-back to the
    farthest column -- twice the overshoot past the farthest other column the day visits."""
    if one_way or not others_x:
        return 0.0
    return 2.0 * x_pace * max(0.0, x_b - max(others_x))


def location_value(sku, aisle, x_b: float, h_b: float, days, where: dict, where_x: dict,
                   x_pace: float, t_new: float, one_way: bool) -> float:
    """g_b for putting `sku`'s lines at (aisle, x_b), script-conditional throughout:
    `where[sku] = aisle`, `where_x[sku] = x`; `t_new` the task-opening cost at that bin."""
    days = [set(d) for d in days]
    if not days:
        return 0.0
    drawn = [d for d in days if sku in d]
    lam = len(drawn) / len(days)
    if not drawn:
        return 0.0
    p0 = palm_open(sku, aisle, days, where)
    dts = []
    for d in drawn:
        xs = [where_x[o] for o in d if o != sku and where.get(o) == aisle and o in where_x]
        if xs:
            dts.append(overshoot_travel(x_b, xs, x_pace, one_way))
    dt = sum(dts) / len(dts) if dts else 0.0
    return LOCATION.evaluate({'lam': lam, 'h_b': h_b, 'P0': p0, 'T_new': t_new,
                              'dT': dt})['g_b']
