"""run_restock_selection.py — phase 1's hand-off: rank the restock rules, choose phase 2's arms.

The inbound funnel is two phases, not one product sweep ("Design the phased funnel",
`.scratch/inbound-optimization`).  Phase 1 is a fresh, inbound-OFF run of the full assignment
suite whose ONLY job is to rank the restock rules; phase 2 sweeps the ten inbound policies over
the rules phase 1 chose.  This module is the seam between them, and it writes the one artifact
that carries the decision:

    <phase-1 run root>/restock_selection.json

**The unit is a restock RULE, not an arm.**  `strategies.CHANNEL_RESTOCKS` filters on
`Strategy.restock`, so choosing `tmin` necessarily takes both `uni_tmin_norsl` and
`opt_tmin_norsl`: 17 rules, each costing 2 arms.  "Top-k arms" is not an expressible unit, and
a selector that ranked arms would hand phase 2 an arm set it cannot configure.

**The metric is TOTAL PRODUCTION HOURS** — unload + put + pick, the objective the whole effort
is about ("Define the inbound objective").  It reaches this module as `ss_prod_total` in each
channel run's `series_json` artifact: a steady-state MEAN of `production_seconds` per batch,
resolved through the contract rather than by name.  Ranking on
pick hours alone — all that `channel_rollup` and `whatif_labor` offer — would systematically
favour arms that buy pick time with put-away time, which is the exact trade phase 2 measures.

Three things about that metric decide the shape of the code below:

* **Do NOT read the cross-profile summary CSV.**  `_aggregate_series` normalizes each profile to
  its own baseline, so that file holds RATIOS; `total_production_time` is deliberately absent
  from `AGGREGATE_ORDER` for the same reason.  Hours are additive and physical, ranks are not,
  so this module sums `ss_prod_total` over the profiles' series documents itself.
* **Absence must be LOUD.**  The metric is capability-gated: a run with no `work_events` rows
  produces no `total_production_time` at all and `ss_prod_total` comes out NaN — correctly, an
  unmeasured leg is not a leg measured at zero.  A selector that read a missing value as a tie
  would rank all seventeen rules on nothing, so a rule with any missing or non-finite reading is
  EXCLUDED from the ranking and reported by name.
* **`fifo` is mandatory, and is not one of the k.**  `run_channel_rollup._baseline_entry` needs
  it as the analysis baseline, and `uni_fifo`/`opt_fifo` are byte-identical runs, which makes
  the same row phase 2's order-blind negative control.  It rides along whether or not it ranks.

Run AFTER `analyze_run` (which writes the series JSONs):

    python -m Optimization.analyze_run <phase-1 run root>
    python -m Optimization.run_restock_selection <phase-1 run root>

Then copy the chosen `arms` into `whatif_config.PHASE2_ARMS` and run phase 2 with
`--spec inbound_policies`.  The copy is deliberate and manual: the arm set is a decision a
person signs off on, and a spec that read a JSON file at import would make it invisible.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_restock_selection.py <dir>); `-m` form needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Inbound.gain import FAITHFUL_GAIN_FAMILIES              # noqa: E402
from Optimization.config.strategies import STRATEGY_BY_KEY   # noqa: E402

#: The series-document field the ranking reads, and the quantity it belongs to.  Named as a
#: pair because the artifact records both: a later reader needs to know which figure to look at
#: as much as which number was summed.
METRIC_FIELD = 'ss_prod_total'
METRIC_QUANTITY = 'total_production_time'

#: How many RULES are chosen, and how many of them may need the gain evaluator extended.
#: The extension cap is what keeps phase 2 costable: only `FAITHFUL_GAIN_FAMILIES` have a
#: faithful bundle today and every other family refuses by design, so a top-five holding more
#: unfaithful families than this backfills from the faithful set instead.
DEFAULT_K = 5
DEFAULT_EXTENSION_CAP = 3

#: The rule that rides along whatever it ranks.
BASELINE_RULE = 'fifo'


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _rule_of(arm_key: str) -> str | None:
    """The restock rule an arm key belongs to, from the strategy grid rather than by parsing.

    `uni_rank_labor_norsl` splits into three fields whose middle one contains underscores, so
    every hand-rolled parse of an arm key is a guess.  `STRATEGY_BY_KEY` already holds the
    answer the driver used when it built the arm, so unknown keys come back None and are
    reported rather than mis-bucketed into a rule that exists.
    """
    s = STRATEGY_BY_KEY.get(arm_key)
    return None if s is None else s.restock


def _tree_for(base_dir: str):
    """(RunTree, [(cell_name, cell_dir)]) for a phase-1 RUN ROOT.

    Rooted at the run root, not a cell dir — unlike `run_channel_rollup`, whose artifacts are
    cell-scoped.  The selection is a property of the RUN: it names the arm set a second run will
    carry, so it belongs beside the run's own spec and layout descriptors rather than inside the
    cell whose numbers produced it.
    """
    from Optimization import runschema
    from Optimization.runschema import contract as _contract
    from Optimization.runschema.resolver import RunTree
    base = os.path.abspath(base_dir)
    try:
        rt = runschema.resolver_for(base)
    except runschema.UnsupportedRunTree:
        head = _contract.head()
        doc = _contract.load(head) if head else None
        if doc is None:
            raise
        rt = RunTree(base, doc, layout={})
    return rt, rt.cells()


def _read_leaves(rt, cell_dir: str):
    """[(ChannelRun, meta, series)] for every analyzed channel run under one cell dir."""
    from Optimization.runschema.runlayout import iter_channel_runs
    out = []
    for run in iter_channel_runs(cell_dir):
        sp = rt.leaf_path(run, 'series_json')
        mp = rt.leaf_path(run, 'sim_meta')
        if not (os.path.exists(sp) and os.path.exists(mp)):
            continue
        with open(mp) as f:
            meta = json.load(f)
        with open(sp) as f:
            series = json.load(f)
        out.append((run, meta, series))
    return out


def _rank_channel(leaves, log) -> dict:
    """Rank every restock rule in one channel.  Lower hours win.

    An arm's score is its `ss_prod_total` SUMMED over the channel's (profile, config) leaves —
    a steady-state per-batch mean of production seconds, summed, then reported in hours.  Every
    arm is measured on the identical leaf set, so the sum is like-for-like; a MIN over leaves
    would let two rules be scored on different profiles and is exactly the "ranks are not
    additive" mistake one level down.

    A RULE's score is the minimum over its own arms, because selecting a rule takes both of them
    and what makes the rule worth carrying into phase 2 is that one of its arms performs.  Both
    arm totals are recorded, so a reader can see when the two disagree.

    An arm missing from a leaf, or reading non-finite there, disqualifies its RULE: a partial sum
    would silently rank a rule that was measured on fewer profiles than its rivals as cheaper
    than all of them.
    """
    leaf_ids, per_arm = [], defaultdict(dict)     # arm -> {leaf_id: seconds}
    for run, meta, series in leaves:
        leaf_id = f'{run.pair}/{run.config}' + (f'/{run.channel}' if run.channel else '')
        leaf_ids.append(leaf_id)
        for s in series.get('strategies', []):
            per_arm[s.get('key')][leaf_id] = s.get(METRIC_FIELD)

    by_rule: dict[str, dict] = defaultdict(lambda: {'arms': {}, 'missing': []})
    unknown = []
    for arm, readings in per_arm.items():
        rule = _rule_of(arm)
        if rule is None:
            unknown.append(str(arm))     # str: a malformed doc can carry a null key, and a
            continue                     # mixed list would blow up `sorted` rather than report
        rec = by_rule[rule]
        absent = [lid for lid in leaf_ids if not _finite(readings.get(lid))]
        if absent:
            rec['missing'].append({'arm': arm, 'leaves': absent})
            rec['arms'][arm] = None
        else:
            rec['arms'][arm] = sum(readings[lid] for lid in leaf_ids) / 3600.0

    rows = []
    for rule, rec in by_rule.items():
        scored = {a: v for a, v in rec['arms'].items() if v is not None}
        best = min(scored, key=scored.get) if scored and not rec['missing'] else None
        rows.append({
            'rule': rule,
            'score_hours': (None if best is None else scored[best]),
            'best_arm': best,
            'arm_hours': rec['arms'],
            'missing': rec['missing'],
            'needs_bundle_extension': rule not in FAITHFUL_GAIN_FAMILIES,
        })
    # Rankable rules first, cheapest first; the disqualified tail keeps a stable alphabetical
    # order and carries `rank: None` so nothing reads its position as a result.
    ok = sorted((r for r in rows if r['score_hours'] is not None),
                key=lambda r: r['score_hours'])
    bad = sorted((r for r in rows if r['score_hours'] is None), key=lambda r: r['rule'])
    for i, r in enumerate(ok, start=1):
        r['rank'] = i
    for r in bad:
        r['rank'] = None
        log(f"    !! {r['rule']}: excluded — {METRIC_QUANTITY} is missing or non-finite on "
            f"{len(r['missing'])} arm(s); a partial sum would rank it cheapest by accident")
    if unknown:
        log(f'    !! {len(unknown)} arm key(s) in the series are not in the strategy grid '
            f'(e.g. {unknown[0]}); they belong to no rule and were dropped')
    return {'leaves': sorted(leaf_ids), 'ranking': ok + bad, 'unknown_arms': sorted(unknown)}


def _choose(ranking, k: int, extension_cap: int, log) -> dict:
    """The top-k rules, with the extension cap applied and the `fifo` rider added.

    The cap is 08's: only `FAITHFUL_GAIN_FAMILIES` have a faithful gain bundle today, and every
    other family refuses at the first drain by design.  If the top k holds more unfaithful
    families than the cap allows, take the highest-ranked `extension_cap` of them and backfill
    from the next faithful rules down — so the cost of phase 2 is bounded by a decision made
    here rather than discovered when an arm dies mid-sweep.
    """
    rankable = [r for r in ranking if r['rank'] is not None]
    chosen, needs_ext, skipped = [], [], []
    for r in rankable:
        if len(chosen) >= k:
            break
        if r['needs_bundle_extension']:
            if len(needs_ext) >= extension_cap:
                skipped.append(r['rule'])
                continue
            needs_ext.append(r['rule'])
        chosen.append(r['rule'])
    if skipped:
        log(f'    extension cap {extension_cap} reached; backfilled past {skipped} '
            f'(each would need _gain_bundle_for extended before phase 2 could run it)')
    if len(chosen) < k:
        log(f'    !! only {len(chosen)} rule(s) available for a k of {k}: the ranking is '
            f'shorter than the ask, so phase 2 sweeps fewer arms than budgeted')
    # `fifo` rides along whether or not it ranked: it is the rollup's baseline AND the
    # order-blind negative control, and its absence makes every phase-2 saving meaningless.
    arms = list(chosen)
    if BASELINE_RULE not in arms:
        arms.append(BASELINE_RULE)
    # ...and the rider needs a bundle of its own, MANDATORILY and outside the cap.  A gain cell
    # builds `_gain_bundle_for(strat, ...)` for every arm in the set, not only for the ones a
    # gain policy was chosen for -- so a phase-2 gain cell refuses the `fifo` rider at worker
    # startup unless the evaluator serves it.  The cap governs which OPTIONAL families are worth
    # extending; this one is not optional, so it is reported separately rather than counted
    # against it.  (Verified 2026-08-31: `_gain_bundle_for` raises for both fifo arms.)
    rider_ext = [BASELINE_RULE] if BASELINE_RULE not in FAITHFUL_GAIN_FAMILIES else []
    if rider_ext:
        log(f'    !! the mandatory {BASELINE_RULE} rider has NO faithful gain bundle: every '
            f'phase-2 gain cell would refuse it at worker startup. This is outside the '
            f'extension cap and blocks phase 2 until the evaluator serves it')
    return {'chosen': chosen, 'arms': sorted(set(arms)),
            'needs_bundle_extension': needs_ext,
            'rider_needs_bundle_extension': rider_ext,
            'backfilled_past': skipped}


def select(base_dir: str, k: int = DEFAULT_K,
           extension_cap: int = DEFAULT_EXTENSION_CAP, log=print) -> dict:
    """Rank the restock rules of a phase-1 run and write the selection artifact at its root.

    `base_dir` is the phase-1 RUN ROOT.  Phase 1 is one cell by design — ranking across two
    layouts would rank two different warehouses on one scale — so a multi-cell run is refused
    rather than silently summed or silently reduced to its first cell.
    """
    rt, cells = _tree_for(base_dir)
    if not cells:
        raise SystemExit(f'no cells under {base_dir}; is this a run root, and has it run?')
    if len(cells) > 1:
        raise SystemExit(
            f'{len(cells)} cells under {base_dir} ({", ".join(n for n, _d in cells)}): phase 1 '
            f'is ONE cell by design. Two layouts cannot be ranked on one hours scale, and '
            f'picking a cell here would hide which one the decision came from')
    cell_name, cell_dir = cells[0]

    leaves = _read_leaves(rt, cell_dir)
    if not leaves:
        raise SystemExit(f'no analyzed channel runs (sim_meta + series JSONs) under {cell_dir}. '
                         f'Run `python -m Optimization.analyze_run {base_dir}` first.')

    by_channel = defaultdict(list)
    for run, meta, series in leaves:
        by_channel[meta.get('channel') or run.channel or 'store'].append((run, meta, series))

    channels = {}
    for channel, chan_leaves in sorted(by_channel.items()):
        log(f'\n{"="*70}\n  {channel}\n{"="*70}')
        ranked = _rank_channel(chan_leaves, log)
        picked = _choose(ranked['ranking'], k, extension_cap, log)
        for r in ranked['ranking']:
            if r['rank'] is None:
                continue
            mark = '*' if r['rule'] in picked['chosen'] else ' '
            ext = '  (needs bundle extension)' if r['needs_bundle_extension'] else ''
            log(f"  {mark} {r['rank']:>2}. {r['rule']:<18} {r['score_hours']:>12,.2f} h "
                f"via {r['best_arm']}{ext}")
        log(f"    chosen: {picked['chosen']}")
        log(f"    arms for phase 2 (with the {BASELINE_RULE} rider): {picked['arms']}")
        channels[channel] = {**ranked, **picked}

    from Optimization.runschema.sim_manifest import read_run_layout, _load_run_spec
    layout = read_run_layout(base_dir) or {}
    spec = _load_run_spec(base_dir) or {}
    doc = {
        'version': 1,
        'written': datetime.now().isoformat(timespec='seconds'),
        'run': {
            'base': os.path.basename(os.path.abspath(base_dir).rstrip(os.sep)),
            'cell': cell_name,
            'spec': layout.get('spec'),
            'schema_id': layout.get('schema_id'),
            'repo_commit': spec.get('repo_commit'),
            'n_batches': spec.get('n_batches'),
            'sampler': spec.get('sampler'),
            'pairs': layout.get('pairs'),
        },
        'metric': {
            'quantity': METRIC_QUANTITY,
            'series_field': METRIC_FIELD,
            'units': 'hours — the steady-state mean of production_seconds per batch, summed '
                     'over the channel\'s (profile, config) leaves, then / 3600',
            'direction': 'lower is better',
            'rule_score': 'the minimum over the rule\'s own arms; both arms are recorded',
            'not_read': 'the cross-profile summary CSV — the aggregator normalizes each '
                        'profile to its own baseline, so it holds ratios, not hours',
        },
        'k': k,
        'extension_cap': extension_cap,
        'baseline_rule': BASELINE_RULE,
        'faithful_gain_families': list(FAITHFUL_GAIN_FAMILIES),
        'channels': channels,
    }
    # `analysis_path`, not `rt.path`: the run is resolved through the contract it was SIMULATED
    # with, and a phase-1 run predating this artifact's declaration has never heard of it —
    # `rt.path` would raise KeyError for a location that is perfectly legal to write.  The
    # analysis doing the writing is today's, so today's contract says where its output goes.
    from Optimization.runschema import analysis_path
    out = analysis_path(rt, 'restock_selection_json')
    if out is None:                        # no contract declares it: this build cannot write it
        raise SystemExit('no run-tree contract declares `restock_selection_json`; run '
                         '`python -m Optimization.runschema.preflight` to adopt the current one')
    tmp = f'{out}.tmp.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, out)
    log(f'\nWrote {out}')
    log('Copy each channel\'s `arms` into whatif_config.PHASE2_ARMS before running '
        '`--spec inbound_policies`.')
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir', help='the phase-1 RUN ROOT (relative names resolve under '
                                     'COMPARISON_OUTPUT_DIR)')
    ap.add_argument('-k', type=int, default=DEFAULT_K, metavar='N',
                    help='how many restock RULES to choose (each costs 2 arms)')
    ap.add_argument('--extension-cap', type=int, default=DEFAULT_EXTENSION_CAP, metavar='N',
                    help='how many chosen rules may lack a faithful gain bundle before the '
                         'selector backfills from the faithful set instead')
    args = ap.parse_args()
    from Optimization.runschema import resolve_base_dir
    base = resolve_base_dir(args.base_dir)
    if not os.path.isdir(base):
        ap.error(f'not a directory: {base}')
    select(base, k=args.k, extension_cap=args.extension_cap)


if __name__ == '__main__':
    main()
