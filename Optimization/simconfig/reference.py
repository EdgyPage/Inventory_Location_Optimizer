"""reference.py — the REFERENCE RUN's driver: measure a window, accept or discard it, write
the calibration record, iterate to the fixed point.

"Choose the calibration procedure" (.scratch/department-calibration, decisions 1-7) and "Declare
the equilibrium bands" (decision 6, caller one) fix what this module does:

    1. a pass runs under a calibration record (pass 0: the committed SEED);
    2. the equilibrium check (`equilibrium.check`) is the WINDOW PRECONDITION over days
       `day_lo..day_hi` of EVERY channel leaf -- a failing window is DISCARDED, never
       averaged in;
    3. a passing window is measured: `s_pick` per channel (Σ task_makespan ÷ Σ total_items,
       a ratio of sums), `s_put` one site value (Σ put seconds ÷ Σ units put, from the
       `work_events` fold -- there is no duration column; per-leaf values are diagnostics),
       the receiving SELF-CHECK (measured seconds per pack against the exact `s_recv` the
       derivation computed from the script; a mismatch beyond `recv_tol` FAILS THE RUN,
       because it means the per-pack charge that ran is not the one the derivation priced
       with), and `K_max` per channel (the window minimum of floor(Σ task seconds ÷
       heaviest task seconds) per day -- "bounds, it does not set");
    4. the candidate record is written with full provenance (run root, commit, warehouse and
       batch fingerprints, cost-model parameters, era flags, the pass list with every pass's
       values) and `provenance: measured` on each constant, the analytic prediction beside it
       and their ratio as the TRAVEL SHARE;
    5. the next pass runs under the candidate; the fixed point stops when derived daily
       demand moves less than `tol` (5%) between consecutive passes, at most `max_passes`.

**What a DISCARDED window contributes.**  Its readings enter the record's pass list (so a
reader can see why it failed) and NOTHING else is `measured` from it.  But the derivation is
deterministic: re-running the same seed against the same catalogue reproduces the same
capped days, so "the next pass runs" would be the same pass.  The session's reading is that
a failed window re-SEEDS the next pass: its seconds-per-unit ratios are written as the next
record's constants with provenance `seed` (an analytic starting guess, not a measurement),
so pass n+1 derives its crews from a better guess and can pass where pass n could not.  A
record leaves this loop with `measured` constants only when a window PASSED.

PURE over what it is handed: `measure_pass` reads a finished run tree through the run-tree
resolver and the persistence loaders; `fixed_point` takes `launch` and `measure` callables
so the loop is testable without a simulation (`Tests/unit/test_reference_run.py`).  The CLI
that launches real passes is `Optimization/run_reference.py`.
"""
from __future__ import annotations

import copy
import json
import math
import os
from datetime import date

from Optimization.simconfig import calibration as _cal
from Optimization.simconfig import equilibrium as _eq
from Optimization.simconfig.constants import PROVENANCE

#: Two consecutive passes whose derived daily demand moved less than this (relative, per
#: channel) have reached the fixed point.  Decision 1: 5%.
DEMAND_TOL: float = 0.05

#: The receiving self-check's tolerance: the seconds the dock charged for every pack in
#: the window against the same packs re-priced from their SKU and quantity with the run's
#: own unload cost, as a relative error of the sums.  A FLOAT tolerance (decision 2),
#: because the comparison is exact per row: an unload has no travel term.  It is NOT a
#: comparison of averages -- the first smoke run put the site-average `s_recv` (dominated
#: by heavy store packs) 7x off a window in which only the fulfillment leaf received, and
#: even within a channel the actual lot mix moves a per-pack average by a few percent.
RECV_TOL: float = 1e-6

#: At most this many passes (decision 1: "at most two").
MAX_PASSES: int = 2

_CHANNELS: tuple[str, ...] = ('store', 'fulfillment')
_PICK_KEY = {'store': 's_pick_store', 'fulfillment': 's_pick_ff'}

#: The run-spec keys that price the other two crews off picking (`crew_cost_spec`) and
#: the dock's own unload coefficients -- the cost-model parameters the record cites.
CREW_COST_KEYS: tuple[str, ...] = ('put_intercept_scale', 'put_item_ratio',
                                   'recv_intercept_scale')
