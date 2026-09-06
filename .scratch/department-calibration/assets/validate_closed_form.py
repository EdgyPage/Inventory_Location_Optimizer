"""Development-time check of the expected-travel closed form against a reference-run leaf.

Usage:  python validate_closed_form.py <pair_dir> <leaf_db> <channel> <lo> <hi> [n_lines_override]

Reproduces the simulator's charges (fast_pick._simulate_picker_deferred) as an expectation
over the batch-content distribution, given the BUILT geometry (warehouse.db) and the
INITIAL placement (bin_placement batch 0), and compares every component with the realized
window: aisle visits (tasks), bin visits, units, x/y travel seconds, cart swaps, handling.
"""
import json, math, os, sqlite3, sys, time
from collections import defaultdict
import numpy as np
from scipy.special import gammainc

sys.path.insert(0, os.environ['REPO'])
from Warehouse.kernel.cost_model import (handle_var, height_multiplier, per_pick, sec_per_inch,
                                         DEFAULT_HEIGHT_BRACKETS)

pair_dir, leaf_db, channel, lo, hi = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
n_override = float(sys.argv[6]) if len(sys.argv) > 6 else None
ndays = hi - lo + 1
leaf_dir = os.path.dirname(os.path.dirname(leaf_db))
cfg = json.load(open(os.path.join(leaf_dir, 'config.json')))
x_pace, y_pace = sec_per_inch(cfg['x_speed']), sec_per_inch(cfg['y_speed'])
brackets = DEFAULT_HEIGHT_BRACKETS
SIZE_H = {'small': 12, 'medium': 24, 'large': 36, 'extra_large': 48, 'singleton': 48,
          'ff_small': 6, 'ff_medium': 12, 'ff_large': 18}
def x_step_of(unit_type): return 48 if unit_type == 'pallet' else 16

def ro(p): return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
t0 = time.time()
# ── geometry ─────────────────────────────────────────────────────────────────────────
wh = ro(os.path.join(pair_dir, 'warehouse.db'))
aisles = {}
for aid, h, cat, ut, size, C, R in wh.execute('select aisle_id, handling_type, category, unit_type, storage_size, bay_x, bay_y from aisle_layout'):
    aisles[aid] = dict(key=(h, cat, size, ut), C=C, R=R, xs=x_step_of(ut), ys=SIZE_H[size], ut=ut)
def x_phys(a, bx): return (bx - 1) * a['xs'] + a['xs'] // 2
def y_phys(a, by): return (by - 1) * a['ys'] + a['ys'] // 2
# ── catalogue section ──────────────────────────────────────────────────────────────────
inv = ro(os.path.join(pair_dir, 'planned_inventory.db'))
where = "handling='fulfillment'" if channel == 'fulfillment' else "handling!='fulfillment'"
sku_rows = inv.execute(f'select sku, weight, length*width*height, relative_frequency, demand_qty_rate from cartons where {where}').fetchall()
skus = np.array([r[0] for r in sku_rows]); W = np.array([r[1] for r in sku_rows], float)
VOL = np.array([r[2] for r in sku_rows], float); FREQ = np.array([r[3] for r in sku_rows], float)
LAM = np.array([r[4] for r in sku_rows], float)
PI = FREQ / FREQ.sum()
VAR = np.array([handle_var(w, v, cfg['pick_weight_coef'], cfg['pick_volume_coef'], cfg['pick_weight_fn'], cfg['pick_volume_fn']) for w, v in zip(W, VOL)])
EQ = LAM + np.exp(-LAM)                     # E[max(1, Poisson)]
idx_of = {int(s): i for i, s in enumerate(skus)}
print(f'section {channel}: {len(skus):,} SKUs  W={FREQ.sum():,.1f}  E[units/line]={float(PI @ EQ):.3f}  ({time.time()-t0:.0f}s)')
# ── placement (batch 0) ───────────────────────────────────────────────────────────────
db = ro(leaf_db)
place = defaultdict(list)
for sku, aid, bx, by, q in db.execute('select sku, aisle_id, bayX, bayY, qty from bin_placement where batch_id=0'):
    i = idx_of.get(sku)
    if i is not None:
        place[i].append((aid, bx, by, q))
for i in place:   # drain order: forward-pick (singleton) first, then location
    place[i].sort(key=lambda t: (0 if aisles[t[0]]['ut'] == 'singleton' else 1, t[0], t[1], t[2]))
