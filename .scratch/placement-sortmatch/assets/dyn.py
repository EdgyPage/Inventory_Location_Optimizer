"""dyn -- the dynamic placement lab: a real arrival stream, real lifetimes, online policies.

The static lab (`lab.py`) prices one drain; it cannot see the steady-state effect that
decides an ONLINE rule -- a cheap bin given to a slow pack now is a cheap bin a fast pack
cannot have later.  This lab replays one arm's recorded stream over batches
(keyframe, keyframe + H] tier by tier:

  * bins       every bin of the tier, with D and M (bin_scores);
  * occupancy  the keyframe's occupied bins, each freed at the batch its stock was
               picked out (picks table: the batch cumulative picks reach its qty);
  * arrivals   the reorder placements, in the order the arm placed them (FIFO);
  * lifetime   each pack's REAL lifetime under the recorded run -- from its placement
               batch to the batch its bin was picked out -- carried to whatever bin the
               policy under test chooses (a SKU's demand does not depend on where its
               pack sits; which of its bins drains first is smallest-first, also
               location-blind);
  * cost       REALISED pick work at the chosen bin: the pack's own recorded lines and
               units, L_u * D_b + (L_u * I + Q_u * (p + v_s)) * M_b.

The policies see only lawful inputs -- the catalogue's f and lambda and the tier's
history before the keyframe -- never the realised lines they are scored on.

POLICIES (each places one pack, FIFO, no lookahead, unless named batch_*):
  uniform      a random free bin (the fifo rule)
  cheapest     the cheapest free bin by D + hbar M, whatever the pack (velocity-blind
               greedy: tmin's bin order fed one pack at a time)
  quantile     the pack's key quantile q among the tier's historical arrivals picks the
               free bin at rank floor(q * free) in cost order (A3a, "share the index")
  ideal        the pack's quantile picks a SLOT among ALL the tier's bins, then the
               nearest free bin to it by cost rank (the `map` idea)
  batch_sort   one batch's packs sorted by key onto that batch's cheapest free bins
               (a drain-wide sort-match: batch lookahead, velocity-aware)
  batch_qsort  batch lookahead + quantile slots: the batch's packs sorted by key onto
               the free bins at their quantile ranks

    python .scratch/placement-sortmatch/assets/dyn.py <run_root> <cell> <channel> <arm>
        [--keyframe 25] [--horizon 14] [--key cost|alpha]
"""
from __future__ import annotations

import argparse
import bisect
import glob
import math
import os
import random
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lab import PER_ITEM, _line_mean, _ro  # noqa: E402


class Fenwick:
    """Counts of FREE bins over cost-rank positions: toggle, prefix count, r-th free."""

    __slots__ = ('n', 't', 'log')

    def __init__(self, flags):
        self.n = len(flags)
        self.t = [0] * (self.n + 1)
        for i, f in enumerate(flags):
            if f:
                self._add(i, 1)
        self.log = 1 << max(0, self.n.bit_length() - 1) if self.n else 0

    def _add(self, i, v):
        i += 1
        while i <= self.n:
            self.t[i] += v
            i += i & -i

    def set_free(self, i, free: bool):
        self._add(i, 1 if free else -1)

    def prefix(self, i):
        """free count in positions [0, i)."""
        s = 0
        while i > 0:
            s += self.t[i]
            i -= i & -i
        return s

    def total(self):
        return self.prefix(self.n)

    def kth(self, k):
        """Position of the k-th free bin (0-based k), by binary lifting."""
        pos, rem, step = 0, k + 1, self.log
        while step:
            nxt = pos + step
            if nxt <= self.n and self.t[nxt] < rem:
                pos, rem = nxt, rem - self.t[nxt]
            step >>= 1
        return pos