UNLOAD_KEYS: tuple[str, ...] = ('inbound_unload_intercept', 'inbound_unload_weight_coef',
                                'inbound_unload_volume_coef')


class ReferenceRunError(RuntimeError):
    """The run cannot be a reference run: the receiving self-check failed, or the tree
    carries no era record to measure against."""


# ── one leaf's window ───────────────────────────────────────────────────────────────────

def _floor_div(a: float, b: float) -> int | None:
    return int(math.floor(a / b)) if b > 0 else None


def measure_leaf(db_path: str, run_id: int, *, day_lo: int, day_hi: int) -> dict:
    """The constants one channel leaf's window yields, as RATIOS OF SUMS.

        {'batches': n, 's_pick': {'seconds', 'units', 'value'},
         'k_max': int | None,           # window minimum of the per-day heaviest-aisle floor
         'k_max_by_day': {day: floor},
         'put':  {'seconds', 'units', 'value'},      # value = s per unit put, or None
         'recv': {'seconds', 'packs', 'units', 'value'}}   # value = s per PACK, or None

    `total_items` is the batch's picked count and `task_makespan` its Σ task time, both in
    `batch_stats` (decision 2); on a DRAINED window nothing was cut, so picked and planned
    agree.  A capped window still yields a ratio (the re-seed reads it), but it is a
    seconds-per-unit under truncation and is never stamped `measured`.
    """
    from Optimization.persistence.Picking_Data import (
        load_batch_stats, load_task_stats, load_work_hours)
    days = set(range(int(day_lo), int(day_hi) + 1))
    batches = [b for b in load_batch_stats(db_path, run_id) if int(b.work_day) in days]
    in_window = {int(b.batch_id) for b in batches}
    day_of = {int(b.batch_id): int(b.work_day) for b in batches}
    pick_s = sum(float(b.task_makespan) for b in batches)
    pick_units = sum(float(b.total_items) for b in batches)
    # K_max per day: floor(total task seconds ÷ heaviest task seconds), window minimum.
    per_day_total: dict = {}
    per_day_max: dict = {}
    for t in load_task_stats(db_path, run_id):
        d = day_of.get(int(t.batch_id))
        if d is None:
            continue
        per_day_total[d] = per_day_total.get(d, 0.0) + float(t.duration)
        per_day_max[d] = max(per_day_max.get(d, 0.0), float(t.duration))
    k_by_day = {d: _floor_div(per_day_total[d], per_day_max[d]) for d in sorted(per_day_total)}
    k_vals = [k for k in k_by_day.values() if k is not None]
    put = {'seconds': 0.0, 'units': 0.0}
    recv = {'seconds': 0.0, 'packs': 0.0, 'units': 0.0}
    for r in load_work_hours(db_path, run_id):
        if int(r['batch_id']) not in in_window:
            continue
        if r['role'] == 'put':
            put['seconds'] += float(r['seconds'] or 0.0)
            put['units'] += float(r.get('units') or 0.0)
        elif r['role'] == 'receive':
            recv['seconds'] += float(r['seconds'] or 0.0)
            recv['packs'] += float(r['n_rows'] or 0)
            recv['units'] += float(r.get('units') or 0.0)
    put['value'] = (put['seconds'] / put['units']) if put['units'] > 0 else None
    recv['value'] = (recv['seconds'] / recv['packs']) if recv['packs'] > 0 else None
    return {
        'batches': len(batches),
        's_pick': {'seconds': pick_s, 'units': pick_units,
                   'value': (pick_s / pick_units) if pick_units > 0 else None},
        'k_max': min(k_vals) if k_vals else None,
        'k_max_by_day': k_by_day,
        'put': put, 'recv': recv,
    }


# ── one pass: every leaf of a finished run ──────────────────────────────────────────────