print(f'placement: {sum(len(v) for v in place.values()):,} bins for {len(place):,} SKUs  ({time.time()-t0:.0f}s)')
# ── realized window ───────────────────────────────────────────────────────────────────
win = f'batch_id between {lo} and {hi}'
r_lines = db.execute(f'select count(*) from (select distinct batch_id, sku from picks where {win})').fetchone()[0] / ndays
r_visits, r_units = db.execute(f'select count(*), sum(quantity) from picks where {win}').fetchone()
r_visits /= ndays; r_units /= ndays
r_tasks = db.execute(f"select count(*) from picker_events where event_type='task_start' and {win}").fetchone()[0] / ndays
r_swaps = db.execute(f"select count(*) from picker_events where event_type='cart_swap' and {win}").fetchone()[0] / ndays
px, py, npx, npy = db.execute(f'select sum(pick_travel_x), sum(pick_travel_y), sum(non_pick_travel_x), sum(non_pick_travel_y) from picker_events where {win}').fetchone()
r_tx, r_ty = (px + npx) / ndays, (py + npy) / ndays
r_makespan, r_items = db.execute(f'select sum(task_makespan), sum(total_items) from batch_stats where {win}').fetchone()
r_makespan /= ndays; r_items /= ndays
r_hand = r_makespan - r_tx - r_ty - r_swaps * cfg['cart_swap_coef']
# exact realized handling from the picks themselves (checks the residual attribution)
r_hand_exact = 0.0; r_vol = 0.0; r_vol2 = 0.0
for sku, aid, bx, by, q in db.execute(f'select sku, aisle_id, bayX, bayY, quantity from picks where {win}'):
    i = idx_of[sku]; a = aisles[aid]; y = y_phys(a, by)
    r_hand_exact += per_pick(height_multiplier(brackets, y), cfg['pick_intercept'], VAR[i], q, cfg['pick_per_item'])
    v = VOL[i] * q; r_vol += v; r_vol2 += v * v
r_hand_exact /= ndays
print(f'realized/day: lines={r_lines:,.0f} visits={r_visits:,.0f} units={r_units:,.0f} tasks={r_tasks:,.0f} swaps={r_swaps:,.0f} '
      f'travel x={r_tx:,.0f}s y={r_ty:,.0f}s handling={r_hand:,.0f}s (exact {r_hand_exact:,.0f}s) makespan={r_makespan:,.0f}s  s_pick={r_makespan/r_items:.2f}')
# ── the expectation ───────────────────────────────────────────────────────────────────
n = n_override if n_override else r_lines
def sf(c, lam):   # P(Poisson(lam) > c) for integer c >= 0
    return gammainc(c + 1, lam)
rate = defaultdict(lambda: None)      # aisle -> (C,R) rate matrix
e_units = 0.0; e_visits = 0.0; e_hand = 0.0; e_vol = 0.0; e_vol2 = 0.0
for i in range(len(skus)):
    bins = place.get(i)
    if not bins:
        continue
    lam = LAM[i]; pi = PI[i]; cum = 0
    line_rate = n * pi
    for k, (aid, bx, by, q) in enumerate(bins):
        reach = 1.0 if k == 0 else sf(cum, lam)          # P(q > cum) ; q = max(1,X) so k=0 always
        if reach < 1e-9:
            break
        a = aisles[aid]
        m = rate[aid]
        if m is None:
            m = rate[aid] = np.zeros((a['C'], a['R']))
        m[bx - 1, by - 1] += line_rate * reach
        # units from this bin: E[min(q, cum+q_bin) - min(q, cum)] = sum_{j=cum}^{cum+q-1} P(q > j)
        js = np.arange(cum, cum + q)
        tails = sf(js, lam); tails[js == 0] = 1.0
        eu = tails.sum()
        eu2 = ((2 * (js - cum) + 1) * tails).sum()
        y = y_phys(a, by); M = height_multiplier(brackets, y)
        e_hand += line_rate * reach * M * (cfg['pick_intercept'] + eu * (cfg['pick_per_item'] + VAR[i]))
        e_units += line_rate * eu
        e_visits += line_rate * reach
        e_vol += line_rate * reach * eu * VOL[i]
        e_vol2 += line_rate * reach * eu2 * VOL[i] ** 2
        cum += q