def load_stream(root, cell, channel, arm, keyframe=25, horizon=14):
    """Per tier: bins (cost-rank order), initial occupancy with free-at batches, and the
    arrival stream with keys, realised work and lifetimes."""
    leaf = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))
    if len(leaf) != 1:
        raise SystemExit(f'expected one sim DB, got {leaf}')
    sim = leaf[0]
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(sim)))
    c = _ro(sim)
    I = c.execute('select pick_intercept from simulation_runs').fetchone()[0]
    score = {(a, x, y): (d, m) for a, x, y, d, m in c.execute(
        'select aisle_id, bayX, bayY, travel_d, height_mult from bin_scores')}
    hv = dict(c.execute('select sku, handle_var from sku_scores'))
    frozen = glob.glob(os.path.join(root, '_frozen', os.path.basename(pair_dir),
                                    'planned_inventory.db'))
    cat = {s: (f, lam) for s, f, lam in _ro(frozen[0]).execute(
        'select sku, relative_frequency, demand_qty_rate from cartons')}
    aisles = {a: ((h, ct, size, unit), bx, by) for a, h, ct, unit, size, bx, by in
              _ro(os.path.join(pair_dir, 'warehouse.db')).execute(
                  'select aisle_id, handling_type, category, unit_type, storage_size, '
                  'bay_x, bay_y from aisle_layout')}
    end = keyframe + horizon
    # picks per bin, in batch order: (batch, qty) -- the emptying clock
    picks = defaultdict(list)
    for b, a, x, y, q in c.execute(
            'select batch_id, aisle_id, bayX, bayY, quantity from picks '
            'where batch_id >= ? order by batch_id, sim_time', (keyframe,)):
        picks[(a, x, y)].append((b, q))
    # placements in (keyframe, end]: in arm order
    places = c.execute(
        'select batch_id, aisle_id, bayX, bayY, sku, qty from bin_placement '
        'where cause = "reorder" and batch_id > ? and batch_id <= ? '
        'order by batch_id, seq', (keyframe, end)).fetchall()
    # history before the keyframe: keys the quantile rule is calibrated on
    hist = c.execute(
        'select aisle_id, sku from bin_placement where cause = "reorder" '
        'and batch_id <= ?', (keyframe,)).fetchall()
    kf = _ro(sim.replace('.db', '.keyframes.db'))
    occ = {(a, x, y): q for a, x, y, q in kf.execute(
        'select aisle_id, bayX, bayY, qty from bin_keyframe where batch_id = ?',
        (keyframe,))}

    def drain_clock(bid, start_batch, qty, after_seq=0):
        """(batch the bin empties, lines, units) for `qty` units put at `start_batch`,
        reading the bin's picks from that batch on.  Beyond the window: end + 1."""
        got, lines = 0, 0
        for b, q in picks.get(bid, ()):
            if b < start_batch:
                continue
            got += q
            lines += 1
            if got >= qty:
                return b, lines, got
        return end + 1, lines, got

    def key_of(sku):
        f, lam = cat.get(sku, (0.0, 1.0))
        eq = _line_mean(lam)
        v = hv.get(sku, 0.0)
        return f, f * (I + eq * (PER_ITEM + v)), v, I + PER_ITEM + v, eq, lam

    tiers = {}
    for a, (key, bx, by) in aisles.items():
        t = tiers.setdefault(key, dict(bins=[], hist=[]))
        for x in range(1, bx + 1):
            for y in range(1, by + 1):
                if (a, x, y) in score:
                    t['bins'].append((a, x, y))
    for a, sku in hist:
        tiers[aisles[a][0]]['hist'].append(key_of(sku)[:2])
    hist_packs = c.execute(
        'select aisle_id, sku, qty from bin_placement where cause = "reorder" '
        'and batch_id <= ?', (keyframe,)).fetchall()
    for key, t in tiers.items():
        t['hist_p'] = []
    for a, sku, qty in hist_packs:
        f, beta, v, labor, eq, lam = key_of(sku)
        tiers[aisles[a][0]]['hist_p'].append(dict(alpha=f, beta=beta, labor=labor, qty=qty,
                                                  eq=eq, lam=lam, v=v))
    for key, t in tiers.items():
        t['arrivals'] = []
        t['init'] = {}
        t['per_batch'] = max(1.0, len(t['hist']) / max(1, keyframe))
    # PER-BIN TIMELINES, attributed in order: each bin's occupants (the keyframe's, then
    # every placement into it, by batch and seq) consume the bin's picks (by batch and sim
    # time) until their quantity is gone.  A pick is never credited to the wrong occupant
    # when a bin empties and refills inside one batch, which a per-placement clock
    # starting at the placement batch would do.
    occupants = defaultdict(list)          # bin -> [(start_batch, qty, arrival or None)]
    for bid, q in occ.items():
        occupants[bid].append((keyframe + 1, q, None))
    arrivals_by_bin = []
    for b, a, x, y, sku, qty in places:
        bid = (a, x, y)
        key = aisles[a][0]
        alpha, beta, v, labor, eq, lam = key_of(sku)
        arr = dict(batch=b, alpha=alpha, beta=beta, labor=labor, qty=qty, eq=eq, lam=lam,
                   v=v, actual=score.get(bid), bid=bid)
        tiers[key]['arrivals'].append(arr)
        occupants[bid].append((b, qty, arr))
    for bid, occs in occupants.items():
        pk = picks.get(bid, [])
        j = 0
        for k, (start, qty, arr) in enumerate(occs):
            # the NEXT occupant's arrival bounds this one: the bin was empty by then, and
            # every pick from then on is the next occupant's
            nxt = occs[k + 1][0] if k + 1 < len(occs) else None
            got, lines = 0, 0
            free_at = nxt if nxt is not None else end + 1
            while j < len(pk) and got < qty:
                bb, q = pk[j]
                if nxt is not None and bb > nxt:
                    break
                j += 1
                if bb < start:
                    continue                 # a pick before this occupant arrived
                got += q
                lines += 1
                if got >= qty:
                    free_at = bb
            if arr is None:
                tiers[aisles[bid[0]][0]]['init'][bid] = free_at
            else:
                arr['life'] = free_at - arr['batch']
                arr['wa'] = lines
                arr['wb'] = lines * I + got * (PER_ITEM + arr['v'])
    return {k: dict(t, score=score) for k, t in tiers.items() if t['arrivals']}, end