def measure_pass(run_root: str, *, day_lo: int, day_hi: int,
                 recv_tol: float = RECV_TOL, log=None) -> dict:
    """Judge and measure the window on EVERY channel leaf of a finished reference run.

    Walks the tree through the run-tree resolver (never a joined path), reads the staffing
    record off `run_spec.json`, builds each leaf's expectations, runs the check, measures.
    Returns

        {'run_root', 'day_lo', 'day_hi', 'passed', 'reasons': [...],
         'leaves': {'<pair>/<config>/<channel>': {'pair', 'channel', 'db', 'verdict',
                                                   'measure', 'expectations'}},
         'pairs': [pair, ...],
         's_pick': {channel: {'value', 'seconds', 'units', 'analytic', 'travel_share'}},
         's_put': {'value', 'seconds', 'units', 'analytic', 'travel_share',
                   'per_leaf': {leaf: value}},
         'recv_check': {'exact', 'realized', 'rel_error', 'tol', 'passed', 'packs'},
         'k_max': {channel: int | None},
         'daily_demand': {channel: float},        # what this pass RAN under
         'staffing': <the run's staffing record>}

    `passed` is the WINDOW verdict (every leaf's check passed).  The receiving self-check is
    judged separately and RAISES `ReferenceRunError` when it fails: that is a broken run,
    not a window to discard.
    """
    from Optimization import runschema
    from Optimization.persistence.Picking_Data import find_run
    rt = runschema.resolver_for(run_root)
    with open(rt.run_spec_json(), encoding='utf-8') as fh:
        spec = json.load(fh)
    staffing = spec.get('staffing') or {}
    if not staffing.get('derived'):
        raise ReferenceRunError(f'{run_root}: run_spec.json carries no derived staffing '
                                f'block; this is not a calibrated-era run')
    from Optimization.persistence.Picking_Data import load_batch_stats, load_receive_events
    from Warehouse.generation.generate_inventory import load_inventory_from_db
    leaves: dict = {}
    pairs: list = []
    catalogues: dict = {}
    for cell, cr, db in rt.sim_dbs():
        channel = cr.channel or 'store'
        run_id = find_run(db)
        if run_id is None:
            raise ReferenceRunError(f'{db}: no run recorded')
        exp = _eq.expectations_for(staffing, pair=cr.pair, channel=channel)
        verdict = _eq.check(db, run_id, day_lo, day_hi, expectations=exp)
        measure = measure_leaf(db, run_id, day_lo=day_lo, day_hi=day_hi)
        # The exact receiving self-check, per leaf: the packs this leaf's dock unloaded,
        # re-priced from the pair's PLANNED catalogue (what the workers stocked) with the
        # channel's own unload price.
        if cr.pair not in catalogues:
            inv = load_inventory_from_db(rt.planned_inventory_db(cell, cr.pair))
            catalogues[cr.pair] = {int(o.sku): o for o in inv.orders}
        pricing_name = staffing['derived'][cr.pair]['channels'][channel]['pricing_config']
        days = set(range(int(day_lo), int(day_hi) + 1))
        in_window = {int(b.batch_id) for b in load_batch_stats(db, run_id)
                     if int(b.work_day) in days}
        recv = recv_exact_check(load_receive_events(db, run_id), catalogues[cr.pair],
                                unload_price_for(pricing_name, spec), in_window=in_window,
                                tol=recv_tol)
        label = f'{cr.pair}/{cr.config}/{channel}'
        leaves[label] = {'pair': cr.pair, 'channel': channel, 'db': db, 'run_id': run_id,
                         'verdict': verdict, 'measure': measure, 'expectations': exp,
                         'recv_check': recv}
        if cr.pair not in pairs:
            pairs.append(cr.pair)
        if log is not None:
            log.info(f'  [reference] {label}: {_eq.summarize(verdict)}')
            log.info(f'  [reference] {label}: receiving self-check '
                     f'{"ok" if recv["passed"] else "FAIL"} -- {recv["packs"]} packs, charged '
                     f'{recv["measured_s"]:,.1f} s vs exact {recv["expected_s"]:,.1f} s'
                     + (f', {recv["unknown_skus"]} unpriceable' if recv['unknown_skus'] else ''))
    if not leaves:
        raise ReferenceRunError(f'{run_root}: no sim DB under the run root')
    passed = all(lf['verdict'].passed for lf in leaves.values())
    reasons = [f'{label}: {r}' for label, lf in leaves.items() for r in lf['verdict'].reasons]

    # s_pick per channel and K_max: ratios of sums across the channel's leaves.
    s_pick: dict = {}
    k_max: dict = {}
    daily: dict = {}
    for ch in _CHANNELS:
        mine = [lf for lf in leaves.values() if lf['channel'] == ch]
        if not mine:
            continue
        sec = sum(lf['measure']['s_pick']['seconds'] for lf in mine)
        units = sum(lf['measure']['s_pick']['units'] for lf in mine)
        # The analytic prediction beside the measured value: the script's own seconds per
        # unit at ground with no travel, off the derived block of the leaf's pair.
        analytic = [staffing['derived'][lf['pair']]['channels'][ch]['script']['analytic_s_pick']
                    for lf in mine]
        an = sum(analytic) / len(analytic)
        val = (sec / units) if units > 0 else None
        s_pick[ch] = {'value': val, 'seconds': sec, 'units': units, 'analytic': an,
                      'travel_share': (val / an) if (val is not None and an > 0) else None}
        ks = [lf['measure']['k_max'] for lf in mine if lf['measure']['k_max'] is not None]
        k_max[ch] = min(ks) if ks else None
        daily[ch] = sum(staffing['derived'][lf['pair']]['channels'][ch]['daily_demand_units']
                        for lf in mine) / len(mine)

    # s_put: ONE site value; the per-leaf values are diagnostics.
    put_s = sum(lf['measure']['put']['seconds'] for lf in leaves.values())
    put_u = sum(lf['measure']['put']['units'] for lf in leaves.values())
    put_analytic = _site_analytic_put(staffing, pairs)
    put_val = (put_s / put_u) if put_u > 0 else None
    s_put = {'value': put_val, 'seconds': put_s, 'units': put_u, 'analytic': put_analytic,
             'travel_share': ((put_val / put_analytic)
                              if (put_val is not None and put_analytic > 0) else None),
             'per_leaf': {label: lf['measure']['put']['value'] for label, lf in leaves.items()}}

    # The receiving self-check, site-wide: the sums of every leaf's exact check, with the
    # per-leaf verdicts beside them and the script's average `s_recv` as a DIAGNOSTIC only.
    measured_s = sum(lf['recv_check']['measured_s'] for lf in leaves.values())
    expected_s = sum(lf['recv_check']['expected_s'] for lf in leaves.values())
    recv_p = sum(lf['recv_check']['packs'] for lf in leaves.values())
    failing = [label for label, lf in leaves.items() if not lf['recv_check']['passed']]
    recv_check = {'measured_s': measured_s, 'expected_s': expected_s, 'packs': recv_p,
                  'rel_error': ((abs(measured_s - expected_s) / expected_s)
                                if expected_s > 0 else None),
                  'tol': float(recv_tol), 'passed': not failing,
                  'per_leaf': {label: lf['recv_check'] for label, lf in leaves.items()},
                  'script_s_recv': {p: staffing['derived'][p]['receiving']['s_recv']['value']
                                    for p in pairs},
                  'measured_s_per_pack': (measured_s / recv_p) if recv_p else None}
    if failing:
        parts = '; '.join(f'{label}: charged {leaves[label]["recv_check"]["measured_s"]:,.1f} s '
                          f'vs exact {leaves[label]["recv_check"]["expected_s"]:,.1f} s over '
                          f'{leaves[label]["recv_check"]["packs"]} packs'
                          f'{" (" + str(leaves[label]["recv_check"]["unknown_skus"]) + " unpriceable)" if leaves[label]["recv_check"]["unknown_skus"] else ""}'
                          for label in failing)
        raise ReferenceRunError(
            f'{run_root}: receiving self-check FAILED (tolerance {recv_tol:.0e}) -- {parts}; '
            f'the per-pack charge that ran is not the one the derivation priced with, so '
            f'this run cannot be a reference run')
    return {'run_root': os.path.abspath(run_root), 'day_lo': int(day_lo), 'day_hi': int(day_hi),
            'passed': passed, 'reasons': reasons, 'leaves': leaves, 'pairs': pairs,
            's_pick': s_pick, 's_put': s_put, 'recv_check': recv_check, 'k_max': k_max,
            'daily_demand': daily, 'staffing': staffing, 'spec': spec}


