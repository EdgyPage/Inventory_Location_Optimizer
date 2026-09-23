"""closed_form.predicted — the closed-form models evaluated at a run's own record, against what
the run did.  A run-scope evaluation: it writes the contract's `closed_form_json` document at
the dossier root and one predicted-vs-realised figure (`closed_form_pngs`); both basenames and
folders come from the declarations, never from text here.

PER CHANNEL SECTION, from the run's staffing record (the coverage calibration: lines per day,
the solved floor, the lead) and its frozen catalogue:

  levels     `models.levels.LEVELS` for every SKU: the share on its line floor, the stock the
             warehouse is sized for (sum Q), and the median cover Q / d in days;
  fresh      the fresh-bin share phi over the run's window (`models.churn`), two ways --
               declared: the unconditional law at the LINE SHARE the declaration prices
                         (what the record alone predicts; S01 says the sampler bends it),
               script:   the count-conditional law on the run's own batch script (exogenous
                         demand, known before any placement);
  realised   the share of units picked from bins a reorder filled inside the window, per arm
             of the reference cell (`run_unload_ranking.inbound_repick`), and the free pool at
             the window's two ends (the flow equilibrium: frees = placements).

Every equation in the JSON's `derivation` is `Model.to_markdown` of the tree that produced the
number beside it.  The study behind it: `.scratch/aisle-churn/` S02, S08, S09.
"""
from __future__ import annotations

import glob
import json
import os
import posixpath
import sqlite3
import statistics
from collections import Counter

from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.core.registry import evaluation

_ARTIFACT = 'closed_form_json'
_FIG_SUBDIR = 'figures/closed_form'


def _head_basename(artifact: str) -> str:
    from Optimization.runschema import contract
    return posixpath.basename(contract.build()['artifacts'][artifact]['path'])


_BASENAME = _head_basename(_ARTIFACT)


def _coverage(spec: dict, pair: str) -> dict:
    cal = ((spec.get('staffing') or {}).get('calibration') or {})
    rec = cal.get(pair) or (next(iter(cal.values())) if cal else {})
    return rec.get('coverage') or {}


def _script(ctx, cell: str, pair: str, skus: set):
    from Optimization.simdriver.batch_precompute import read_batches_blob
    for pkl in ctx.rt.glob('batches_cache', cell=cell, pair=pair):
        blob = read_batches_blob(pkl)
        if blob and blob[1] and next(iter(blob[1][0].items)) in skus:
            return blob[1]
    return None


def _realised(db: str, arm: str, H: int) -> dict:
    from Optimization.persistence.Picking_Data import (find_run, load_bin_placements,
                                                      load_pick_bins)
    from Optimization.run_unload_ranking import inbound_repick
    run_id = find_run(db, arm)
    picks = [p for p in load_pick_bins(db, run_id) if int(p['batch_id']) < H]
    places = [p for p in load_bin_placements(db, run_id)
              if p.get('cause') != 'reorder' or int(p['batch_id']) < H]
    out = {'served_share': inbound_repick(picks, places).get('served_share')}
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    try:
        free = dict(con.execute('select batch_id, sum(free) from free_index where batch_id < ? '
                                'group by batch_id', (H,)))
    except sqlite3.OperationalError:
        free = {}
    finally:
        con.close()
    if free:
        out['free_first'], out['free_last'] = free[min(free)], free[max(free)]
    return out


def section_prediction(orders, cov: dict, channel: str, H: int, script=None) -> dict:
    """The record's own predictions for one section (no simulation output read)."""
    from Optimization.simconfig.models import churn, levels
    lines = float((cov.get('lines_per_day') or {}).get(channel) or 0.0)
    floor = float((((cov.get('floor') or {}).get(channel)) or {}).get('floor_lines') or 1.0)
    lead = cov.get('lead') or {}
    transit = float(lead.get('transit_days') or 0.0)
    unit = float(lead.get('lead_unit_days') or 1.0)
    rows = levels.section(orders, lines_per_day=lines, floor_lines=floor, transit_days=transit,
                          lead_unit_days=unit, coverage_days=float(cov.get('coverage_days') or 10.0),
                          safety_days=float(cov.get('safety_days') or 2.0))
    covers = [r['Q'] / r['d'] for _o, r in rows if r['d'] > 0]
    ell = unit + transit
    W = sum(float(o.demand.relative_frequency) for o in orders) or 1.0
    phi_declared = churn.section_phi([lines * float(o.demand.relative_frequency) / W
                                      for o in orders], float(H), ell)
    out = {
        'n_skus': len(orders), 'lines_per_day': lines, 'floor_lines': floor, 'lead_days': ell,
        'on_floor_share': sum(r['on_floor'] for _o, r in rows) / max(1, len(rows)),
        'sum_Q': sum(r['Q'] for _o, r in rows),
        'median_cover_days': statistics.median(covers) if covers else None,
        'phi_declared': phi_declared,
    }
    if script:
        cnt = Counter(s for b in script[:H] for s in b.items)
        skus = {o.sku for o in orders}
        lines_n = sum(n for s, n in cnt.items() if s in skus)
        served = sum(churn.served_given_count(n, float(H), ell)
                     for s, n in cnt.items() if s in skus)
        out['phi_script'] = served / lines_n if lines_n else None
    return out


