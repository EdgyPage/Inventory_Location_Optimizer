"""run_unload_ranking.py — phase 2's hand-off: rank the UNLOADING policies of a finished
phase-2 run and write the contract's `unload_ranking_json` artifact at the run root.

The twin of `run_restock_selection` (phase 1's hand-off), one level up the funnel:

  * phase 1 ranked restocking RULES on picking labour, in one cell;
  * phase 2 runs phase 1's winning rule pair (plus the `fifo` rider) under every unloading
    policy, one CELL per policy (`whatif_config.phase2_inbound_axis`), and this ranks the
    CELLS;
  * phase 3 crosses the best few of each in one confirmation run.

THE SCORE, decided with the user on 2026-09-18 (memory `inbound-campaign-is-a-three-phase-
funnel`): **total site labour** — picking plus put-away plus receiving, which is exactly the
`ss_prod_total` each leaf's series document already carries (`common/series.py`: unload +
put + pick, meaned over the steady-state window) — summed over a unit's two leaves and
over the inventory pairs, in hours, lower is better.  Picking labour alone cannot rank
unloading policies: across the campaign's first three cells it was identical to 0.01%.
Two cells whose labour differs by less than the NOISE FLOOR are a tie, and the tie is
broken by **yard overage** (`yard_overage_days`, the whole run's trailer-days past the free
threshold, from the SITE yard through `frames._ydf`), lower first.  The floor is declared,
not measured — 0.1%, the size of the cross-cell pick deltas the three-cell analysis found —
and the artifact records it beside every rank so a reader can re-draw the line.

WHAT A UNIT IS HERE.  Phase 2 is COUPLED: one site, two leaves, and the site yard's DB is
named after the ARM PAIR it served (the contract's `site_inbound_db` template).  So the
units are enumerated from the site DBs through the run-tree contract (`rt.site_inbound_dbs`,
`rt.arm_pair_of`, `rt.arm_pair_halves`), never from a directory name, and each unit maps to
a RULE PAIR and a stock mode through the strategy grid (`STRATEGY_BY_KEY`), never by parsing
the key.  A cell's score for a rule pair sums that pair's units (both stock modes), so the
rider's ranking is reported beside the winner's and neither is read as the other.

Refusals, each closing a way to hand phase 3 a plausible answer instead of an error:

  * an UNCOUPLED root (no site DB anywhere) — phase 2 is coupled by definition and the
    tie-break lives in the site yard;
  * an unanalyzed root (no series documents) — says what to run, like phase 1's selector;
  * a single cell — nothing to rank, and a ranking of one would still print a rank 1.

The output is a DECISION RECORD.  Copy the chosen cell set into `whatif_config` by hand for
phase 3 (`PHASE3_UNLOAD`, beside `PHASE2_PAIRS`), for the same reason phase 1's pairs are
copied: a spec that read a JSON file at import would make the decision invisible.

Run AFTER `analyze_run` (which writes the series JSONs):
    python -m Optimization.analyze_run <phase-2 run root>
    python -m Optimization.run_unload_ranking <phase-2 run root> [--pair store_rule,ful_rule]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Optimization.config.strategies import STRATEGY_BY_KEY                    # noqa: E402
from Optimization.run_restock_selection import (                             # noqa: E402
    BASELINE_RULE_PAIR, RULE_PAIR_ORDER, _finite, _read_leaves, _rule_of, _tree_for)

#: The labour term: the series field and the quantity it belongs to.  Recorded on the
#: artifact so a later reader knows which figure to look at as well as which number summed.
METRIC_FIELD = 'ss_prod_total'
METRIC_QUANTITY = 'total_production_time'
#: The tie-break: the whole run's accrued trailer-days past the free threshold.
TIE_QUANTITY = 'yard_overage_days'
#: Two cells closer than this (relative to the cheaper) are a tie on labour.  DECLARED.
DEFAULT_NOISE_FLOOR_PCT = 0.1
#: How many cells the hand-off names for phase 3.
DEFAULT_K = 3


# ── the pure part: ordering cells ─────────────────────────────────────────────────────

def rank_cells(entries: list, noise_floor_pct: float) -> list:
    """Order `entries` (`{'cell', 'labor_hours', 'overage_days'}`) cheapest labour first,
    breaking ties inside the floor by overage.  Returns NEW dicts with `rank`, `tie_group`,
    `decided_by` and `margin_pct` added; entries with a non-finite labour go last with
    `rank: None`.

    A tie is not transitive — A within the floor of B and B of C says nothing about A and C —
    so "tie" is defined operationally: walking the labour order, an entry joins the OPEN
    group while it is within the floor of that group's cheapest member, else it opens a new
    one.  Inside a group the order is overage, then labour, then name; across groups it is
    labour.  `margin_pct` is the labour gap to the NEXT rank as a percent of this one, so a
    reader sees the separations the plan asked for rather than a bare list.
    """
    ok = sorted((dict(e) for e in entries if _finite(e.get('labor_hours'))),
                key=lambda e: (e['labor_hours'], str(e['cell'])))
    bad = sorted((dict(e) for e in entries if not _finite(e.get('labor_hours'))),
                 key=lambda e: str(e['cell']))
    groups: list[list] = []
    for e in ok:
        if groups:
            floor = groups[-1][0]['labor_hours']
            within = (e['labor_hours'] - floor) <= (abs(floor) * noise_floor_pct / 100.0)
            if within:
                groups[-1].append(e)
                continue
        groups.append([e])
    ordered = []
    for gi, g in enumerate(groups, start=1):
        g.sort(key=lambda e: ((e['overage_days'] if _finite(e.get('overage_days'))
                               else math.inf), e['labor_hours'], str(e['cell'])))
        for e in g:
            e['tie_group'] = gi
            e['decided_by'] = 'labour' if len(g) == 1 else 'overage'
            ordered.append(e)
    for i, e in enumerate(ordered, start=1):
        e['rank'] = i
        nxt = ordered[i]['labor_hours'] if i < len(ordered) else None
        e['margin_pct'] = (None if nxt is None or not e['labor_hours']
                           else (nxt - e['labor_hours']) / e['labor_hours'] * 100.0)
    for e in bad:
        e['rank'] = None
        e['tie_group'] = None
        e['decided_by'] = None
        e['margin_pct'] = None
    return ordered + bad


# ── the readers: units, their labour and their overage ────────────────────────────────

def _site_units(rt, cell: str, pair: str, have: dict) -> list:
    """`[(arm_pair, {channel: arm_key}, site_db_path)]` for one (cell, inventory pair).

    From the site DBs through the contract: the DB's stem names the arm pair, and the
    halves resolve POSITIONALLY against the layout's channel order (`arm_pair_halves`).
    `[]` on an uncoupled run — no site DB exists to enumerate.
    """
    out = []
    for db in rt.site_inbound_dbs(cell, pair):
        arm_pair = rt.arm_pair_of(db)
        halves = rt.arm_pair_halves(arm_pair, have)
        if halves:
            out.append((arm_pair, halves, db))
    return out


def _unit_overage(rt, runs_by_channel: dict, halves: dict, site_db: str,
                  threshold_days: float, log) -> float:
    """This unit's `yard_overage_days` from the SITE yard: the trailer rows the site DB
    recorded for the arm pair, closed at the run end (the later of the two leaves' last
    batch, exactly as `requests._arm_end_s` reads it at site scope), folded by `_ydf`
    against the CELL's recorded threshold.  NaN when the site DB holds no run."""
    from Optimization.persistence.Picking_Data import (
        find_run, load_batch_stats, load_yard_trailers)
    from Optimization.Performance_Evaluations.common.frames import _bdf, _ydf
    run_id = find_run(site_db)
    if run_id is None:
        log(f'    !! {os.path.basename(site_db)}: the site DB holds no run')
        return float('nan')
    rows = load_yard_trailers(site_db, run_id)
    if not rows:
        return 0.0
    end_s = 0.0
    for channel, arm in halves.items():
        run = runs_by_channel.get(channel)
        if run is None:
            continue
        leaf_db = rt.leaf_path(run, 'sim_db', strategy=arm)
        leaf_run = find_run(leaf_db, arm) if os.path.exists(leaf_db) else None
        if leaf_run is None:
            continue
        df = _bdf(load_batch_stats(leaf_db, leaf_run))
        if not df.empty:
            end_s = max(end_s, float((df['batch_start_time'] + df['duration']).max()))
    return float(_ydf(rows, end_s, threshold_days)['overage_days'].sum())