def run_policy(tier, end, policy, key='cost', seed=0, scale=None, loads=None):
    """Replay one tier under `policy`; returns (realised cost, wall seconds, packs).

    `qscale:<mode>` is the quantile rule with the index SCALED: a pack at key quantile q
    takes the free bin at rank floor(q * W) among the cheapest, W chosen by <mode>:
      batch    the tier's mean arrivals per batch over its history (emulates a batch
               sort-match online: the batch's hottest pack gets the cheapest free bin,
               its coldest the batch-size-th)
      batchK   K x that
      occ      the tier's occupied count now
      free     the free count (= `quantile`)
    """
    score = tier['score']
    bins = tier['bins']
    D = np.array([score[b][0] for b in bins])
    M = np.array([score[b][1] for b in bins])
    hist = np.array(tier['hist']) if tier['hist'] else np.zeros((0, 2))
    hb = (hist[:, 1].sum() / hist[:, 0].sum()) if len(hist) and hist[:, 0].sum() else 0.0
    cost = D + hb * M
    order = np.argsort(cost, kind='stable')            # rank -> bin index
    rank_of = np.empty(len(bins), dtype=int)
    rank_of[order] = np.arange(len(bins))
    Dr, Mr = D[order], M[order]

    Dm, Mm = D.mean(), M.mean()

    def pk(p):
        """The pack's sort key under `key`:
          cost    f x (Dbar + h Mbar)                 the per-period pick-cost rate
          alpha   f                                   lines per period
          visits  Q / E[q] x (Dbar + h Mbar)          lifetime pick work
          units   f lambda x (Dbar + h Mbar)          units per period
          labor   f lambda x labor_cost               the pools' expected_labor
        """
        h = p['beta'] / p['alpha'] if p['alpha'] > 0 else 0.0
        at = Dm + h * Mm
        if key == 'alpha':
            return p['alpha']
        if key == 'cost':
            return p['alpha'] * at
        if key == 'visits':
            return p['qty'] / p['eq'] * at
        if key == 'units':
            return p['alpha'] * p['lam'] * at
        if key == 'labor':
            return p['alpha'] * p['lam'] * p['labor']
        raise ValueError(key)

    def pack_key(a, b, p=None):
        return pk(p)

    hk = (np.sort([pk(p) for p in tier.get('hist_p', [])])[::-1]
          if tier.get('hist_p') else np.array([]))

    def quantile(k):
        """Share of historical arrivals with a HIGHER key (0 = the hottest pack)."""
        if not len(hk):
            return 0.5
        return float(np.searchsorted(-hk, -k, side='left')) / len(hk)

    if policy == 'recorded':
        # The arm's own bins, priced on the same realised work: the anchor.
        t0 = time.perf_counter()
        tot = sum(p['wa'] * p['actual'][0] + p['wb'] * p['actual'][1]
                  for p in tier['arrivals'] if p['actual'] is not None)
        if loads is not None:
            for p in tier['arrivals']:
                if p['actual'] is not None:
                    loads[p['bid'][0]] = loads.get(p['bid'][0], 0.0) + p['wa']
        return tot, time.perf_counter() - t0, len(tier['arrivals'])
    init = tier['init']
    free = np.ones(len(bins), dtype=bool)
    release = defaultdict(list)                        # batch -> ranks to free
    idx = {b: i for i, b in enumerate(bins)}
    for bid, fb in init.items():
        r = rank_of[idx[bid]]
        free[r] = False
        release[fb].append(r)
    fw = Fenwick(free)
    rng = random.Random(seed)
    per_batch = tier.get('per_batch', 1.0)
    total = 0.0
    arrivals = tier['arrivals']
    t0 = time.perf_counter()
    by_batch = defaultdict(list)
    for p in arrivals:
        by_batch[p['batch']].append(p)
    for b in sorted(set(list(by_batch) + list(release))):
        # bins picked out in this batch or earlier are free for this batch's drain (the
        # release rule the recorded run agrees with: `s04_fidelity.py`, 0 violations)
        for bb in [x for x in list(release) if x <= b]:
            for r in release.pop(bb):
                if not free[r]:
                    free[r] = True
                    fw.set_free(r, True)
        batch = by_batch.get(b, [])
        if not batch:
            continue
        seats = []
        nfree = fw.total()
        if policy == 'batch_tmin':
            # tmin's own keys: packs by f x labor_cost desc onto free bins by D asc
            ordr = sorted(range(len(batch)), key=lambda i: -(batch[i]['alpha'] * batch[i]['labor']))
            freeD = sorted((D[order[r]], r) for r in np.nonzero(free)[0])
            seats = [None] * len(batch)
            for j, i in enumerate(ordr[:len(freeD)]):
                seats[i] = freeD[j][1]
            for r in seats:
                if r is not None:
                    free[r] = False
                    fw.set_free(r, False)
        elif policy.startswith('batch'):
            ks = [pk(p) for p in batch]
            ordr = sorted(range(len(batch)), key=lambda i: -ks[i])
            if policy == 'batch_sort':
                ranks = [fw.kth(j) for j in range(min(len(batch), nfree))]
            else:                                      # batch_qsort
                tg = sorted(min(nfree - 1, int(quantile(ks[i]) * nfree)) for i in ordr)
                ranks, used = [], set()
                for t in tg:
                    r = fw.kth(max(0, min(nfree - 1, t)))
                    while r in used:
                        t += 1
                        r = fw.kth(min(nfree - 1, t))
                        if t >= nfree:
                            break
                    used.add(r)
                    ranks.append(r)
                ranks.sort()
            seats = [None] * len(batch)
            for j, i in enumerate(ordr[:len(ranks)]):
                seats[i] = ranks[j]
            for i, r in enumerate(seats):
                if r is not None:
                    free[r] = False
                    fw.set_free(r, False)
        else:
            for p in batch:
                nfree = fw.total()
                if nfree == 0:
                    seats.append(None)
                    continue
                if policy == 'uniform':
                    r = fw.kth(rng.randrange(nfree))
                elif policy == 'cheapest':
                    r = fw.kth(0)
                elif policy == 'quantile':
                    q = quantile(pk(p))
                    r = fw.kth(min(nfree - 1, int(q * nfree)))
                elif policy.startswith('qscale:'):
                    mode = policy.split(':', 1)[1]
                    q = quantile(pk(p))
                    if mode.startswith('batch'):
                        mult = float(mode[5:] or 1)
                        W = mult * per_batch
                    elif mode == 'occ':
                        W = len(bins) - nfree
                    else:
                        W = nfree
                    r = fw.kth(min(nfree - 1, int(q * W)))
                elif policy == 'ideal':
                    q = quantile(pk(p))
                    slot = min(len(bins) - 1, int(q * len(bins)))
                    below = fw.prefix(slot)
                    cands = []
                    if below < nfree:
                        cands.append(fw.kth(below))           # first free at/after slot
                    if below > 0:
                        cands.append(fw.kth(below - 1))       # last free before slot
                    r = min(cands, key=lambda c: abs(c - slot))
                else:
                    raise ValueError(policy)
                free[r] = False
                fw.set_free(r, False)
                seats.append(r)
        for p, r in zip(batch, seats):
            if r is None:
                continue
            total += p['wa'] * Dr[r] + p['wb'] * Mr[r]
            release[b + max(1, p['life'])].append(r)
            if loads is not None:
                a = bins[order[r]][0]
                loads[a] = loads.get(a, 0.0) + p['wa']
    return total, time.perf_counter() - t0, sum(len(v) for v in by_batch.values())


