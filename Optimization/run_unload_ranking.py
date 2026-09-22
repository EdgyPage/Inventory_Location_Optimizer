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
threshold, from the SITE yard through `frames._ydf`), lower first.  The floor was declared
(0.1%) until 2026-09-22; it is now MEASURED per rule pair from the paired per-batch series
(`measured_floor`: every cell draws the same script, so the per-batch gap to the reference
cell is paired, and its moving-block bootstrap interval is the cell's own uncertainty), and
the declared value is the fallback for a run whose batches cannot be read.  The artifact
records which applied and every cell's gap with its interval, so a reader can re-draw the
line.

THE CENSUS, the adjustment, and the refusal it can still trigger.  A planned line whose SKU
has nothing on any shelf costs the score NOTHING, because there is no bin to walk to — so a
score read alone would rank "leave it all in the yard" first.  `ss_unservable` is the
census of that demand.  Since 2026-09-22 the score that RANKS prices those lines at the
leaf's own MEAN priced line (`adjusted_owed`: `owed * W / (W - unservable)` per batch, W
the script's planned lines rebuilt from the pair's batch pickle) — nothing invented, the
one price the leaf itself measured — because on the 2026-09-20 campaign the unpriced
lines were ~35% of the gap between the two ends of the ranking.  A MATERIAL census still
refuses: when availability rather than placement decided the run, no pricing rule makes
the ranking about placement, and this tool writes the record, names no cells, and exits
non-zero.

TWO VERDICTS AND A CONTROL the document carries beside the ranks, because a rank list
cannot state them: `discriminating` is false when every cell lands in ONE tie group (the
metric did not separate the policies, which is a result and not a ranking);
`rank_agreement` reports whether the REPLICATING rule pairs ordered the cells the same way
— independent replications of the same question, so disagreement is the honest signal
that neither ordering is a property of the unloading policy; and `rider_control` reads the
mandatory `fifo` rider as what it is (CONTEXT.md: Rider): a CONTROL under which a
placement-gain policy degenerates to arrival order, so it names the policies that did
nothing at all under it and never vetoes the winner pair's order.  Until 2026-09-22 the
rider was counted as a replication and vetoed every ranking it disagreed with.

WHICH METRIC.  The placement score by default; a spec may declare another through
`whatif_config.SPECS[name]['ranking']` (the fill trial ranks on pick labour), and the
artifact names what it used.

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

import numpy as np                                                            # noqa: E402

from Optimization.config.strategies import STRATEGY_BY_KEY                    # noqa: E402
from Optimization.run_restock_selection import (                             # noqa: E402
    BASELINE_RULE_PAIR, RULE_PAIR_ORDER, _finite, _read_leaves, _rule_of, _tree_for)
from Optimization.Performance_Evaluations.common.stats_core import _boot_ci   # noqa: E402
from Optimization.Performance_Evaluations.common.style import _WIN            # noqa: E402

#: The score: the series field and the quantity it belongs to.  Recorded on the artifact so
#: a later reader knows which figure to look at as well as which number summed.
#:
#: THE DEFAULT, not the only one.  A spec may DECLARE what ranks it (`whatif_config.SPECS[
#: name]['ranking']`, the same keys as `DEFAULT_METRIC`), and `_metric_for` reads that off
#: the run's recorded spec name -- so one tool ranks phase 2's era run on the placement
#: score and the fill trial on pick labour, and the artifact says which it used.  These
#: module names stay because every fixture and every reader of the artifact spells them.
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
#: Two cells closer than this (relative to the cheaper) are a tie on labour.  DECLARED --
#: and since 2026-09-22 the FALLBACK: when the run's leaf DBs are readable the floor is
#: MEASURED from the paired per-batch series (`measured_floor`), and this value applies
#: only to a run whose batches cannot be read (a vintage without the column, a tree with
#: no DBs).  The artifact records which it used.
DEFAULT_NOISE_FLOOR_PCT = 0.1
#: How many cells the hand-off names for phase 3.
DEFAULT_K = 3

#: What a spec may declare to rank on.  `field` is the series scalar summed per unit,
#: `column` the per-batch `batch_stats` column it is the steady-state mean of (None when the
#: scalar has no per-batch form, in which case the floor stays declared), `census_*` the
#: honesty term read beside it (None for a metric with none), `exact_field` the second
#: opinion (None for none).  `direction` is always lower-is-better here; a metric that ranks
#: the other way is a different tool.
DEFAULT_METRIC = {
    'quantity': METRIC_QUANTITY, 'field': METRIC_FIELD, 'column': 'pick_owed_s',
    'census_quantity': CENSUS_QUANTITY, 'census_field': CENSUS_FIELD,
    'census_column': 'unservable_weight', 'exact_field': EXACT_FIELD,
    'declared_by': 'default',
}


def _metric_for(layout: dict, log=print) -> dict:
    """The ranking metric the run's SPEC declares, else the default, as one dict.

    Read off `run_layout.json`'s recorded spec name through `whatif_config.SPECS`; a spec
    with no `ranking` key ranks on the default.  A run whose spec is no longer registered
    (renamed, retired) ranks on the default OUT LOUD rather than refusing: the artifact
    names what it used, and a reader who wanted the other metric can see it did not get it.
    """
    name = layout.get('spec')
    declared = None
    if name:
        try:
            from Optimization.config.whatif_config import SPECS
            declared = (SPECS.get(name) or {}).get('ranking')
        except ImportError:                                   # a tree without the config
            declared = None
    if declared is None:
        if name:
            log(f'  metric: spec {name!r} declares no ranking; ranking on the default '
                f'{METRIC_FIELD}')
        return dict(DEFAULT_METRIC)
    m = dict(DEFAULT_METRIC)
    m.update(declared)
    m['declared_by'] = f'spec:{name}'
    return m


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


