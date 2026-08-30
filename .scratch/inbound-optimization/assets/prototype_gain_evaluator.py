"""PROTOTYPE — throwaway. Wayfinder ticket 04: the gain-evaluator fidelity ladder.

Answers three questions and nothing else (delete freely once the ticket is resolved):

  1. Where between "full greedy-sequential virtual pool" and "independent per-item
     best-bin" does the resulting TRAILER ORDERING stop changing?
  2. What does each rung cost at realistic scale (standing trailers x items per load
     x candidate bins)?  This number is the caching stakes for ticket 06.
  3. What contract should the real evaluator have?

Faithful-to-arm is taken literally at the top rung: it drives the repo's actual
`_RankedAssignPool` (tmin: minimize=True, default pick-effort order incl. the
co-occurrence term) over stub units shaped exactly like
Tests/unit/test_ranked_assign_pool_equivalence.py's fixtures.  Costs are priced with
the real cost model (`per_pick`, `handle_var`, `height_multiplier`, `SpeedProfile`) —
the pool CHOOSES bins by its own D, the evaluator PRICES the choice with put+pick
work, which is exactly the disagreement ticket 10 decision 5 requires us to honor.

The plan loop is ticket 10 decision 4's greedy, verbatim:

    while candidates:
      gain(t) = E[put+pick work if t's load places from the pool NOW]
              - E[same if deferred to NEXT drain's pool (leftovers + predicted)]
      take argmax gain (ties -> FIFO by arrival); virtually consume the chosen load
      -> the returned order

Run:  python .scratch/inbound-optimization/assets/prototype_gain_evaluator.py
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from collections import defaultdict

# entry-script bootstrap (the one legal sys.path.insert site, CLAUDE.md section 2)
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, _REPO)

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.kernel.cost_model import (DEFAULT_HEIGHT_BRACKETS, SpeedProfile,
                                         handle_var, height_multiplier, per_pick)
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement import Assignment_Functions as af

# ── stub shapes (the exact fields the real pool reads; equivalence-test pattern) ──

class Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class Order:
    __slots__ = ('sku', 'labor_cost', 'demand')

    def __init__(self, sku, labor_cost, f, q):
        self.sku, self.labor_cost, self.demand = sku, labor_cost, Demand(f, q)


class Unit:
    __slots__ = ('order', 'qty', 'key')

    def __init__(self, order, qty, key):
        self.order, self.qty, self.key = order, qty, key


class Affinity:
    __slots__ = ('_sku_to_idx', '_matrix')

    def __init__(self, skus, pairs=()):
        self._sku_to_idx = {s: i for i, s in enumerate(sorted(skus))}
        n = len(self._sku_to_idx)
        try:
            import numpy as np
            from scipy.sparse import csr_matrix
        except ImportError:                                   # pragma: no cover
            self._matrix = None
            return
        m = np.zeros((n, n), dtype=np.float32)
        for a, b, lift in pairs:
            i, j = self._sku_to_idx[a], self._sku_to_idx[b]
            m[i, j] = m[j, i] = lift
        self._matrix = csr_matrix(m)


# ── the world: realistic geometry, zipf demand, real labor_cost formula ───────────

PICK = PickConfig()                                # repo defaults: 4.0/1.0 ft/s etc.
PICK_SPEED = SpeedProfile(PICK.x_speed, PICK.y_speed)
PUT_SPEED = SpeedProfile(2.0, 4.0)                 # settings.py PUT_FOOT_X / PUT_FOOT_Y
AISLE_LEN_IN = 2400.0                              # aisle_width_for(50) = 200 ft
LEVELS = 10                                        # aisle_height_for(10) = 480 in


def d_pick(b):
    return PICK_SPEED.x_pace * b.x_phys + PICK_SPEED.y_pace * b.y_phys


def d_put(b):
    return PUT_SPEED.x_pace * b.x_phys + PUT_SPEED.y_pace * b.y_phys


def make_world(rng, n_skus, n_classes, n_aisles, bins_per_class,
               predicted_frac, predicted_low_bias):
    """SKUs, per-class two-tier bin pools, affinity, and the frozen cost tables."""
    skus = list(range(1, n_skus + 1))
    orders = {}
    for i, s in enumerate(skus):
        f = (1.0 / (i + 1) ** 0.8)                          # zipf-ish frequency
        w = math.exp(rng.gauss(1.5, 0.8))                   # lbs
        v = math.exp(rng.gauss(6.0, 1.0))                   # in^3
        var = handle_var(w, v, PICK.pick_weight_coef, PICK.pick_volume_coef)
        labor = per_pick(1.0, PICK.pick_intercept, var, 1)  # the precomputed per-pick effort
        q = max(1.0, rng.gauss(4.0, 2.0))                   # per-visit draw
        orders[s] = Order(s, labor, f, q)
    sku_class = {s: rng.randrange(n_classes) for s in skus}
    var_of = {s: handle_var(math.exp(rng.gauss(1.5, 0.8)), math.exp(rng.gauss(6.0, 1.0)),
                            PICK.pick_weight_coef, PICK.pick_volume_coef) for s in skus}

    empties, predicted = {}, {}
    for c in range(n_classes):
        now_bins, pred_bins = [], []
        for _ in range(bins_per_class):
            aid = rng.randrange(n_aisles)
            x = rng.uniform(0.0, AISLE_LEN_IN)
            y = 48.0 * rng.randrange(LEVELS)
            b = Bin(aid, x, y)
            if rng.random() < predicted_frac:
                # predicted clears skew toward LOW-D bins when biased: demand drains
                # the good bins a tmin-placed warehouse filled first.
                if predicted_low_bias and rng.random() < 0.7:
                    b = Bin(aid, x * 0.4, 48.0 * rng.randrange(max(1, LEVELS // 3)))
                pred_bins.append(b)
            else:
                now_bins.append(b)
        empties[c], predicted[c] = now_bins, pred_bins

    pair_skus = rng.sample(skus, min(40, n_skus))
    pairs = [(a, b, rng.uniform(1.5, 6.0))
             for a, b in zip(pair_skus[::2], pair_skus[1::2])]
    aff = Affinity(skus, pairs)
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {aff._sku_to_idx[s]: f for s, f in fbs.items()}
    # a plausible already-placed state so the co-occurrence term is live (frozen per drain)
    seeded = {a: set(rng.sample(skus, min(8, n_skus))) for a in range(n_aisles)}
    state = {
        'aisle_sku_sets': defaultdict(set, {a: set(v) for a, v in seeded.items()}),
        'aisle_idx_sets': defaultdict(set, {a: {aff._sku_to_idx[s] for s in v}
                                            for a, v in seeded.items()}),
        'aisle_demand_sum': defaultdict(float, {a: rng.uniform(0.0, 3.0)
                                                for a in range(n_aisles)}),
    }
    return orders, sku_class, var_of, empties, predicted, aff, fbs, qbs, fbi, state


def make_trailers(rng, n_trailers, units_per_load, orders, sku_class, var_of):
    """Standing trailers: contiguous SKU lots (FIFO loading), grouped-by-class loads."""
    trailers = []
    skus = list(orders)
    for seq in range(n_trailers):
        load = []
        left = units_per_load
        while left > 0:
            s = rng.choice(skus)
            lot = min(left, rng.randint(1, 8))
            for _ in range(lot):
                qty = rng.randint(4, 40)                    # pieces in the pack
                load.append(Unit(orders[s], qty, sku_class[s]))
            left -= lot
        trailers.append((seq, load))
    return trailers


# ── pricing one (unit, bin) pair: the objective's put + pick work ─────────────────

def pair_cost(unit, b, var_of):
    """put (travel, paid once) + pick (E[visits] x per-visit at-location + travel)."""
    put = d_put(b)
    visits = max(1.0, unit.qty / unit.order.demand.quantity_rate)
    hmult = height_multiplier(DEFAULT_HEIGHT_BRACKETS, b.y_phys)
    at_bin = per_pick(hmult, PICK.pick_intercept, var_of[unit.order.sku],
                      unit.order.demand.quantity_rate)
    return put + visits * (d_pick(b) + at_bin)


def exhaustion_cost(pool_bins, var_of, unit):
    """A load unit the virtual pool cannot seat: charge the class's worst bin +50%.
    Crude stand-in for the per-unit spill path; counted so the contract can say how
    often it fires."""
    if pool_bins:
        worst = max(pool_bins, key=d_pick)
        return pair_cost(unit, worst, var_of) * 1.5
    return 600.0 * max(1.0, unit.qty / unit.order.demand.quantity_rate)


# ── the fidelity ladder: four rungs of the SAME greedy plan ───────────────────────
# Every rung implements plan(trailers) -> ordered seqs via ticket 10's greedy; they
# differ ONLY in how a load's placement (and hence its cost) is estimated.

def _wp():
    return WorkloadParams.from_pick_config(PICK)


class FullPoolRung:
    """L3 — the real `_RankedAssignPool` per class, per evaluation, over the current
    virtual candidates; serve order = the pool's own sort_key (co-occurrence live);
    virtual consumption = the chosen load's takes removed from the class lists."""

    name = 'L3 full-pool'

    def __init__(self, world):
        (self.orders, self.sku_class, self.var_of, empties, predicted, self.aff,
         self.fbs, self.qbs, self.fbi, self.state) = world
        self.now = {c: list(bs) for c, bs in empties.items()}
        self.pred = {c: list(bs) for c, bs in predicted.items()}
        self.exhausted = 0
        self.wp = _wp()

    def _place(self, by_class, pools):
        total, takes = 0.0, []
        for c, units in by_class.items():
            cands = pools.get(c, [])
            if not cands:
                for u in units:
                    total += exhaustion_cost(cands, self.var_of, u)
                    self.exhausted += 1
                continue
            # fresh copies of the arm's mutable placement state: purity over frozen ctx
            ass = defaultdict(set, {a: set(v) for a, v in self.state['aisle_sku_sets'].items()})
            ais = defaultdict(set, {a: set(v) for a, v in self.state['aisle_idx_sets'].items()})
            ads = defaultdict(float, self.state['aisle_demand_sum'])
            pool = af._RankedAssignPool(list(cands), self.aff, self.wp, ass, ais, ads,
                                        self.fbi, self.fbs, self.qbs, beta=1.0,
                                        minimize=True)
            for u in pool.order(list(units)):
                b, _score = pool.take(u)
                if b is None:
                    total += exhaustion_cost(cands, self.var_of, u)
                    self.exhausted += 1
                else:
                    total += pair_cost(u, b, self.var_of)
                    takes.append((c, b))
        return total, takes

    def gain(self, load):
        by_class = defaultdict(list)
        for u in load:
            by_class[u.key].append(u)
        now_cost, takes = self._place(by_class, self.now)
        next_pool = {c: self.now.get(c, []) + self.pred.get(c, []) for c in by_class}
        next_cost, _ = self._place(by_class, next_pool)
        return next_cost - now_cost, takes

    def consume(self, takes):
        for c, b in takes:
            self.now[c] = [x for x in self.now[c] if x is not b]

    def plan(self, trailers):
        remaining, out = list(trailers), []
        while remaining:
            scored = []
            for seq, load in remaining:
                g, takes = self.gain(load)
                scored.append((g, -seq, seq, load, takes))
            g, _, seq, load, takes = max(scored)
            self.consume(takes)
            out.append(seq)
            remaining = [t for t in remaining if t[0] != seq]
        return out


