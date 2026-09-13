"""run_restock_selection.py — phase 1's hand-off: rank the restock rules, pair them for phase 2.

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

**Phase 2 runs the COUPLED site dock, so the hand-off is a diagonal of RULE PAIRS.**  Under
one site dock a work unit finalizes both channel leaves at once, so phase 2 cannot sweep the
two channels' rule sets independently: it needs to be told which store rule runs beside which
fulfillment rule.  The pairing is the rank diagonal — store's 1st with fulfillment's 1st, and
so on — because the campaign's question is "does space-aware inbound beat FIFO"; the arm pair
is a CONTROL, not an axis, and the cross product would spend ~4x the wall confounding the
inbound comparison with a placement interaction nobody asked about.  Hence `rule_pairs` in the
artifact, built from the ORDERED `chosen` lists; `arms` keeps its per-channel meaning, for the
leaf projections ("Re-shape the funnel for arm pairs", `.scratch/site-dock`).

**Two consequences of that coupling land here rather than in phase 2:**

* **The extension cap counts the UNION of families across both channels.**  Extending
  `_gain_bundle_for` is work per FAMILY, site-wide — both channels choosing `map` costs one
  extension, not two — so a per-channel cap of 3 could silently commit the project to six.
  Channels are consumed in this module's existing deterministic order and that order is
  RECORDED, because an alphabetical tiebreak between two unrelated hour scales is a decision,
  not an implementation detail.
* **Phase 1 is uncoupled and phase 2 is not, so hours do not cross the boundary.**  The
  artifact stamps `put_regime: 'per-leaf'` to say so.  Nothing in the codebase reads two run
  roots, so there is no join to gate — the stamp and the published caveat are the whole of how
  the asymmetry is carried.  The one reachable mistake IS gated: pointed at a coupled run root,
  `select()` refuses rather than emitting a hand-off indistinguishable from a real one.

Run AFTER `analyze_run` (which writes the series JSONs):

    python -m Optimization.analyze_run <phase-1 run root>
    python -m Optimization.run_restock_selection <phase-1 run root>

Then copy `rule_pairs.chosen` into phase 2's rule-pair list and run phase 2 with
`--spec inbound_policies`.  The copy is deliberate and manual: the rule set is a decision a
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
from Optimization.simconfig import staffing as _staffing      # noqa: E402

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

#: The two channels a rule pair joins, in the tuple order every pair is written in.  Store
#: first, because that is the order the campaign reads and a tuple whose order is implicit is
#: a tuple that gets read backwards.  `pair` already means the INVENTORY PROFILE everywhere in
#: this harness (`run.pairs` in this very artifact, `<cell>/<pair>/`, `_derive_staffing_for_pair`),
#: so nothing here is a bare `pair`: it is a RULE pair, spelled out.
STORE_CHANNEL = 'store'
FULFILLMENT_CHANNEL = 'fulfillment'
RULE_PAIR_ORDER = (STORE_CHANNEL, FULFILLMENT_CHANNEL)

#: The rule pair that rides along whatever ranks, for the same two reasons the scalar rider
#: does — the rollup's baseline and the order-blind negative control — and outside both k and
#: the extension cap.
BASELINE_RULE_PAIR = (BASELINE_RULE, BASELINE_RULE)

#: How phase 1 fields the site's put and receiving crews, stamped on the artifact.  Phase 1 is
#: the UNCOUPLED leaf model, so each leaf fields the whole derived SITE crew and the site's
#: labour is fielded twice; phase 2 couples and fields it once.  What doubles is put and
#: receiving CAPACITY, not labour seconds, so under drain-or-cap the hours move by an amount
#: that differs PER ARM — precisely by how much an arm trades put time for pick time, which is
#: the trade phase 2 exists to measure.  A rank comparison across the boundary is therefore no
#: more invariant than an hours comparison.
PUT_REGIME = 'per-leaf'


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


def _choose(ranking, k: int, extension_cap: int, log, committed: list[str]) -> dict:
    """The top-k rules, with the extension cap applied and the `fifo` rider added.

    The cap is 08's: only `FAITHFUL_GAIN_FAMILIES` have a faithful gain bundle today, and every
    other family refuses at the first drain by design.  If the top k holds more unfaithful
    families than the cap allows, take the highest-ranked `extension_cap` of them and backfill
    from the next faithful rules down — so the cost of phase 2 is bounded by a decision made
    here rather than discovered when an arm dies mid-sweep.

    **`committed` is the UNION, and it is mutated.**  It carries the distinct unfaithful
    families already committed by the channels consumed before this one, and this channel adds
    to it.  Extending `_gain_bundle_for` is work per FAMILY, site-wide, so a family the other
    channel already paid for is free here, and the cap bounds the total bill rather than one
    channel's share of it — a per-channel cap of N can commit 2N, which is not bounding the
    thing the cap exists to bound.  The caller passes the same list to every channel and
    records both the union and the ORDER the channels were consumed in: one channel's choice
    now depends on the other's through an order that means nothing physically, and there is no
    order-free alternative, because the two rankings share units but not scales and so admit no
    global rank to allocate against.  Declared and recorded beats arbitrary and hidden.
    """
    rankable = [r for r in ranking if r['rank'] is not None]
    chosen, needs_ext, skipped = [], [], []
    for r in rankable:
        if len(chosen) >= k:
            break
        if r['needs_bundle_extension']:
            if r['rule'] not in committed:       # already paid for by an earlier channel: free
                if len(committed) >= extension_cap:
                    skipped.append(r['rule'])
                    continue
                committed.append(r['rule'])
            needs_ext.append(r['rule'])
        chosen.append(r['rule'])
    if skipped:
        log(f'    extension cap {extension_cap} reached (union across channels so far: '
            f'{committed}); backfilled past {skipped} '
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
    # against it.  Served since ticket 21 (the uniform adapter), so this reports empty today --
    # it stays because the rider is the one arm whose absence from the faithful set kills every
    # gain cell, and a future BASELINE_RULE change must be caught here, not at worker startup.
    rider_ext = [BASELINE_RULE] if BASELINE_RULE not in FAITHFUL_GAIN_FAMILIES else []
    if rider_ext:
        log(f'    !! the mandatory {BASELINE_RULE} rider has NO faithful gain bundle: every '
            f'phase-2 gain cell would refuse it at worker startup. This is outside the '
            f'extension cap and blocks phase 2 until the evaluator serves it')
    return {'chosen': chosen, 'arms': sorted(set(arms)),
            'needs_bundle_extension': needs_ext,
            'rider_needs_bundle_extension': rider_ext,
            'backfilled_past': skipped}


def _rule_pairs(channels: dict, log) -> dict:
    """The diagonal: rank-aligned `(store_rule, fulfillment_rule)`, store first.

    Built from the two channels' ORDERED `chosen` lists, never from `arms` — `arms` is
    `sorted(set(...))` and the sort destroys rank order, which is the only thing a diagonal
    reads.  The ranking itself stays per channel: the two hour scales are not comparable (the
    channels' receiving loads differ ~7x, the same fact behind `PHASE2_THRESHOLD_DAYS`), so the
    diagonal is applied AFTER ranking rather than by merging the rankings.

    **Ragged rankings zip to the common length and the artifact says so.**  The two lists
    genuinely can differ in length — a rule disqualified for a missing reading, a cap that
    backfills on one side only, or `len(chosen) < k`.  The zip takes the common length, logs
    loudly and stamps `complete: false` with the reason; the caller still WRITES, because the
    ranking is the valuable part and a human needs to read it to decide between re-running
    phase 1 and accepting a shorter campaign.  The refusal that costs money belongs at the
    phase-2 launcher, not here.

    **A one-channel run produces no pairs at all, and does not fabricate the rider.**  A
    diagonal needs two rankings; a pair list holding nothing but a manufactured
    `('fifo', 'fifo')` would look like a (very short) campaign rather than like the
    store-only run it came from.
    """
    absent = [c for c in RULE_PAIR_ORDER if c not in channels]
    if absent:
        reason = (f'no rule pairs: the run has no {" or ".join(absent)} ranking '
                  f'(channels present: {sorted(channels) or "none"}). A diagonal needs both '
                  f'channels ranked; phase 2 cannot be handed a pairing from this run')
        log(f'\n    !! {reason}')
        return {'order': list(RULE_PAIR_ORDER), 'chosen': [], 'complete': False,
                'incomplete_reason': reason, 'rider': list(BASELINE_RULE_PAIR),
                'needs_bundle_extension': []}

    chosen = {c: list(channels[c]['chosen']) for c in RULE_PAIR_ORDER}
    n = min(len(v) for v in chosen.values())
    reason = None
    if len({len(v) for v in chosen.values()}) > 1:
        reason = ('ragged rankings: ' + ', '.join(f'{c} chose {len(chosen[c])}'
                                                  for c in RULE_PAIR_ORDER) +
                  f'; zipped to the common length {n}, so the longer channel\'s tail is '
                  f'dropped. Re-run phase 1, or accept a campaign of {n} rule pair(s)')
        log(f'\n    !! {reason}')
    pairs = [[chosen[c][i] for c in RULE_PAIR_ORDER] for i in range(n)]
    if list(BASELINE_RULE_PAIR) not in pairs:
        pairs.append(list(BASELINE_RULE_PAIR))

    # A rule pair is runnable only if BOTH its members are faithful: a gain cell builds a
    # bundle for every arm in the set, not only for the arms a gain policy was chosen for, and
    # refuses at worker startup for any arm it cannot price (`Inbound/gain.py`).  Reported per
    # pair as well as per rule, because "both channels are individually fine" is not the
    # property that makes a unit run.
    ext = [{'rule_pair': p,
            'unfaithful': [r for r in dict.fromkeys(p) if r not in FAITHFUL_GAIN_FAMILIES]}
           for p in pairs if any(r not in FAITHFUL_GAIN_FAMILIES for r in p)]
    return {'order': list(RULE_PAIR_ORDER), 'chosen': pairs, 'complete': reason is None,
            'incomplete_reason': reason, 'rider': list(BASELINE_RULE_PAIR),
            'needs_bundle_extension': ext}


def select(base_dir: str, k: int = DEFAULT_K,
           extension_cap: int = DEFAULT_EXTENSION_CAP, log=print) -> dict:
    """Rank the restock rules of a phase-1 run and write the selection artifact at its root.

    `base_dir` is the phase-1 RUN ROOT.  Phase 1 is one cell by design — ranking across two
    layouts would rank two different warehouses on one scale — so a multi-cell run is refused
    rather than silently summed or silently reduced to its first cell.
    """
    from Optimization.runschema.sim_manifest import read_run_layout, _load_run_spec
    layout = read_run_layout(base_dir) or {}
    # The one place in the whole hand-off where a wrong-phase number can enter the campaign
    # silently.  Phase 1 is uncoupled BY DEFINITION — it is what `put_regime` below stamps —
    # so a coupled root is not a phase-1 run; pointed at one the selector would rank coupled
    # leaves perfectly happily and emit an artifact indistinguishable from a real one.  Written
    # defensively: `coupled` is falsy on every run that exists today, so this is inert until
    # the marker lands and bites the moment it does.
    if layout.get('coupled'):
        raise SystemExit(
            f'{base_dir} is a COUPLED run root (`run_layout.json` says coupled: true), and '
            f'phase 1 is uncoupled by definition. Ranking its leaves would produce a hand-off '
            f'artifact indistinguishable from a real one, carrying phase-2 hours into a '
            f'phase-1 decision. Point this at the phase-1 run root instead')

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

    # One list, threaded through every channel: the extension cap bounds the UNION of distinct
    # unfaithful families, and `sorted()` is the order that union is consumed in.  It is
    # alphabetical — fulfillment, then store — which means nothing physically and is therefore
    # recorded on the artifact rather than left to be re-derived from this line.
    committed: list[str] = []
    channels = {}
    for channel, chan_leaves in sorted(by_channel.items()):
        log(f'\n{"="*70}\n  {channel}\n{"="*70}')
        ranked = _rank_channel(chan_leaves, log)
        picked = _choose(ranked['ranking'], k, extension_cap, log, committed)
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

    rule_pairs = _rule_pairs(channels, log)
    if rule_pairs['chosen']:
        log(f"\n  rule pairs ({' , '.join(rule_pairs['order'])}), rank-aligned:")
        for i, p in enumerate(rule_pairs['chosen'], start=1):
            rider = '  (the mandatory rider)' if p == list(BASELINE_RULE_PAIR) else ''
            log(f'    {i:>2}. {p[0]:<18} x {p[1]:<18}{rider}')
    for e in rule_pairs['needs_bundle_extension']:
        log(f"    !! the rule pair {e['rule_pair']} cannot run until _gain_bundle_for serves "
            f"{e['unfaithful']}: a gain cell prices EVERY arm in the set, so one unfaithful "
            f"member refuses the whole unit at worker startup")

    spec = _load_run_spec(base_dir) or {}
    # THE STAFFING PIN.  Phase 1 ranks the rules of ONE warehouse: the crews, the levels, the
    # line floor and the script all follow from the derivation this run made at setup.  The
    # campaign then puts a BUILD between the phases BY DESIGN -- no phase-1 number is ever
    # published, which is what makes that legal, and "Extend the gain bundles" is exactly such
    # a build.  A change that moves the derivation therefore leaves phase 2 running a different
    # site from the one whose ranking it is executing, and nothing in the run tree joins two run
    # roots to notice.  The digest is what phase 2 carries; the projection beside it is what a
    # refusal is DIAGNOSED from ("Re-size the funnel in site days").
    _derived = (spec.get('staffing') or {}).get('derived') or {}
    staffing_pin = {
        'sigfigs': _staffing.PIN_SIGFIGS,
        'pin': {lab: _staffing.pin_digest(d) for lab, d in sorted(_derived.items())},
        'projection': {lab: _staffing.pin_of(d) for lab, d in sorted(_derived.items())},
        'note': "copy `pin` into whatif_config.PHASE2_STAFFING_PIN. `inbound_policies` "
                "refuses to start without it, and every work unit refuses if its own "
                "derivation hashes to something else -- so a between-phase build that moves "
                "the crews, the day's demand, the two prices or the script depth is caught "
                "before a cell runs rather than after the campaign publishes",
    }
    if not _derived:
        log('')
        log('  !! this run recorded no derived staffing block, so there is no pin to copy (a '
            'flag-off run, or one predating the era). `inbound_policies` will refuse.')
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
        'extension_cap_scope': 'the UNION of distinct unfaithful families across both '
                               'channels — extending _gain_bundle_for is work per FAMILY, '
                               'site-wide, so both channels choosing the same family costs '
                               'one extension and a per-channel cap of N could commit 2N',
        'extension_channel_order': sorted(by_channel),
        'extension_union': list(committed),
        'baseline_rule': BASELINE_RULE,
        'baseline_rule_pair': list(BASELINE_RULE_PAIR),
        'faithful_gain_families': list(FAITHFUL_GAIN_FAMILIES),
        'staffing': staffing_pin,
        'put_regime': PUT_REGIME,
        'put_regime_note': 'phase 1 runs the UNCOUPLED leaf model, so each leaf fields the '
                           'whole derived SITE put and receiving crew and the site\'s labour '
                           'is fielded twice; phase 2 couples and fields it once. What '
                           'doubles is CAPACITY, not labour seconds, so under drain-or-cap '
                           'the hours move by an amount that differs per arm — by exactly how '
                           'much an arm trades put time for pick time, the trade phase 2 '
                           'exists to measure. Neither hours nor RANKS are comparable across '
                           'the phases; the matrix\'s own deltas are what the campaign '
                           'publishes. Nothing reads two run roots, so this stamp and the '
                           'published caveat are the whole of how that is carried',
        'rule_pairs': rule_pairs,
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
    log('Copy `rule_pairs.chosen` into phase 2\'s rule-pair list before running '
        '`--spec inbound_policies`; each channel\'s `arms` is the leaf projection of it.')
    if staffing_pin['pin']:
        log('Copy `staffing.pin` into whatif_config.PHASE2_STAFFING_PIN as well -- phase 2 '
            'refuses to start without it:')
        for _lab, _dig in staffing_pin['pin'].items():
            log(f'    {_lab!r}: {_dig!r},')
    if not rule_pairs['complete']:
        log(f"!! `rule_pairs.complete` is false — {rule_pairs['incomplete_reason']}")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir', help='the phase-1 RUN ROOT (relative names resolve under '
                                     'COMPARISON_OUTPUT_DIR)')
    ap.add_argument('-k', type=int, default=DEFAULT_K, metavar='N',
                    help='how many restock RULES to choose (each costs 2 arms)')
    ap.add_argument('--extension-cap', type=int, default=DEFAULT_EXTENSION_CAP, metavar='N',
                    help='how many DISTINCT unfaithful families, counted as a union across '
                         'both channels, may be committed before the selector backfills from '
                         'the faithful set instead')
    args = ap.parse_args()
    from Optimization.runschema import resolve_base_dir
    base = resolve_base_dir(args.base_dir)
    if not os.path.isdir(base):
        ap.error(f'not a directory: {base}')
    select(base, k=args.k, extension_cap=args.extension_cap)


if __name__ == '__main__':
    main()