def adjusted_owed(owed: float, unservable: float, planned_weight: float) -> float:
    """The score with its unpriced lines priced at the leaf's MEAN priced line.

    `owed` prices `planned_weight - unservable` lines and charges the rest nothing, so a
    cell that leaves more stock in the yard reads CHEAPER for it.  On the 2026-09-20
    campaign that was ~35% of the fifo-vs-gforecast gap: gforecast stranded 170-250 more
    fulfillment lines a batch and was credited zero for each.  Scaling by the priced share
    charges each unpriced line what the priced ones cost on average -- the one price the
    leaf itself measured, so nothing is invented -- and leaves a leaf with no census exactly
    as it was.  NaN when the weight cannot price anything (all lines unservable).
    """
    if not _finite(owed):
        return float('nan')
    if not unservable:
        # EXACTLY as it was, not `owed * W / W`: the division is not an identity in float
        # arithmetic, and a leaf with no census must rank on the byte it measured.
        return owed
    priced = planned_weight - unservable
    if not _finite(planned_weight) or planned_weight <= 0 or priced <= 0:
        return float('nan')
    return owed * planned_weight / priced


def measured_floor(by_cell: dict, reference: str, *, batch_ids: list | None = None) -> dict:
    """The noise floor MEASURED from paired per-batch scores, per cell against the reference.

    `by_cell` is `{cell: {batch_id: score}}` for one rule pair (the pair's units summed).
    Every cell of a run draws the same script from the same seed, so the per-batch
    difference between two cells is PAIRED, and its serial correlation is the sim's
    (memory `per-batch-series-are-autocorrelated`): the interval is `_boot_ci`'s
    moving-block bootstrap of the mean relative difference over the batches both cells
    carry, never an iid resample.  The floor is the LARGEST 95% half-width among the cells,
    so two cells closer than any cell's own uncertainty are a tie.

    Returns `{'floor_pct', 'reference', 'cells': {cell: {'gap_pct', 'ci_pct': [lo, hi],
    'n_batches'}}, 'source'}`; `floor_pct` is None (and `source` says why) when no cell
    has at least three shared batches with the reference, so the caller falls back to the
    declared value.
    """
    ref = by_cell.get(reference) or {}
    cells: dict = {}
    widest = None
    for cell, series in sorted(by_cell.items()):
        if cell == reference or not series:
            continue
        shared = sorted(b for b in series if b in ref and _finite(series[b])
                        and _finite(ref[b]) and ref[b])
        if batch_ids is not None:
            shared = [b for b in shared if b in batch_ids]
        d = np.array([(series[b] - ref[b]) / ref[b] * 100.0 for b in shared], dtype=float)
        if d.size < 3:
            cells[cell] = {'gap_pct': None, 'ci_pct': None, 'n_batches': int(d.size)}
            continue
        lo, hi = _boot_ci(d, np.mean)
        cells[cell] = {'gap_pct': float(d.mean()), 'ci_pct': [lo, hi], 'n_batches': int(d.size)}
        half = (hi - lo) / 2.0
        if _finite(half) and (widest is None or half > widest):
            widest = half
    # A floor of EXACTLY zero is not a measurement to rank on: every interval collapsed
    # (every cell byte-identical to the reference, or a constant offset with no
    # batch-to-batch variance), and a zero floor would let float noise in a sum decide
    # rank and call it discrimination.  The caller falls back to the declared value.
    if widest is not None and widest <= 0.0:
        return {'floor_pct': None, 'reference': reference, 'cells': cells,
                'source': 'unmeasurable: every paired interval has zero width'}
    return {
        'floor_pct': widest, 'reference': reference, 'cells': cells,
        'source': ('measured' if widest is not None else
                   'unmeasurable: no cell shares three finite batches with the reference'),
    }


def control_verdict(ranked: list, reference: str) -> dict:
    """What the RIDER says, read as a control rather than a replication.

    Under FIFO restock a placement-gain policy degenerates to arrival order (memory
    `pick-owed-s-replaces-flow-totals-for-unload-ranking`), so the rider's cells that read
    BYTE-IDENTICAL to the reference on score and overage are the policies that did nothing
    at all under it -- a fact about the pairing, reported as one -- and the cells that
    moved are the policies that reorder on a rule independent of placement.  Neither is a
    ranking of unloading policies, which is why this never vetoes the winner pair's order.
    """
    ref = next((e for e in ranked if e['cell'] == reference), None)
    if ref is None or ref.get('rank') is None:
        return {'reference': reference, 'inert': [], 'moved': [],
                'note': 'the reference cell has no rider reading, so the control is silent'}
    inert, moved = [], []
    for e in ranked:
        if e['cell'] == reference or e.get('rank') is None:
            continue
        same = (math.isclose(e['owed_seconds'], ref['owed_seconds'], rel_tol=1e-9)
                and math.isclose(e['overage_days'], ref['overage_days'], rel_tol=1e-9,
                                 abs_tol=1e-9))
        (inert if same else moved).append(e['cell'])
    return {
        'reference': reference, 'inert': inert, 'moved': moved,
        'note': ('a CONTROL, not a replication: under FIFO restock a gain policy degenerates '
                 'to arrival order, so `inert` names the policies that did nothing at all '
                 'under the rider and `moved` the ones that reorder on a rule independent '
                 'of placement. Read beside the winner pair; never a veto on it.'),
    }