class MergeRung:
    """L2 — contention-aware without the pool machinery: per class the k cheapest
    (by the arm's own D) current bins go to the load's units in freq x labor_cost
    priority order.  No co-occurrence, no aisle bookkeeping.  Same consumption."""

    name = 'L2 merge'

    def __init__(self, world):
        (self.orders, self.sku_class, self.var_of, empties, predicted, _aff,
         _fbs, _qbs, _fbi, _state) = world
        self.wp = _wp()
        sp = SpeedProfile(self.wp.x_speed, self.wp.y_speed)
        self.dkey = lambda b: sp.x_pace * b.x_phys + sp.y_pace * b.y_phys
        self.now = {c: sorted(bs, key=self.dkey) for c, bs in empties.items()}
        self.pred = {c: list(bs) for c, bs in predicted.items()}
        self.exhausted = 0

    def _place(self, by_class, pools):
        total, takes = 0.0, []
        for c, units in by_class.items():
            cands = pools.get(c, [])
            ordered = sorted(units, key=lambda u: u.order.demand.relative_frequency
                             * u.order.labor_cost, reverse=True)
            for i, u in enumerate(ordered):
                if i < len(cands):
                    total += pair_cost(u, cands[i], self.var_of)
                    takes.append((c, cands[i]))
                else:
                    total += exhaustion_cost(cands, self.var_of, u)
                    self.exhausted += 1
        return total, takes

    def gain(self, load):
        by_class = defaultdict(list)
        for u in load:
            by_class[u.key].append(u)
        now_cost, takes = self._place(by_class, self.now)
        merged = {c: sorted(self.now.get(c, []) + self.pred.get(c, []), key=self.dkey)
                  for c in by_class}
        next_cost, _ = self._place(by_class, merged)
        return next_cost - now_cost, takes

    def consume(self, takes):
        for c, b in takes:
            self.now[c] = [x for x in self.now[c] if x is not b]

    plan = FullPoolRung.plan


