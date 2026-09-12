"""Reproduce the v2 duplicate draws on the reference pair's fulfillment section and
instrument the tree, so the mechanism is PROVEN rather than inferred."""
import json
import os
import pickle
import random
import sys

sys.path.insert(0, os.environ['REPO'])   # sibling-asset convention; see validate_closed_form.py
import Optimization.config.sim_config    # loads .env

from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.picking.Workload_Builder import (BatchConfig, _Fenwick,
                                                _get_partner_map)
from Optimization.simdriver.batch_precompute import _load_worker_inventory

# Paths come from the .env key plus the run's OWN run_spec, never a machine-local
# absolute: `pairs` records the exact (name, inventory db, affinity db) the run used.
RUN = os.path.join(os.environ['COMPARISON_OUTPUT_DIR'], 'comparison_20260912_055947')
_SPEC = json.load(open(os.path.join(RUN, 'run_spec.json')))
PAIR_NAME, INV_DB, AFF_DB = _SPEC['pairs'][0]
PAIR = os.path.join(RUN, 'k1_off', PAIR_NAME)


def instrumented_v2(candidates, k, affinity, rng):
    """v2 verbatim + per-draw instrumentation."""
    r = rng
    partner_map = _get_partner_map(affinity)
    n = len(candidates)
    sku_to_idx = {c.sku: i for i, c in enumerate(candidates)}
    base = [c.demand.relative_frequency for c in candidates]
    lift_mult = [1.0] * n
    active = [True] * n
    fw = _Fenwick(base)
    selected = []
    dups = []
    max_w_seen = 0.0

    for step in range(k):
        total = fw.total()
        if total <= 0.0:
            break
        u = r.uniform(0.0, total)
        idx = fw.find(u)
        if not active[idx]:
            true_total = sum(fw.w)
            dups.append(dict(step=step, idx=idx, u=u, tree_total=total,
                             true_total=true_total, w_at_idx=fw.w[idx],
                             u_minus_true=u - true_total,
                             max_w=max_w_seen, n=n))
        chosen = candidates[idx]
        selected.append(chosen)
        active[idx] = False
        fw.set(idx, 0.0)
        for partner_sku, lv in partner_map.get(chosen.sku, []):
            j = sku_to_idx.get(partner_sku)
            if j is not None and active[j]:
                lift_mult[j] *= lv
                nw = base[j] * lift_mult[j]
                if nw > max_w_seen:
                    max_w_seen = nw
                fw.set(j, nw)
    return selected, dups, fw, base, lift_mult, active


def main():
    inv = _load_worker_inventory(INV_DB, None, None, 'fulfillment')
    aff = AffinityStore(AFF_DB)
    n = len(inv.orders)
    print(f"fulfillment section: n={n} skus, distinct skus={len(set(c.sku for c in inv.orders))}")
    freqs = [c.demand.relative_frequency for c in inv.orders]
    print(f"relative_frequency: min={min(freqs):.6e} max={max(freqs):.6e} "
          f"sum={sum(freqs):.6f} zeros={sum(1 for f in freqs if f == 0.0)}")

    cfg = BatchConfig(inventory_size=n, mean_fraction=0.018138,
                      std_fraction=0.018138 * 0.25, sampler='v2')

    for i in range(3):
        r = random.Random(1001337 + i)
        mean = cfg.mean_fraction * cfg.inventory_size
        std = cfg.std_fraction * cfg.inventory_size
        num_skus = max(1, min(cfg.inventory_size, round(r.gauss(mean, std))))
        k = min(num_skus, n)
        sel, dups, fw, base, lift_mult, active = instrumented_v2(inv.orders, k, aff, r)
        skus = [c.sku for c in sel]
        print(f"\nbatch {i}: k={k} drawn={len(sel)} distinct_sku={len(set(skus))} "
              f"dup_draws={len(dups)} ({100.0*len(dups)/max(1,k):.2f}%)")
        if dups:
            d = dups[0]
            print(f"  first dup @step {d['step']}: idx={d['idx']} w_at_idx={d['w_at_idx']!r}")
            print(f"    tree_total={d['tree_total']!r}  true_total(sum w)={d['true_total']!r}")
            print(f"    u={d['u']!r}   u - true_total = {d['u_minus_true']:.6e}")
            print(f"    max weight seen so far = {d['max_w']:.6e}")
            over = sum(1 for x in dups if x['u'] > x['true_total'])
            print(f"  dups where u > true_total: {over}/{len(dups)}")
            import collections
            cnt = collections.Counter(x['idx'] for x in dups)
            print(f"  top repeat indices: {cnt.most_common(6)}")
            print(f"  tree_total - true_total at first dup: "
                  f"{d['tree_total'] - d['true_total']:.6e}")


if __name__ == '__main__':
    main()
