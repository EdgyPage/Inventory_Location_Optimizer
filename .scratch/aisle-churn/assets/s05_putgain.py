"""S05b -- the put-side gain of a ranked rule, in closed form, against the campaign.

A put costs travel from the mouth plus the at-location term M(y)(I + q p + q v)
(`PutawayCost.closed_form`).  A rule that places lower and nearer cuts both, so relative to a
velocity-blind rule (fifo: the free pool's own mix, M_free)

    dPut / Put = s_loc (M_rank / M_free - 1) + s_trav (T_rank / T_free - 1)       (models.churn.PUT_GAIN)

with s_loc, s_trav the at-location and travel shares of the fifo arm's put cost.  M_rank is
the S09 frontier law's placement-weighted multiplier over the window; T_rank / T_free is read
off the placements (the frontier carries D but the put's travel is from the mouth, not the
pick's).  Measured: `work_events` role 'put' seconds per unit.

    python .scratch/aisle-churn/assets/s05_putgain.py <run_root> <cell> <rank_arm> [<M_rank>]
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def _price(root, cell, arm, law, geo, orders, vx, vy):
    db = glob.glob(os.path.join(root, cell, '*', '*', 'store', f'sim_{arm}.db'))[0]
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    trav = loc = mu = units = 0.0
    for a, x, y, sku, q in con.execute("select aisle_id, bayX, bayY, sku, qty from "
                                       "bin_placement where cause = 'reorder' and batch_id < 40"):
        g = geo.by_id[a]
        o = orders[sku]
        r = law.result(x=g.x_of(x), y=g.y_of(y), vx=vx, vy=vy, w=o.weight, vol=o.volume(), q=q)
        trav += r['travel']
        loc += r['at_location']
        mu += r['M'] * q
        units += q
    d, qq = con.execute("select sum(duration), sum(qty) from work_events where role = 'put' "
                        "and batch_id < 40").fetchone()
    return {'travel': trav / units, 'loc': loc / units, 'M': mu / units,
            'measured': d / qq}


def main(root, cell, rank_arm, m_rank=None):
    from s06_pick import _geometry, _pick_cfg
    from Optimization.config import settings as st
    from Optimization.simconfig.models import churn
    from Warehouse.generation.generate_inventory import load_run_inventory
    from Warehouse.operations.putaway import PutawayCost
    geo = _geometry(glob.glob(os.path.join(root, cell, '*', 'warehouse.db'))[0])
    law = PutawayCost.from_pick(_pick_cfg('store')).closed_form
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    f = _price(root, cell, 'uni_fifo_norsl', law, geo, orders, st.PUT_FOOT_X, st.PUT_FOOT_Y)
    r = _price(root, cell, rank_arm, law, geo, orders, st.PUT_FOOT_X, st.PUT_FOOT_Y)
    tot = f['travel'] + f['loc']
    pred = churn.PUT_GAIN_MODEL.evaluate({
        's_loc': f['loc'] / tot, 's_trav': f['travel'] / tot,
        'M_rank': float(m_rank) if m_rank else r['M'], 'M_free': f['M'],
        'T_rank': r['travel'], 'T_free': f['travel']})['put_gain']
    print(f'fifo:  law {tot:.1f} s/unit (travel {f["travel"]:.1f}, at-location {f["loc"]:.1f}; '
          f'M {f["M"]:.3f})  measured {f["measured"]:.1f}')
    print(f'rank:  law {r["travel"] + r["loc"]:.1f} s/unit (travel {r["travel"]:.1f}, '
          f'M {r["M"]:.3f})  measured {r["measured"]:.1f}')
    print(f'put gain: closed form {pred:+.1%} (M_rank {"frontier " + str(m_rank) if m_rank else "placed"})'
          f'   measured {r["measured"] / f["measured"] - 1:+.1%}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(*a[:3], *(a[3:4]))
