"""lab -- the placement lab: real placement problems, candidate matchers, exact optimum.

The research harness of `.scratch/placement-sortmatch/`.  Everything here READS a finished
simulation root (sqlite `mode=ro`) and the kernel's cost law; nothing writes the repo.

THE PROBLEM.  A drain hands a tier of free bins a batch of arriving packs.  Each pack u
(SKU s) placed in bin b costs, per period of picking,

    C(u, b) = alpha_u * D_b + beta_u * M_b
    alpha_u = f_s                               (lines per period: travel is paid per line)
    beta_u  = f_s * (I + E[q_s] * (p + v_s))    (the at-location term, scaled by height)

-- `Pick._pick_time` / `inventory_optimal._optimal_work_assign`'s law, with D_b the bin's
travel seconds and M_b its height multiplier as the simulator stamped them
(`bin_scores.travel_d` / `height_mult`).  C is a sum of TWO rank-1 terms, so a single
"sort the packs, sort the bins, match by index" is exact only when every pack has the
same h_u = beta_u / alpha_u, or inside one height bracket.

THE INSTANCES (`load_drains`).  From one arm's sim DB and its warehouse:
  * bins      -- every bin of the channel (aisle_layout x bay grid), with its BinKey (the
                 aisle's), D and M (bin_scores);
  * occupancy -- the bins holding stock at a keyframe batch (bin_keyframe);
  * arrivals  -- the reorder placements of the NEXT batch (bin_placement, cause
                 'reorder'), with the SKU, the pack's BinKey and its quantity;
  * SKU law   -- f and lambda from the catalogue (`planned_inventory.cartons`),
                 v_s from `sku_scores.handle_var`, I from `simulation_runs`.
A drain is one tier (BinKey): its free bins and the packs that arrived for it.

THE MATCHERS (`MATCHERS`).  Each maps (packs, free bins) -> one bin per pack:
  uniform   a random free bin (the fifo rule's draw)
  tmin      packs by f x labor_cost desc onto bins by D asc (the merge rung)
  sm_*      sort-match: packs by a scalar key desc onto bins by D + hbar M asc
  lap       the exact optimum (scipy), over the n cheapest bins of each bracket
            (within a bracket every pack's cost rises with D, so a bin outside its
            bracket's n cheapest is dominated: the reduction is exact)

    python .scratch/placement-sortmatch/assets/lab.py <run_root> <cell> <channel> <arm>
        [--keyframe 25] [--batches 1]
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field

import numpy as np

try:
    from scipy.optimize import linear_sum_assignment
except ImportError:                     # pragma: no cover
    linear_sum_assignment = None

PER_ITEM = 0.5                          # WorkloadParams.pick_per_item (repo default)


# ── data ────────────────────────────────────────────────────────────────────────────

@dataclass
class Drain:
    key: tuple                          # the tier's BinKey (handling, category, size, unit)
    D: np.ndarray                       # free bins' travel seconds
    M: np.ndarray                       # free bins' height multipliers
    aisle: np.ndarray                   # free bins' aisle ids
    alpha: np.ndarray                   # packs' travel weight
    beta: np.ndarray                    # packs' height weight
    labor: np.ndarray                   # packs' qty-1 ground cost (tmin's key: f x labor)
    sku: np.ndarray = field(default=None)
    actual: np.ndarray = field(default=None)   # index of the bin the ARM really used

    @property
    def n(self):
        return len(self.alpha)

    @property
    def m(self):
        return len(self.D)


def _ro(path):
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


def _line_mean(lam: float) -> float:
    """E[units per line] for the shifted-Poisson line law the catalogue stamps
    (`Demand.line.mean()`, memory S02): lambda + e^-lambda."""
    return lam + math.exp(-lam)


def load_drains(root, cell, channel, arm, keyframe=25, batches=1):
    """[Drain] for every tier with arrivals in the `batches` after `keyframe`."""
    leaf = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))
    if not leaf:
        leaf = glob.glob(os.path.join(root, cell, '*', channel, channel, f'sim_{arm}.db'))
    if len(leaf) != 1:
        raise SystemExit(f'expected one sim DB for {cell}/{channel}/{arm}, got {leaf}')
    sim = leaf[0]
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(sim)))
    wh = os.path.join(pair_dir, 'warehouse.db')
    frozen = glob.glob(os.path.join(root, '_frozen', os.path.basename(pair_dir),
                                    'planned_inventory.db'))
    c = _ro(sim)
    I = c.execute('select pick_intercept from simulation_runs').fetchone()[0]
    score = {(a, x, y): (d, m) for a, x, y, d, m in c.execute(
        'select aisle_id, bayX, bayY, travel_d, height_mult from bin_scores')}
    law = {s: (v, lc) for s, v, lc in c.execute(
        'select sku, handle_var, labor_cost from sku_scores')}
    arrivals = c.execute(
        'select aisle_id, bayX, bayY, sku, qty from bin_placement where cause = "reorder" '
        'and batch_id > ? and batch_id <= ? order by batch_id, seq',
        (keyframe, keyframe + batches)).fetchall()
    kf = _ro(sim.replace('.db', '.keyframes.db'))
    occupied = {(a, x, y) for a, x, y in kf.execute(
        'select aisle_id, bayX, bayY from bin_keyframe where batch_id = ?', (keyframe,))}
    w = _ro(wh)
    aisles = {a: ((h, cat, size, unit), bx, by) for a, h, cat, unit, size, bx, by in
              w.execute('select aisle_id, handling_type, category, unit_type, '
                        'storage_size, bay_x, bay_y from aisle_layout')}
    cat = {}
    if frozen:
        cat = {s: (f, lam) for s, f, lam in _ro(frozen[0]).execute(
            'select sku, relative_frequency, demand_qty_rate from cartons')}
    # free bins per tier
    free: dict = {}
    free_ids: dict = {}
    for a, (key, bx, by) in aisles.items():
        for x in range(1, bx + 1):
            for y in range(1, by + 1):
                if (a, x, y) in occupied or (a, x, y) not in score:
                    continue
                free.setdefault(key, []).append((a, *score[(a, x, y)]))
                free_ids.setdefault(key, []).append((a, x, y))
    # packs per tier (the tier of the aisle the sim put them in)
    # The ARM'S OWN bin for each pack.  A bin that freed during the batch was not free at
    # the keyframe; it is added to the tier's free list (it WAS free when the arm used it),
    # so every matcher sees the same bins the recorded placement did.
    packs: dict = {}
    for a, x, y, sku, qty in arrivals:
        key = aisles[a][0]
        f, lam = cat.get(sku, (0.0, 1.0))
        v, lc = law.get(sku, (0.0, I + PER_ITEM))
        eq = _line_mean(lam)
        packs.setdefault(key, []).append((f, f * (I + eq * (PER_ITEM + v)), lc, sku,
                                          (a, x, y)))
    out = []
    for key, ps in packs.items():
        fb = list(free.get(key, []))
        where = {}
        # free-list entries carry only (aisle, D, M); rebuild their (a, x, y) identity
        ident = free_ids.get(key, [])
        for i, bid in enumerate(ident):
            where[bid] = i
        act = []
        for p in ps:
            bid = p[4]
            if bid not in where and bid in score:
                where[bid] = len(fb)
                ident = ident + [bid]
                fb.append((bid[0], *score[bid]))
            act.append(where.get(bid, -1))
        if not fb or not ps:
            continue
        fb = np.array(fb, dtype=float)
        pa = np.array([p[:4] for p in ps], dtype=float)
        out.append(Drain(key, D=fb[:, 1], M=fb[:, 2], aisle=fb[:, 0].astype(int),
                         alpha=pa[:, 0], beta=pa[:, 1], labor=pa[:, 2],
                         sku=pa[:, 3].astype(int), actual=np.array(act, dtype=int)))
    return out


# ── matchers: (Drain) -> index of the bin each pack takes (-1 = unseated) ───────────

def _seat(n, order_packs, order_bins):
    out = np.full(n, -1, dtype=int)
    k = min(len(order_packs), len(order_bins))
    out[order_packs[:k]] = order_bins[:k]
    return out


def m_uniform(d: Drain, seed=0):
    rng = random.Random(seed)
    bins = list(range(d.m))
    rng.shuffle(bins)
    return _seat(d.n, np.arange(d.n), np.array(bins, dtype=int))


def m_tmin(d: Drain):
    """The merge rung: f x labor_cost desc onto D asc (stable)."""
    return _seat(d.n, np.argsort(-(d.alpha * d.labor), kind='stable'),
                 np.argsort(d.D, kind='stable'))


def _hbar(d: Drain):
    a = d.alpha.sum()
    return d.beta.sum() / a if a > 0 else 0.0


def m_sm(d: Drain, key='alpha'):
    """Sort-match with the bin key D + hbar M (hbar = the drain's alpha-weighted mean
    h) and a scalar pack key:
      alpha  -- f (the travel weight)
      cost   -- alpha * Dbar + beta * Mbar: the pack's expected pick cost at an average
                free bin (the user's "highest expected pick cost first")
    """
    hb = _hbar(d)
    if key == 'alpha':
        pk = d.alpha
    elif key == 'cost':
        pk = d.alpha * d.D.mean() + d.beta * d.M.mean()
    else:
        raise ValueError(key)
    return _seat(d.n, np.argsort(-pk, kind='stable'),
                 np.argsort(d.D + hb * d.M, kind='stable'))


def _reduced(d: Drain):
    """The candidate bins the exact optimum can use: the n cheapest-D of each bracket."""
    keep = []
    for mval in np.unique(d.M):
        idx = np.nonzero(d.M == mval)[0]
        keep.extend(idx[np.argsort(d.D[idx], kind='stable')][:d.n])
    return np.array(sorted(keep), dtype=int)


def m_lap(d: Drain):
    cand = _reduced(d)
    cost = np.outer(d.alpha, d.D[cand]) + np.outer(d.beta, d.M[cand])
    r, c = linear_sum_assignment(cost)
    out = np.full(d.n, -1, dtype=int)
    out[r] = cand[c]
    return out


def m_bracket(d: Drain):
    """A2 without a solver: per-bracket sort-match, with packs dealt to brackets by the
    exact exchange rule of a two-attribute cost.  Brackets are ordered by M; a pack's
    PREFERENCE for a low bracket is its h = beta/alpha (height weight per unit of
    travel weight).  Deal the n packs to brackets in h-descending order, each bracket
    taking at most its free count, filling low-M brackets first -- then sort-match by
    alpha within each bracket.  (A heuristic; `lap` is the reference.)"""
    h = np.where(d.alpha > 0, d.beta / np.maximum(d.alpha, 1e-300), 0.0)
    order = np.argsort(-h, kind='stable')
    out = np.full(d.n, -1, dtype=int)
    pos = 0
    for mval in np.unique(d.M):
        idx = np.nonzero(d.M == mval)[0]
        take = order[pos:pos + len(idx)]
        pos += len(take)
        bins = idx[np.argsort(d.D[idx], kind='stable')]
        packs = take[np.argsort(-d.alpha[take], kind='stable')]
        out[packs] = bins[:len(packs)]
        if pos >= d.n:
            break
    return out


def m_recorded(d: Drain):
    """The bins the arm itself used -- the anchor: priced on the same objective, the
    recorded fifo arm must land where the uniform matcher does."""
    return d.actual.copy()


MATCHERS = {
    'recorded': m_recorded,
    'uniform': m_uniform,
    'tmin': m_tmin,
    'sm_alpha': lambda d: m_sm(d, 'alpha'),
    'sm_cost': lambda d: m_sm(d, 'cost'),
    'bracket': m_bracket,
    'lap': m_lap,
}


# ── scoring ─────────────────────────────────────────────────────────────────────────

def objective(d: Drain, seat) -> float:
    """Sum of C(u, b) over seated packs (per period)."""
    ok = seat >= 0
    return float((d.alpha[ok] * d.D[seat[ok]] + d.beta[ok] * d.M[seat[ok]]).sum())


def aisle_peak(d: Drain, seat) -> float:
    """Max over aisles of the alpha mass this drain adds (the one-picker-per-aisle
    proxy), relative to the mean over aisles that received anything."""
    ok = seat >= 0
    if not ok.any():
        return 0.0
    load: dict = {}
    for a, w in zip(d.aisle[seat[ok]], d.alpha[ok]):
        load[a] = load.get(a, 0.0) + w
    v = np.array(list(load.values()))
    return float(v.max() / v.mean())


def score(drains, names=None):
    names = names or list(MATCHERS)
    tot = {n: 0.0 for n in names}
    wall = {n: 0.0 for n in names}
    peak = {n: [] for n in names}
    for d in drains:
        for n in names:
            t0 = time.perf_counter()
            s = MATCHERS[n](d)
            wall[n] += time.perf_counter() - t0
            tot[n] += objective(d, s)
            peak[n].append(aisle_peak(d, s))
    return tot, wall, peak


def report(drains, names=None):
    tot, wall, peak = score(drains, names)
    names = list(tot)
    lap, uni = tot.get('lap'), tot.get('uniform')
    units = sum(d.n for d in drains)
    print(f'{len(drains)} tiers, {units} packs, {sum(d.m for d in drains)} free bins')
    print(f'{"matcher":10s} {"objective":>14s} {"vs lap":>8s} {"prize kept":>10s} '
          f'{"us/pack":>8s} {"aisle peak":>10s}')
    for n in names:
        gap = (tot[n] / lap - 1) if lap else float('nan')
        kept = ((uni - tot[n]) / (uni - lap)) if (lap is not None and uni is not None
                                                   and uni != lap) else float('nan')
        print(f'{n:10s} {tot[n]:14.1f} {gap:+8.3%} {kept:10.1%} '
              f'{1e6 * wall[n] / max(units, 1):8.1f} {np.median(peak[n]):10.2f}')
    return tot, wall, peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('cell')
    ap.add_argument('channel')
    ap.add_argument('arm')
    ap.add_argument('--keyframe', type=int, default=25)
    ap.add_argument('--batches', type=int, default=1)
    a = ap.parse_args()
    drains = load_drains(a.root, a.cell, a.channel, a.arm, a.keyframe, a.batches)
    report(drains)


if __name__ == '__main__':
    main()