def unload_price_for(pricing_name: str, spec: dict):
    """The `UnloadCost` a channel's dock charged, rebuilt from the run spec the way the
    runner builds it: the channel's pick config by reference -> `PutawayCost.from_pick`
    scaled by the two put scales -> `UnloadCost.from_putaway` scaled by the receive scale
    -> the inbound unload overlay (`inbound_unload_*`) where set.  A spec key recorded as
    None (a pre-field run) leaves the class-default scale in place, as the runner does."""
    from dataclasses import replace
    from Inbound.unload import UnloadCost
    from Warehouse.operations.putaway import PutawayCost
    from Optimization.config.sim_config import _build_pick_cfg
    from Optimization.simconfig import PICK_CONFIG_BY_KEY
    pick_cfg = _build_pick_cfg(PICK_CONFIG_BY_KEY[pricing_name].cfg, num_pickers=1)
    put_kw = {}
    if spec.get('put_intercept_scale') is not None:
        put_kw['intercept_scale'] = float(spec['put_intercept_scale'])
    if spec.get('put_item_ratio') is not None:
        put_kw['item_ratio'] = float(spec['put_item_ratio'])
    put = PutawayCost.from_pick(pick_cfg, **put_kw)
    recv_kw = {}
    if spec.get('recv_intercept_scale') is not None:
        recv_kw['intercept_scale'] = float(spec['recv_intercept_scale'])
    cost = UnloadCost.from_putaway(put, **recv_kw)
    overlay = {}
    for key, field_name in (('inbound_unload_intercept', 'intercept'),
                            ('inbound_unload_weight_coef', 'weight_coef'),
                            ('inbound_unload_volume_coef', 'volume_coef')):
        if spec.get(key) is not None:
            overlay[field_name] = float(spec[key])
    return replace(cost, **overlay) if overlay else cost