def _threshold_for(layout: dict, spec: dict, cell: str, log) -> float:
    """The CELL's free-yard threshold: its layout record first (phase 2 carries the fee on
    the inbound axis), the run spec next, this build's default last and out loud."""
    for rec in layout.get('cells') or []:
        if rec.get('name') == cell:
            v = (rec.get('inbound') or {}).get('fee_threshold_days')
            if v is not None:
                return float(v)
    v = spec.get('inbound_fee_threshold_days')
    if v is not None:
        return float(v)
    from Optimization.config import settings as _settings
    log(f'    !! {cell}: no recorded fee threshold; falling back to this build\'s default '
        f'of {_settings.INBOUND_FEE_THRESHOLD_DAYS:g} d')
    return float(_settings.INBOUND_FEE_THRESHOLD_DAYS)


def _units_of_cell(rt, cell: str, cell_dir: str, threshold_days: float, log) -> list:
    """One row per unit of the cell: its rule pair, stock mode, labour hours and overage."""
    leaves = _read_leaves(rt, cell_dir)
    if not leaves:
        return []
    by_pair: dict = defaultdict(dict)          # inventory pair -> {channel: (run, series)}
    for run, _meta, series in leaves:
        by_pair[run.pair][run.channel] = (run, series)
    rows = []
    for pair, per_channel in sorted(by_pair.items()):
        have = {ch: {s.get('key') for s in ser.get('strategies', [])}
                for ch, (_run, ser) in per_channel.items()}
        secs = {ch: {s.get('key'): s.get(METRIC_FIELD) for s in ser.get('strategies', [])}
                for ch, (_run, ser) in per_channel.items()}
        runs_by_channel = {ch: run for ch, (run, _ser) in per_channel.items()}
        for arm_pair, halves, site_db in _site_units(rt, cell, pair, have):
            readings = [secs.get(ch, {}).get(arm) for ch, arm in halves.items()]
            labor = (sum(readings) / 3600.0) if all(_finite(x) for x in readings) else None
            store_arm = halves.get(RULE_PAIR_ORDER[0])
            rule_pair = [_rule_of(halves.get(ch)) for ch in RULE_PAIR_ORDER]
            strat = STRATEGY_BY_KEY.get(store_arm) if store_arm else None
            rows.append({
                'cell': cell, 'pair': pair, 'arm_pair': arm_pair,
                'arms': {ch: halves.get(ch) for ch in RULE_PAIR_ORDER},
                'rule_pair': rule_pair,
                'stock_mode': getattr(strat, 'initial', None),
                'labor_hours': labor,
                'overage_days': _unit_overage(rt, runs_by_channel, halves, site_db,
                                              threshold_days, log),
            })
    return rows


