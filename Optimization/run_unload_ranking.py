"""run_unload_ranking.py — phase 2's hand-off: rank the UNLOADING policies of a finished
phase-2 run and write the contract's `unload_ranking_json` artifact at the run root.

The twin of `run_restock_selection` (phase 1's hand-off), one level up the funnel:

  * phase 1 ranked restocking RULES on picking labour, in one cell;
  * phase 2 runs phase 1's winning rule pair (plus the `fifo` rider) under every unloading
    policy, one CELL per policy (`whatif_config.phase2_inbound_axis`), and this ranks the
    CELLS;
  * phase 3 crosses the best few of each in one confirmation run.

THE SCORE — and the correction of 2026-09-19 that is the reason this module changed.

It ranked on **total site labour** (`ss_prod_total`: unload + put + pick, meaned over the
steady-state window).  That was wrong, and wrong in a way that produced an answer rather
than an error: every term in it is a FLOW, and over a window long enough to unload
everything a flow total is INVARIANT to the ORDER the unloading happened in.  Same
trailers, same packs, same seconds.  The first phase-2 ranking came back an exact tie
across ten cells and that was the honest reply to a question the metric could not be asked.

The score now, set with the user on 2026-09-19: **the pick work the PLANNED demand owes the
placement** — `ss_pick_owed`, the steady-state mean of `batch_stats.pick_owed_s`, summed
over a unit's two leaves and the inventory pairs, in seconds, lower is better.  Dock labour
is the same whichever order the trailers come apart in; where the stock LANDS is not, and
that is what the whole warehouse pays for afterwards.  It is a STATE read, so it can see an
ordering policy: what the run's own planned batches would cost served from where the stock
currently stands, priced with the same `cost_model.per_pick` arithmetic as `optimal_work`.

Two cells whose score differs by less than the NOISE FLOOR are a tie, and the tie is broken
by **yard overage** (`yard_overage_days`, the whole run's trailer-days past the free
threshold, from the SITE yard through `frames._ydf`), lower first.  The floor is declared,
not measured — 0.1% — and the artifact records it beside every rank so a reader can
re-draw the line.

THE CENSUS, and the refusal it can trigger.  A planned line whose SKU has nothing on any
shelf costs the score NOTHING, because there is no bin to walk to — so a score read alone
would rank "leave it all in the yard" first.  `ss_unservable` is the census of that demand.
When it is materially non-zero the run was decided by AVAILABILITY rather than by
placement, and this tool writes the record, names no cells for phase 3, and exits non-zero.
A fabricated penalty would make the score rankable again at the price of making it partly
fiction, with no way to recover which force moved it.

TWO VERDICTS the document carries beside the ranks, because a rank list cannot state
either: `discriminating` is false when every cell lands in ONE tie group (the metric did
not separate the policies, which is a result and not a ranking), and `rank_agreement`
reports whether the rule pairs that ran ordered the cells the same way — they are
independent replications of the same question, so disagreement is the honest signal that
neither ordering is a property of the unloading policy.

WHAT A UNIT IS HERE.  Phase 2 is COUPLED: one site, two leaves, and the site yard's DB is
named after the ARM PAIR it served (the contract's `site_inbound_db` template).  So the
units are enumerated from the site DBs through the run-tree contract (`rt.site_inbound_dbs`,
`rt.arm_pair_of`, `rt.arm_pair_halves`), never from a directory name, and each unit maps to
a RULE PAIR and a stock mode through the strategy grid (`STRATEGY_BY_KEY`), never by parsing
the key.  A cell's score for a rule pair sums that pair's units (both stock modes), so the
rider's ranking is reported beside the winner's and neither is read as the other.

Refusals, each closing a way to hand phase 3 a plausible answer instead of an error:

  * a MATERIAL census — the ranking would be about availability, not placement;
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

#: The score: the series field and the quantity it belongs to.  Recorded on the artifact so
#: a later reader knows which figure to look at as well as which number summed.
METRIC_FIELD = 'ss_pick_owed'
METRIC_QUANTITY = 'pick_owed_s'
#: The closed form's second opinion, meaned over the keyframe batches of the window.  A
#: DIFFERENT MODEL of the same placement (cart routing against relative frequency, where
#: the score walks each SKU's own bins against the planned script), so it is never
#: differenced against the score -- it is ranked, and the two ORDERS are compared.
#:
#: ASYMMETRIC, and the asymmetry is measured (`Tests/unit/
#: test_pick_owed_agrees_with_the_closed_form.py`): the closed form walks every aisle once
#: a day, so its travel term is ~0.4% of its total and it cannot see AISLE CHOICE at all.
#: Its placement signal is the height bracket, which the score also carries.  Agreement is
#: therefore weak evidence; DISAGREEMENT is the informative direction and means the score
#: moved on something a second model prices the other way.  Reported, never enforced.
EXACT_FIELD = 'ss_pick_owed_exact'
#: The honesty term, read beside the score and never added to it.  Planned demand with no
#: shelf stock, which the score prices at zero.
CENSUS_FIELD = 'ss_unservable'
CENSUS_QUANTITY = 'unservable_weight'
#: Above this share of a cell's own score, the census stops being a footnote and the tool
#: refuses to name cells.  DECLARED, like the noise floor beside it.
DEFAULT_CENSUS_MATERIAL_PCT = 1.0
#: The tie-break: the whole run's accrued trailer-days past the free threshold.
TIE_QUANTITY = 'yard_overage_days'
#: Two cells closer than this (relative to the cheaper) are a tie on labour.  DECLARED.
DEFAULT_NOISE_FLOOR_PCT = 0.1
#: How many cells the hand-off names for phase 3.
DEFAULT_K = 3


# ── the pure part: ordering cells ─────────────────────────────────────────────────────

def rank_cells(entries: list, noise_floor_pct: float) -> list:
    """Order `entries` (`{'cell', 'owed_seconds', 'overage_days'}`) cheapest score first,
    breaking ties inside the floor by overage.  Returns NEW dicts with `rank`, `tie_group`,
    `decided_by` and `margin_pct` added; entries with a non-finite score go last with
    `rank: None`.

    A tie is not transitive — A within the floor of B and B of C says nothing about A and C —
    so "tie" is defined operationally: walking the score order, an entry joins the OPEN
    group while it is within the floor of that group's cheapest member, else it opens a new
    one.  Inside a group the order is overage, then score, then name; across groups it is
    score.  `margin_pct` is the score gap to the NEXT rank as a percent of this one, so a
    reader sees the separations rather than a bare list.

    `owed_seconds` was `owed_seconds` until 2026-09-19, when the score stopped being a flow
    total.  Renamed rather than reused: a decision record whose key says hours of labour
    while the number is seconds owed by a placement is the kind of thing that gets quoted.
    """
    ok = sorted((dict(e) for e in entries if _finite(e.get('owed_seconds'))),
                key=lambda e: (e['owed_seconds'], str(e['cell'])))
    bad = sorted((dict(e) for e in entries if not _finite(e.get('owed_seconds'))),
                 key=lambda e: str(e['cell']))
    groups: list[list] = []
    for e in ok:
        if groups:
            floor = groups[-1][0]['owed_seconds']
            within = (e['owed_seconds'] - floor) <= (abs(floor) * noise_floor_pct / 100.0)
            if within:
                groups[-1].append(e)
                continue
        groups.append([e])
    ordered = []
    for gi, g in enumerate(groups, start=1):
        g.sort(key=lambda e: ((e['overage_days'] if _finite(e.get('overage_days'))
                               else math.inf), e['owed_seconds'], str(e['cell'])))
        for e in g:
            e['tie_group'] = gi
            e['decided_by'] = 'score' if len(g) == 1 else 'overage'
            ordered.append(e)
    for i, e in enumerate(ordered, start=1):
        e['rank'] = i
        nxt = ordered[i]['owed_seconds'] if i < len(ordered) else None
        e['margin_pct'] = (None if nxt is None or not e['owed_seconds']
                           else (nxt - e['owed_seconds']) / e['owed_seconds'] * 100.0)
    for e in bad:
        e['rank'] = None
        e['tie_group'] = None
        e['decided_by'] = None
        e['margin_pct'] = None
    return ordered + bad


def discriminating(ranked: list) -> bool:
    """Did the SCORE separate the cells at all, or did the tie-break decide everything?

    False when every ranked cell sits in one tie group.  That is not a failure of the run
    and not a bug — it is the result “these policies do not differ on this metric, at this
    scale, over this window” — but a rank list cannot say it, and a reader handed
    `chosen[0]` has no way to tell it from a real separation.  The 2026-09-18 ranking that
    prompted the whole metric change would have reported False here.
    """
    groups = {e['tie_group'] for e in ranked if e['rank'] is not None}
    return len(groups) > 1


def rank_agreement(rankings: dict) -> dict:
    """Do the rule pairs that ran order the cells the same way?

    Each rule pair is an INDEPENDENT replication of the same question — same cells, same
    geometry, same planned demand, a different restocking rule underneath.  If the unloading
    policy is what the score is measuring, the orders agree; if they disagree, the order is
    a property of the rule pair and not of the policy, and neither ranking should be handed
    to phase 3 as though it were.

    Returns `{'pairs': [...], 'orders': {pair: [cells]}, 'agree': bool, 'common': [cells],
    'note': str}`.  Compared over the cells EVERY pair ranked, because a pair that poisoned
    a cell has no opinion about it and must not be read as disagreeing.  Fewer than two
    pairs with an opinion answers `agree: None` — unknown, not true.
    """
    orders = {lbl: [e['cell'] for e in r if e['rank'] is not None]
              for lbl, r in rankings.items()}
    have = {lbl: o for lbl, o in orders.items() if o}
    if len(have) < 2:
        return {'pairs': sorted(orders), 'orders': orders, 'agree': None, 'common': [],
                'note': 'fewer than two rule pairs produced a ranking, so agreement is '
                        'unknown rather than true'}
    common = set.intersection(*[set(o) for o in have.values()])
    restricted = {lbl: [c for c in o if c in common] for lbl, o in have.items()}
    first = next(iter(restricted.values()))
    agree = all(o == first for o in restricted.values())
    return {
        'pairs': sorted(orders), 'orders': orders, 'agree': agree,
        'common': sorted(common),
        'note': ('every rule pair ordered the shared cells identically, so the order is a '
                 'property of the unloading policy'
                 if agree else
                 'the rule pairs DISAGREE on the order of the shared cells. Each is an '
                 'independent replication of the same question, so the order is a property '
                 'of the rule pair rather than of the unloading policy; phase 3 should not '
                 'be handed either one as the answer'),
    }


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
    """One row per unit of the cell: its rule pair, stock mode, score, census and overage.

    The score and the census are read from the SAME series document in the same pass, and
    both are summed over the unit's two leaves.  They must travel together: a score is only
    a placement result to the extent its census is zero.
    """
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
        cens = {ch: {s.get('key'): s.get(CENSUS_FIELD) for s in ser.get('strategies', [])}
                for ch, (_run, ser) in per_channel.items()}
        exct = {ch: {s.get('key'): s.get(EXACT_FIELD) for s in ser.get('strategies', [])}
                for ch, (_run, ser) in per_channel.items()}
        runs_by_channel = {ch: run for ch, (run, _ser) in per_channel.items()}
        for arm_pair, halves, site_db in _site_units(rt, cell, pair, have):
            readings = [secs.get(ch, {}).get(arm) for ch, arm in halves.items()]
            owed = sum(readings) if all(_finite(x) for x in readings) else None
            # A leaf that recorded no census reads 0.0 rather than poisoning the unit: the
            # column is NULL on a vintage that predates the score, and such a run has no
            # score to poison either -- `owed` is already None there.
            unserv = sum(x for x in (cens.get(ch, {}).get(arm) for ch, arm in halves.items())
                         if _finite(x))
            # SUMMED over the unit's two leaves like the score, and None unless BOTH
            # answered: this is a cross-check, so a half-measured unit is no check at all
            # and must not quietly become a cheaper one.
            _ex = [exct.get(ch, {}).get(arm) for ch, arm in halves.items()]
            exact = sum(_ex) if _ex and all(_finite(x) for x in _ex) else None
            store_arm = halves.get(RULE_PAIR_ORDER[0])
            rule_pair = [_rule_of(halves.get(ch)) for ch in RULE_PAIR_ORDER]
            strat = STRATEGY_BY_KEY.get(store_arm) if store_arm else None
            rows.append({
                'cell': cell, 'pair': pair, 'arm_pair': arm_pair,
                'arms': {ch: halves.get(ch) for ch in RULE_PAIR_ORDER},
                'rule_pair': rule_pair,
                'stock_mode': getattr(strat, 'initial', None),
                'owed_seconds': owed,
                'unservable': unserv,
                'exact_s_per_unit': exact,
                'overage_days': _unit_overage(rt, runs_by_channel, halves, site_db,
                                              threshold_days, log),
            })
    return rows


def _label(rule_pair) -> str:
    return '/'.join(str(r) for r in rule_pair)


# ── the ranking, over a finished run ──────────────────────────────────────────────────

def rank(base_dir: str, *, k: int = DEFAULT_K, noise_floor_pct: float = DEFAULT_NOISE_FLOOR_PCT,
         census_material_pct: float = DEFAULT_CENSUS_MATERIAL_PCT,
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

    # Per (rule pair, cell): the score summed over the pair's units (both stock modes,
    # every inventory pair), census and overage likewise.  A unit missing a reading poisons
    # its cell for that pair: a partial sum would rank a cell measured on fewer leaves as
    # cheaper, which is the one arithmetic error that always favours the broken cell.
    by_pair_cell: dict = defaultdict(lambda: {'score': 0.0, 'census': 0.0, 'overage': 0.0,
                                              'exact': 0.0, 'exact_missing': False,
                                              'n': 0, 'missing': False})
    for u in units:
        rec = by_pair_cell[(_label(u['rule_pair']), u['cell'])]
        rec['n'] += 1
        if _finite(u['owed_seconds']):
            rec['score'] += u['owed_seconds']
        else:
            rec['missing'] = True
        rec['census'] += u['unservable'] if _finite(u.get('unservable')) else 0.0
        if _finite(u.get('exact_s_per_unit')):
            rec['exact'] += u['exact_s_per_unit']
        else:
            rec['exact_missing'] = True
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

    rankings, chosen, verdicts = {}, {}, {}
    worst_census_pct = 0.0
    for lbl in pairs:
        entries = [{'cell': cell, 'owed_seconds': (None if rec['missing'] else rec['score']),
                    'unservable': rec['census'],
                    'census_pct': (100.0 * rec['census'] / rec['score']
                                   if rec['score'] else (0.0 if not rec['census'] else None)),
                    'overage_days': rec['overage'], 'n_units': rec['n']}
                   for (l, cell), rec in by_pair_cell.items() if l == lbl]
        ranked = rank_cells(entries, noise_floor_pct)
        rankings[lbl] = ranked
        verdicts[lbl] = discriminating(ranked)
        chosen[lbl] = [e['cell'] for e in ranked if e['rank'] is not None][:k]
        for e in ranked:
            if e['census_pct'] is None:
                worst_census_pct = math.inf
            else:
                worst_census_pct = max(worst_census_pct, e['census_pct'])
        log(f'\n  rule pair {lbl}:'
            + ('' if verdicts[lbl] else '   !! ONE TIE GROUP -- the score did not separate '
                                        'these cells'))
        for e in ranked:
            m = '' if e['margin_pct'] is None else f'  +{e["margin_pct"]:.3f}% to next'
            sc = 'n/a' if e['owed_seconds'] is None else f"{e['owed_seconds']:,.1f}"
            cp = 'n/a' if e['census_pct'] is None else f"{e['census_pct']:.3f}%"
            log(f"    {str(e['rank']):>4}  {e['cell']:<20} {sc:>14} s owed  "
                f"census {cp:>8}  overage {e['overage_days']:.3f} d  "
                f"[{e['decided_by']}]{m}")

    # THE PROXY CHECK.  The same cells ranked again on the closed form, per rule pair,
    # and the two orders compared.  Reported, never enforced: a disagreement is a finding
    # about the metric, and turning it into a refusal here would mean a tool that cannot
    # report its own most interesting result.  Absent (`None`) on every run whose arms took
    # no check -- keyframes off, flag-off, or a vintage before 2026-09-19.
    exact_rankings, exact_check = {}, None
    for lbl in pairs:
        ents = [{'cell': cell,
                 'owed_seconds': (None if rec['exact_missing'] else rec['exact']),
                 'overage_days': rec['overage']}
                for (l, cell), rec in by_pair_cell.items() if l == lbl]
        if any(_finite(e['owed_seconds']) for e in ents):
            exact_rankings[lbl] = rank_cells(ents, noise_floor_pct)
    if exact_rankings:
        exact_check = {lbl: {
            'score_order': [e['cell'] for e in rankings[lbl] if e['rank'] is not None],
            'exact_order': [e['cell'] for e in exact_rankings[lbl] if e['rank'] is not None],
        } for lbl in exact_rankings}
        for lbl, rec in exact_check.items():
            rec['agree'] = rec['score_order'] == rec['exact_order']
        _bad = sorted(l for l, r in exact_check.items() if not r['agree'])
        if _bad:
            log(f'\n  !! the closed form orders the cells DIFFERENTLY from the score on '
                f'{", ".join(_bad)}. The score is a proxy for pick work; an independent '
                f'evaluation of the same placements disagreeing with it is the one result '
                f'that says the proxy cannot be used.')

    agreement = rank_agreement(rankings)
    if agreement['agree'] is False:
        log(f'\n  !! {agreement["note"]}')
    refused = None
    if worst_census_pct > census_material_pct:
        shown = ('non-finite' if worst_census_pct == math.inf
                 else f'{worst_census_pct:.3f}%')
        refused = {
            'reason': 'material census',
            'worst_census_pct': (None if worst_census_pct == math.inf else worst_census_pct),
            'threshold_pct': census_material_pct,
            'detail': f'the worst cell carried unservable planned demand at {shown} of its '
                      f'own score, over the declared {census_material_pct:g}% floor. The '
                      f'score prices a line with no shelf stock at ZERO, so this ranking '
                      f'would be about availability rather than about placement. Re-run '
                      f'with enough coverage for the planned window, or rank on a metric '
                      f'that models the stock-out.',
        }
        log(f'\n  !! REFUSING to name cells: {refused["detail"]}')
        chosen = {lbl: [] for lbl in chosen}

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
            'units': 'seconds -- the steady-state mean of batch_stats.pick_owed_s, what the '
                     'run\'s PLANNED batches would cost served from where the stock stands, '
                     'summed over the unit\'s two leaves and the inventory pairs; a cell\'s '
                     'score for a rule pair sums that pair\'s units (both stock modes)',
            'direction': 'lower is better',
            'why': 'a STATE read, not a flow. Every flow total (labour, unload seconds, '
                   'throughput) is invariant to an unloading ORDER over a window long '
                   'enough to unload everything, which is why the 2026-09-18 ranking on '
                   'ss_prod_total came back an exact tie across ten cells.',
            'tie_break': {'quantity': TIE_QUANTITY,
                          'units': 'trailer-days past the cell\'s free threshold, whole run, '
                                   'from the SITE yard through frames._ydf',
                          'direction': 'lower is better',
                          'applies_within_pct': noise_floor_pct},
            'noise_floor_pct': noise_floor_pct,
            'noise_floor_note': 'DECLARED, not measured. Two cells closer than this on the '
                                'score are a tie and the overage decides.',
            'census': {'quantity': CENSUS_QUANTITY, 'series_field': CENSUS_FIELD,
                       'units': 'planned lines with no shelf stock, summed like the score',
                       'material_pct': census_material_pct,
                       'note': 'NEVER added to the score. A line with no shelf stock costs '
                               'the score zero, so a run whose census is material was '
                               'decided by availability rather than by placement and this '
                               'tool names no cells.'},
        },
        'discriminating': verdicts,
        'discriminating_note': 'per rule pair: false when every cell landed in ONE tie '
                               'group, i.e. the score did not separate the policies. A '
                               'result, not a failure -- but not a ranking either.',
        'rank_agreement': agreement,
        'exact_check': exact_check,
        'exact_check_note': 'the same cells ranked on ' + EXACT_FIELD + ', the closed-form '
                            'expected pick re-taken over each placement at keyframe '
                            'cadence. A DIFFERENT MODEL at a different grain (seconds per '
                            'unit), so only the ORDERS are comparable. null when no arm '
                            'took the check. Reported, never enforced -- AND ASYMMETRIC: '
                            'the closed form walks every aisle once a day, so its travel '
                            'term is ~0.4% of its total and it cannot see aisle choice at '
                            'all. Its placement signal is the height bracket, which the '
                            'score also carries. AGREEMENT here is therefore weak evidence; '
                            'DISAGREEMENT is the informative direction, and it means the '
                            'score has moved on something the closed form prices the other '
                            'way. Measured in Tests/unit/'
                            'test_pick_owed_agrees_with_the_closed_form.py.',
        'refused': refused,
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
    if refused is not None:
        log(f'NO cells chosen for phase 3: {refused["reason"]}')
    else:
        log(f'Chosen for phase 3 on {primary!r}: {chosen.get(primary)}')
        if not verdicts.get(primary, True):
            log(f'  !! {primary!r} did not discriminate: every cell is in one tie group, so '
                f'`chosen` is the OVERAGE order, not a placement result')
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir', help='the phase-2 RUN ROOT')
    ap.add_argument('-k', type=int, default=DEFAULT_K, metavar='N',
                    help=f'cells to name for phase 3 (default {DEFAULT_K})')
    ap.add_argument('--noise-floor-pct', type=float, default=DEFAULT_NOISE_FLOOR_PCT,
                    metavar='PCT', help='score gap below which two cells tie and overage '
                                        f'decides (default {DEFAULT_NOISE_FLOOR_PCT})')
    ap.add_argument('--census-material-pct', type=float,
                    default=DEFAULT_CENSUS_MATERIAL_PCT, metavar='PCT',
                    help='unservable planned demand above this share of a cell\'s own score '
                         'makes the ranking about availability, and the tool names no cells '
                         f'(default {DEFAULT_CENSUS_MATERIAL_PCT})')
    ap.add_argument('--pair', default=None, metavar='STORE_RULE,FUL_RULE',
                    help='the rule pair to rank on (default: the first non-rider pair the run '
                         'carries)')
    a = ap.parse_args()
    primary = tuple(a.pair.split(',')) if a.pair else None
    sys.stdout.reconfigure(errors='replace')
    doc = rank(a.base_dir, k=a.k, noise_floor_pct=a.noise_floor_pct,
               census_material_pct=a.census_material_pct, primary_pair=primary)
    # The record is written either way -- the census evidence is the most useful thing this
    # tool produces on a run it cannot rank -- but the exit code has to carry the refusal,
    # or a campaign script reads a document with an empty `chosen` as a successful ranking.
    if doc.get('refused'):
        sys.exit(2)


if __name__ == '__main__':
    main()