def recv_exact_check(rows, orders_by_sku: dict, cost, *, in_window, tol: float = RECV_TOL) -> dict:
    """Re-price every received pack in the window and compare with what the dock charged.

    `rows` are `load_receive_events` rows, `orders_by_sku` the pair's catalogue, `cost`
    the channel's `UnloadCost` (`unload_price_for`), `in_window` the batch ids the window
    holds.  Returns `{packs, units, measured_s, expected_s, rel_error, tol, unknown_skus,
    passed}`.  A pack whose SKU the catalogue does not hold is COUNTED and priced at
    nothing, which makes the check fail rather than pass a row it could not price.
    """
    from Inbound.unload import unload_cost
    measured = expected = 0.0
    packs = units = unknown = 0
    for r in rows:
        if int(r['batch_id']) not in in_window:
            continue
        packs += 1
        units += int(r['qty'] or 0)
        measured += float(r['duration'] or 0.0)
        o = orders_by_sku.get(int(r['sku']))
        if o is None:
            unknown += 1
            continue
        expected += unload_cost(o.weight, o.volume(), int(r['qty']), cost)
    rel = (abs(measured - expected) / expected) if expected > 0 else (0.0 if packs == 0 else None)
    return {'packs': packs, 'units': units, 'measured_s': measured, 'expected_s': expected,
            'rel_error': rel, 'tol': float(tol), 'unknown_skus': unknown,
            'passed': unknown == 0 and rel is not None and rel <= tol}


def _site_analytic_put(staffing: dict, pairs: list) -> float:
    """The script's analytic put seconds per unit, summed over the pairs' channels."""
    s = u = 0.0
    for p in pairs:
        for ch, rec in (staffing['derived'][p].get('channels') or {}).items():
            if rec is None:
                continue
            s += float(rec['script'].get('put_s') or 0.0)
            u += float(rec['script'].get('put_units') or 0.0)
    return (s / u) if u > 0 else 0.0


# ── the record ─────────────────────────────────────────────────────────────────────────

def _constant_entry(m: dict, *, provenance: str, note: str) -> dict:
    """A record `constants` entry from a measured block.  A travel share below 1.0 cannot
    be honest (the analytic carries no travel) and the loader refuses it, so it is dropped
    with the reason kept in the note; the recorded `value` prices the run regardless."""
    if provenance not in PROVENANCE:
        raise ValueError(f'provenance {provenance!r} not in {PROVENANCE}')
    share = m.get('travel_share')
    entry = {'value': m['value'], 'travel_share': share, 'provenance': provenance,
             'analytic': m.get('analytic'), 'note': note}
    if share is not None and share < 1.0:
        entry['travel_share'] = None
        entry['note'] = (f'{note} travel_share {share:.4f} < 1.0 was NOT recorded: the '
                         f'analytic prediction carries no travel, so a measured value below '
                         f'it says the window truncated tasks (a cut) or the pricing moved; '
                         f'read the pass entry.')
    return entry