POLICIES = ('recorded', 'uniform', 'cheapest', 'batch_tmin', 'quantile', 'ideal', 'batch_sort', 'batch_qsort',
            'qscale:batch0.25', 'qscale:batch0.5', 'qscale:batch', 'qscale:batch2')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('cell')
    ap.add_argument('channel')
    ap.add_argument('arm')
    ap.add_argument('--keyframe', type=int, default=25)
    ap.add_argument('--horizon', type=int, default=14)
    ap.add_argument('--key', default='cost')
    a = ap.parse_args()
    tiers, end = load_stream(a.root, a.cell, a.channel, a.arm, a.keyframe, a.horizon)
    tot = {p: 0.0 for p in POLICIES}
    wall = {p: 0.0 for p in POLICIES}
    n = 0
    for key, t in tiers.items():
        for p in POLICIES:
            c, w, k = run_policy(t, end, p, a.key)
            tot[p] += c
            wall[p] += w
        n += k
    u = tot['uniform']
    print(f'{len(tiers)} tiers, {n} packs, key={a.key}')
    print(f'{"policy":12s} {"realised work":>14s} {"vs uniform":>10s} {"us/pack":>8s}')
    for p in POLICIES:
        print(f'{p:12s} {tot[p]:14.1f} {tot[p] / u - 1:+10.3%} {1e6 * wall[p] / n:8.1f}')


if __name__ == '__main__':
    main()