r_walked = db.execute(f"select sum(total_bins) from picker_events where event_type='task_start' and {win}").fetchone()[0] / ndays
print(f'realized walked bins/day={r_walked:,.0f} (picked {r_visits:,.0f})  units/line={r_units/r_lines:.2f}  visits/line={r_visits/r_lines:.2f}')
if os.environ.get('EMP'):
    # empirical mode: per-bin Poisson rates from the window's own picks, scaled up to the
    # bins actually WALKED (an emptied bin is walked to and not recorded as a pick) -- this
    # tests the routing expectation (tasks, x_max, y walk) apart from the demand model.
    scale = r_walked / r_visits
    rate = defaultdict(lambda: None)
    for aid, bx, by, cnt in db.execute(f'select aisle_id, bayX, bayY, count(*) from picks where {win} group by 1,2,3'):
        a = aisles[aid]; m = rate[aid]
        if m is None:
            m = rate[aid] = np.zeros((a['C'], a['R']))
        m[bx - 1, by - 1] += cnt / ndays * scale
    e_visits = sum(float((1 - np.exp(-m)).sum()) for m in rate.values())
    print(f'EMPIRICAL rates: bins with visits={sum(int((m>0).sum()) for m in rate.values()):,}  E[walked]/day={e_visits:,.0f}')
if os.environ.get('SMEAR'):
    # steady-state placement under a uniform (FIFO) restock: every reorder lands on a
    # uniformly random free bin of its class, so a SKU's bins are spread over the CLASS,
    # not the initial map -- smear each class's total visit rate evenly over its bins.
    class_rate = defaultdict(float); class_n = defaultdict(int)
    for aid, a in aisles.items():
        class_n[a['key']] += a['C'] * a['R']
    for aid, m in rate.items():
        class_rate[aisles[aid]['key']] += float(m.sum())
    rate = defaultdict(lambda: None)
    for aid, a in aisles.items():
        k = a['key']
        if class_rate[k] > 0:
            rate[aid] = np.full((a['C'], a['R']), class_rate[k] / class_n[k])
    print(f'SMEARED over {sum(1 for k in class_rate if class_rate[k] > 0)} classes')
print(f'accumulated rates ({time.time()-t0:.0f}s)')
ONE_WAY = bool(os.environ.get('ONEWAY'))
CV = float(os.environ.get('CV', '0'))       # day-to-day CV of the line count (0 = fixed n)
if CV > 0:
    gh_x, gh_w = np.polynomial.hermite_e.hermegauss(7)   # probabilists' Hermite: N(0,1)
    nodes = [(max(0.05, 1.0 + CV * x), w / gh_w.sum()) for x, w in zip(gh_x, gh_w)]
else:
    nodes = [(1.0, 1.0)]
e_tasks = 0.0; e_x = 0.0; e_y = 0.0
for aid, m0 in rate.items():
  for scale_n, wt in nodes:
    m = m0 * scale_n
    a = aisles[aid]; C, R = a['C'], a['R']
    v = 1.0 - np.exp(-m)                       # per-bin visit probability
    u = 1.0 - np.prod(1.0 - v, axis=1)          # per-column
    if u.max() < 1e-12:
        continue
    surv = np.cumprod((1.0 - u)[::-1])[::-1]    # prod_{c'>=c}(1-u)
    tail_above = np.append(surv[1:], 1.0)       # prod_{c'>c}(1-u)
    xs = np.array([x_phys(a, c + 1) for c in range(C)], float)
    e_tasks += wt * (1.0 - surv[0])
    # two-way lane: x travel is x_max (the path is monotone in x from the mouth);
    # one-way lane: the exit walks to the far end, so x travel is the aisle length L.
    e_x += wt * ((1.0 - surv[0]) * C * a['xs'] if ONE_WAY else float((xs * u * tail_above).sum()))
    ys = np.array([0.0] + [y_phys(a, r + 1) for r in range(R)])
    D = np.abs(ys[:, None] - ys[None, :])       # (R+1)x(R+1)
    state = np.zeros(R + 1); state[0] = 1.0
    ey = 0.0
    for c in range(C):
        uc = u[c]
        if uc < 1e-12:
            continue
        vr = v[c]
        q = vr / vr.sum()                        # single-visit row marginal
        # exact E[y_high - y_low | visited]: P(high=j) = v_j prod_{r>j}(1-v_r)/u
        above = np.cumprod((1.0 - vr)[::-1])[::-1]; above = np.append(above[1:], 1.0)
        below = np.cumprod(1.0 - vr); below = np.insert(below[:-1], 0, 1.0)
        e_high = float((ys[1:] * vr * above).sum()) / uc
        e_low = float((ys[1:] * vr * below).sum()) / uc
        entry = float(state @ D[:, 1:] @ q)
        ey += uc * (entry + (e_high - e_low))
        state = (1.0 - uc) * state + uc * np.concatenate(([0.0], q))
    # one-way exit descends from the last pick's height (the end-state distribution)
    e_y += wt * (ey + (float(state @ ys) if ONE_WAY else 0.0))
