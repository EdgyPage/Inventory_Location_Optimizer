"""Development-time check of the stationary-fragmentation closed form against a finished
era run (department-calibration 34).  ONE-TIME: the two finished runs are a correctness
check for the formula, never a pipeline step.

Usage:  python validate_fragmentation.py <run_name> [keyframe_batch]

The run root is `$COMPARISON_OUTPUT_DIR/<run_name>` (the .env key; no path is ever typed
here).  Reads the run's `run_spec.json` coverage record (the declared line count and the
fielded buckets), rebuilds the pair's planned inventory, runs
`simconfig.fragmentation.section_fragmentation` per channel with the transient at every
day of the window, and compares

  * the section's free-index drawdown per day (`batch_stats.free_bins`, whose whole-
    geometry total moves ONLY by the leaf's own section, so `free[0] - free[t]` is the
    section's extra bins after `t` days) with `expected_extra_at[t]`;
  * the keyframe sidecar's per-size-tier bin counts at batch 0 and <keyframe_batch> with
    the per-bucket expectation summed by tier.

Both leaves are read off the `opt_fifo` arm (uni_fifo is byte-identical, memory
`fifo-restock-ignores-initial-placement`).
"""
import glob, json, os, sqlite3, sys, time
from collections import defaultdict

sys.path.insert(0, os.environ.get('REPO', os.getcwd()))
from Optimization.simconfig import fragmentation as fr                 # noqa: E402
from Optimization.simconfig.staffing import regime_orders               # noqa: E402
from Warehouse.generation.generate_inventory import load_inventory_from_db  # noqa: E402
from Warehouse.kernel.regime import FULFILLMENT, STORE                  # noqa: E402

run = sys.argv[1]
kf_batch = int(sys.argv[2]) if len(sys.argv) > 2 else 25
root = os.path.join(os.environ['COMPARISON_OUTPUT_DIR'], run)


def ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro&immutable=1', uri=True)


spec = json.load(open(os.path.join(root, 'run_spec.json')))
cal = spec['staffing']['calibration']
(pair, rec), = cal.items()
cov = rec['coverage']
pair_dir = glob.glob(os.path.join(root, '*', pair))[0]
t0 = time.perf_counter()
inv = load_inventory_from_db(os.path.join(pair_dir, 'planned_inventory.db'))
print(f'loaded {len(inv.orders):,} planned SKUs in {time.perf_counter() - t0:.0f}s')