@evaluation(key='closed_form.predicted', label='Closed-form predictions vs the run',
            scope='run', needs=('catalogue',), out_subdir=('', _FIG_SUBDIR))
def render(ctx, params):
    from Optimization.Performance_Evaluations.closed_form import render as draw
    from Optimization.simconfig.models import churn
    from Optimization.simconfig.staffing import regime_orders
    from Warehouse.generation.generate_inventory import load_run_inventory
    spec = ctx.run_spec()
    H = int(spec.get('n_batches') or 0)
    cats = dict(ctx.catalogue_dbs())
    if not H or not cats:
        ctx.log.warning('  closed form: no window or no catalogue; skipped')
        return
    runs = list(ctx.rt.channel_runs())
    ref_cell = sorted({c for c, _cr in runs})[0]
    doc = {'run': os.path.basename(os.path.abspath(ctx.run_root)), 'window_batches': H,
           'reference_cell': ref_cell, 'sections': {}}
    rows = []
    for cell, cr in runs:
        if cell != ref_cell or cr.pair not in cats:
            continue
        ch = cr.channel_key
        orders = regime_orders(load_run_inventory(cats[cr.pair]).orders, ch)
        if not orders:
            continue
        sec = doc['sections'].setdefault(ch, {})
        if 'predicted' not in sec:
            script = _script(ctx, cell, cr.pair, {o.sku for o in orders})
            sec['predicted'] = section_prediction(orders, _coverage(spec, cr.pair), ch, H,
                                                  script)
        real = sec.setdefault('realised', {})
        for db in sorted(glob.glob(ctx.rt.leaf_path(cr, 'sim_db', strategy='*'))):
            if db.endswith('.keyframes.db'):
                continue
            arm = os.path.basename(db)[4:-3]
            real[arm] = _realised(db, arm, H)
    for ch, sec in sorted(doc['sections'].items()):
        p = sec['predicted']
        shares = [r['served_share'] for r in sec.get('realised', {}).values()
                  if r.get('served_share') is not None]
        mean = statistics.mean(shares) if shares else None
        for label, key in (('declared line share', 'phi_declared'),
                           ('the run\'s script', 'phi_script')):
            if p.get(key) is not None:
                rows.append({'label': f'{ch}: from {label}', 'predicted': 100.0 * p[key],
                             'realised': None if mean is None else 100.0 * mean,
                             'lo': None if not shares else 100.0 * min(shares),
                             'hi': None if not shares else 100.0 * max(shares)})
    ex = churn.FRESH.evaluate({'lam': 0.01, 'H': float(H), 'ell': 2.766})
    doc['derivation'] = churn.FRESH.to_markdown(ex, title='fresh-bin law (worked at 0.01 '
                                                          'lines a day)')
    doc['note'] = ('phi: share of picking an inbound decision can reach (lines predicted, '
                   'units realised; S08 names the gap).  Realised range is across the '
                   'reference cell\'s arms.')
    with open(os.path.join(io.out_dir(ctx, pick=''), _BASENAME), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)
    if rows:
        figs = io.out_dir(ctx, pick=_FIG_SUBDIR)
        draw.predicted_vs_realised(
            rows, os.path.join(figs, 'fresh_share.png'),
            title='Fresh-bin share: closed form vs the run',
            subtitle=f'{H}-batch window, reference cell {ref_cell}; the bar spans its arms')
    ctx.log.info(f'  wrote the closed-form predictions ({len(doc["sections"])} sections)')