def _label(rule_pair) -> str:
    return '/'.join(str(r) for r in rule_pair)


# ── the ranking, over a finished run ──────────────────────────────────────────────────

def rank(base_dir: str, *, k: int = DEFAULT_K, noise_floor_pct: float = DEFAULT_NOISE_FLOOR_PCT,
         primary_pair: tuple | None = None, log=print) -> dict:
    """Rank the cells of a finished, analyzed, coupled phase-2 run and write the artifact."""
    rt, cells = _tree_for(base_dir)
    layout = getattr(rt, 'layout', None) or {}
    from Optimization.runschema.sim_manifest import _load_run_spec
    spec = _load_run_spec(os.path.abspath(base_dir)) or {}
    if len(cells) < 2:
        raise SystemExit(f'{base_dir}: {len(cells)} cell(s); an unloading ranking needs at least '
                         f'two cells to rank, and a ranking of one would still print a rank 1')

    log(f'Unload ranking over {base_dir}')
    units: list = []
    analyzed = False
    for cell, cell_dir in cells:
        thr = _threshold_for(layout, spec, cell, log)
        rows = _units_of_cell(rt, cell, cell_dir, thr, log)
        analyzed = analyzed or bool(_read_leaves(rt, cell_dir))
        log(f'  {cell}: {len(rows)} unit(s) at threshold {thr:g} d')
        units.extend(rows)
    if not analyzed:
        raise SystemExit(f'{base_dir}: no analyzed leaf (no series document) under any cell. '
                         f'Run `python -m Optimization.analyze_run {base_dir}` first.')
    if not units:
        raise SystemExit(f'{base_dir}: no site DB under any cell -- an UNCOUPLED run. Phase 2 '
                         f'is coupled by definition (PHASE2_RUN_DEFAULTS) and the tie-break '
                         f'reads the site yard; this root cannot be ranked.')

    # Per (rule pair, cell): labour summed over the pair's units (both stock modes, every
    # inventory pair), overage likewise.  A unit missing a reading poisons its cell for that
    # pair: a partial sum would rank a cell measured on fewer leaves as cheaper.
    by_pair_cell: dict = defaultdict(lambda: {'labor': 0.0, 'overage': 0.0, 'n': 0,
                                              'missing': False})
    for u in units:
        rec = by_pair_cell[(_label(u['rule_pair']), u['cell'])]
        rec['n'] += 1
        if _finite(u['labor_hours']):
            rec['labor'] += u['labor_hours']
        else:
            rec['missing'] = True
        rec['overage'] += u['overage_days'] if _finite(u['overage_days']) else 0.0
    pairs = sorted({lbl for lbl, _c in by_pair_cell})
    rider = _label(BASELINE_RULE_PAIR)
    if primary_pair is not None:
        primary = _label(primary_pair)
        if primary not in pairs:
            raise SystemExit(f'--pair {primary!r} ran in no cell; this run carries {pairs}')
    else:
        candidates = [p for p in pairs if p != rider]
        primary = candidates[0] if candidates else (pairs[0] if pairs else None)
        if len(candidates) > 1:
            log(f'  !! {len(candidates)} non-rider rule pairs ran; ranking on {primary!r} '
                f'(the first by name). Name the winner with --pair to be explicit.')

    rankings, chosen = {}, {}
    for lbl in pairs:
        entries = [{'cell': cell, 'labor_hours': (None if rec['missing'] else rec['labor']),
                    'overage_days': rec['overage'], 'n_units': rec['n']}
                   for (l, cell), rec in by_pair_cell.items() if l == lbl]
        ranked = rank_cells(entries, noise_floor_pct)
        rankings[lbl] = ranked
        chosen[lbl] = [e['cell'] for e in ranked if e['rank'] is not None][:k]
        log(f'\n  rule pair {lbl}:')
        for e in ranked:
            m = '' if e['margin_pct'] is None else f'  +{e["margin_pct"]:.3f}% to next'
            hrs = 'n/a' if e['labor_hours'] is None else f"{e['labor_hours']:.3f}"
            log(f"    {str(e['rank']):>4}  {e['cell']:<20} {hrs:>10} h  "
                f"overage {e['overage_days']:.3f} d  [{e['decided_by']}]{m}")

    doc = {
        'version': 1,
        'written': datetime.now().isoformat(timespec='seconds'),
        'run': {
            'base': os.path.basename(os.path.abspath(base_dir).rstrip(os.sep)),
            'spec': layout.get('spec'), 'schema_id': layout.get('schema_id'),
            'repo_commit': spec.get('repo_commit'), 'n_batches': spec.get('n_batches'),
            'cells': [c for c, _d in cells], 'pairs': layout.get('pairs'),
            'coupled': True,
        },
        'metric': {
            'quantity': METRIC_QUANTITY, 'series_field': METRIC_FIELD,
            'units': 'hours -- the steady-state mean of production_seconds (unload + put + '
                     'pick) per batch, summed over the unit\'s two leaves and the inventory '
                     'pairs, then / 3600; a cell\'s score for a rule pair sums that pair\'s '
                     'units (both stock modes)',
            'direction': 'lower is better',
            'tie_break': {'quantity': TIE_QUANTITY,
                          'units': 'trailer-days past the cell\'s free threshold, whole run, '
                                   'from the SITE yard through frames._ydf',
                          'direction': 'lower is better',
                          'applies_within_pct': noise_floor_pct},
            'noise_floor_pct': noise_floor_pct,
            'noise_floor_note': 'DECLARED, not measured: the size of the cross-cell pick '
                                'deltas the three-cell analysis of 2026-09-18 found. Two '
                                'cells closer than this on labour are a tie.',
        },
        'k': k,
        'rule_pair_order': list(RULE_PAIR_ORDER),
        'rider': rider,
        'primary_pair': primary,
        'rankings': rankings,
        'chosen': chosen,
        'units': units,
        'hand_off': 'copy `chosen[primary_pair]` into whatif_config.PHASE3_UNLOAD by hand; '
                    'a spec that read this file at import would make the decision invisible',
    }
    from Optimization.runschema import analysis_path
    out = analysis_path(rt, 'unload_ranking_json')
    if out is None:
        raise SystemExit('no run-tree contract declares `unload_ranking_json`; run '
                         '`python -m Optimization.runschema.preflight` to adopt the current one')
    tmp = f'{out}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, out)
    log(f'\nWrote {out}')
    log(f'Chosen for phase 3 on {primary!r}: {chosen.get(primary)}')
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir', help='the phase-2 RUN ROOT')
    ap.add_argument('-k', type=int, default=DEFAULT_K, metavar='N',
                    help=f'cells to name for phase 3 (default {DEFAULT_K})')
    ap.add_argument('--noise-floor-pct', type=float, default=DEFAULT_NOISE_FLOOR_PCT,
                    metavar='PCT', help='labour gap below which two cells tie and overage '
                                        f'decides (default {DEFAULT_NOISE_FLOOR_PCT})')
    ap.add_argument('--pair', default=None, metavar='STORE_RULE,FUL_RULE',
                    help='the rule pair to rank on (default: the first non-rider pair the run '
                         'carries)')
    a = ap.parse_args()
    primary = tuple(a.pair.split(',')) if a.pair else None
    sys.stdout.reconfigure(errors='replace')
    rank(a.base_dir, k=a.k, noise_floor_pct=a.noise_floor_pct, primary_pair=primary)


if __name__ == '__main__':
    main()