def _pass_entry(result: dict, *, pass_no: int, accepted: bool) -> dict:
    """What the record's `passes` list keeps of one pass: everything a reader needs to tell
    converged from cut off, and why a window was discarded."""
    return {
        'pass': int(pass_no),
        'run_root': os.path.basename(result['run_root']),
        'window': [result['day_lo'], result['day_hi']],
        'accepted': bool(accepted),
        'reasons': list(result['reasons']),
        's_pick': {ch: {k: v for k, v in m.items()} for ch, m in result['s_pick'].items()},
        's_put': {k: v for k, v in result['s_put'].items()},
        'recv_check': dict(result['recv_check']),
        'k_max': dict(result['k_max']),
        'daily_demand': dict(result['daily_demand']),
        'verdicts': {label: lf['verdict'].as_dict() for label, lf in result['leaves'].items()},
    }


def candidate_record(prev: dict, result: dict, *, pass_no: int) -> dict:
    """The next calibration record, from the previous one and a measured pass.

    A PASSING window replaces the constants with `measured` values (travel share = measured
    ÷ analytic beside each), records `k_max`, and stamps the run's provenance.  A FAILING
    window keeps the record's provenance fields as they were and RE-SEEDS the constants from
    its ratios (provenance `seed`, see the module docstring) so the next pass derives from a
    better guess; `k_max` is left alone (a capped day's floor is not a bound).  Either way
    the pass is appended to `passes`.
    """
    rec = copy.deepcopy(prev)
    rec.pop('_path', None)
    staffing = result['staffing']
    spec = result.get('spec') or {}
    accepted = bool(result['passed'])
    prov = 'measured' if accepted else 'seed'
    consts = rec.setdefault('constants', {})
    for ch in _CHANNELS:
        m = result['s_pick'].get(ch)
        if m is None or m['value'] is None:
            continue
        note = (f'pass {pass_no}: Σ task_makespan ÷ Σ total_items over days '
                f'{result["day_lo"]}-{result["day_hi"]} of the {ch} leaf'
                + ('' if accepted else ' -- window DISCARDED (see passes), re-seed only'))
        consts.setdefault('s_pick', {})[ch] = _constant_entry(m, provenance=prov, note=note)
    if result['s_put']['value'] is not None:
        note = (f'pass {pass_no}: Σ put seconds ÷ Σ units put from work_events, one site '
                f'value; per-leaf values in the pass entry'
                + ('' if accepted else ' -- window DISCARDED, re-seed only'))
        consts['s_put'] = _constant_entry(result['s_put'], provenance=prov, note=note)
    if accepted:
        consts['k_max'] = {ch: (int(v) if v is not None else None)
                           for ch, v in result['k_max'].items()}
        for ch in _CHANNELS:
            consts['k_max'].setdefault(ch, None)
        pair0 = result['pairs'][0]
        cal0 = (staffing.get('calibration') or {}).get(pair0) or {}
        rec['warehouse_fingerprint'] = cal0.get('run_fingerprint')
        rec['batch_fingerprint'] = _batch_fingerprints(result['run_root'])
        rec['run_root'] = os.path.basename(result['run_root'])
        rec['commit'] = spec.get('repo_commit')
        rec['repo_dirty'] = spec.get('repo_dirty')
        rec['cost_model'] = {
            'crew_cost': {k: spec.get(k) for k in CREW_COST_KEYS},
            'unload': {k: spec.get(k) for k in UNLOAD_KEYS},
            'staffing_inputs': staffing.get('inputs'),
            'pricing_config': {ch: (staffing['derived'][pair0]['channels'].get(ch) or {})
                               .get('pricing_config') for ch in _CHANNELS},
        }
        rec['era'] = {
            'day_seconds': staffing['derived'][pair0]['day_seconds'],
            'releases_per_day': spec.get('releases_per_day'),
            'shift_drain_or_cap': spec.get('shift_drain_or_cap'),
            'roll_over_unpicked': spec.get('roll_over_unpicked'),
            'cut_at_day_end': spec.get('cut_at_day_end'),
        }
    rec['pass'] = int(pass_no)
    rec['created'] = date.today().isoformat()
    rec.setdefault('passes', []).append(_pass_entry(result, pass_no=pass_no, accepted=accepted))
    rec['notes'] = ('Measured by the reference run: see `passes` for every pass\'s window '
                    'verdict and values; `converged` says whether the fixed point closed.'
                    if accepted else
                    'RE-SEEDED from a discarded window: no constant here is measured. See '
                    '`passes` for why the window failed.')
    _cal.validate_record(rec, source=f'candidate pass {pass_no}')
    return rec