class IndepConsumeRung(MergeRung):
    """L1 — independent per-item best-bin (every unit of a class priced at the SAME
    current head bin, no within-load contention), but the chosen load still consumes
    its class heads between plan steps (cross-step contention survives)."""

    name = 'L1 indep+consume'

    def _place(self, by_class, pools):
        total, takes = 0.0, []
        for c, units in by_class.items():
            cands = pools.get(c, [])
            head = cands[0] if cands else None
            for u in units:
                if head is not None:
                    total += pair_cost(u, head, self.var_of)
                else:
                    total += exhaustion_cost(cands, self.var_of, u)
                    self.exhausted += 1
            takes.extend((c, b) for b in cands[:len(units)])
        return total, takes


class StaticRung(MergeRung):
    """L0 — independent per-item best-bin and NO consumption anywhere: gains are
    computed once against the frozen tiers and the plan is one sort."""

    name = 'L0 static'

    def plan(self, trailers):
        scored = []
        for seq, load in trailers:
            g, _ = self.gain(load)
            scored.append((g, -seq, seq))
        return [seq for _g, _ns, seq in sorted(scored, reverse=True)]


RUNGS = [FullPoolRung, MergeRung, IndepConsumeRung, StaticRung]


# ── agreement metrics ─────────────────────────────────────────────────────────────