LEAVES = {'store': (STORE, 'store/store'), 'fulfillment': (FULFILLMENT, 'ful_calibrated/fulfillment')}
for ch, (regime, leaf) in LEAVES.items():
    n = float(cov['lines_per_day'][ch])
    section = regime_orders(inv.orders, regime)
    db = os.path.join(pair_dir, leaf, 'sim_opt_fifo_norsl.db')
    s = ro(db)
    free = [r[0] for r in s.execute('select free_bins from batch_stats order by batch_id')]
    topups = sum(r[0] or 0 for r in s.execute('select put_topups from batch_stats'))
    days = list(range(len(free)))
    t0 = time.perf_counter()
    out = fr.section_fragmentation(section, lines_per_day=n, days=days)
    dt = time.perf_counter() - t0
    print(f'\n== {ch}: {out["n_skus"]:,} SKUs, {out["n_classes"]} classes, n = {n:,.1f} lines/day, '
          f'{dt:.1f}s; put_topups over the run {topups}')
    print(f'   fielded {out["fielded_bins_per_sku"]:.3f} bins/SKU -> stationary '
          f'{out["stationary_bins_per_sku"]:.3f}; expected extra {out["expected_extra"]:,.0f} bins '
          f'(section of {sum(r["fielded"] for r in out["buckets"].values()):,} fielded)')
    # -- the record's buckets vs the chain's fielded counts (a self-check on the tier map)
    rec_b = {(b['handling'], b['category'], b['size'], b['unit']): b for b in cov['final'][ch]['fielded']['buckets']}
    mism = [(k, r['fielded'], rec_b[k]['requirement']) for k, r in out['buckets'].items()
            if k in rec_b and r['fielded'] != rec_b[k]['requirement']]
    missing = [k for k in out['buckets'] if k not in rec_b]
    print(f'   tier map vs record requirement: {len(mism)} mismatching bucket(s), {len(missing)} unknown'
          + (f'  {mism[:5]}' if mism else ''))
    # -- the free-index drawdown per day
    drop = [free[0] - f for f in free]
    exp = out['expected_extra_at']
    print('   day   realized   expected   resid   resid%')
    for t in (1, 2, 5, 10, 15, 20, 25, 30, 35, len(free) - 1):
        if t < len(free):
            r = exp[t] - drop[t]
            print(f'   {t:3d}  {drop[t]:9,d}  {exp[t]:9,.0f}  {r:+7,.0f}  {100 * r / drop[t] if drop[t] else 0:+6.1f}%')
    w0, w1 = 20, len(free) - 1
    if w1 > w0:
        print(f'   window {w0}-{w1}: realized {drop[w1] - drop[w0]:,} expected {exp[w1] - exp[w0]:,.0f} '
              f'({100 * ((exp[w1] - exp[w0]) / (drop[w1] - drop[w0]) - 1):+.1f}%)')
    # -- keyframes per tier
    kf = ro(os.path.join(pair_dir, leaf, 'sim_opt_fifo_norsl.keyframes.db'))
    occ = defaultdict(dict)
    for b, ut, size, cnt in kf.execute('select batch_id, unit_type, storage_size, count(*) from bin_keyframe group by 1,2,3'):
        occ[b][size] = cnt
    if kf_batch in occ:
        by_tier_exp = defaultdict(float); by_tier_f = defaultdict(int)
        for (h, c, size, ut), r in out['buckets'].items():
            by_tier_exp[size] += r['expected_extra_at'][kf_batch]
            by_tier_f[size] += r['fielded']
        print(f'   keyframe batch {kf_batch} per tier: fielded / kf0 / realized delta / expected delta / resid')
        tot_r = tot_e = 0
        for tier in sorted(occ[0]):
            d_real = occ[kf_batch][tier] - occ[0][tier]
            d_exp = by_tier_exp[tier]
            tot_r += d_real; tot_e += d_exp
            print(f'     {tier:12s} {by_tier_f[tier]:9,d} {occ[0][tier]:9,d} {d_real:+8,d} {d_exp:+9,.0f} {d_exp - d_real:+7,.0f}')
        print(f'     {"total":12s} {"":9s} {"":9s} {tot_r:+8,d} {tot_e:+9,.0f} {tot_e - tot_r:+7,.0f}')
    # -- the chain scored at each SKU's REALIZED line count (picks, batches < kf_batch),
    #    which separates the shelf physics from the line-rate law
    if kf_batch in occ:
        bins0 = dict(kf.execute('select sku, count(*) from bin_keyframe where batch_id=0 group by 1'))
        bins1 = dict(kf.execute(f'select sku, count(*) from bin_keyframe where batch_id={kf_batch} group by 1'))
        lines = dict(s.execute(f'select sku, count(distinct batch_id) from picks where batch_id < {kf_batch} group by 1'))
        byj = defaultdict(lambda: [0, 0.0, 0.0])
        for c in section:
            j = lines.get(c.sku, 0)
            if j == 0:
                continue
            chain = out['chains'][fr.class_key(c)]
            a = byj[min(j, 4)]
            a[0] += 1; a[1] += bins1.get(c.sku, 0) - bins0.get(c.sku, 0)
            a[2] += float(chain.units_at_lines(j).sum() - len(chain.f))
        tr = sum(v[1] for v in byj.values()); tp = sum(v[2] for v in byj.values())
        print(f'   scored at the REALIZED line counts by batch {kf_batch}: {sum(v[0] for v in byj.values()):,} SKUs lined, '
              f'realized {tr:,.0f} vs chain {tp:,.0f} ({100 * (tp / tr - 1):+.1f}%)')
        print('     line-batches j: SKUs / realized mean / chain mean')
        for j in sorted(byj):
            n_, r_, p_ = byj[j]
            print(f'       {j}{"+" if j == 4 else " "} {n_:7,d}  {r_ / n_:+.3f}  {p_ / n_:+.3f}')
        # how the section's lines spread over SKUs: realized vs the line share
        import numpy as np
        lam = np.array([n * float(c.demand.relative_frequency) for c in section]) / sum(float(c.demand.relative_frequency) for c in section)
        touched = sum(1 for c in section if lines.get(c.sku, 0) > 0)
        print(f'   SKUs touched by batch {kf_batch}: realized {touched:,} vs the line share Poisson {np.sum(1 - np.exp(-lam * kf_batch)):,.0f}')
    # -- the stationary picture by plan family, for the write-up
    fam = defaultdict(lambda: [0, 0.0, 0.0])
    for key, chain in out['chains'].items():
        members = sum(1 for c in section if fr.class_key(c) == key)
        fam[key[3]][0] += members; fam[key[3]][1] += chain.stationary_extra * members
        fam[key[3]][2] += len(chain.f) * members
    top = sorted(fam.items(), key=lambda kv: -kv[1][1])[:8]
    print('   plans carrying the most stationary extra: plan / SKUs / extra bins / fielded bins')
    for plan, (m, e, f) in top:
        print(f'     {str(plan):32s} {m:7,d} {e:9,.0f} {f:9,.0f}')
