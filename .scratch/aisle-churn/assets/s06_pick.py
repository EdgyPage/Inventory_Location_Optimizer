"""S06 -- pick labour per unit: the closed form over a keyframe's placement against the simulator.

For every leaf of a run root and every keyframe batch K in [lo, hi): rebuild the geometry from
the run's `aisle_layout`, read the placement at K from the keyframes sidecar, and price it with
`expected_travel` three ways --

  (a) RECORDED   as the run itself does (`strategy_runner.expected_pick_over`): the bins of a SKU
                 drained forward-pick first, then location; lines weighted by the LINE SHARE;
  (b) DRAIN      the simulator's drain order (ADR-0003: smallest on hand first, then location);
  (c) DRAIN+RATE (b) with each SKU weighted by its REALISED lines in the SAME batches the
                 realised cost is read over (S01: the sampler's affinity lift reshapes the
                 rates; on the store a window's few heavy lines move its cost by tens of
                 percent, so the fair test conditions on which SKUs were asked),

-- and compare s_pick with the realised seconds per unit, task_makespan / total_items, over the
batches from K to the next keyframe (or hi).

    python .scratch/aisle-churn/assets/s06_pick.py <run_root> <lo> <hi> [<script_offset>]

`script_offset` is the batch the pick script starts at (a fill trial's `fill_batches`; 0 on an
ordinary run).
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _geometry(warehouse_db):
    from Optimization.simconfig import expected_travel as et
    con = sqlite3.connect('file:' + warehouse_db + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute('select * from aisle_layout')]
    return et.Geometry.from_layout_rows(rows)


def _pick_cfg(channel):
    from Optimization.config.sim_config import _build_pick_cfg
    from Optimization.simconfig import PICK_CONFIGS
    name = 'store' if channel == 'store' else 'ful_calibrated'
    spec = next(s for s in PICK_CONFIGS if s.name == name)
    return _build_pick_cfg(spec.cfg, num_pickers=1)


def _dist(bin_map, geometry, drain):
    from Optimization.simconfig import expected_travel as et
    if not drain:
        return et.PlacementDist.initial(bin_map, geometry)
    sites = {}
    for sku, bins in bin_map.items():
        rows = sorted(bins, key=lambda b: (b[3], b[0], b[1], b[2]))   # qty, then location
        sites[sku] = [et.Site(geometry.by_id[a].key, int(q), a, int(bx), int(by))
                      for a, bx, by, q in rows]
    return et.PlacementDist('initial', sites)


def main(root, lo, hi, offset=0):
    from Optimization.simconfig import expected_travel as et
    from Optimization.simconfig.staffing import regime_orders
    from Optimization.simdriver.batch_precompute import read_batches_blob
    from Warehouse.generation.generate_inventory import load_run_inventory
    spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
    (_pair, der), = spec['staffing']['derived'].items()
    orders = load_run_inventory(glob.glob(os.path.join(root, '_frozen', '*',
                                                       'planned_inventory.db'))[0]).orders
    print(f'{"leaf":40s} {"arm":26s} {"K":>4}  {"realised":>8}  {"(a) rec":>14}  '
          f'{"(b) drain":>14}  {"(c) +rate":>14}')
    for kf in sorted(glob.glob(os.path.join(root, 'k1_off_*', '*', '*', '*', 'sim_*.keyframes.db'))):
        parts = os.path.normpath(kf).split(os.sep)
        channel, cell_pair = parts[-2], os.path.join(*parts[-5:-3])
        db = kf.replace('.keyframes.db', '.db')
        pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(kf)))
        geo = _geometry(os.path.join(pair_dir, 'warehouse.db'))
        cfg = _pick_cfg(channel)
        sec = regime_orders(orders, channel)
        exp = der['channels'][channel]['expected']
        lines, cv = float(exp['lines']), float(exp.get('cv') or 0.0)
        # the window's realised line rates, from this channel's script
        script = None
        for pkl in glob.glob(os.path.join(pair_dir, '_batches_*.pkl')):
            blob = read_batches_blob(pkl)
            if blob and blob[1] and next(iter(blob[1][0].items)) in {c.sku for c in sec}:
                script = blob[1]
        kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        ks = [k for (k,) in kcon.execute('select distinct batch_id from bin_keyframe '
                                        'where batch_id >= ? and batch_id < ? order by 1',
                                        (lo, hi))]
        for i, K in enumerate(ks):
            end = ks[i + 1] if i + 1 < len(ks) else hi
            bm = defaultdict(list)
            for a, bx, by, sku, q in kcon.execute(
                    'select aisle_id, bayX, bayY, sku, qty from bin_keyframe where batch_id = ?',
                    (K,)):
                bm[sku].append((a, bx, by, q))
            tm, items = con.execute('select sum(task_makespan), sum(total_items) from '
                                    'batch_stats where batch_id >= ? and batch_id < ?',
                                    (K, end)).fetchone()
            real = tm / items
            cnt = Counter(s for b in (script or [])[K - offset:end - offset] for s in b.items)
            rates = {c.sku: cnt.get(c.sku, 0) for c in sec} if script else None
            out = []
            for drain, rate in ((False, False), (True, False), (True, True)):
                sec_w = sec
                if rate and rates:
                    sec_w = [SimpleNamespace(sku=c.sku, weight=c.weight, volume=c.volume,
                                             demand=SimpleNamespace(
                                                 relative_frequency=rates[c.sku] + 1e-9,
                                                 line=c.demand.line))
                             for c in sec]
                r = et.accumulate(sec_w, cfg, _dist(bm, geo, drain), geo)
                out.append(et.expected_pick(r, geo, cfg, lines, cv)['s_pick'])
            arm = os.path.basename(db)[4:-3]
            print(f'{cell_pair[:40]:40s} {arm:26s} {K:4d}  {real:8.2f}  ' + '  '.join(
                f'{v:7.2f} {v / real - 1:+6.1%}' for v in out))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]),
         int(sys.argv[4]) if len(sys.argv) > 4 else 0)