def kendall_tau(a, b):
    pos = {v: i for i, v in enumerate(b)}
    n, disc = len(a), 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos[a[i]] > pos[a[j]]:
                disc += 1
    pairs = n * (n - 1) // 2
    return 1.0 - 2.0 * disc / pairs if pairs else 1.0


def topk_overlap(a, b, k):
    return len(set(a[:k]) & set(b[:k])) / k if k else 1.0


def first_divergence(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None


# ── the experiment grid ───────────────────────────────────────────────────────────

def run_cell(seed, T, U, n_aisles, bins_per_class, n_classes=6, n_skus=400,
             predicted_frac=0.35, predicted_low_bias=True, verbose=False):
    rng = random.Random(seed)
    world = make_world(rng, n_skus, n_classes, n_aisles, bins_per_class,
                       predicted_frac, predicted_low_bias)
    orders, sku_class, var_of = world[0], world[1], world[2]
    trailers = make_trailers(rng, T, U, orders, sku_class, var_of)
    results = {}
    for cls in RUNGS:
        rung = cls(world)
        t0 = time.perf_counter()
        order = rung.plan(trailers)
        dt = time.perf_counter() - t0
        results[cls.name] = (order, dt, rung.exhausted)
    ref_order = results[RUNGS[0].name][0]
    doors = 4
    if verbose:
        for name, (order, dt, ex) in results.items():
            print(f'    {name:18s} {dt*1000:9.1f} ms  exhausted={ex:4d}  order={order}')
    rows = []
    for name, (order, dt, ex) in results.items():
        rows.append({
            'rung': name, 'ms': dt * 1000.0,
            'tau': kendall_tau(order, ref_order),
            'top_doors': topk_overlap(order, ref_order, doors),
            'top_half': topk_overlap(order, ref_order, max(1, T // 2)),
            'first_div': first_divergence(order, ref_order),
            'exhausted': ex,
        })
    return rows


def main():
    print(__doc__.split('Run:')[0])
    print('=' * 78)
    grid = [
        # (T standing trailers, U units/load, aisles, bins per class)
        (6,   30, 12, 120),
        (12, 100, 24, 300),
        (24, 300, 32, 600),
    ]
    seeds = (0, 1, 2)
    agg = defaultdict(lambda: defaultdict(list))
    for T, U, A, B in grid:
        print(f'\n-- scale T={T} trailers x U={U} units/load x {B} bins/class '
              f'({A} aisles), {len(seeds)} seeds --')
        for seed in seeds:
            for row in run_cell(seed, T, U, A, B, verbose=(seed == seeds[0])):
                for k, v in row.items():
                    if k != 'rung':
                        agg[(T, U, B)][(row['rung'], k)].append(v)
        print(f'    {"rung":18s} {"ms/plan":>10s} {"tau":>7s} {"top4":>6s} '
              f'{"tophalf":>8s} {"firstdiv":>9s} {"exhaust":>8s}')
        for cls in RUNGS:
            n = cls.name
            g = agg[(T, U, B)]
            ms = sum(g[(n, 'ms')]) / len(seeds)
            tau = sum(g[(n, 'tau')]) / len(seeds)
            t4 = sum(g[(n, 'top_doors')]) / len(seeds)
            th = sum(g[(n, 'top_half')]) / len(seeds)
            fd = [x for x in g[(n, 'first_div')] if x is not None]
            fdv = f'{min(fd)}' if fd else '-'
            ex = sum(g[(n, 'exhausted')]) / len(seeds)
            print(f'    {n:18s} {ms:10.1f} {tau:7.3f} {t4:6.2f} {th:8.2f} '
                  f'{fdv:>9s} {ex:8.0f}')

    # the same grid with UNBIASED predicted clears (deferral tier no better than now)
    print('\n-- robustness: predicted tier NOT biased toward good bins --')
    for T, U, A, B in grid[1:2]:
        for seed in seeds:
            rows = run_cell(seed, T, U, A, B, predicted_low_bias=False)
            line = '  '.join(f"{r['rung']}: tau={r['tau']:.3f}" for r in rows)
            print(f'    seed {seed}: {line}')

    # caching stakes: what one plan costs vs. drains per run
    print('\n-- caching stakes (feeds ticket 06) --')
    T, U, A, B = grid[-1]
    t0 = time.perf_counter()
    run_cell(0, T, U, A, B)
    dt = time.perf_counter() - t0
    print(f'    worst grid cell, ALL 4 rungs incl. instrumentation: {dt:.2f} s')
    print(f'    a run = ~100 drains/arm (N_BATCHES) x plan-per-drain;')
    print(f'    L3 at T={T}: see ms/plan above -> per-arm seconds = ms/plan / 10')


if __name__ == '__main__':
    main()
