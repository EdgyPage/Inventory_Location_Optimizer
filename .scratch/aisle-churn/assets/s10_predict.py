"""S10 -- the registered predictions for the (k, c) grid, computed BEFORE any grid run.

P1, the fresh-bin share phi over a 40-day window, per section and demand density k: the
unconditional fresh-bin law (`models.churn.FRESH`) summed over the 40k catalogue's SKUs at the
SAMPLER's per-SKU rates.  The sampler's rate is not the line share (S01: the affinity lift);
it is carried over from the 400k campaign as a per-frequency-decile lift R_dec = realised /
line-share rate, applied to the 40k catalogue's line share at k times the declared lines:

    lambda_s(k) = k * n_40k * pi_s * R_dec(s),      phi(k) = sum served(lambda_s, H, l) / sum lambda_s H

The lead l is the k = 1 record's order-to-shelf lead (1 batch to fire + the transit).  Nothing
here reads a grid run.

    python .scratch/aisle-churn/assets/s10_predict.py <campaign_root_400k> <root_40k>
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

KS = {'store': (1, 3, 10, 30), 'fulfillment': (1, 3, 10)}
H = 40.0
N_DEC = 10


def _section_script(root, pair_glob, skus):
    from Optimization.simdriver.batch_precompute import read_batches_blob
    for pkl in glob.glob(os.path.join(root, pair_glob, '_batches_*.pkl')):
        blob = read_batches_blob(pkl)
        if blob and blob[1] and next(iter(blob[1][0].items)) in skus:
            return blob[1]
    return None


def lift_by_decile(root, section_orders, n_lines, depth=40):
    """R_dec per frequency decile: realised daily rate over the line-share rate, from the
    campaign's own script (first `depth` batches)."""
    skus = {o.sku for o in section_orders}
    script = _section_script(root, os.path.join('k1_off_fifo', '*'), skus)
    cnt = Counter(s for b in script[:depth] for s in b.items)
    tot = sum(o.demand.relative_frequency for o in section_orders)
    ranked = sorted(section_orders, key=lambda o: o.demand.relative_frequency)
    out = []
    per = len(ranked) / N_DEC
    for d in range(N_DEC):
        grp = ranked[int(d * per):int((d + 1) * per)]
        real = sum(cnt.get(o.sku, 0) for o in grp) / depth
        share = sum(n_lines * o.demand.relative_frequency / tot for o in grp)
        out.append((grp[-1].demand.relative_frequency, real / share))
    return out, cnt


def main(root400, root40):
    from Optimization.simconfig.models import churn
    from Optimization.simconfig.staffing import regime_orders
    from Warehouse.generation.generate_inventory import load_run_inventory

    def _spec(root):
        s = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
        return list(s['staffing']['calibration'].values())[0]['coverage']
    c400, c40 = _spec(root400), _spec(root40)
    o400 = load_run_inventory(glob.glob(os.path.join(root400, '_frozen', '*',
                                                     'planned_inventory.db'))[0]).orders
    o40 = load_run_inventory(glob.glob(os.path.join(root40, '_frozen', '*',
                                                    'planned_inventory.db'))[0]).orders
    ell = 1.0 + float(c40['lead']['transit_days'])
    print(f'lead l = {ell:.3f} batches (1 to fire + {c40["lead"]["transit_days"]:.3f} transit)')
    for sec in ('store', 'fulfillment'):
        s400, s40 = regime_orders(o400, sec), regime_orders(o40, sec)
        lift, _ = lift_by_decile(root400, s400, c400['lines_per_day'][sec])
        print(f'\n{sec}: 400k lift by frequency decile (realised / line share): '
              + ' '.join(f'{r:.2f}' for _f, r in lift))
        tot = sum(o.demand.relative_frequency for o in s40)
        n = c40['lines_per_day'][sec]
        base = []
        for o in s40:
            f = o.demand.relative_frequency
            R = next((r for top, r in lift if f <= top), lift[-1][1])
            base.append(n * f / tot * R)
        for k in KS[sec]:
            phi = churn.section_phi([k * lam for lam in base], H, ell)
            print(f'  k = {k:3d}: lines/day {k * sum(base):9,.1f}   P1 phi(lines) = {phi:6.2%}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
