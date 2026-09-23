"""S09 -- breathing room as a moving cost frontier: the closed form for how a ranked rule's
placements degrade as it consumes the good free bins, against the campaign.

THE RULE (`_TravelBalancedPool.take`, Rank_labor / Rank_cartlabor).  The aisle is chosen by
LPT load balance -- in aggregate the aisles of a class are fed evenly -- and WITHIN the aisle
the bin is the head of the height bracket b minimising

        M_b * h_s + D_b                      h_s = I + per_item + handle_var_s  (labor_cost)

where D_b is the travel cost of the bracket's nearest free bin.  So each bracket is a queue
consumed from its cheap end, and the only state is where each bracket's FRONT has got to.

THE FRONT.  Let N_b(D) be the class's free bins of bracket b with cost <= D at the window's
start, and let freed bins return at rate r_b, landing at costs distributed as the bracket's
OCCUPIED bins (a bin frees when its stock is picked out, so frees come from where stock sits).
A free landing in front of the front is taken at once; one landing behind waits for the front.
With C_b(t) the placements the bracket has taken, the front is where everything that has ever
been available at or below it equals what has been taken:

        N_b(D_b) + R_b(t) * Q_b(D_b) = C_b(t)                                          (1)

(R_b(t) = frees to date, Q_b = the share of the bracket's occupied bins at cost <= D).  The
placement share of bracket b at time t is the share of arriving units for which b wins:

        s_b(t) = Pr_h[ argmin_b' (M_b' h + D_b'(t)) = b ]                              (2)

and C_b' = m s_b, closing the system.  FREES split two ways: a share psi of placed units is
picked out again inside the window (S08's re-picked share) and frees where it was PUT, at the
front, so it is retaken at once; the rest frees in proportion to the occupied bins:

        r_b = r [ (1 - psi) sigma_b  +  psi s_b ]          (sigma_b = bracket share of occupied)

Everything is an input read off the window's start (keyframe) plus m, r (measured flows), psi
(S08), and the placed units' h -- nothing is fitted to the ground share it predicts.

    python .scratch/aisle-churn/assets/s09_front.py <run_root> <cell> <channel> <arm> [<lo> <hi> <psi> <class|aisle|aisle-measured|aisle-waterfill>]
"""
from __future__ import annotations

import bisect
import glob
import os
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

BLOCK = 10
STEPS = 20            # Euler steps per day