def _batch_fingerprints(run_root: str) -> dict | None:
    """{pair: [fingerprint, ...]} off the batch caches the run precomputed, through the
    resolver's `batches_cache` glob; None when the contract does not know the artifact."""
    from Optimization import runschema
    try:
        rt = runschema.resolver_for(run_root)
        out: dict = {}
        for cell, cr in rt.channel_runs():
            if cr.pair in out:
                continue
            paths = rt.glob('batches_cache', cell=cell, pair=cr.pair)
            fps = sorted(os.path.basename(p)[len('_batches_'):-len('.pkl')] for p in paths)
            out[cr.pair] = fps
        return out or None
    except Exception:                                    # noqa: BLE001 - provenance only
        return None


def converged(prev_daily: dict, new_daily: dict, *, tol: float = DEMAND_TOL) -> tuple:
    """(converged, {channel: relative move}) between two passes' derived daily demand."""
    moves: dict = {}
    for ch, new in new_daily.items():
        old = prev_daily.get(ch)
        if old is None or old <= 0:
            moves[ch] = None
            continue
        moves[ch] = abs(float(new) - float(old)) / float(old)
    known = [m for m in moves.values() if m is not None]
    return (bool(known) and all(m < tol for m in known)), moves


def write_record(rec: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=2)
        fh.write('\n')
    return os.path.abspath(path)


# ── the fixed point ─────────────────────────────────────────────────────────────────────

def fixed_point(*, launch, measure, seed_record: dict, out_dir: str,
                max_passes: int = MAX_PASSES, tol: float = DEMAND_TOL, log=None) -> dict:
    """Iterate passes until derived daily demand stops moving, or the pass budget is spent.

    `launch(record_path, pass_no) -> run_root` runs one pass under the record at that path;
    `measure(run_root) -> result` is `measure_pass` bound to the window.  Both are injected
    so the loop is testable with stubs.  Each pass's candidate record is written to
    `<out_dir>/calibration_record.pass<n>.json` BEFORE the pass that runs under it, and the
    final record to `<out_dir>/calibration_record.candidate.json`.

    Returns `{'record', 'record_path', 'passes': [result, ...], 'converged', 'measured'}`:
    `measured` says whether the final record's constants came from a PASSING window;
    `converged` whether the last two passes agreed within `tol`.  A record that is neither
    is a re-seed and must not be committed as the calibration record.
    """
    record = copy.deepcopy(seed_record)
    record.pop('_path', None)
    results: list = []
    prev_daily: dict | None = None
    conv = False
    measured = False
    for n in range(int(max_passes)):
        rec_path = write_record(record, os.path.join(out_dir, f'calibration_record.pass{n}.json'))
        if log is not None:
            log.info(f'[reference] pass {n}: launching under {rec_path}')
        run_root = launch(rec_path, n)
        result = measure(run_root)
        results.append(result)
        if log is not None:
            log.info(f'[reference] pass {n}: window '
                     f'{"ACCEPTED" if result["passed"] else "DISCARDED"}'
                     + ('' if result['passed'] else ' -- ' + '; '.join(result['reasons'][:4])))
        record = candidate_record(record, result, pass_no=n)
        measured = bool(result['passed'])
        if prev_daily is not None:
            conv, moves = converged(prev_daily, result['daily_demand'], tol=tol)
            if log is not None:
                log.info(f'[reference] pass {n}: daily demand moved '
                         + ', '.join(f'{ch} {m:.1%}' if m is not None else f'{ch} n/a'
                                     for ch, m in moves.items())
                         + f' -> {"converged" if conv else "not converged"}')
        prev_daily = result['daily_demand']
        if conv and measured:
            break
    record['converged'] = bool(conv and measured)
    record['cut_off'] = not record['converged']
    path = write_record(record, os.path.join(out_dir, 'calibration_record.candidate.json'))
    return {'record': record, 'record_path': path, 'passes': results,
            'converged': record['converged'], 'measured': measured}
