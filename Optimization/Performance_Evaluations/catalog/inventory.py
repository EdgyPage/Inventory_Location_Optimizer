"""catalog.inventory — the inventory model the run PRODUCED, not the one its formula states.

WHY THIS EXISTS.  The published pages carried an equilibrium/reorder model in prose:
`q_eq = round(coverage x dbar)` and `ROP = round(dbar x (lead + safety))`.  Checked against
the run's own frozen catalogue, the stated equilibrium is off by an order of magnitude
(coverage 10 x mean demand ~= 39 against a realised mean of ~3.5) and neither ROP formula
reproduces even 55 % of the stored values.  Nothing was wrong with the simulation; the
prose described the planner's intent and the catalogue records what survived
`sample_to_capacity`, which rescales both to fit the warehouse.  A page cannot state a
formula for that — it has to publish the distribution.

WHAT THIS EMITS.  Realised distributions for the four quantities the pages talk about, the
per-SKU reproduction rate of each candidate formula (so "neither one" is a measurement
rather than an assertion), and the implied rescale factor.  A page renders these instead of
restating a formula, and the next time the planner changes, the page changes with it.
"""
import csv
import json
import math
import os
import sqlite3

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io

#: Candidate closed forms for the stored reorder point, each named where it comes from.
#: They are TESTED, not chosen: the emitted `match_pct` is what a page may quote.
_ROP_FORMULAS = (
    ('ceil_lead_plus_1',
     'ceil(dbar * (lead + 1)), clamped to [1, eq-1] — build_inventory_from_plan',
     lambda d, lead, eq: max(1, min(eq - 1, math.ceil(d * (lead + 1)))) if eq > 1 else 1),
    ('round_lead_plus_safety',
     'round(dbar * (lead + 2)), clamped to [1, eq-1] — build_inventory_with_profile, '
     'safety=REORDER_SAFETY_BATCHES',
     lambda d, lead, eq: max(1, min(eq - 1, round(d * (lead + 2.0)))) if eq > 1 else 1),
)

_FIELDS = ('equilibrium_qty', 'reorder_point', 'lead_time_mean', 'expected_batch_demand')


def _dist(vals: list) -> dict:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return {}
    n = len(vals)

    def q(p):
        return float(vals[min(n - 1, max(0, int(round(p * (n - 1)))))])
    return {'n': n, 'mean': sum(vals) / n, 'median': q(0.5),
            'p5': q(0.05), 'p25': q(0.25), 'p75': q(0.75), 'p95': q(0.95),
            'min': float(vals[0]), 'max': float(vals[-1])}


def pair_model(db_path: str) -> dict:
    """Realised distributions + formula reproduction rates for one frozen catalogue."""
    uri = 'file:' + db_path.replace('\\', '/').replace('#', '%23') + '?mode=ro'
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            'SELECT expected_batch_demand, equilibrium_qty, reorder_point, lead_time_mean '
            'FROM cartons')]
    finally:
        con.close()
    if not rows:
        return {}

    out = {'n_skus': len(rows),
           'distributions': {f: _dist([r.get(f) for r in rows]) for f in _FIELDS}}

    hits = {name: 0 for name, _d, _fn in _ROP_FORMULAS}
    for r in rows:
        d, eq = r['expected_batch_demand'], r['equilibrium_qty']
        lead, rp = r['lead_time_mean'], r['reorder_point']
        for name, _desc, fn in _ROP_FORMULAS:
            try:
                if fn(d, lead, eq) == rp:
                    hits[name] += 1
            except (TypeError, ValueError):
                pass
    out['rop_formulas'] = [
        {'name': name, 'expression': desc,
         'match_pct': 100.0 * hits[name] / len(rows)}
        for name, desc, _fn in _ROP_FORMULAS]

    # The rescale: the planner sizes equilibrium as coverage x demand, then
    # `sample_to_capacity` scales the whole catalogue to fit the warehouse.  The ratio of
    # what was stored to what the formula asks for IS that step, and it is the number that
    # explains why the published formula and the published catalogue disagree.
    planned = [r['expected_batch_demand'] for r in rows if r['expected_batch_demand']]
    stored = [r['equilibrium_qty'] for r in rows if r['expected_batch_demand']]
    out['capacity_rescale'] = {
        'note': ('stored equilibrium / (coverage x expected demand). 1.0 would mean the '
                 'planner had its way; anything else is the warehouse-fit rescale in '
                 'sample_to_capacity@Warehouse/inventory/inventory_planning.py.'),
        'ratio_at_coverage_10': _dist([s / (10.0 * p) for s, p in zip(stored, planned) if p]),
    }
    return out


# Two homes, declared rather than improvised: the JSON is a dossier document a macro
# loads by name, the CSV belongs beside the other tables.  A tuple out_subdir is the
# registry's mechanism for exactly this, and `io.out_dir(ctx, pick=...)` must name a
# declared member — so neither file can drift into a directory nothing declares.
@evaluation(key='catalog.inventory', label='The inventory model this run actually produced',
            scope='run', needs=('catalogue',), out_subdir=('', 'tables'))
def render(ctx, params):
    pairs = ctx.catalogue_dbs()
    if not pairs:
        return
    doc = {'note': ('Measured from the run\'s own frozen catalogue, not from the '
                    'generator\'s parameters or a config average. Where a published '
                    'formula and these numbers disagree, these are what the simulation '
                    'stocked.'),
           'source': 'planned_inventory@run tree (contract alias)',
           'pairs': {}}
    for pair, path in pairs:
        m = pair_model(path)
        if m:
            doc['pairs'][pair] = m
    if not doc['pairs']:
        return

    root = io.out_dir(ctx, pick='')
    with open(os.path.join(root, 'inventory_model.json'), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)

    flat = []
    for pair, m in doc['pairs'].items():
        for field, d in m['distributions'].items():
            flat.append({'pair': pair, 'quantity': field, **d})
    tdir = io.out_dir(ctx, pick='tables')
    with open(os.path.join(tdir, 'inventory_model.csv'), 'w', newline='',
              encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0]))
        w.writeheader()
        w.writerows(flat)
    for pair, m in doc['pairs'].items():
        best = max(m['rop_formulas'], key=lambda f: f['match_pct'])
        ctx.log.info(f"  inventory model {pair}: best ROP formula reproduces "
                     f"{best['match_pct']:.1f}% of stored values")