def front_model(free, occ, m_day, r_day, hs, brackets, days, psi, steps=STEPS):
    """The frontier ODE (1)-(2) for one class.

    free / occ : {M: sorted list of D}  the free and the occupied bins of each bracket at t = 0
    m_day, r_day : {day: placements}, frees per day
    hs : the placed units' h values (the empirical distribution the argmin is taken over)
    Returns {day: {M: share}}."""
    Ms = sorted(free)
    taken = {M: 0.0 for M in Ms}           # C_b
    freed = {M: 0.0 for M in Ms}           # R_b (frees behind-or-in-front, bulk part only)
    occ_n = {M: len(occ.get(M, ())) for M in Ms}
    occ_tot = sum(occ_n.values()) or 1
    sigma = {M: occ_n[M] / occ_tot for M in Ms}
    s = {M: 1.0 / len(Ms) for M in Ms}
    out = {}

    def front(M):
        """Smallest D with N(D) + R Q(D) >= C + 1 -- the next bin the bracket would give."""
        f, o, C, R = free[M], occ.get(M, []), taken[M], freed[M]
        cand = sorted(set(f[::max(1, len(f) // 400)] + o[::max(1, len(o) // 400)]
                          + ([f[-1]] if f else []) + ([o[-1]] if o else [])))
        lo_, hi_ = 0, len(cand) - 1
        if not cand:
            return float('inf')

        def avail(D):
            n = bisect.bisect_right(f, D)
            q = bisect.bisect_right(o, D) / len(o) if o else 0.0
            return n + R * q
        if avail(cand[-1]) < C + 1:
            return float('inf')            # the bracket is exhausted
        while lo_ < hi_:
            mid = (lo_ + hi_) // 2
            if avail(cand[mid]) >= C + 1:
                hi_ = mid
            else:
                lo_ = mid + 1
        return cand[lo_]

    for day in range(days):
        m, r = m_day.get(day, 0) / steps, r_day / steps
        acc = defaultdict(float)
        for _ in range(steps):
            Dh = {M: front(M) for M in Ms}
            win = defaultdict(int)
            for h in hs:
                win[min(Ms, key=lambda M: M * h + Dh[M])] += 1
            s = {M: win[M] / len(hs) for M in Ms}
            for M in Ms:
                taken[M] += m * s[M]
                # the bulk frees accumulate (1); the psi part lands at the front, retaken at once,
                # so it offsets consumption directly
                freed[M] += r * (1 - psi) * sigma[M]
                taken[M] -= r * psi * s[M]
                acc[M] += s[M] / steps
        out[day] = dict(acc)
    return out


def _M(y, brackets):
    for top, mult in brackets:
        if y < top:
            return mult
    return brackets[-1][1]


def main(root, cell, channel, arm, lo=0, hi=40, psi=0.092, mode='class'):
    from s06_pick import _geometry, _pick_cfg
    from Warehouse.generation.generate_inventory import load_run_inventory
    from Warehouse.kernel.cost_model import sec_per_inch
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    kf = db.replace('.db', '.keyframes.db')
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(db)))
    geo = _geometry(os.path.join(pair_dir, 'warehouse.db'))
    cfg = _pick_cfg(channel)
    br = cfg.height_brackets
    xp, yp = sec_per_inch(cfg.x_speed), sec_per_inch(cfg.y_speed)
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
    K = kcon.execute('select min(batch_id) from bin_keyframe where batch_id >= ?',
                     (lo,)).fetchone()[0]
    occupied, skus_at = set(), defaultdict(set)
    for a, bx, by, sku in kcon.execute(
            'select aisle_id, bayX, bayY, sku from bin_keyframe where batch_id = ?', (K,)):
        occupied.add((a, bx, by))
        skus_at[a].add(sku)
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    placed = con.execute("select batch_id, aisle_id, bayX, bayY, sku from bin_placement "
                         "where cause = 'reorder' and batch_id > ? and batch_id < ?",
                         (K, hi)).fetchall()
    by_cls = defaultdict(list)
    for b, a, bx, by, sku in placed:
        by_cls[geo.by_id[a].key].append((b, geo.by_id[a].y_of(by), sku, a))
    meas = defaultdict(lambda: [0, 0, 0.0])
    pred = defaultdict(lambda: [0.0, 0.0, 0.0])
    days = hi - K - 1
    for key, rows in by_cls.items():
        free, occ = defaultdict(list), defaultdict(list)
        for a in geo.by_class[key]:
            for c in range(1, a.C + 1):
                for r in range(1, a.R + 1):
                    x, y = a.x_of(c), a.y_of(r)
                    M = _M(y, br)
                    (occ if (a.aisle_id, c, r) in occupied else free)[M].append(x * xp + y * yp)
        for v in list(free.values()) + list(occ.values()):
            v.sort()
        for M in occ:
            free.setdefault(M, [])
        m_day = defaultdict(int)
        hs = []
        m_aisle = defaultdict(lambda: defaultdict(int))
        for o in orders.values():
            if not o.labor_cost:
                o.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef,
                                     cfg.pick_volume_coef, cfg.pick_weight_fn,
                                     cfg.pick_volume_fn, pick_per_item=cfg.pick_per_item)
        for b, y, sku, aid in rows:
            m_day[b - K - 1] += 1
            m_aisle[aid][b - K - 1] += 1
            o = orders[sku]
            o.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef,
                                 cfg.pick_weight_fn, cfg.pick_volume_fn,
                                 pick_per_item=cfg.pick_per_item)
            hs.append(o.labor_cost)
            M = _M(y, br)
            mm = meas[(b - lo) // BLOCK]
            mm[0] += 1; mm[1] += (M == 1.0); mm[2] += M
        r_day = len(rows) / days          # flow equilibrium: frees = placements (measured)
        if mode == 'class':
            sh = front_model(free, occ, m_day, r_day, hs[::max(1, len(hs) // 300)], br, days, psi)
        else:
            # PER AISLE: the LPT balance feeds each aisle of the class an equal share whatever
            # its own fronts hold, so each aisle runs (1)-(2) on its own bins at 1/A the rates
            aisles = geo.by_class[key]
            A = len(aisles)
            m_fill = defaultdict(dict)
            if mode == 'aisle-waterfill':
                # the LPT concentration PREDICTED by water-filling (s09_waterfill): each day's
                # placements go to the aisles its added labour raises, in proportion
                from s09_waterfill import water_level
                loads = [sum(orders[x].expected_labor for x in skus_at.get(a.aisle_id, ()))
                         for a in aisles]
                e_day = defaultdict(float)
                for b, _y, sku, _a in rows:
                    e_day[b - K - 1] += orders[sku].expected_labor
                Wc = 0.0
                prev = [0.0] * A
                for d in range(days):
                    Wc += e_day.get(d, 0.0)
                    if Wc <= 0:
                        continue
                    lam, _sh = water_level(loads, Wc)
                    cur = [max(0.0, lam - L) for L in loads]
                    dW = sum(cur) - sum(prev)
                    for a, c_, p_ in zip(aisles, cur, prev):
                        if dW > 0 and c_ > p_:
                            m_fill[a.aisle_id][d] = m_day.get(d, 0) * (c_ - p_) / dW
                    prev = cur
            hs_a = hs[::max(1, len(hs) // 60)]
            sh = defaultdict(lambda: defaultdict(float))
            for a in aisles:
                fa, oa = defaultdict(list), defaultdict(list)
                for c in range(1, a.C + 1):
                    for r in range(1, a.R + 1):
                        x, y = a.x_of(c), a.y_of(r)
                        M = _M(y, br)
                        (oa if (a.aisle_id, c, r) in occupied else fa)[M].append(x * xp + y * yp)
                for v in list(fa.values()) + list(oa.values()):
                    v.sort()
                for M in set(free) | set(occ):
                    fa.setdefault(M, [])
                if mode == 'aisle-waterfill':
                    ma = m_fill[a.aisle_id]
                    one = front_model(fa, oa, ma, r_day * sum(ma.values()) / max(1, len(rows)),
                                      hs_a, br, days, psi, steps=4)
                elif mode == 'aisle-measured':
                    # the LPT's concentration taken as MEASURED: this aisle's own placements
                    ma = m_aisle[a.aisle_id]
                    one = front_model(fa, oa, ma, sum(ma.values()) / days, hs_a, br, days,
                                      psi, steps=4)
                else:
                    one = front_model(fa, oa, {d: n / A for d, n in m_day.items()}, r_day / A,
                                      hs_a, br, days, psi, steps=4)
                src = {'aisle-measured': m_aisle, 'aisle-waterfill': m_fill}.get(mode)
                wa = (lambda d, src=src, aid=a.aisle_id:
                      src[aid].get(d, 0) / max(1e-9, m_day.get(d, 0))) if src else                     (lambda d: 1.0 / A)
                for d, shares in one.items():
                    for M, v in shares.items():
                        sh[d][M] += v * wa(d)
        if os.environ.get('S09_PER_CLASS'):
            n0 = sum(m_day.get(d, 0) for d in range(9))
            g0 = sum(1 for b, y, _s, _a in rows if b - K - 1 < 9 and _M(y, br) == 1.0)
            p0 = sum(m_day.get(d, 0) * sh[d].get(1.0, 0.0) for d in range(9))
            if n0 >= 150:
                print(f'    {"/".join(key):48s} free ground {len(free.get(1.0, [])):6,d} '
                      f'of {sum(map(len, free.values())):7,d}  placed 1-9 {n0:5,d}  '
                      f'ground meas {g0 / n0:6.1%}  model {p0 / n0:6.1%}  h med '
                      f'{sorted(hs)[len(hs) // 2]:.1f}')
        for d, shares in sh.items():
            n = m_day.get(d, 0)
            p = pred[(d + K + 1 - lo) // BLOCK]
            p[0] += n; p[1] += n * shares.get(1.0, 0.0)
            p[2] += n * sum(M * v for M, v in shares.items())
    print(f'{cell}/{channel}/{arm}: {len(placed):,} reorder placements after keyframe {K}, '
          f'{len(by_cls)} classes, psi {psi}, mode {mode}')
    print(f'  {"block":>9}  {"measured ground / M":>20}  {"front model":>16}')
    for blk in sorted(meas):
        m, p = meas[blk], pred[blk]
        print(f'  {lo + blk * BLOCK:3d}-{lo + blk * BLOCK + BLOCK - 1:3d}   {m[1] / m[0]:7.1%}  '
              f'{m[2] / m[0]:.3f}     {p[1] / p[0]:7.1%}  {p[2] / p[0]:.3f}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3], *(int(x) for x in a[4:6]),
         *([float(a[6])] if len(a) > 6 else []), *(a[7:8]))
