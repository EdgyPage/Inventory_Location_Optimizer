"""S05 -- receiving and put-away cost per pack and per unit: the closed form against the simulator.

The staffing derivation prices the script's implied lots with the simulator's own packer and the
cost laws the classes carry (`UnloadCost.closed_form`, `PutawayCost.closed_form`), put-away at the
expected travel of a CLASS-UNIFORM destination (`expected_travel.put_site_pricer`, i.e. the fifo
placement rule's steady state).  Its per-pack and per-unit costs are in the run's staffing
record.  This reads the realised costs off `work_events` per leaf, over a window, and compares --
by placement rule, because the put cost depends on WHERE the rule puts a pack and the record
prices only the uniform case.

    python .scratch/aisle-churn/assets/s05_inbound_costs.py <run_root> <lo> <hi>
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys


def main(root: str, lo: int, hi: int) -> None:
    spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
    (_pair, der), = spec['staffing']['derived'].items()
    pred = {}
    for ch, rec in der['channels'].items():
        if not rec:
            continue
        s = rec['script']
        pred[ch] = {'recv_per_pack': s['recv_s'] / s['packs'],
                    'recv_per_unit': s['recv_s'] / s['put_units'],
                    'put_per_unit': s['put_s'] / s['put_units'],
                    'units_per_pack': s['put_units'] / s['packs']}
    print(f'{"leaf":44s} {"arm":28s}  {"recv s/pack":>18}  {"u/pack":>13}  {"put s/unit":>20}')
    for db in sorted(glob.glob(os.path.join(root, 'k1_off_*', '*', '*', '*', 'sim_*.db'))):
        if db.endswith('keyframes.db'):
            continue
        parts = os.path.normpath(db).split(os.sep)
        ch = parts[-2]
        p = pred.get(ch)
        if p is None:
            continue
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        row = {}
        for role, n, u, sec in con.execute(
                'select role, count(*), sum(qty), sum(duration) from work_events where '
                'batch_id >= ? and batch_id < ? and role in ("put", "receive") group by role',
                (lo, hi)):
            row[role] = (n, u, sec)
        rn, ru, rs = row.get('receive', (0, 0, 0.0))
        pn, pu, ps = row.get('put', (0, 0, 0.0))
        if not rn or not pu:
            continue
        rpp, upp, ppu = rs / rn, ru / rn, ps / pu
        arm = os.path.basename(db)[4:-3]
        print(f'{parts[-5] + "/" + ch:44s} {arm:28s}  {rpp:7.2f} vs {p["recv_per_pack"]:7.2f} '
              f'{rpp / p["recv_per_pack"] - 1:+6.1%}  {upp:5.2f} vs {p["units_per_pack"]:5.2f}  '
              f'{ppu:7.2f} vs {p["put_per_unit"]:7.2f} {ppu / p["put_per_unit"] - 1:+6.1%}')


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