def rank_agreement(rankings: dict, control: str | None = None) -> dict:
    """Do the rule pairs that ran order the cells the same way?

    Each NON-CONTROL rule pair is an INDEPENDENT replication of the same question — same
    cells, same geometry, same planned demand, a different restocking rule underneath.  If
    the unloading policy is what the score is measuring, the orders agree; if they
    disagree, the order is a property of the rule pair and not of the policy, and neither
    ranking should be handed to phase 3 as though it were.

    `control` names the rider (CONTEXT.md: Rider), which is EXCLUDED: its degeneracy under
    FIFO restock is on the record, so its disagreeing with the winner pair says nothing
    about the policies, and until 2026-09-22 it vetoed every ranking it disagreed with.  It
    is read through `control_verdict` instead.

    Returns `{'pairs': [...], 'orders': {pair: [cells]}, 'agree': bool, 'common': [cells],
    'note': str}`.  Compared over the cells EVERY pair ranked, because a pair that poisoned
    a cell has no opinion about it and must not be read as disagreeing.  Fewer than two
    replications with an opinion answers `agree: None` — unknown, not true.
    """
    orders = {lbl: [e['cell'] for e in r if e['rank'] is not None]
              for lbl, r in rankings.items()}
    have = {lbl: o for lbl, o in orders.items() if o and lbl != control}
    if len(have) < 2:
        return {'pairs': sorted(orders), 'orders': orders, 'agree': None, 'common': [],
                'control': control,
                'note': ('fewer than two replicating rule pairs produced a ranking'
                         + (f' (the rider {control!r} is a control, not a replication)'
                            if control in orders else '')
                         + ', so agreement is unknown rather than true')}
    common = set.intersection(*[set(o) for o in have.values()])
    restricted = {lbl: [c for c in o if c in common] for lbl, o in have.items()}
    first = next(iter(restricted.values()))
    agree = all(o == first for o in restricted.values())
    return {
        'pairs': sorted(orders), 'orders': orders, 'agree': agree,
        'common': sorted(common), 'control': control,
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


def _unit_trailers(site_db: str) -> int | None:
    """How many trailers this unit's site yard handled over the run, or None when the site
    DB is absent or holds no run -- the count a reader scales a yard bill by."""
    from Optimization.persistence.Picking_Data import find_run, load_yard_trailers
    if not os.path.exists(site_db):
        return None
    run_id = find_run(site_db)
    if run_id is None:
        return None
    return len(load_yard_trailers(site_db, run_id))


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


def _leaf_batch_rows(rt, run, arm: str) -> tuple | None:
    """`(rows, n_batches)` for one arm of one leaf, or None when the leaf DB is absent or
    holds no run: its `batch_stats` rows sorted by batch -- the per-batch form of the
    score, which the series document carries only SMOOTHED (`series._series` rolls it
    over `_SMOOTH` batches) and which the floor and the adjustment both need raw -- and
    the batch count the ARM itself recorded (`simulation_runs.n_batches`, on the
    guaranteed surface), which is the prefix the worker weighted the script over
    (`batches[:n_batches]`).  The row count is the fallback when an older vintage did not
    stamp it, and a leaf that finalized short of its plan is not the rows' business."""
    from Optimization.persistence.Picking_Data import find_run, load_batch_stats, run_identity
    db = rt.leaf_path(run, 'sim_db', strategy=arm)
    if not os.path.exists(db):
        return None
    run_id = find_run(db, arm)
    if run_id is None:
        return None
    rows = sorted(load_batch_stats(db, run_id), key=lambda s: s.batch_id)
    if not rows:
        return None
    n = run_identity(db, run_id).get('n_batches')
    return rows, (int(n) if n else len(rows))


def inbound_repick(pick_rows, placement_rows) -> dict:
    """How much of what inbound put away inside the window was picked again inside it.

    THE QUESTION a reader of the ranking asks first once the score barely moves: could an
    unloading rule have mattered here at all?  It can only change which empty bin an
    arriving pack takes, so it has leverage only over stock that is PICKED FROM those bins
    before the window ends.  Two shares, both unit-level, both from the run's own tables:

      served_share   = units picked from bins that received a reorder placement earlier in
                       the window (same (sku, bin), placement batch <= pick batch)
                       / all units picked in the window;
      repicked_share = of the units reorder placed in the window, the share picked again
                       before it ended -- per bin, min(units picked after the placement,
                       units placed), summed / units placed.

    "Picked again" is ANY pick from the bin after the placement, at unit grain; a bin that
    was already stocked when the placement topped it up counts its later picks against the
    placed units first (a floor, since the earlier stock may have served some of them).
    `pick_rows` is the `pick_bins` named query (sku, batch_id, aisle_id, bayX, bayY, units);
    `placement_rows` is `load_bin_placements` (the `cause` column selects the reorders).
    """
    first_placed: dict = {}                    # (sku, aisle, x, y) -> earliest reorder batch
    placed_units: dict = defaultdict(float)
    initial_bins: dict = defaultdict(set)      # sku -> bins its setup stock occupied
    for p in placement_rows:
        key = (p['sku'], p['aisle_id'], p['bayX'], p['bayY'])
        if p.get('cause') == 'initial':
            initial_bins[p['sku']].add(key)
            continue
        if p.get('cause') != 'reorder':
            continue
        b = int(p['batch_id'])
        if key not in first_placed or b < first_placed[key]:
            first_placed[key] = b
        placed_units[key] += float(p.get('qty') or 0.0)
    picked_after: dict = defaultdict(float)
    picked_total = 0.0
    served = 0.0
    picked_skus: set = set()
    for r in pick_rows:
        u = float(r.get('units') or 0.0)
        picked_total += u
        picked_skus.add(r['sku'])
        key = (r['sku'], r['aisle_id'], r['bayX'], r['bayY'])
        fb = first_placed.get(key)
        if fb is not None and int(r['batch_id']) >= fb:
            served += u
            picked_after[key] += u
    placed = float(sum(placed_units.values()))
    repicked = float(sum(min(picked_after.get(k, 0.0), q) for k, q in placed_units.items()))
    # How many bins a PICKED product held at setup: the N in the score's 1/(N+1) dilution.
    # From the initial placements, so it is the run's own count and not a keyframe read.
    n_bins = [len(initial_bins[s]) for s in picked_skus if s in initial_bins]
    return {
        'picked_units': picked_total,
        'served_units': served,
        'served_share': (served / picked_total) if picked_total else None,
        'placed_units': placed,
        'repicked_units': repicked,
        'repicked_share': (repicked / placed) if placed else None,
        'picked_skus': len(picked_skus),
        'bins_per_picked_sku': (sum(n_bins) / len(n_bins)) if n_bins else None,
    }


def _leaf_repick(rt, run, arm: str) -> dict | None:
    """`inbound_repick` for one arm of one leaf, or None when the leaf DB is absent, holds
    no run, or its vintage cannot serve the `pick_bins` query."""
    from Optimization.persistence.Picking_Data import (
        find_run, load_bin_placements, load_pick_bins)
    db = rt.leaf_path(run, 'sim_db', strategy=arm)
    if not os.path.exists(db):
        return None
    run_id = find_run(db, arm)
    if run_id is None:
        return None
    picks = load_pick_bins(db, run_id)
    if not picks:
        return None
    return inbound_repick(picks, load_bin_placements(db, run_id))


def _basis_is_script(rows, census_column: str | None) -> bool:
    """Whether the census was weighted in LINES (the script's planned lines) rather than in
    catalogue FREQUENCY (a worker that could not load its batch list samples inline and
    weights by `freq_by_sku`, which sums to ~1 -- `strategy_runner._build_arm`).  The
    pickle's fingerprint records what the parent handed the worker, not what the worker
    used, so the tool checks the census's own units: a line-weighted census is a count and
    is never strictly between 0 and 1.  A frequency-weighted one must not be priced by a
    line total -- the adjustment would be numerically negligible and the `adjusted: True`
    it printed would be false."""
    if not census_column:
        return True
    for s in rows:
        u = getattr(s, census_column, None)
        if _finite(u) and 0.0 < u < 1.0:
            return False
    return True


class _PlannedWeights:
    """The script's planned-line TOTAL per leaf, from the pair directory's batch pickles.

    `pick_owed_s` prices `W - unservable_weight` lines, where W is the sum of
    `batch_precompute.planned_lines` over the batches the worker fielded; the adjustment
    needs W and nothing records it, so this rebuilds it from the same pickle by the same
    function.  Which pickle: the leaf's `batches_fingerprint` (in `sim_meta.json` since
    2026-09-22) when it has one, else the pickle whose SKUs are the leaf's -- a mixed run
    keeps one list per channel side by side, and the channels' catalogues are disjoint by
    regime, so the match is all-or-nothing.  Cached per pickle, because a campaign list is
    forty batches of ~120k lines and every arm of a leaf shares it.
    """

    #: A pickle whose SKUs cover the leaf's by less than this is not the leaf's list.
    MATCH = 0.99

    def __init__(self, rt, log):
        self.rt, self.log = rt, log
        self._blobs: dict = {}        # path -> (fingerprint, batches, sku set) | None
        self._totals: dict = {}       # (path, n_batches) -> W
        self._skus: dict = {}         # leaf path -> the leaf's SKU set
        self._said: set = set()

    def _blob(self, path):
        if path not in self._blobs:
            from Optimization.simdriver.batch_precompute import read_batches_blob
            got = read_batches_blob(path)
            # The SKU set is built LAZILY (`_blob_skus`): ~4.8M inserts per campaign
            # pickle, and the fingerprint path never reads it.
            self._blobs[path] = None if got is None else [got[0], got[1], None]
        return self._blobs[path]

    def _blob_skus(self, path) -> set:
        b = self._blob(path)
        if b is None:
            return set()
        if b[2] is None:
            b[2] = {s for batch in b[1] for s in batch.items}
        return b[2]

    def _leaf_skus(self, run, arm) -> set:
        """The leaf's catalogue, from one arm's `sku_scores` -- every arm of a leaf fields
        the same inventory, so it is read once per leaf directory, not once per arm."""
        if run.path in self._skus:
            return self._skus[run.path]
        from Optimization.persistence.Picking_Data import find_run, load_sku_scores
        db = self.rt.leaf_path(run, 'sim_db', strategy=arm)
        run_id = find_run(db, arm) if os.path.exists(db) else None
        got = {r['sku'] for r in load_sku_scores(db, run_id)} if run_id is not None else set()
        self._skus[run.path] = got
        return got

    def total(self, cell: str, run, meta: dict, arm: str, n_batches: int) -> float | None:
        try:
            paths = self.rt.glob('batches_cache', cell=cell, pair=run.pair)
        except KeyError:                      # a contract without the artifact
            paths = []
        chosen = None
        want = (meta or {}).get('batches_fingerprint')
        if want:
            chosen = next((p for p in paths if (self._blob(p) or (None,))[0] == want), None)
        elif paths:
            skus = self._leaf_skus(run, arm)
            for p in paths:
                bs = self._blob_skus(p) if skus else set()
                if not bs:
                    continue
                if len(bs & skus) / len(bs) >= self.MATCH:
                    chosen = p
                    break
        if chosen is None:
            key = (cell, run.pair, run.channel)
            if key not in self._said:
                self._said.add(key)
                self.log(f'    !! {cell}/{run.channel or run.config}: no batch list matches this '
                         f'leaf ({len(paths)} pickle(s) in the pair dir); its census cannot be '
                         f'priced and its units rank on the UNADJUSTED score')
            return None
        tk = (chosen, n_batches)
        if tk not in self._totals:
            from Optimization.simdriver.batch_precompute import planned_lines
            self._totals[tk] = float(sum(planned_lines(self._blob(chosen)[1], n_batches).values()))
        return self._totals[tk]


def _adjusted_leaf(rows, metric: dict, planned_weight) -> tuple | None:
    """`(steady-state mean, {batch: adjusted})` for one arm from its raw batch rows, or None
    when the rows carry no reading.  Adjusted per batch by `adjusted_owed` when the metric
    has a census AND the weight is known; otherwise the raw column, so a metric with no
    census (pick labour) still gets its per-batch form for the floor."""
    col, ccol = metric.get('column'), metric.get('census_column')
    if not col:
        return None
    by_batch: dict = {}
    for s in rows:
        v = getattr(s, col, None)
        if not _finite(v):
            continue
        if ccol and planned_weight is not None:
            u = getattr(s, ccol, None)
            v = adjusted_owed(v, u if _finite(u) else 0.0, planned_weight)
            if not _finite(v):
                continue
        by_batch[int(s.batch_id)] = float(v)
    if not by_batch:
        return None
    maxb = max(by_batch)
    lo = maxb - _WIN + 1
    win = [v for b, v in by_batch.items() if b >= lo]
    return float(np.mean(win)), by_batch


def _units_of_cell(rt, cell: str, cell_dir: str, threshold_days: float, log,
                   metric: dict | None = None, weights=None, repick_on: bool = True) -> list:
    """One row per unit of the cell: its rule pair, stock mode, score, census and overage.

    The score and the census are read from the SAME series document in the same pass, and
    both are summed over the unit's two leaves.  They must travel together: a score is only
    a placement result to the extent its census is zero.

    Since 2026-09-22 the score that RANKS is the census-ADJUSTED one (`adjusted_owed`), read
    per batch from the leaf DBs and meaned over the same window the series used; the series
    scalar is kept beside it as `owed_unadjusted`.  A unit whose every leaf could be
    adjusted says `adjusted: True`; one that could not -- no DB, no batch list, a vintage
    without the column -- ranks on the series scalar and says so, because a half-adjusted
    unit would be a cheaper unit for the wrong reason.  `by_batch` is the unit's adjusted
    per-batch score summed over its leaves (None unless every leaf answered), the input
    to `measured_floor`.
    """
    metric = metric or DEFAULT_METRIC
    leaves = _read_leaves(rt, cell_dir)
    if not leaves:
        return []
    by_pair: dict = defaultdict(dict)          # inventory pair -> {channel: (run, meta, series)}
    for run, meta, series in leaves:
        by_pair[run.pair][run.channel] = (run, meta, series)
    rows = []
    for pair, per_channel in sorted(by_pair.items()):
        have = {ch: {s.get('key') for s in ser.get('strategies', [])}
                for ch, (_run, _m, ser) in per_channel.items()}
        secs = {ch: {s.get('key'): s.get(metric['field']) for s in ser.get('strategies', [])}
                for ch, (_run, _m, ser) in per_channel.items()}
        cens = {ch: {s.get('key'): (s.get(metric['census_field'])
                                    if metric.get('census_field') else None)
                     for s in ser.get('strategies', [])}
                for ch, (_run, _m, ser) in per_channel.items()}
        exct = {ch: {s.get('key'): (s.get(metric['exact_field'])
                                    if metric.get('exact_field') else None)
                     for s in ser.get('strategies', [])}
                for ch, (_run, _m, ser) in per_channel.items()}
        runs_by_channel = {ch: run for ch, (run, _m, _ser) in per_channel.items()}
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
            # THE ADJUSTED SCORE, per leaf from the raw batch rows.
            adj_ss, by_batch, pw = [], defaultdict(float), {}
            complete = owed is not None
            for ch, arm in halves.items():
                run, meta, _ser = per_channel[ch]
                got = _leaf_batch_rows(rt, run, arm) if complete else None
                if not got:
                    complete = False
                    break
                brs, n_planned = got
                w = None
                if metric.get('census_column') and weights is not None:
                    if not _basis_is_script(brs, metric['census_column']):
                        log(f'    !! {cell}/{ch}/{arm}: its census is frequency-weighted (the '
                            f'worker sampled inline), so no line total can price it; the unit '
                            f'ranks on the UNADJUSTED score')
                        complete = False
                        break
                    w = weights.total(cell, run, meta, arm, n_planned)
                    if w is None:
                        complete = False
                        break
                pw[ch] = w
                got = _adjusted_leaf(brs, metric, w)
                if got is None:
                    complete = False
                    break
                adj_ss.append(got[0])
                for b, v in got[1].items():
                    by_batch[b] += v
            store_arm = halves.get(RULE_PAIR_ORDER[0])
            rule_pair = [_rule_of(halves.get(ch)) for ch in RULE_PAIR_ORDER]
            strat = STRATEGY_BY_KEY.get(store_arm) if store_arm else None
            # THE RE-PICK SHARE, per leaf: the sensitivity the score can have at all.
            repick = {ch: _leaf_repick(rt, per_channel[ch][0], arm)
                      for ch, arm in halves.items()} if repick_on else None
            rows.append({
                'cell': cell, 'pair': pair, 'arm_pair': arm_pair,
                'arms': {ch: halves.get(ch) for ch in RULE_PAIR_ORDER},
                'rule_pair': rule_pair,
                'stock_mode': getattr(strat, 'initial', None),
                'inbound_repick': repick,
                'trailers': _unit_trailers(site_db),
                'owed_seconds': (sum(adj_ss) if complete else owed),
                'owed_unadjusted': owed,
                'adjusted': bool(complete and metric.get('census_column')),
                'planned_weight': {ch: pw.get(ch) for ch in RULE_PAIR_ORDER} if complete else None,
                'by_batch': dict(by_batch) if complete else None,
                'unservable': unserv,
                'exact_s_per_unit': exact,
                'overage_days': _unit_overage(rt, runs_by_channel, halves, site_db,
                                              threshold_days, log),
            })
    return rows


def _label(rule_pair) -> str:
    return '/'.join(str(r) for r in rule_pair)


def _site_crews(spec: dict) -> dict:
    """{pair: {'receiving_crew', 'put_crew', 'day_seconds', 'receiving_expected_utilization'}}
    from the run spec's derived staffing record, or {} on a run that derived none."""
    out = {}
    for pair, rec in ((spec.get('staffing') or {}).get('derived') or {}).items():
        if not isinstance(rec, dict):
            continue
        recv, put = rec.get('receiving') or {}, rec.get('put') or {}
        out[pair] = {
            'receiving_crew': recv.get('crew'), 'put_crew': put.get('crew'),
            'day_seconds': rec.get('day_seconds'),
            'receiving_expected_utilization': recv.get('expected_utilization'),
            'provenance': rec.get('provenance'),
        }
    return out


# ── the ranking, over a finished run ──────────────────────────────────────────────────

def rank(base_dir: str, *, k: int = DEFAULT_K, noise_floor_pct: float = DEFAULT_NOISE_FLOOR_PCT,
         census_material_pct: float = DEFAULT_CENSUS_MATERIAL_PCT,
         primary_pair: tuple | None = None, declared_floor: bool = False, log=print) -> dict:
    """Rank the cells of a finished, analyzed, coupled phase-2 run and write the artifact.

    `noise_floor_pct` is the DECLARED floor, used when the paired per-batch series cannot be
    read for a rule pair or when `declared_floor` forces it; otherwise the floor is
    `measured_floor`'s, per rule pair, and the artifact says which applied.
    """
    rt, cells = _tree_for(base_dir)
    layout = getattr(rt, 'layout', None) or {}
    from Optimization.runschema.sim_manifest import _load_run_spec
    spec = _load_run_spec(os.path.abspath(base_dir)) or {}
    if len(cells) < 2:
        raise SystemExit(f'{base_dir}: {len(cells)} cell(s); an unloading ranking needs at least '
                         f'two cells to rank, and a ranking of one would still print a rank 1')

    log(f'Unload ranking over {base_dir}')
    metric = _metric_for(layout, log)
    log(f'  metric: {metric["field"]} ({metric["quantity"]}), declared by {metric["declared_by"]}')
    weights = _PlannedWeights(rt, log)
    cell_names = [c for c, _d in cells]
    reference = layout.get('reference') if layout.get('reference') in cell_names else cell_names[0]
    units: list = []
    thresholds: dict = {}
    analyzed = False
    for cell, cell_dir in cells:
        thr = _threshold_for(layout, spec, cell, log)
        thresholds[cell] = thr
        rows = _units_of_cell(rt, cell, cell_dir, thr, log, metric=metric, weights=weights)
        analyzed = analyzed or bool(_read_leaves(rt, cell_dir))
        n_adj = sum(1 for r in rows if r['adjusted'])
        log(f'  {cell}: {len(rows)} unit(s) at threshold {thr:g} d'
            + (f', {n_adj} census-adjusted' if rows else ''))
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
    by_pair_cell: dict = defaultdict(lambda: {'score': 0.0, 'raw': 0.0, 'census': 0.0,
                                              'overage': 0.0, 'exact': 0.0,
                                              'exact_missing': False, 'n': 0, 'missing': False,
                                              'adjusted': True, 'by_batch': defaultdict(float),
                                              'batch_missing': False, 'trailers': 0})
    for u in units:
        rec = by_pair_cell[(_label(u['rule_pair']), u['cell'])]
        rec['n'] += 1
        if _finite(u['owed_seconds']):
            rec['score'] += u['owed_seconds']
        else:
            rec['missing'] = True
        rec['raw'] += u['owed_unadjusted'] if _finite(u.get('owed_unadjusted')) else 0.0
        rec['adjusted'] = rec['adjusted'] and bool(u.get('adjusted'))
        # The per-batch form, summed over the pair's units, for the floor -- and None for
        # the cell unless EVERY unit answered, for the same reason a missing reading poisons
        # the score: a cell measured on fewer units would carry a narrower interval.
        if u.get('by_batch'):
            for b, v in u['by_batch'].items():
                rec['by_batch'][b] += v
        else:
            rec['batch_missing'] = True
        rec['census'] += u['unservable'] if _finite(u.get('unservable')) else 0.0
        if _finite(u.get('exact_s_per_unit')):
            rec['exact'] += u['exact_s_per_unit']
        else:
            rec['exact_missing'] = True
        rec['overage'] += u['overage_days'] if _finite(u['overage_days']) else 0.0
        rec['trailers'] += u['trailers'] or 0
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

    rankings, chosen, verdicts, floors = {}, {}, {}, {}
    worst_census_pct = 0.0
    for lbl in pairs:
        # THE FLOOR, per rule pair: measured from the paired per-batch series when every
        # cell of the pair carried one, else the declared value, and the record says which.
        per_batch = {cell: dict(rec['by_batch'])
                     for (l, cell), rec in by_pair_cell.items()
                     if l == lbl and not rec['batch_missing'] and not rec['missing']}
        floor = measured_floor(per_batch, reference) if not declared_floor else {
            'floor_pct': None, 'reference': reference, 'cells': {},
            'source': 'declared by --declared-floor'}
        applied = floor['floor_pct'] if floor['floor_pct'] is not None else noise_floor_pct
        floors[lbl] = dict(floor, applied_pct=applied,
                           declared_pct=noise_floor_pct,
                           source=('measured' if floor['floor_pct'] is not None
                                   else f"declared ({floor['source']})"))
        entries = [{'cell': cell, 'owed_seconds': (None if rec['missing'] else rec['score']),
                    'owed_unadjusted': rec['raw'], 'adjusted': rec['adjusted'],
                    'unservable': rec['census'],
                    'census_pct': (100.0 * rec['census'] / rec['score']
                                   if rec['score'] else (0.0 if not rec['census'] else None)),
                    'overage_days': rec['overage'], 'n_units': rec['n'],
                    'trailers': rec['trailers'],
                    'gap_pct': (floor['cells'].get(cell) or {}).get('gap_pct'),
                    'gap_ci_pct': (floor['cells'].get(cell) or {}).get('ci_pct')}
                   for (l, cell), rec in by_pair_cell.items() if l == lbl]
        ranked = rank_cells(entries, applied)
        rankings[lbl] = ranked
        verdicts[lbl] = discriminating(ranked)
        chosen[lbl] = [e['cell'] for e in ranked if e['rank'] is not None][:k]
        for e in ranked:
            if e['census_pct'] is None:
                worst_census_pct = math.inf
            else:
                worst_census_pct = max(worst_census_pct, e['census_pct'])
        log(f'\n  rule pair {lbl}:  floor {applied:.4f}% ({floors[lbl]["source"]})'
            + ('' if verdicts[lbl] else '   !! ONE TIE GROUP -- the score did not separate '
                                        'these cells'))
        for e in ranked:
            # Signed: inside a tie group the order is overage, so the score can RISE to
            # the next rank.
            m = '' if e['margin_pct'] is None else f'  {e["margin_pct"]:+.3f}% to next'
            sc = 'n/a' if e['owed_seconds'] is None else f"{e['owed_seconds']:,.1f}"
            cp = 'n/a' if e['census_pct'] is None else f"{e['census_pct']:.3f}%"
            ci = ('' if not e.get('gap_ci_pct') else
                  f"  gap {e['gap_pct']:+.4f}% [{e['gap_ci_pct'][0]:+.4f}, {e['gap_ci_pct'][1]:+.4f}]")
            log(f"    {str(e['rank']):>4}  {e['cell']:<20} {sc:>14} s owed"
                f"{'' if e['adjusted'] else ' (unadjusted)'}  census {cp:>8}  "
                f"overage {e['overage_days']:.3f} d  [{e['decided_by']}]{m}{ci}")

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
            exact_rankings[lbl] = rank_cells(ents, floors[lbl]['applied_pct'])
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

    # THE RE-PICK SHARES per (rule pair, cell, channel), units-weighted over the pair's
    # units: the one number that says whether an unloading rule had anything to work with.
    repick_by: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {'picked_units': 0.0, 'served_units': 0.0, 'placed_units': 0.0,
                 'repicked_units': 0.0, 'n_units': 0, '_bins': [], '_skus': []})))
    for u in units:
        for ch, r in (u.get('inbound_repick') or {}).items():
            if not r:
                continue
            rec = repick_by[_label(u['rule_pair'])][u['cell']][ch]
            for k in ('picked_units', 'served_units', 'placed_units', 'repicked_units'):
                rec[k] += r[k]
            rec['n_units'] += 1
            if r.get('bins_per_picked_sku') is not None:
                rec['_bins'].append(r['bins_per_picked_sku'])
            if r.get('picked_skus') is not None:
                rec['_skus'].append(r['picked_skus'])
    inbound_repick_doc = {
        lbl: {cell: {ch: dict({k: v for k, v in rec.items() if not k.startswith('_')},
                              served_share=(rec['served_units'] / rec['picked_units']
                                            if rec['picked_units'] else None),
                              repicked_share=(rec['repicked_units'] / rec['placed_units']
                                              if rec['placed_units'] else None),
                              bins_per_picked_sku=(sum(rec['_bins']) / len(rec['_bins'])
                                                   if rec['_bins'] else None),
                              picked_skus=(max(rec['_skus']) if rec['_skus'] else None))
                     for ch, rec in by_ch.items()}
               for cell, by_ch in by_cell.items()}
        for lbl, by_cell in repick_by.items()}
    for lbl in sorted(inbound_repick_doc):
        ref = inbound_repick_doc[lbl].get(reference) or {}
        if ref:
            log(f'\n  re-pick under {lbl} in {reference}: '
                + ', '.join(f"{ch}: {v['served_share']:.1%} of picked units from inbound bins, "
                            f"{v['repicked_share']:.1%} of placed units picked again"
                            for ch, v in sorted(ref.items())
                            if v['served_share'] is not None))

    agreement = rank_agreement(rankings, control=rider)
    if agreement['agree'] is False:
        log(f'\n  !! {agreement["note"]}')
    rider_control = (control_verdict(rankings[rider], reference) if rider in rankings
                     else None)
    if rider_control is not None:
        log(f'\n  rider {rider!r} as control against {reference!r}: '
            f'{len(rider_control["inert"])} inert '
            f'({", ".join(rider_control["inert"]) or "none"}), '
            f'{len(rider_control["moved"])} moved')
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
        'version': 2,
        'written': datetime.now().isoformat(timespec='seconds'),
        'run': {
            'base': os.path.basename(os.path.abspath(base_dir).rstrip(os.sep)),
            'spec': layout.get('spec'), 'schema_id': layout.get('schema_id'),
            'repo_commit': spec.get('repo_commit'), 'n_batches': spec.get('n_batches'),
            'cells': [c for c, _d in cells], 'pairs': layout.get('pairs'),
            'reference': reference, 'coupled': True,
            # THE SITE CREWS the era derived, per inventory pair, copied from the run spec's
            # staffing record so a page can cite the receiving crew that worked the doors
            # (a factor register prints `recv_crew_size: 0`, which means "no AUTHORED crew"
            # and reads as "no crew" to anyone who does not know the era).
            'site_crews': _site_crews(spec),
        },
        'metric': {
            'quantity': metric['quantity'], 'series_field': metric['field'],
            'batch_column': metric.get('column'), 'declared_by': metric['declared_by'],
            'units': 'seconds -- the steady-state mean of the per-batch column, summed over '
                     'the unit\'s two leaves and the inventory pairs; a cell\'s score for a '
                     'rule pair sums that pair\'s units (both stock modes). For pick_owed_s: '
                     'what the run\'s PLANNED batches would cost served from where the stock '
                     'stands.',
            'direction': 'lower is better',
            'why': 'a STATE read, not a flow. Every flow total (labour, unload seconds, '
                   'throughput) is invariant to an unloading ORDER over a window long '
                   'enough to unload everything, which is why the 2026-09-18 ranking on '
                   'ss_prod_total came back an exact tie across ten cells.',
            'adjustment': {
                'applies': bool(metric.get('census_column')),
                'rule': 'owed * W / (W - unservable), per batch, W = the script\'s planned '
                        'lines rebuilt from the pair\'s batch pickle by '
                        'batch_precompute.planned_lines',
                'why': 'the score prices a line with no shelf stock at ZERO, so a cell that '
                       'leaves more in the yard reads cheaper for it; on the 2026-09-20 '
                       'campaign that was ~35% of the fifo-vs-gforecast gap. Pricing each '
                       'unpriced line at the leaf\'s own mean priced line charges it what '
                       'the leaf measured, nothing invented. `owed_unadjusted` keeps the '
                       'raw scalar beside it; a unit that could not be adjusted says '
                       '`adjusted: false` and ranks on the raw one.'},
            'tie_break': {'quantity': TIE_QUANTITY,
                          'units': 'trailer-days past the cell\'s free threshold, whole run, '
                                   'from the SITE yard through frames._ydf',
                          'direction': 'lower is better',
                          'applies_within_pct': 'per rule pair: noise_floor[pair].applied_pct',
                          # The threshold each cell's overage was folded against: the CELL's
                          # own record (a phase-2 axis carries it per cell), which overrides
                          # the run-level `inbound_fee_threshold_days` a factor register
                          # prints.  Recorded here so a page can cite the value it used.
                          'threshold_days': thresholds},
            'noise_floor_pct': noise_floor_pct,
            'noise_floor_note': 'the DECLARED floor, applied only where the paired per-batch '
                                'series could not be read. Where it could, the floor is '
                                'MEASURED per rule pair (see noise_floor): the widest 95% '
                                'moving-block bootstrap half-width of any cell\'s mean paired '
                                'per-batch gap to the reference, so two cells closer than any '
                                'cell\'s own uncertainty tie and the overage decides.',
            'census': {'quantity': metric.get('census_quantity'),
                       'series_field': metric.get('census_field'),
                       'units': 'planned lines with no shelf stock, summed like the score',
                       'material_pct': census_material_pct,
                       'note': 'Reported beside the score and priced INTO it only through '
                               'the adjustment above, never as a fabricated penalty. A run '
                               'whose census is material was decided by availability '
                               'rather than by placement and this tool names no cells. '
                               '`census_pct` divides lines by the ADJUSTED seconds, so the '
                               'material threshold reads against a denominator a few tenths '
                               'of a percent larger than the raw score\'s.'},
        },
        'noise_floor': floors,
        'inbound_repick': inbound_repick_doc,
        'inbound_repick_note': 'per rule pair, cell and channel, units-weighted over the '
                               'pair\'s units (both stock modes). served_share = units picked '
                               'from bins a reorder placed earlier in the window / units '
                               'picked; repicked_share = of the units reorder placed in the '
                               'window, those picked again before it ended (per bin, '
                               'min(picked after placement, placed)). Unit grain, any pick '
                               'after the placement. The leverage an unloading rule can have '
                               'at all: it changes only which empty bin a placed pack takes.',
        'discriminating': verdicts,
        'discriminating_note': 'per rule pair: false when every cell landed in ONE tie '
                               'group, i.e. the score did not separate the policies. A '
                               'result, not a failure -- but not a ranking either.',
        'rank_agreement': agreement,
        'rider_control': rider_control,
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
        # The per-batch vectors stay in memory: forty floats a unit is not a record, and the
        # floor block already carries what they were reduced to.
        'units': [{k: v for k, v in u.items() if k != 'by_batch'} for u in units],
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
    ap.add_argument('--declared-floor', action='store_true',
                    help='apply --noise-floor-pct even where the per-batch series could be '
                         'read (default: the floor is MEASURED per rule pair and the declared '
                         'value is the fallback)')
    a = ap.parse_args()
    primary = tuple(a.pair.split(',')) if a.pair else None
    sys.stdout.reconfigure(errors='replace')
    doc = rank(a.base_dir, k=a.k, noise_floor_pct=a.noise_floor_pct,
               census_material_pct=a.census_material_pct, primary_pair=primary,
               declared_floor=a.declared_floor)
    # The record is written either way -- the census evidence is the most useful thing this
    # tool produces on a run it cannot rank -- but the exit code has to carry the refusal,
    # or a campaign script reads a document with an empty `chosen` as a successful ranking.
    if doc.get('refused'):
        sys.exit(2)


if __name__ == '__main__':
    main()