e_tx, e_ty = e_x * x_pace, e_y * y_pace
Ev = e_vol / e_visits; Ev2 = e_vol2 / e_visits
cap = cfg['cart_capacity']
e_swaps = e_vol / (cap - Ev + Ev2 / (2 * Ev))
e_swaps_naive = e_vol / cap
e_total = e_tx + e_ty + e_swaps * cfg['cart_swap_coef'] + e_hand
print(f'expected/day @ n={n:,.0f} lines: visits={e_visits:,.0f} units={e_units:,.0f} tasks={e_tasks:,.0f} swaps={e_swaps:,.0f} (naive {e_swaps_naive:,.0f}) '
      f'travel x={e_tx:,.0f}s y={e_ty:,.0f}s handling={e_hand:,.0f}s total={e_total:,.0f}s  s_pick={e_total/e_units:.2f}  ({time.time()-t0:.0f}s)')
def pct(e, r): return f'{100*(e/r-1):+.1f}%' if r else 'n/a'
print('ratios expected/realized: '
      f'tasks {pct(e_tasks, r_tasks)}  visits {pct(e_visits, r_visits)}  units {pct(e_units, r_units)}  '
      f'x {pct(e_tx, r_tx)}  y {pct(e_ty, r_ty)}  swaps {pct(e_swaps, r_swaps)}  handling {pct(e_hand, r_hand_exact)}  '
      f'per-visit: x {pct(e_tx/e_visits, r_tx/r_visits)} y {pct(e_ty/e_visits, r_ty/r_visits)} hand {pct(e_hand/e_visits, r_hand_exact/r_visits)}  '
      f'per-task x {pct(e_tx/e_tasks, r_tx/r_tasks)}  visits/task {pct(e_visits/e_tasks, r_visits/r_tasks)}')
# ── put-away: exact pricing of the window's reorder placements + the class-uniform expectation ──
if os.environ.get('PUT'):
    put_xp, put_yp = sec_per_inch(2.0), sec_per_inch(4.0)      # PUT_FOOT_X / PUT_FOOT_Y
    p_int, p_item = cfg['pick_intercept'] * 0.5, cfg['pick_per_item'] * 0.2
    class_bins = defaultdict(list)                              # BinKey -> [(x, y)]
    for aid, a in aisles.items():
        for c in range(a['C']):
            for r in range(a['R']):
                class_bins[a['key']].append((x_phys(a, c + 1), y_phys(a, r + 1)))
    class_mean = {k: (np.mean([x for x, _ in v]) * put_xp + np.mean([y for _, y in v]) * put_yp) for k, v in class_bins.items()}
    pred = 0.0; pred_travel = 0.0; unif_travel = 0.0; n_put = 0; units = 0
    for sku, aid, bx, by, q in db.execute(f"select sku, aisle_id, bayX, bayY, qty from bin_placement where cause='reorder' and {win}"):
        i = idx_of.get(sku)
        if i is None:
            continue
        a = aisles[aid]; x, y = x_phys(a, bx), y_phys(a, by)
        tr = x * put_xp + y * put_yp
        pred_travel += tr; unif_travel += class_mean[a['key']]
        pred += tr + per_pick(height_multiplier(brackets, y), p_int, VAR[i], q, p_item)
        n_put += 1; units += q
    real_s, real_units, real_n = db.execute(f"select sum(duration), sum(qty), count(*) from work_events where role='put' and {win}").fetchone()
    print(f'PUT window: {n_put} placements {units} units; realized {real_n} events {real_units} units {real_s:,.0f}s -> s_put={real_s/real_units:.2f}; '
          f'priced exactly {pred:,.0f}s ({pct(pred, real_s)}); travel/placement realized-destinations {pred_travel/n_put:.1f}s vs class-uniform {unif_travel/n_put:.1f}s ({pct(unif_travel, pred_travel)})')
