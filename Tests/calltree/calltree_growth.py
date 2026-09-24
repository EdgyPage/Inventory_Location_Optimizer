"""
calltree_growth.py — size-ladder runner + complexity-exponent fitting.

The O(n²)-hidden-at-small-scale defense: run a scenario across a ladder of input sizes and
fit log-log slopes. Two instruments, sharp one first:

  CALL COUNTS   exact and deterministic under fixed seeds, so calls(n) fits cleanly. A
                function whose count exponent is well above the ladder knob's expected
                exponent is a super-linear suspect even when its wall share at small n
                is invisible.
  WALLS         untraced per-section wall times corroborate (noisier — machine effects).

Rigor levels:
  --ladder meso   traced in-process rungs (minutes). Source of the count exponents.
  --ladder deep   real `run_simulation` subprocess per rung with --workers N
                  (default 18) — true-scale walls including sqlite/spawn/parallel
                  effects; sections parsed from each run's own log (macro adapter).
                  A full ladder is the occasional ~1-hour rigorous session; use
                  --dry-run to print the planned rungs and commands first.

A ladder also carries a CONFIGURATION, not just a size.  Without one the put-away and
receiving machinery is structurally dead in every rung -- `build_assets` never enables timing,
never swaps the queue set, never builds a dock -- so a fix to `_admit_held` or `HeldItems` had
no coverage from any runnable ladder at all.  `--config` fixes that; see `CONFIGS`.

Usage:
    python Tests/calltree/calltree_growth.py --ladder meso --seed 42
    python Tests/calltree/calltree_growth.py --ladder meso --knob batches
    python Tests/calltree/calltree_growth.py --knob skus --config split_staging4
    python Tests/calltree/calltree_growth.py --ladder deep --workers 18 --dry-run

Output: out/growth_<ladder>_<knob>_s<seed>.json + a fitted-exponent offender table on
stdout + log-log PNG (out/render/growth_<...>.png).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import calltree_scenarios as scenarios
from Warehouse.kernel import perf_probe as _perf
from calltree_tracer import CallTreeTracer, SECTIONS

_OUT_DIR = os.path.join(_HERE, 'out')

# Ladder definitions: knob -> list of (label_value, build_kwargs, run_kwargs).
# The scaled knob should produce roughly-linear growth in most per-item work; the fitted
# exponent is read AGAINST that expectation (thresholds below).
_MESO_LADDERS: dict[str, list[dict]] = {
    'skus':    [dict(n_skus=n) for n in (500, 1_000, 2_000, 4_000, 8_000)],
    'bins':    [dict(n_skus=2_000, bins_per_aisle=b) for b in (40, 60, 100, 160, 240)],
    'batches': [dict(n_skus=1_000, n_batches=n) for n in (5, 10, 20, 40, 80)],
    'pickers': [dict(n_skus=1_000, n_pickers=k) for k in (4, 8, 12, 16, 20)],
    # THE YARD KNOB: tighten the receiving whistle at a FIXED catalogue, so the only thing that
    # moves is yard depth.
    #
    # Why not `skus` for the inbound cells.  `plan_order` is O(T^2) in yard depth T, and the skus
    # knob moves four things at once: catalogue size, units per trailer, trailer count, AND the
    # warehouse (`plan_warehouse` sizes to the catalogue, so every tier the evaluator scans grows
    # too).  An exponent fitted against `skus` cannot separate "more trailers" from "bigger
    # loads" from "bigger tiers" -- the same conflation `_PER_PLACEMENT`'s docstring records
    # costing a round of the 2026-08 work.  Holding the catalogue fixed and starving only the
    # service side isolates T.
    #
    # Why the whistle and not doors/crew/trailer type: it is the ONLY lever that stands a yard at
    # all.  `_unload_split` has exactly one exit that is not "nothing workable anywhere"
    # (receiving.py:1078), so with no whistle every freed door immediately pulls the next yard
    # trailer and the yard empties inside every drain.  A doors x crew x trailer-type sweep moved
    # final yard depth not at all; the whistle took it 0 -> 2 -> 38 -> 59.
    #
    # FIT AGAINST MEASURED DEPTH, NOT AGAINST THE KNOB.  The whistle-to-depth map is nonlinear
    # and saturating; the depth-to-cost map is the one under test.  The rung's x is the mean
    # standing yard at a drain's freeze, read off the inbound probe (`yard_T_sum / inb_drains`).
    # It was `place_loads / plan_orders` inverted from T(T+1) until the yard plan went lazy
    # (O1 of `.scratch/inbound-fullscale-perf/`), after which that ratio stopped being T's.
    #
    # Seconds are absolute and therefore CATALOGUE-SPECIFIC: these are calibrated for n_skus=600
    # on `_INBOUND_RECIPE`. A different catalogue size needs re-calibration, because what matters
    # is the whistle as a fraction of that rung's own uncapped receiving makespan.
    # THE YARD LADDER MEASURES YARD DEPTH.  Until 2026-09-20 it varied `recv_deadline`
    # 400s -> 50s at 600 SKUs and recovered a measured T of 2.29, 2.29, 2.29, 2.37, 2.37 --
    # a 3.4% span, over which it nonetheless fitted exponents and reported r2 = 1.00.  Every
    # `--knob yard` number ever taken from it is a two-point fit.  Probed that day, with the
    # end-of-run standing yard as the readout:
    #
    #     recv_deadline 80 -> 50, 600 skus, 4 doors ......... depth 0 -> 0   (no effect)
    #     dock_doors 4 -> 1, 600 skus ...................... depth 0 -> 0   (no effect)
    #     n_skus 600 -> 2,400, 1 door ...................... depth 0 -> 21
    #     n_skus 2,400, doors 4 -> 1 ....................... depth 18 -> 21 (arrival-bound)
    #
    # So depth is set by ARRIVAL VOLUME, and the deadline whistle does not touch it.  Scaling
    # `n_skus` would move it, but that also rebuilds the warehouse, which confounds yard depth
    # with catalogue size -- and the `skus` knob already measures the latter.  The ARRIVAL LEAD
    # separates them: it spreads the same reorders over more time at an IDENTICAL catalogue,
    # so the only thing moving across these rungs is how many trailers stand at once.
    #
    #     lead 960 -> T ~ 2     lead 240 -> T ~ 15     lead 0 -> T ~ 32
    #     lead 480 -> T ~ 4     lead 120 -> T ~ 22
    #
    # T ~ 2 to 32, which brackets the campaign's measured 16.7-17.0 mean (25 max).  What that
    # buys is the whole point: `plan_order` costs T(T+1) `place_load` calls, so these rungs
    # span 6 -> 1,056 of the work that actually grows -- 176x, against the old ladder's 1.00x.
    'yard':    [dict(n_skus=2_400, n_batches=10, dock_doors=1, door_team=1,
                     recv_deadline=80.0, lead_minutes=lm, lead_spread=sp)
                for lm, sp in ((960.0, 0.7), (480.0, 0.7), (240.0, 0.7),
                               (120.0, 0.5), (0.0, 0.0))],
}
# run_simulation args per rung; sized so 5 rungs fit ~an hour at 18 workers.
#
# THE BIN CAPS ARE GONE, and that is a fix rather than a simplification.  Every rung used to
# carry `s_max_bins`/`ff_max_bins`, and on 2026-09-16 all three runnable rungs failed with
# `UnfieldableRequirement` -- the caps bind BELOW the era's declared stock levels, so the planner
# refuses rather than fielding under the line floor.  That is the documented behaviour of a cap
# now, not a regression: `INBOUND_PERF_FINDINGS.md` records `run_fullfid` refusing for exactly
# this reason and concludes "NO cap value would have worked", because a smaller warehouse raises
# lines/day, which grows the levels, which needs more bins.
#
# `--coverage-days` is the lever that actually shrinks a run: it lowers the DECLARATION, so the
# warehouse the planner sizes to comes down with it.  2 days against the production default of 10.
_DEEP_LADDER = [
    dict(max_skus=10_000, coverage_days=2.0, n_batches=15),
    dict(max_skus=20_000, coverage_days=2.0, n_batches=15),
    dict(max_skus=40_000, coverage_days=2.0, n_batches=15),
    # 60k is not a doubling, and that is the point.  The `save_s` knee sits somewhere in
    # 40k..80k, and with only 2x rungs a knee is one data point -- indistinguishable from
    # "this machine, that afternoon".  A mid rung brackets it.
    dict(max_skus=60_000, coverage_days=2.0, n_batches=15),
    dict(max_skus=80_000, coverage_days=2.0, n_batches=15),
]

# Offender thresholds: exponent above which a fit is flagged, per instrument.
FLAG_COUNT_EXP = 1.30
#: How far the last local exponent must sit from the first before the DIRECTION of a
#: series is called, rather than treated as noise between rungs.  0.25 is wide enough
#: that the four converging offenders in the 2026-09-16 meso artifact all clear it
#: (smallest gap 0.30, `_aisle_best` diverging by 0.15 stays 'sustained') and narrow
#: enough to catch a ratio a full rung before it reaches its ceiling.
FLAG_TREND_DELTA = 0.25
#: An ARM's total is superlinear above this.  Lower than FLAG_COUNT_EXP on purpose: an arm
#: total is a sum of real seconds over a whole simulation, so 1.25 is already a large
#: effect, and the deep ladder's linear arms sit at 0.91-1.04 with the two genuinely
#: superlinear ones (cmin, cmax) at 1.29 -- measured, save excluded.
FLAG_ARM_EXP = 1.20
FLAG_TIME_EXP  = 1.50
MIN_R2         = 0.90       # don't flag garbage fits
MIN_CALLS      = 200        # ignore trivial functions at the largest rung
#: The smallest POSITIVE wall a fit may rest on.  The existing `max(ys) < 0.01` gate asks
#: whether a section ever got big; this asks whether the fit's own anchor was real.  The
#: deep ladder's top offender was `t_task` at k=3.916 over walls
#: [0.0, 0.0004, 0.0356, 0.0927] -- 0.4 ms is timer noise, and it outranked everything
#: true in the artifact.  A suppressed fit is RECORDED in report['suppressed'], never
#: dropped: 'too small to fit here' is itself a finding, and silently vanishing is how
#: this repo has lost findings before.
MIN_WALL_S     = 0.005
#: Offenders are ranked by their PROJECTED size this many times past the top rung, not by
#: bare exponent.  k answers 'how fast', magnitude answers 'how much', and only the two
#: together answer 'what should I fix'.  See `_severity_sort`.
PROJECT_FACTOR = 10.0


def _fit_loglog(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """(slope, r²) of ln(y) ~ ln(x); requires positive xs/ys, >= 3 points."""
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 3:
        return float('nan'), 0.0
    n  = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx == 0:
        return float('nan'), 0.0
    slope = sxy / sxx
    syy = sum((p[1] - my) ** 2 for p in pts)
    r2 = 0.0 if syy == 0 else (sxy * sxy) / (sxx * syy)
    return slope, r2


def _counts_under(tree: dict, parent: str) -> dict[str, int]:
    """name -> calls, over the DIRECT children of every node named `parent`.

    `_flat_counts` sums a name across all paths, which is right for a growth fit and wrong for
    a flow.  `PutQueue.admit` is called both by `_queue` (an arrival) and by `_admit_held` (a
    retry), and only the second is the refill loop's cost -- the number that went from 13
    million to 53 thousand.  Summed together they are one meaningless total.
    """
    out: dict[str, int] = {}

    def walk(node: dict) -> None:
        for c in node.get('children', []):
            if c.get('kind') == 'ext':
                continue
            if c['name'] == parent:
                for g in c.get('children', []):
                    if g.get('kind') == 'ext':
                        continue
                    out[g['name']] = out.get(g['name'], 0) + g['calls']
            walk(c)

    walk(tree)
    return out


def _counts_anywhere_under(tree: dict, parent: str) -> dict[str, int]:
    """name -> calls, over every DESCENDANT of every node named `parent`.

    `_counts_under` above walks DIRECT children, which is right for the put-away flows it was
    written for: `PutQueue.admit` really is called straight from `_admit_held`.  It is wrong
    for anything reached through a dispatcher, and the inbound entry calls are all reached
    through one -- the real chain is

        YardTransit.yard_order -> priorities.bounded_order -> gain_forecast -> gain.plan_order

    so a direct-child lookup for `plan_order` under `yard_order` matches nothing and reports
    ZERO, which reads as "the yard ranking never ran".  Both anchors were in the tree the whole
    time (`gain:plan_order` 4 calls, `transit:YardTransit.yard_order` 4 calls); only the
    RELATIONSHIP was wrong, and `test_flow_anchors_resolve` cannot see a relationship because it
    resolves symbols against modules and never looks at a tree.

    A separate function rather than a parameter on the old one, so no existing flow can change
    value: `route_calls` and `held_retry_touches` are archived series.

    A descendant is counted once, under the NEAREST enclosing `parent` -- recursion stops at a
    nested `parent`, so two dispatchers cannot double-count the same subtree.
    """
    out: dict[str, int] = {}

    def collect(node: dict) -> None:
        for c in node.get('children', []):
            if c.get('kind') == 'ext' or c['name'] == parent:
                continue                      # a nested parent owns its own subtree
            out[c['name']] = out.get(c['name'], 0) + c['calls']
            collect(c)

    def walk(node: dict) -> None:
        for c in node.get('children', []):
            if c.get('kind') == 'ext':
                continue
            if c['name'] == parent:
                collect(c)
            walk(c)

    walk(tree)
    return out


#: A single step whose local exponent exceeds the median of the earlier steps by this much
#: is a KNEE.  Tuned against the one real instance: `save_s` went +0.37, +0.56, +3.62 per
#: doubling, so the last step clears the earlier median by 3.15.  Set well below that -- the
#: cost of a false knee is one line of output, the cost of a missed one is a production
#: threshold nobody sees.
FLAG_KNEE_JUMP = 1.00


def _local_exponents(xs, ys) -> list[float]:
    """Per-step log-log slopes: `k_i` between consecutive rungs, not one fit over all of them."""
    out = []
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 > 0 and x1 > 0 and y0 > 0 and y1 > 0 and x1 != x0:
            out.append(math.log(y1 / y0) / math.log(x1 / x0))
        else:
            out.append(float('nan'))
    return out


def _trend(xs, ys) -> dict | None:
    """Which WAY the local exponents are going -- the part a single fit cannot say.

    A fitted k answers "what power law describes these rungs"; it cannot answer "is this a
    power law at all".  A bounded ratio climbing toward a ceiling fits one beautifully and
    is not a complexity finding: it is a transient, and the fit it produces is arithmetically
    impossible past the point where the ratio would exceed its bound.

    THE CASE THIS EXISTS FOR, measured.  `AffinityStore.delta_lift_idxs` led every archived
    `skus` ladder back to August at k = 1.56-1.61, r2 = 0.994, and was the round's largest
    remaining candidate.  It is called once per reclaimed bin whose SKU was that aisle's last,
    against `_index_add` once per reclaimed bin unconditionally -- so the ratio between them
    cannot exceed 1.0, and the code guarantees it.  Measured: 0.169, 0.303, 0.506, 0.718,
    0.871, with local exponents falling 1.92 -> 1.30 and the ratio's own falling 0.84 -> 0.28.
    Extending the fit puts the ratio above 1.0 at roughly 13,000 SKUs; the ladder spans 500
    to 8,000, entirely inside the ramp.  Four of the seven flagged offenders in that artifact
    were converging like this, and the ranking put them above the one that was not.

    Returns None below three local exponents -- two points cannot show a direction.
    """
    ks = _local_exponents(xs, ys)
    if len(ks) < 3 or any(k != k for k in ks):
        return None
    first, last = ks[0], ks[-1]
    falling = last <= first - FLAG_TREND_DELTA
    if falling and last < FLAG_COUNT_EXP:
        verdict = 'saturating'          # heading to linear -- probably not a finding
    elif falling:
        verdict = 'settling'            # the FIT overstates; the CLASS is still real
    elif last >= first + FLAG_TREND_DELTA:
        verdict = 'accelerating'
    else:
        verdict = 'sustained'
    out = {'local': [round(k, 2) for k in ks], 'verdict': verdict,
           'first': round(first, 2), 'last': round(last, 2)}
    if verdict == 'saturating':
        # `projected` assumes the fitted k keeps holding, which is what this just denied.
        out['why'] = ('local k is falling, so the fitted k describes a transient and '
                      '`projected` over-reads; find the denominator this is a ratio of '
                      'and check whether that ratio has a ceiling before refactoring')
    elif verdict == 'settling':
        out['why'] = (f'local k is falling but has settled near {last:.2f}, not near 1 -- '
                      f'the single fit overstates the early rungs, and the settled value is '
                      f'the cost class. Quote the LAST local exponent, not the fit.')
    return out


def _knee(xs, ys) -> dict | None:
    """The last step's local exponent against the median of the ones before it.

    Returns a finding when the series ELBOWS rather than curving.  This exists because the
    r-squared gate is exactly wrong for a knee: a clean power law fits well and gets flagged,
    while a threshold crossed between two rungs fits badly and is dropped.  `save_s` on the
    deep ladder -- the biggest super-linear jump anywhere in that artifact -- scored r2=0.77
    and was reported as nothing.
    """
    ks = _local_exponents(xs, ys)
    if len(ks) < 2 or any(k != k for k in ks):
        return None
    last, earlier = ks[-1], sorted(ks[:-1])
    n = len(earlier)
    med = earlier[n // 2] if n % 2 else (earlier[n // 2 - 1] + earlier[n // 2]) / 2
    if last - med < FLAG_KNEE_JUMP:
        return None
    return {'local_exponents': [round(k, 3) for k in ks],
            'last_step_k': round(last, 3), 'earlier_median_k': round(med, 3),
            'at_x': xs[-1]}


def _flows_warning(flows: dict, config: str) -> str | None:
    """The per-rung "nothing was measured" line, or None when something was.

    It reads FLOWS, which is what it is about.  It used to be the `else` of the
    per-ENTRY-CALL branch -- an inbound-only quantity -- so on every non-inbound config it
    fired regardless of the flows, printing "the put-away/receiving path did not execute
    under cfg=split_staging4" two lines below `held_appends=40,720`, and telling the reader
    to switch to the config they were already running.

    That is the failure this package's README records having been burned by once already: a
    run whose held path executed millions of times reported `held: 0`, and the zero was read
    as "the path never ran".  A warning that cries wolf on every rung trains the reader to
    skip the one line that exists to stop them trusting a zero.
    """
    if any(flows.values()):
        return None
    hint = ('' if config == 'split_staging4'
            else ' Use --config split_staging4 to exercise it.')
    return (f'flows: ALL ZERO -- no named flow fired under cfg={config}, so the '
            f'put-away/receiving path was NOT MEASURED here.{hint}')


def _project(ys: list, k: float, factor: float = PROJECT_FACTOR) -> float:
    """The series' last value carried `factor` times further along the ladder.

    y(factor*x) = y(x) * factor**k for a power law, so this needs no refit.  It is a
    projection and nothing more: it assumes the fitted exponent keeps holding, which is
    exactly what a knee does not do -- which is why knees are ranked separately below.
    """
    if not ys or ys[-1] <= 0 or k != k:
        return 0.0
    try:
        return float(ys[-1]) * (factor ** k)
    except OverflowError:
        return float('inf')


#: Cost class, most directly priced first.  Seconds are a cost; a call count is a proxy
#: for one; a ratio is a shape; a knee is a warning that the fit does not hold at all.
#: Ranking ACROSS these by a shared number would invent a common unit that does not
#: exist, so the table groups by class and sorts by magnitude WITHIN each.
_KIND_ORDER = {'section-wall': 0, 'arm-total': 0, 'save-part': 0,
               'call-count': 1, 'flow': 1,
               'per-placement': 2, 'per-row': 2,
               'knee': 3}


#: Where each offender kind's own series lives in the report, so `_trend` can be read off
#: what was already computed.  `section-wall` walls are stored ROUNDED to 4 dp; that is below
#: the MIN_WALL_S floor those offenders must clear, so no flagged series is distorted by it.
_TREND_SERIES = {'call-count':    ('functions', 'counts'),
                 'flow':          ('flows', 'counts'),
                 'per-placement': ('flows_per_placement', 'ratios'),
                 'section-wall':  ('sections', 'walls'),
                 'arm-total':     ('arm_totals', 'values'),
                 'arm-growth':    ('arm_growth', 'values'),
                 'save-part':     ('save_decomposition', 'walls'),
                 'per-row':       ('save_decomposition', 'ratios')}


def _severity_sort(offenders: list) -> list:
    """Group by cost class, then by projected magnitude, then by exponent.

    Replaces a bare `sort(key=-exponent)`, which put a 92 ms section fitted off a 0.4 ms
    anchor above every real finding in the deep artifact.  The project states the rule
    it was breaking: element counts must be weighted by COST CLASS before ranking.
    """
    return sorted(offenders,
                  key=lambda o: (_KIND_ORDER.get(o.get('kind'), 9),
                                 # False sorts first: a series heading TO LINEAR goes last
                                 # in its class, however big its fitted k.  Only that
                                 # verdict demotes -- a series whose local exponents fall
                                 # and then SETTLE near 2 is a clean quadratic, and
                                 # demoting it was this guard's first bug (cmin's
                                 # `score_of`, 9.2M calls, local k 2.41 1.64 2.00 2.02).
                                 # It is still listed either way -- the trend is a
                                 # judgement the reader makes, not one the tool makes.
                                 (o.get('trend') or {}).get('verdict') == 'saturating',
                                 -(o.get('projected') or 0.0),
                                 -(o.get('exponent') or 0.0)))


def _flat_counts(tree: dict) -> dict[str, int]:
    """name -> calls summed across paths (project nodes only)."""
    out: dict[str, int] = {}

    def walk(node: dict) -> None:
        for c in node.get('children', []):
            if c.get('kind') == 'ext':
                continue
            out[c['name']] = out.get(c['name'], 0) + c['calls']
            walk(c)

    walk(tree)
    return out


# ── meso ladder ──────────────────────────────────────────────────────────────

#: Scenario CONFIGURATION a rung may carry, on top of its size. Passed to `build_assets`.
#: An explicit list rather than `**kwargs`: a typo in a ladder definition must fail at
#: `build_assets` rather than being silently dropped, which is what used to happen to every
#: key this function did not name.
_BUILD_KEYS = ('put_timing', 'put_split', 'put_staging', 'put_crew', 'recv_crew',
               'strategy', 'coverage', 'safety', 'target_fill',
               'inbound', 'trailer_type', 'dock_doors', 'lead_minutes', 'lead_spread',
               'lead_seed', 'yard_policy', 'dock_policy', 'local_policy', 'trailer_bound',
               'crew_allocation', 'door_team', 'fee_threshold_days', 'urgency_horizon_days')
#: ...and the two whistles, which go to `run_meso` rather than `build_assets`. These are what
#: make a level GROW, and a level that never grows hides every cost proportional to it.
_RUN_KEYS = ('put_deadline', 'recv_deadline')

#: The four keys the base build reads directly, before the `_BUILD_KEYS` pass-through.
_LADDER_KEYS = ('n_skus', 'bins_per_aisle', 'n_pickers', 'n_batches')
_LEGAL_KEYS  = frozenset(_LADDER_KEYS + _BUILD_KEYS + _RUN_KEYS)


@dataclass(frozen=True)
class LadderConfig:
    """A scenario shape a ladder runs IN, layered under every rung.

    `overlay` is merged UNDER the rung dict, so the rung always owns the axis it scales --
    `--knob bins --config staging4` gets the ladder's `bins_per_aisle`, not the recipe's.
    `rungs` optionally replaces `_MESO_LADDERS` for a knob, because a configuration can make a
    rung far more expensive and the affordable ladder is part of the configuration.
    """

    why    : str
    overlay: dict
    rungs  : dict = field(default_factory=dict)


#: The put-away RECIPE: a tight warehouse that actually refuses placements.  Not a taste
#: preference -- production coverage (10.0/2.0) has enough slack that nothing is ever held, so
#: the held path stays dead however the queues are configured.
_PUT_RECIPE = dict(bins_per_aisle=40, coverage=2.0, safety=0.4)
#: ...and smaller rungs, because staging makes a rung much more expensive and because these are
#: the exact sizes the archived `cfg-*` artifacts used.  Comparability with them is the only
#: external check a reproduction of those numbers has.
_PUT_RUNGS  = {'skus': [dict(n_skus=n) for n in (300, 600, 1_200, 2_400)]}

#: The INBOUND recipe.  Deliberately NOT `_PUT_RECIPE`, and the difference is the point.
#:
#: `_PUT_RECIPE` buys its backpressure by starving the WAREHOUSE (`bins_per_aisle=40`,
#: `coverage=2.0`) — a tight warehouse that refuses placements.  Inbound pressure is a different
#: thing: an arrival/service imbalance at the dock.  Starving the warehouse here would hide the
#: yard behind a put-away backlog, so the warehouse stays roomy and the pressure comes from the
#: receiving whistle instead.
#:
#: Coverage stays at PRODUCTION (10/2).  Measured on this builder at 600 SKUs: coverage 2.0 gives
#: Q median 8 with 12.8% of SKUs at Q=1; coverage 10 gives Q median 39 with 2.5% at Q=1.  Higher
#: coverage also makes every reorder bigger, so it raises the arrival rate — it helps twice, and
#: it is the opposite of what `_PUT_RECIPE` wants.
#:
#: What it CANNOT fix is units-per-bin: `Order.__init__` samples dimensions triangular(3,48,48),
#: mode AT the pallet footprint, so 46% of synthetic SKUs fit exactly one unit per pallet
#: position at EVERY coverage.  That is a property of the synthetic builder, not of the levels.
#: The production generator's own creation plan does not have it (median 4 per pallet, 14.5% at
#: one) — see ticket 02 — which is why the real fix is a generated catalogue, not a knob here.
_INBOUND_RECIPE = dict(bins_per_aisle=100, coverage=10.0, safety=2.0,
                       inbound=True, trailer_type='53', dock_doors=4, recv_crew=2,
                       put_timing=True, trailer_bound=None)
#: Smaller rungs than the put ladder: a standing yard makes every rung far more expensive, and
#: the affordable ladder is part of the configuration (`LadderConfig.rungs` exists for this).
_INBOUND_RUNGS = {'skus': [dict(n_skus=n) for n in (300, 600, 1_200, 2_400)]}

#: Named scenario configurations.  Public: `calltree_memory` imports this.
#:
#: `put_timing=True` is held constant across the 2x2 cells DELIBERATELY.  `build_assets` turns
#: timing on implicitly for `put_split` but not for a bare `put_staging`, so without pinning it
#: "split, no staging" would be timed and "staging, single queue" would not, and the two cells
#: would not be comparable.
#:
#: `baseline_put` is NOT called `baseline`: an archived artifact already carries `cfg-baseline`
#: and it means something else.  Tag values are permanent, and reusing one merges two
#: experiments in `out/index.json` forever.
CONFIGS: dict[str, LadderConfig] = {
    'none': LadderConfig(
        why='plain size ladder; the put-away and receiving machinery never executes',
        overlay={}),

    # -- the ASSIGNMENT-FAMILY cells ------------------------------------------------
    # Every other config here runs `DEFAULT_STRATEGY` ('uni_rank_labor_norsl'), which is a
    # TRAVEL-BALANCED arm.  So `_RankedAssignPool` -- the pool behind tmin, tmax,
    # rank_random and rank_popularity -- was structurally unreachable from every rung, and
    # its per-placement `min(head_D, key=...)` scan over every aisle appeared in no offender
    # table at all.  That is not evidence it is cheap; it is the same blind spot that let a
    # quadratic live in `_admit_held` through every release until `--config` existed.
    #
    # Two cells because the two selectors are different problems: `tmin` uses the plain
    # min/max over head D, `rank_popularity` keys on the LIVE `aisle_demand_sum` that
    # `take` itself increments.
    'ranked_tmin': LadderConfig(
        why='tmin over _RankedAssignPool -- the plain min/max aisle selector, which no '
            'other config reaches',
        overlay=dict(strategy='uni_tmin_norsl')),
    'ranked_popularity': LadderConfig(
        why='rank_popularity over _RankedAssignPool -- the selector that reads the live '
            'aisle_demand_sum its own takes mutate',
        overlay=dict(strategy='uni_rank_popularity_norsl')),
    # -- the CLUSTER family ----------------------------------------------------------
    # The deep ladder's slowest arm at every rung belongs to this family, and its total_s
    # DIVERGES (local k 0.98 -> 1.61) while the 136-arm sum stays flat at k=1.05.  The meso
    # ladder could not see any of it: `_CoDemandPool` and `_ClusterMapPool` are unreachable
    # from every other cell here, exactly as `_RankedAssignPool` was before the two cells
    # above existed.  Two cells because the pools are different code: cluster_map walks
    # favored locations with an intra-aisle compaction pass, cmin/cmax are co-demand.
    'cluster_map': LadderConfig(
        why='_ClusterMapPool -- map favored-location + cohesion + intra-aisle compaction; '
            'held the deep ladder\'s slowest-arm slot at the two smallest rungs',
        overlay=dict(strategy='uni_cluster_map_norsl')),
    'cmin': LadderConfig(
        why='_CoDemandPool (minimum-cluster) -- the co-demand pool, which took the deep '
            'ladder\'s slowest-arm slot at 60,000 SKUs',
        overlay=dict(strategy='uni_cmin_norsl')),
    'baseline_put': LadderConfig(
        why='timed put-away, unbounded floor -- the 2x2 origin cell',
        overlay=dict(_PUT_RECIPE, put_timing=True), rungs=_PUT_RUNGS),
    'split': LadderConfig(
        why='three put streams, unbounded floors -- isolates the cost of the split alone',
        overlay=dict(_PUT_RECIPE, put_timing=True, put_split=True), rungs=_PUT_RUNGS),
    'staging4': LadderConfig(
        why='one queue with a floor of 4 -- isolates backpressure alone',
        overlay=dict(_PUT_RECIPE, put_timing=True, put_staging=4), rungs=_PUT_RUNGS),
    'split_staging4': LadderConfig(
        why='three streams AND a floor of 4 -- the configuration the _admit_held work was '
            'measured on',
        overlay=dict(_PUT_RECIPE, put_timing=True, put_split=True, put_staging=4),
        rungs=_PUT_RUNGS),
    'receiving': LadderConfig(
        why='a dock in front of the put queues',
        overlay=dict(_PUT_RECIPE, put_timing=True, recv_crew=1), rungs=_PUT_RUNGS),
    'dayshift': LadderConfig(
        why="the 200-batch stress run's shape: staged floors, a dock, and both whistles",
        overlay=dict(_PUT_RECIPE, put_timing=True, put_split=True, put_staging=8,
                     recv_crew=1, put_deadline=2_000.0, recv_deadline=30.0),
        rungs=_PUT_RUNGS),

    # ── the INBOUND cells ─────────────────────────────────────────────────────────────
    # Each adds exactly ONE mechanism to the one above it, so an exponent is attributable.
    # Two things are load-bearing across all of them:
    #
    #   `trailer_bound=None`.  `bounded_order` slices the candidate list to `bound` before the
    #   entry sees it (priorities.py:208), which makes `plan_order` CONSTANT-TIME.  A ladder run
    #   under a bound measures the bound, not the greedy.  It is the production DEFAULT, so this
    #   is also the one knob a reader must check before believing any archived inbound artifact.
    #
    #   `recv_deadline`.  The receiving whistle is the ONLY thing that stands a yard.
    #   `_unload_split`'s loop has exactly one exit that is not "nothing workable anywhere"
    #   (receiving.py:1078), so with no whistle every freed door immediately pulls the next yard
    #   trailer and the yard empties inside every drain -- regardless of doors, crew size or
    #   trailer type.  Measured across a doors x crew x trailer-type sweep: all three are
    #   NON-LEVERS without it.  The values below are calibrated for `_INBOUND_RUNGS`' sizes; a
    #   different rung size needs a different whistle, because the quantity that matters is the
    #   whistle as a FRACTION of that rung's uncapped receiving makespan, not its absolute
    #   seconds.  Re-calibrate before trusting a new rung, and see ticket 05.
    # NOT called `inbound_v1`.  `build_assets` wires the STANDING transit (`YardTransit`) only;
    # v1's drain-everything `TrailerTransit` has no scenario support, so a cell claiming to be v1
    # would be a dock and no trailers at all -- a name promising more than the cell delivers,
    # which is the same class of error as a flow anchor that silently reads 0.  This is the
    # roomy-warehouse CONTROL: everything `inbound_yard` has except the yard itself.
    'inbound_off': LadderConfig(
        why='the inbound-OFF control on the inbound recipe: a dock and a put crew, no trailers '
            'and no yard -- the baseline every inbound cell is read against',
        overlay=dict(_INBOUND_RECIPE, inbound=False, trailer_bound=None),
        rungs=_INBOUND_RUNGS),
    'inbound_yard': LadderConfig(
        why='the standing yard under fifo/fifo -- real doors and the space timeline, no gain arm',
        overlay=dict(_INBOUND_RECIPE, recv_deadline=80.0),
        rungs=_INBOUND_RUNGS),
    'inbound_gain_pool': LadderConfig(
        why='THE HEADLINE CELL: gain_forecast on both knobs over a POOL adapter, so every '
            'virtual placement pays _make_pool -- the O(T^2) greedy with the aisle-dict copy',
        overlay=dict(_INBOUND_RECIPE, recv_deadline=80.0,
                     yard_policy='gain_forecast', dock_policy='gain_forecast',
                     strategy='uni_rank_labor_norsl'),
        rungs=_INBOUND_RUNGS),
    'inbound_gain_merge': LadderConfig(
        why='the same arm set over the MERGE adapter (tmin), which opens no pool -- the control '
            'that separates _make_pool cost from the rest of the evaluator',
        overlay=dict(_INBOUND_RECIPE, recv_deadline=80.0,
                     yard_policy='gain_forecast', dock_policy='gain_forecast',
                     strategy='uni_tmin_norsl'),
        rungs=_INBOUND_RUNGS),
    'inbound_gain_bounded': LadderConfig(
        why='inbound_gain_pool WITH a trailer bound -- prices what the existing production knob '
            'already buys, since a bound makes plan_order constant-time',
        overlay=dict(_INBOUND_RECIPE, recv_deadline=80.0,
                     yard_policy='gain_forecast', dock_policy='gain_forecast',
                     strategy='uni_rank_labor_norsl', trailer_bound=8),
        rungs=_INBOUND_RUNGS),
}

for _name, _cfg in CONFIGS.items():      # at import: a typo fails on --help, not mid-ladder
    _bad = set(_cfg.overlay) - _LEGAL_KEYS
    if _bad:
        raise ValueError(f'config {_name!r} names key(s) nothing reads: {sorted(_bad)}')
    for _rl in _cfg.rungs.values():
        for _r in _rl:
            _bad = set(_r) - _LEGAL_KEYS
            if _bad:
                raise ValueError(f'config {_name!r} rung names key(s) nothing reads: '
                                 f'{sorted(_bad)}')

#: FLOWS -- cumulative over the run, read from the TRACED pass.  `(tree name, parent or None)`.
#:
#: A flow proves the path RAN.  The `levels` beside it only say where the backlog FINISHED, and
#: a path that executed thirteen million times and drained by the last batch reports every
#: level as zero -- which is exactly how this ladder reported the held path as dead while its
#: own `counts` recorded `_admit_held: 10,666` in the same artifact.
#:
#: Drift-gated by `test_calltree_anchors.py::test_flow_anchors_resolve`, and the gate matters
#: more here than for SECTION_MAP: a rename makes a flow report 0, and 0 reads as "never ran".
_FLOW_COUNTS: dict[str, tuple[str, str | None]] = {
    'refill_passes'     : ('Inventory_Management:Inventory_Manager._admit_held', None),
    'held_retry_touches': ('put_queue:PutQueue.admit',
                           'Inventory_Management:Inventory_Manager._admit_held'),
    'held_appends'      : ('put_queue:HeldItems.append', None),
    'queue_admissions'  : ('put_queue:PutQueue.admit', None),
    'route_calls'       : ('put_queue:PutQueueSet.route', None),
    'dock_arrivals'     : ('dock:Dock.arrive', None),
    # PLACEMENT side.  `open_pool` counts pool constructions exactly; the pool's own
    # `__init__` walks every candidate once, so `bins_scanned` is the exact number of
    # candidate bins examined.  Together they decompose the prologue cost that a staging
    # floor multiplies: opens x candidates-per-open.
    'pool_opens'        : ('Assignment_Functions:'
                           '_build_travel_balanced_pool_fn.<locals>.open_pool', None),
    'pool_takes'        : ('Assignment_Functions:_TravelBalancedPool.take', None),
    # NO `bins_scanned` counter, and its absence is a RESULT rather than an oversight.
    # Candidate bins used to be countable exactly, because the prologue paid one Python call
    # per candidate: `sort(key=lambda ...)`, and `list.sort` calls a key exactly once per
    # element.  6d862a2 removed every per-candidate Python call -- heapify has no key
    # function and the geometry memo replaced four property calls with one dict lookup -- so
    # there is nothing left for the tracer to see.  Candidate VOLUME is now a derived
    # quantity: `pool_opens x |free list|`, and the free list is not traceable either.
    # If it ever needs measuring again, measure it deliberately; do not reach for
    # `_TravelBalancedPool.__init__`, which counts OPENS and would silently read as bins.

    # ── THE INBOUND PATH ──────────────────────────────────────────────────────────────
    # Countable for the first time: until `build_assets(inbound=True)` existed, every symbol
    # below was structurally dead in every runnable rung, so a ladder reported the whole
    # subsystem as costless and was believed.
    'drains'            : ('receiving:SiteReceiving.receive', None),
    'freezes'           : ('space:SpaceTimeline.freeze', None),
    'view_composes'     : ('site_space:compose_site_view', None),
    # THE ENTRY CALL, and it is the ladder's denominator.  A gain arm sets BOTH knobs to one
    # name, so `plan_order` runs twice per drain -- but over DIFFERENT candidate sets: the yard
    # (unbounded) and the staged set (bounded by `doors`).  Splitting them by parent is the only
    # way to tell an O(T_yard^2) term from an O(doors^2) one; the totals cannot.
    # DEEP, and it has to be: the entry is reached through `priorities.bounded_order` and the
    # arm's registry entry, so the plan is a GRANDCHILD of the ranking call, never a direct
    # child.  Declared direct, both of these read 0 for their whole life.
    #
    # ANCHORED ON `_traced`, THE ONE FRAME EVERY GAIN ENTRY CALLS, NOT ON `plan_order`.  Since
    # O1 (`.scratch/inbound-fullscale-perf/`) a drain reads the YARD ranking as a pull queue
    # (`YardTransit.yard_ranking`) over the generator `plan_order_iter`: creating a generator
    # runs no frame, and its rounds execute later, under `LazyRanking.popleft`.  Anchored on
    # `plan_order` the yard flow read 0 again.  `_traced` runs at the ranking call in both
    # forms, once per entry call.
    'yard_plans'        : ('gain:_traced', 'transit:YardTransit.yard_ranking', True),
    'dock_plans'        : ('gain:_traced', 'transit:YardTransit.dock_order', True),
    'plan_orders'       : ('gain:_traced', None),
    # The greedy's fan-out.  An EAGER plan costs exactly T(T+1) `place_load` calls; a lazy
    # yard plan costs p(2T - p + 1) for the p trailers the drain pulled, so the ratio
    # `place_loads / plan_orders` no longer inverts to T and the yard knob reads its depth
    # off the inbound probe instead (`_measured_yard_depth`).
    'place_loads'       : ('gain:_Evaluator.place_load', None),
    # The suspect.  Each open copies up to six whole-warehouse aisle dicts (`AISLE_COPIERS`);
    # measured at ~5 opens per `place_load`, so opens run at roughly 5*T^2 per entry call.
    'pool_rebuilds'     : ('gain:_Evaluator._make_pool', None),
    'tier_sorts'        : ('gain:_Evaluator._tier_sorted', None),
    'avail_builds'      : ('gain:_Evaluator._avail', None),
    'window_aggs'       : ('gain:_window_rates', None),
    'unload_prices'     : ('dock:Dock.unload_seconds', None),
    'team_probes'       : ('dock:Dock.team_next_free', None),
    'trailer_plans'     : ('transit:YardTransit.planned_lots', None),
}

#: FLOWS worth reporting PER PLACEMENT as well as absolutely.
#:
#: This is the sharpest lesson of the 2026-08 growth work.  An absolute count exponent
#: conflates "each unit of work got more expensive" with "there are more units of work", and
#: those have different fixes.  Under a staging floor `bins_scanned` fitted k=1.74 -- which says
#: nothing about which one it was.  The ratio said it immediately: bins-scanned PER PLACEMENT
#: went 2.47 -> 71.7 while takes-per-placement stayed flat, so per-placement work was unchanged
#: and the prologue was simply being re-paid.  A denominator turns a number into a claim.
_PER_PLACEMENT = ('pool_opens', 'pool_takes', 'refill_passes',
                  'held_retry_touches', 'held_appends')

#: FLOWS reported PER ENTRY CALL, denominated on `plan_orders`.
#:
#: Placements are the wrong denominator for the inbound evaluator, and using them would repeat
#: exactly the mistake `_PER_PLACEMENT` exists to prevent.  `plan_order` is a RANKING: its cost is
#: set by how many trailers it ranks, not by how many units eventually get binned -- and the two
#: move in OPPOSITE directions once the yard stands.  Measured at 600 SKUs over a whistle sweep:
#: pool opens rose 8,496 -> 65,496 while placements FELL 8,519 -> 5,230, so a per-placement ratio
#: would have read as a 12x blow-up made of two different effects stacked on each other.
#:
#: `place_loads / plan_orders` is the sharpest number this ladder produces and needs no fitting to
#: read: `plan_order` costs exactly T(T+1) `place_load` calls, so the ratio IS the measured T^2.
#: Invert it (`T = (-1 + sqrt(1 + 4r)) / 2`) and compare against the yard depth the rung actually
#: reached -- if they disagree, the ladder is measuring something other than the greedy.
_PER_ENTRY = ('place_loads', 'pool_rebuilds', 'tier_sorts', 'avail_builds', 'window_aggs')


def _flows(tree: dict, flat: dict[str, int]) -> dict[str, int]:
    """Every `_FLOW_COUNTS` entry, resolved against one traced tree."""
    under: dict[str, dict[str, int]] = {}
    out: dict[str, int] = {}
    deep: dict[str, dict[str, int]] = {}
    for key, spec in _FLOW_COUNTS.items():
        name, parent = spec[0], spec[1]
        want_deep = len(spec) > 2 and spec[2]
        if parent is None:
            out[key] = flat.get(name, 0)
        elif want_deep:
            if parent not in deep:
                deep[parent] = _counts_anywhere_under(tree, parent)
            out[key] = deep[parent].get(name, 0)
        else:
            if parent not in under:
                under[parent] = _counts_under(tree, parent)
            out[key] = under[parent].get(name, 0)
    return out


def _levels(mgr) -> dict[str, int]:
    """The backlog where it FINISHED.  A level -- never a statement about what ran.

    THE YARD BELONGS HERE, and its absence was a hole in the traced/untraced divergence check
    below: two passes that agreed on picks, placements and the put-side levels while differing
    in the YARD would have passed silently, and the yard is the one quantity the inbound cells
    exist to vary.  `getattr` throughout because a flag-off run binds a `BatchTransit`, which has
    no yard at all, and because `dock_depth` REFUSES on a site-scoped leaf.
    """
    tr = getattr(mgr, 'transit', None)
    out = {'queue_depth': mgr.queue_depth, 'held': len(mgr._held)}
    try:
        out['dock_depth'] = mgr.dock_depth
    except Exception:                      # site-scoped leaf: the dock is the site's, not ours
        out['dock_depth'] = -1
    if getattr(tr, 'STANDING', False):
        out['yard_depth'] = len(getattr(tr, '_yard', ()))
        out['staged'] = len(getattr(tr, '_staged', ()))
    return out


def _split_kwargs(kwargs: dict, seed: int) -> tuple[dict, dict, int]:
    """One merged rung -> `(build_kwargs, run_kwargs, n_batches)`.

    Shared with `calltree_memory` so the two tools cannot drift on what a rung means.
    """
    build = dict(n_skus=kwargs.get('n_skus', 2_000),
                 bins_per_aisle=kwargs.get('bins_per_aisle', 100),
                 n_pickers=kwargs.get('n_pickers', 10),
                 seed=seed)
    build.update({k: kwargs[k] for k in _BUILD_KEYS if k in kwargs})
    run_kw = {k: kwargs[k] for k in _RUN_KEYS if k in kwargs}
    return build, run_kw, kwargs.get('n_batches', 20)


def run_meso_ladder(knob: str, seed: int, config: str = 'none') -> dict:
    cfg = CONFIGS[config]
    rungs = cfg.rungs.get(knob) or _MESO_LADDERS[knob]
    results = []
    for rung in rungs:
        # Overlay first, RUNG WINS -- the rung must always own the axis it scales.
        kwargs = dict(cfg.overlay, **rung)
        build, run_kw, n_batches = _split_kwargs(kwargs, seed)

        # untraced walls
        assets = scenarios.build_assets(**build)
        # The `yard` knob's x is PROVISIONAL here and is replaced below by the measured yard
        # depth, once the traced pass has produced the flows to derive it from.  The whistle is
        # only the dial; depth is the quantity the cost actually scales with, and the map between
        # them is nonlinear and saturating.
        x = {'skus': assets.sizes['n_skus_sampled'], 'bins': assets.sizes['n_bins'],
             'batches': n_batches, 'pickers': build['n_pickers'],
             'yard': run_kw.get('recv_deadline') or 0.0}[knob]
        _perf.drain()
        t0 = time.perf_counter()
        r_u = scenarios.run_meso(assets, n_batches=n_batches, seed=seed, **run_kw)
        wall = time.perf_counter() - t0
        _probe = _perf.drain()[1]
        # Read off the UNTRACED instance -- the one whose wall and picks are reported -- and
        # read it before rebinding, so two warehouses are never alive at once.
        levels_u = _levels(assets.mgr)
        del assets

        # traced counts
        assets = scenarios.build_assets(**build)
        tr = CallTreeTracer(track_c_calls=False)     # counts of project fns; lower overhead
        tr.start()
        r_t = scenarios.run_meso(assets, n_batches=n_batches, seed=seed, tracer=tr, **run_kw)
        tr.stop()
        tree = tr.tree().to_dict()
        counts = _flat_counts(tree)
        flows = _flows(tree, counts)
        levels_t = _levels(assets.mgr)

        # The two passes must be the same run, or reading traced counts as a description of
        # the untraced wall is unlicensed.  `calltree_capture` already checks this; the ladder
        # never did.
        if ((r_u.picks, r_u.placements) != (r_t.picks, r_t.placements)
                or levels_u != levels_t):
            raise RuntimeError(
                f'traced and untraced passes diverged at {knob}={x}: '
                f'picks {r_u.picks} vs {r_t.picks}, placements {r_u.placements} vs '
                f'{r_t.placements}, levels {levels_u} vs {levels_t}. The counts below would '
                f'describe a different run than the walls beside them.')

        per_pl = ({k: flows[k] / r_u.placements for k in _PER_PLACEMENT if flows.get(k)}
                  if r_u.placements else {})
        _entries = flows.get('plan_orders', 0)
        per_en = ({k: flows[k] / _entries for k in _PER_ENTRY if flows.get(k)}
                  if _entries else {})
        if knob == 'yard':
            # FIT AGAINST MEASURED DEPTH: the mean standing yard at a drain's freeze, off the
            # inbound probe (`yard_T_sum / inb_drains`, `Inbound.receiving`).  This used to
            # invert `place_loads / plan_orders` = T(T+1), which stopped holding when the yard
            # plan went lazy (O1): a drain now prices only the trailers it pulls.  Fall back to
            # the whistle only when no drain ran.
            _d = _probe.get('inb_drains', 0)
            if _d:
                x = _probe.get('yard_T_sum', 0) / _d
                print(f'      yard knob: whistle={run_kw.get("recv_deadline")}s -> '
                      f'measured T={x:.2f} (mean yard at freeze over {_d:,} drains; '
                      f'{_entries:,} entry calls, {_probe.get("plan_rounds", 0):,} plan '
                      f'rounds); final yard depth={levels_u.get("yard_depth", "n/a")}')

        results.append({'x': x, 'kwargs': kwargs, 'wall_s': wall,
                        'sections': r_u.sections, 'picks': r_u.picks,
                        'placements': r_u.placements, 'counts': counts,
                        'flows': flows, 'flows_per_placement': per_pl,
                        'flows_per_entry': per_en,
                        'levels': levels_u, 'levels_traced': levels_t})

        _fl = ' '.join(f'{k}={v:,}' for k, v in flows.items() if v)
        print(f'  rung {knob}={x}: wall={wall:.2f}s picks={r_u.picks:,} '
              f'placements={r_u.placements:,} fns={len(counts)}')
        if _fl:
            print(f'      flows (traced, cumulative): {_fl}')
        if per_en:
            import math as _math
            _r = per_en.get('place_loads')
            _t = (-1 + _math.sqrt(1 + 4 * _r)) / 2 if _r else None
            print('      per ENTRY CALL (n=%d): %s%s' % (
                _entries,
                ' '.join(f'{k}={v:,.1f}' for k, v in per_en.items()),
                f'   -> T(T+1)-equivalent T={_t:.1f} (a lazy yard plan prices less than '
                f'T(T+1): this is not the depth)' if _t else ''))
        # Per-placement ratios are NOT an inbound quantity and must not hide behind `per_en`.
        # Nested there, they never printed per-rung on any config but the inbound cells --
        # while still being computed and fitted, so the summary showed what the rungs did not.
        if per_pl:
            print('      per placement: '
                  + ' '.join(f'{k}={v:.2f}' for k, v in sorted(per_pl.items())))
        _warn = _flows_warning(flows, config)
        if _warn:
            print(f'      {_warn}')
        print(f'      levels (untraced, END of run -- a level, not coverage): '
              f"q={levels_u['queue_depth']:,} dock={levels_u['dock_depth']:,} "
              f"held={levels_u['held']:,}")
    return {'knob': knob, 'config': config, 'rungs': results}


# ── deep ladder ──────────────────────────────────────────────────────────────

_RUN_ROOT_RE = re.compile(r'^(?:Output directory|All simulations complete\.\s+Root)\s*:\s*'
                          r'(.+?)\s*$', re.MULTILINE)


def _run_root_from(stdout: str) -> str | None:
    """The run root the subprocess reported, or None.

    Taken from the child's own stdout rather than reconstructed: the root carries a timestamp
    and a spec-dependent prefix, and guessing it would silently pick up someone else's run.
    """
    hits = _RUN_ROOT_RE.findall(stdout or '')
    for h in reversed(hits):                    # the completion line is the authoritative one
        if os.path.isdir(h):
            return h
    return None


def _sample_rss(proc, every: float = 2.0) -> dict:
    """Peak RSS of the whole process tree while `proc` runs.

    Merged in from `calltree_memory.deep_rss`, which builds a BYTE-IDENTICAL command to this
    ladder's and therefore measured a different run of the same thing.  Two artifacts from two
    runs could never show that a wall knee and a memory knee were the same event -- which
    matters, because memory pressure is the leading hypothesis for the knee this tier found.
    """
    try:
        import psutil
    except ImportError:
        return {'peak_total_gib': None, 'peak_worker_gib': None, 'rss_samples': 0}
    try:
        ps = psutil.Process(proc.pid)
    except psutil.Error:
        return {'peak_total_gib': None, 'peak_worker_gib': None, 'rss_samples': 0}
    peak_total = peak_worker = 0
    n = 0
    while proc.poll() is None:
        try:
            procs = [ps] + ps.children(recursive=True)
            rss = [p.memory_info().rss for p in procs]
        except psutil.Error:                    # a child exited mid-walk; not worth a retry
            rss = []
        if rss:
            peak_total = max(peak_total, sum(rss))
            if len(rss) > 1:
                peak_worker = max(peak_worker, max(rss[1:]))
            n += 1
        time.sleep(every)
    return {'peak_total_gib': round(peak_total / 2 ** 30, 2),
            'peak_worker_gib': round(peak_worker / 2 ** 30, 2),
            'rss_samples': n}


def _sum_by_arm(rows, valfn) -> dict:
    """Total `valfn` per arm NAME, summed over the (cell, pair, config, channel) rows it has.

    Not a dict comprehension: the arm name is not the table's unique key, and assigning rather
    than summing keeps one arbitrary row and discards the rest, in silence.
    """
    out: dict = {}
    for r in rows:
        name = str(r.get('arm'))
        out[name] = out.get(name, 0.0) + valfn(r)
    return {k: round(v, 2) for k, v in out.items()}


def _arm_rollup(run_root: str, workers: int) -> dict:
    """Per-arm runtime rows, rolled up -- the deep tier's SHARP instrument.

    Everything here is a TOTAL, not a mean, which is the whole point: `Sum(total_s) / workers`
    is an honest model of the simulation phase, and `Sum(total_s) - Sum(sections)` is the batch
    loop's unattributed tail (the row-accumulation block that sits inside `elapsed` and in no
    section).  Per-arm `total_s` localizes a knee to WHICH ARM, which no mean can.
    """
    from Optimization.persistence import runtime_metrics as rm
    rows = rm.load_rows(run_root)
    if not rows:
        return {}
    def _f(r, k):
        v = r.get(k)
        return float(v) if v is not None else 0.0
    total = sum(_f(r, 'total_s') for r in rows)
    sect = {col: sum(_f(r, col) for r in rows) for col, _label in rm.SECTIONS}
    slowest = max(rows, key=lambda r: _f(r, 'total_s'))
    peaks = [_f(r, 'peak_rss_mib') for r in rows if r.get('peak_rss_mib')]
    return {
        'arms': len(rows),
        'total_s_sum': round(total, 2),
        # the model of the phase: perfectly-packed arms across the pool
        'phase_model_s': round(total / max(workers, 1), 2),
        'sections_sum': {k: round(v, 2) for k, v in sect.items()},
        # A2: what no section accounts for.  Reported as a SHARE too, because the absolute
        # number grows with the run and the share is the thing that should stay flat.
        'residual_s': round(total - sum(sect.values()), 2),
        'residual_frac': round((total - sum(sect.values())) / total, 4) if total else None,
        'precomp_s_sum': round(sum(_f(r, 'precomp_s') for r in rows), 2),
        'slowest_arm': {'arm': slowest.get('arm'), 'total_s': round(_f(slowest, 'total_s'), 2)},
        'total_s_max': round(_f(slowest, 'total_s'), 2),
        # EVERY arm, not just the largest.  `total_s_max` is a max over a MIGRATING
        # argmax -- on the 2026-09-16 deep ladder it ran 36 -> 411 s with local exponents
        # 0.98, 1.03, 1.43, 1.61 while `total_s_sum` stayed flat at k=1.05, and the arm
        # holding it changed three times.  A max over a changing argmax is not any arm's
        # growth curve.  136 floats per rung is nothing; losing them cost an hour.
        #
        # SUMMED, NOT ASSIGNED, and this was a real defect for one ladder: the table's
        # unique key is (cell, pair, config, channel, arm), so a 136-row run holds only 34
        # distinct arm NAMES -- four rows each.  A dict comprehension keyed on the name kept
        # whichever row iterated last and silently threw away three quarters of the run, so
        # the per-arm fit ran on one arbitrary row per arm and the printed count read 34
        # where the rollup beside it said 136.  Summing is what "this arm's cost" means.
        'per_arm_total_s': _sum_by_arm(rows, lambda r: _f(r, 'total_s')),
        # ...and the same totals with the DB-save section removed.  On the 2026-09-16 deep
        # ladder `save_s` was 35.9% -> 48.6% of total with a KNEE at the top rung (local k
        # 1.27, 1.06, 0.97, 2.30), and it lifted nearly every arm at exactly that rung: 33 of
        # 34 arms read as accelerating on `total_s` and only 22 on `total_s - save_s`, with
        # the median last local exponent falling 1.68 -> 1.09.  An arm's PLACEMENT cost is
        # the question the cluster cells ask; carrying both series is what lets a reader tell
        # a per-arm divergence from a shared I/O step.
        'per_arm_ex_save_s': _sum_by_arm(rows, lambda r: _f(r, 'total_s') - _f(r, 'save_s')),
        'peak_rss_mib_max': round(max(peaks), 1) if peaks else None,
        # A4: the SECOND axis.  The ladder scales SKUs and bins together, so every per-bin
        # cost is charged to the SKU exponent unless the bin count is carried alongside.
        'n_bins': int(_f(slowest, 'n_bins')) or None,
        'n_aisles': int(_f(slowest, 'n_aisles')) or None,
    }


def _catalogue_size(profiles_dir=None, profile_run=None):
    """(label, declared SKUs) of the pair a rung would bind, or (None, 0).

    Counted from `cartons`, not read from a metadata field: `run_metadata` on these
    catalogues is a key/value table with no `num_skus`, and a size a catalogue merely
    CLAIMS is exactly what this check exists to distrust.
    """
    import pathlib
    import sqlite3
    import Optimization.run_simulation as _rs
    root = profiles_dir or _rs._DEFAULT_PROFILES_DIR
    try:
        if profile_run:
            from Schema.profile_resolver import ProfileTree
            label, inv, _aff = ProfileTree(root).pairs(profile_run)[0]
        else:
            label, inv, _aff = _rs.find_latest_db_pairs(root)[0]
    except (IndexError, OSError, KeyError, ValueError):
        return None, 0
    try:
        con = sqlite3.connect(pathlib.Path(inv).as_uri() + '?mode=ro', uri=True)
        n = con.execute('select count(*) from cartons').fetchone()[0]
        con.close()
        return label, int(n)
    except sqlite3.Error:
        return label, 0


def run_deep_ladder(workers: int, dry_run: bool, profiles_dir=None,
                    profile_run=None) -> dict:
    """Real run_simulation per rung; sections parsed from each run's own log.

    THE CATALOGUE IS CHECKED FIRST, and the ladder refuses rather than truncating. Each
    rung passes `--max-skus N`, and `--max-skus` above the catalogue is neither an error
    nor a warning -- it takes everything, which in the output is indistinguishable from a
    subsystem that stopped growing. `INBOUND_PERF_FINDINGS.md` records the cost: three
    rungs with identical priced quantities, read as a trend, "one run measured three
    times". The meso tier was fixed by letting the declaration pick the fixture; this is
    the same fix for the tier that shells out.
    """
    label, have = _catalogue_size(profiles_dir, profile_run)
    if not have:
        raise SystemExit(
            'REFUSING the deep ladder: no readable catalogue pair under '
            f'{profiles_dir or "the default profiles dir"} (bound: {label}). '
            'Pass --profiles-dir <root produced by generate_profile_suite.py>.')

    # DROP what the catalogue cannot serve, and SAY SO.  `--max-skus` above the catalogue
    # is neither an error nor a warning -- it takes everything, which in the output is
    # indistinguishable from a subsystem that stopped growing.  INBOUND_PERF_FINDINGS.md
    # records the cost of learning that the hard way: three rungs with identical priced
    # quantities, read as a trend, "one run measured three times".
    rungs = [k for k in _DEEP_LADDER if k['max_skus'] <= have]
    dropped = [k['max_skus'] for k in _DEEP_LADDER if k['max_skus'] > have]
    print(f'  catalogue: {label} -- {have:,} SKUs', flush=True)
    if dropped:
        print('  DROPPED rung(s) %s: above the catalogue, so they would run the SAME %s\n'
              '           SKUs as the top surviving rung and read as flat. This is a\n'
              '           SHORTER ladder, not a saturated one -- read the span below.'
              % (', '.join(f'{d:,}' for d in dropped), f'{have:,}'), flush=True)
    if len(rungs) < 3:
        raise SystemExit(
            f'REFUSING: only {len(rungs)} rung(s) fit under a {have:,}-SKU catalogue, and a\n'
            'log-log fit needs three positive points. Generate a larger catalogue or\n'
            'shorten _DEEP_LADDER deliberately.')
    span = rungs[-1]['max_skus'] / rungs[0]['max_skus']
    print(f'  span: {rungs[0]["max_skus"]:,} -> {rungs[-1]["max_skus"]:,} '
          f'({span:.1f}x over {len(rungs)} rungs)', flush=True)

    results, failed = [], []
    for kwargs in rungs:
        cmd = [sys.executable, '-m', 'Optimization.run_simulation',
               '--workers', str(workers), '--spec', 'single',
               '--n-batches', str(kwargs['n_batches']),
               '--max-skus', str(kwargs['max_skus']),
               '--coverage-days', str(kwargs['coverage_days']),
               '--keyframe-interval', '0']
        if profiles_dir:
            cmd += ['--profiles-dir', str(profiles_dir)]
        if profile_run:
            cmd += ['--profile-run', str(profile_run)]
        if dry_run:
            print('  would run:', ' '.join(cmd))
            continue
        print(f"  rung max_skus={kwargs['max_skus']:,}: running "
              f'({workers} workers)...', flush=True)
        env = dict(os.environ, MPLBACKEND='Agg')
        # stdout to a FILE, not a pipe: the RSS sampler below needs the child running while we
        # poll it, and a pipe that fills would deadlock behind a reader that is sleeping.
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as fh:
            t0 = time.perf_counter()
            proc = subprocess.Popen(cmd, cwd=_REPO_ROOT, env=env, stdout=fh,
                                    stderr=subprocess.STDOUT)
            rss = _sample_rss(proc)
            rc = proc.wait()
            wall = time.perf_counter() - t0
            fh.seek(0)
            out_txt = fh.read()
        if rc != 0:
            print(f'  RUNG FAILED (exit {rc}); last output:')
            print('\n'.join(out_txt.splitlines()[-10:]))
            failed.append(kwargs['max_skus'])
            continue
        try:
            parsed = scenarios.macro_sections()   # newest run.log = the one we just made
        except scenarios.ScenarioUnavailable as e:
            print(f'  rung done but log unparsable: {e}')
            parsed = {'sections': {}, 'overlay': {}, 'census': {}, 'wall': {},
                      'source': 'unparsable'}

        run_root = _run_root_from(out_txt)
        arms = _arm_rollup(run_root, workers) if run_root else {}

        results.append({'x': kwargs['max_skus'], 'kwargs': kwargs,
                        'wall_s': wall,
                        # MEANS PER BATCH, averaged over every arm -- NOT a share of the wall.
                        # Kept for continuity with archived deep artifacts and renamed in the
                        # report so nothing sums them against a phase again.
                        'sections_mean_per_batch': parsed['sections'],
                        # NOT a partition and never summed against one: `kf` is inside `pre`,
                        # `gc` overlaps everything, and sql+pkl+drn ARE `db`.  Carried beside
                        # the sections so the largest growing term in this tier can be fitted
                        # apart from the two costs it was sharing a stopwatch with.
                        'overlay_mean_per_batch': parsed.get('overlay', {}),
                        'census_mean_per_batch': parsed.get('census', {}),
                        # THE WALL SPLIT, off the rung's own log.  `phase_model_s` below is
                        # the simulation half; the analysis half runs in the SAME subprocess
                        # and was therefore never separated from it, which is how a startup
                        # term got described as "a fixed ~48 s per arm" when it is 25 s -> 81 s
                        # per wave and rising (k=0.56).  A residual nobody can name is a
                        # residual nobody fixes.
                        'wall_split': parsed.get('wall', {}),
                        'source': parsed['source'], 'counts': {},
                        'run_root': run_root, 'rss': rss, 'arms': arms})
        print(f'  rung done in {wall / 60:.1f} min ({parsed["source"]})')
        if arms:
            print(f"      {arms['arms']} arms: Sum(total_s)={arms['total_s_sum']:,.0f}s "
                  f"-> phase model {arms['phase_model_s'] / 60:.1f} min "
                  f"(actual {wall / 60:.1f})")
            print(f"      unattributed by any section: {arms['residual_s']:,.0f}s "
                  f"({arms['residual_frac']:.1%})   slowest arm "
                  f"{arms['slowest_arm']['arm']} at {arms['slowest_arm']['total_s']:,.0f}s")
            print(f"      n_bins={arms['n_bins']:,} peak_rss_arm={arms['peak_rss_mib_max']}M "
                  f"peak_rss_tree={rss.get('peak_total_gib')}G")
            _ws = parsed.get('wall') or {}
            if _ws.get('span_s'):
                _model = arms['phase_model_s']
                _resid = _ws['span_s'] - _model - _ws['analysis_s']
                _waves = max(arms['arms'] / max(workers, 1), 1e-9)
                print(f"      wall split: sim model {_model / 60:.1f}m + analysis "
                      f"{_ws['analysis_s'] / 60:.1f}m ({_ws['analysis_s'] / _ws['span_s']:.0%}) "
                      f"+ startup/sched {_resid / 60:.1f}m ({_resid / _waves:.0f}s per wave)")
                _ov = parsed.get('overlay') or {}
                if _ov.get('t_save_build'):
                    print(f"      index build {_ov['t_save_build']:.2f}s/arm "
                          f"({_ov['t_save_build'] * arms['arms']:,.0f}s total) -- inside save_s")
        else:
            print('      NO runtime_metrics rows — the per-arm instrument is unavailable, so '
                  'this rung has only a wall.')
    # A LADDER THAT MEASURED NOTHING MUST NOT REPORT NOTHING-IS-WRONG.
    #
    # Observed 2026-09-16: all three rungs exited 1 with UnfieldableRequirement, and the
    # ladder went on to print "no super-linear offenders flagged at these thresholds" and
    # archive a JSON.  `RUNG FAILED ... continue` dropped each rung and nothing downstream
    # could tell "measured and clean" from "never ran" -- the same failure shape as the
    # ALL-ZERO flows warning, one level up, and the one this package's README already
    # names: a level is not coverage.
    if failed:
        listed = ', '.join(f'{f:,}' for f in failed)
        msg = (f'{len(failed)} of {len(rungs)} rung(s) FAILED ({listed}) -- a fit over the '
               f'survivors would describe a different ladder than the one requested.')
        if len(results) < 3:
            raise SystemExit(
                'REFUSING to report: ' + msg
                + f' Only {len(results)} rung(s) produced data, and a log-log fit needs three'
                  ' positive points. NOTHING WAS MEASURED -- fix the rungs before reading any'
                  ' exponent.')
        print('  WARNING: ' + msg, flush=True)
    return {'knob': 'max_skus(deep)', 'rungs': results, 'failed_rungs': failed}


# ── fitting + report ─────────────────────────────────────────────────────────

def _rt_sections():
    """`runtime_metrics.SECTIONS`, imported lazily -- the meso tier must not import product
    persistence just to fit a ladder."""
    try:
        from Optimization.persistence import runtime_metrics as rm
        return rm.SECTIONS
    except ImportError:
        return ()


def fit_report(ladder: dict) -> dict:
    rungs = ladder['rungs']
    xs = [r['x'] for r in rungs]
    report = {'knob': ladder['knob'], 'config': ladder.get('config', 'none'), 'xs': xs,
              'sections': {}, 'functions': {}, 'flows': {},
              'flows_per_placement': {}, 'arm_totals': {}, 'arm_growth': {},
              'arm_growth_ex_save': {},
              'save_decomposition': {},
              'knees': {},
              'offenders': [], 'suppressed': []}
    if len(rungs) < 3:
        return report

    # MESO rungs carry `sections` (real untraced walls for one arm).  DEEP rungs carry
    # `sections_mean_per_batch`, which is a MEAN PER BATCH averaged over every arm in the run
    # -- not a share of anything.  They are fitted the same way because an exponent of a mean
    # is still meaningful; they are NEVER summed against a wall.  The report labels which.
    _sec_key = 'sections' if 'sections' in rungs[0] else 'sections_mean_per_batch'
    report['sections_units'] = ('untraced wall seconds, one arm' if _sec_key == 'sections'
                                else 'MEAN seconds per batch, averaged over every arm')
    for sec in SECTIONS:
        ys = [r.get(_sec_key, {}).get(sec, 0.0) for r in rungs]
        if max(ys, default=0.0) < 0.01:
            continue
        slope, r2 = _fit_loglog(xs, ys)
        # THE ANCHOR IS A PROPERTY OF THE SERIES, NOT OF THE VERDICT, so it is computed for
        # every fit rather than only for the ones that qualify as offenders.  It used to be
        # computed inside the branch below, which meant a fit too noisy to be an offender was
        # also too noisy to be WARNED about -- exactly backwards, because a noisy fit on a
        # sub-millisecond anchor is the case that most needs the warning.  `t_task` sat in the
        # section table at k=2.55 on a 0.6 ms anchor, unannotated, on two consecutive ladders.
        anchor = min((y for y in ys if y > 0), default=0.0)
        report['sections'][sec] = {'exponent': round(slope, 3), 'r2': round(r2, 3),
                                   'walls': [round(y, 4) for y in ys],
                                   'anchor_s': round(anchor, 6),
                                   'noise_anchored': bool(anchor and anchor < MIN_WALL_S)}
        if r2 >= MIN_R2 and slope >= FLAG_TIME_EXP:
            if anchor < MIN_WALL_S:
                report['suppressed'].append(
                    {'kind': 'section-wall', 'name': sec, 'exponent': round(slope, 3),
                     'r2': round(r2, 3), 'anchor_s': round(anchor, 6),
                     'why': (f'fit rests on a {anchor * 1000:.2f} ms point, under the '
                             f'{MIN_WALL_S * 1000:.0f} ms floor -- re-measure at a scale '
                             f'where this section is real before believing k')})
            else:
                report['offenders'].append(
                    {'kind': 'section-wall', 'name': sec, 'exponent': round(slope, 3),
                     'r2': round(r2, 3), 'last': round(ys[-1], 4),
                     'projected': round(_project(ys, slope), 4),
                     'units': 'seconds'})

    # ── THE SAVE DECOMPOSITION ───────────────────────────────────────────────────
    # `t_save` is this tier's largest growing term -- 48.6% of the run at a local k of 2.30
    # -- and until 2026-09-17 it was ONE stopwatch over a Python drain, a SQLite flush and a
    # pickle write with a retry loop.  Fitted HERE and not in `report['sections']` because
    # the three sum to `db`; putting them beside it would double-count the whole section.
    #
    # THE RATIO IS THE POINT.  Eight arms of one toy run wrote 166,078-166,278 rows each --
    # a 1.00x spread -- while their save_s varied 2.8x.  That is cost PER ROW, not volume,
    # and an absolute exponent cannot tell those apart: "each write got more expensive" and
    # "there are more writes" have different fixes.  A denominator turns a number into a claim.
    _ov = [r.get('overlay_mean_per_batch', {}) for r in rungs]
    _rows = [r.get('census_mean_per_batch', {}).get('n_rows', 0.0) for r in rungs]
    for _name in ('t_save_sqlite', 't_save_pickle', 't_save_drain', 't_kf', 't_gc'):
        ys = [o.get(_name, 0.0) for o in _ov]
        if max(ys, default=0.0) < MIN_WALL_S:
            continue                      # absent on meso, and zero on any pre-2026-09-17 log
        slope, r2 = _fit_loglog(xs, ys)
        report['save_decomposition'][_name] = {
            'exponent': round(slope, 3), 'r2': round(r2, 3),
            'walls': [round(y, 4) for y in ys],
            'local': [round(k, 3) for k in _local_exponents(xs, ys)]}
        if r2 >= MIN_R2 and slope >= FLAG_TIME_EXP:
            report['offenders'].append(
                {'kind': 'save-part', 'name': _name, 'exponent': round(slope, 3),
                 'r2': round(r2, 3), 'last': round(ys[-1], 4),
                 'projected': round(_project(ys, slope), 4), 'units': 'seconds'})

        pairs = [(x, y / n) for x, y, n in zip(xs, ys, _rows) if n > 0 and y > 0]
        if len(pairs) < 3:
            continue
        px, py = [p[0] for p in pairs], [p[1] for p in pairs]
        pslope, pr2 = _fit_loglog(px, py)
        report['save_decomposition'][_name + '_per_row'] = {
            'exponent': round(pslope, 3), 'r2': round(pr2, 3),
            'ratios': [round(v, 9) for v in py],
            'local': [round(k, 3) for k in _local_exponents(px, py)],
            'rows': [round(n, 1) for n in _rows]}
        # Gated at FLAG_TREND_DELTA, as `flows_per_placement` is: a PER-UNIT cost that rises
        # at all is already the finding, so the bar is far below the absolute-wall bar.
        if pr2 >= MIN_R2 and pslope >= FLAG_TREND_DELTA:
            report['offenders'].append(
                {'kind': 'per-row', 'name': _name + '_per_row',
                 'exponent': round(pslope, 3), 'r2': round(pr2, 3),
                 'last': round(py[-1], 9), 'projected': round(_project(py, pslope), 9),
                 'units': 'seconds per row'})

    names = set()
    for r in rungs:
        names.update(r['counts'])
    for name in sorted(names):
        ys = [r['counts'].get(name, 0) for r in rungs]
        if ys[-1] < MIN_CALLS:
            continue
        slope, r2 = _fit_loglog(xs, [float(y) for y in ys])
        entry = {'exponent': round(slope, 3), 'r2': round(r2, 3), 'counts': ys}
        report['functions'][name] = entry
        if r2 >= MIN_R2 and slope >= FLAG_COUNT_EXP:
            report['offenders'].append({'kind': 'call-count', 'name': name,
                                        'exponent': round(slope, 3), 'r2': round(r2, 3),
                                        'last': ys[-1],
                                        'projected': round(_project(ys, slope)),
                                        'units': 'calls', 'counts': ys})

    # FLOWS.  Fitted like call counts, but reported separately because they answer a
    # different question: not "which function grows" but "did this configuration exercise the
    # path at all, and how fast does the work grow when it does".
    flow_names: set = set()
    for r in rungs:
        flow_names.update(r.get('flows', {}))
    for name in sorted(flow_names):
        ys = [r.get('flows', {}).get(name, 0) for r in rungs]
        if not any(ys):
            continue        # never executed here; a NaN fit would also be invalid strict JSON
        slope, r2 = _fit_loglog(xs, [float(y) for y in ys])
        if slope != slope:                      # NaN: too few positive points
            continue
        report['flows'][name] = {'exponent': round(slope, 3), 'r2': round(r2, 3),
                                 'counts': ys}
        if r2 >= MIN_R2 and slope >= FLAG_COUNT_EXP:
            report['offenders'].append({'kind': 'flow', 'name': name,
                                        'last': ys[-1],
                                        'projected': round(_project(ys, slope)),
                                        'units': 'calls',
                                        'exponent': round(slope, 3), 'r2': round(r2, 3),
                                        'counts': ys})

    # PER-PLACEMENT ratios.  A ratio that grows is the finding an absolute count cannot
    # make: it says the work per unit rose, not merely that there were more units.  Flagged at
    # the same threshold, because a ratio exponent above 1 is already super-linear per unit.
    ratio_names: set = set()
    for r in rungs:
        ratio_names.update(r.get('flows_per_placement', {}))
    for name in sorted(ratio_names):
        ys = [r.get('flows_per_placement', {}).get(name, 0.0) for r in rungs]
        if not all(ys):
            continue          # absent at some rung: a ratio over a missing flow means nothing
        slope, r2 = _fit_loglog(xs, ys)
        if slope != slope:
            continue
        report['flows_per_placement'][name] = {'exponent': round(slope, 3),
                                               'r2': round(r2, 3),
                                               'ratios': [round(y, 3) for y in ys]}
        if r2 >= MIN_R2 and slope >= 0.30:
            report['offenders'].append({'kind': 'per-placement', 'name': name,
                                        'exponent': round(slope, 3), 'r2': round(r2, 3)})

    # ── PER-ARM TOTALS (deep only) ───────────────────────────────────────────────
    # These are sums over every arm, not means, so they ARE commensurable with the phase and
    # can be fitted, summed and compared.  This is what the deep tier should have been reading
    # all along: `runtime_metrics.db` has carried it per arm for as long as the tier has
    # existed.
    arms = [r.get('arms') or {} for r in rungs]
    if all(a.get('total_s_sum') for a in arms):
        series = {'total_s_sum': [a['total_s_sum'] for a in arms],
                  'residual_s': [a['residual_s'] for a in arms],
                  'n_bins': [a.get('n_bins') or 0 for a in arms],
                  'peak_rss_mib_max': [a.get('peak_rss_mib_max') or 0 for a in arms]}
        for col, _label in _rt_sections():
            series[col] = [a['sections_sum'].get(col, 0.0) for a in arms]
        for name, ys in series.items():
            if not all(y > 0 for y in ys):
                continue
            slope, r2 = _fit_loglog(xs, [float(y) for y in ys])
            if slope != slope:
                continue
            report['arm_totals'][name] = {'exponent': round(slope, 3), 'r2': round(r2, 3),
                                          'values': ys}
            if r2 >= MIN_R2 and slope >= FLAG_TIME_EXP and name != 'n_bins':
                anchor = min((y for y in ys if y > 0), default=0.0)
                seconds = name.endswith('_s') or name.endswith('_s_sum')
                if seconds and anchor < MIN_WALL_S:
                    report['suppressed'].append(
                        {'kind': 'arm-total', 'name': name, 'exponent': round(slope, 3),
                         'r2': round(r2, 3), 'anchor_s': round(anchor, 6),
                         'why': 'fit rests on a sub-resolution wall'})
                else:
                    report['offenders'].append(
                        {'kind': 'arm-total', 'name': name, 'last': ys[-1],
                         'projected': round(_project(ys, slope), 4),
                         'units': 'seconds' if seconds else '',
                         'exponent': round(slope, 3), 'r2': round(r2, 3)})
        # A8: PER-ARM. The sum can be linear while one FAMILY pulls away, and the sum is
        # what every section exponent above is built from. An arm missing from any rung is
        # skipped rather than zero-filled -- a dead worker must not read as a fast arm.
        # Both series, so a shared I/O step cannot masquerade as 34 separate divergences.
        _per_arm: dict = {}
        _per_arm_ex: dict = {}
        for a in arms:
            for _arm, _v in (a.get('per_arm_total_s') or {}).items():
                _per_arm.setdefault(_arm, []).append(_v)
            for _arm, _v in (a.get('per_arm_ex_save_s') or {}).items():
                _per_arm_ex.setdefault(_arm, []).append(_v)
        for _arm, _ys in sorted(_per_arm_ex.items()):
            if len(_ys) != len(xs) or not all(y > 0 for y in _ys):
                continue
            _k, _r2 = _fit_loglog(xs, [float(y) for y in _ys])
            if _k != _k:
                continue
            _t = _trend(xs, _ys)
            report['arm_growth_ex_save'][_arm] = {
                'exponent': round(_k, 3), 'r2': round(_r2, 3), 'values': _ys,
                'trend': _t['verdict'] if _t else None,
                'local': _t['local'] if _t else None}
        for _arm, _ys in sorted(_per_arm.items()):
            if len(_ys) != len(xs) or not all(y > 0 for y in _ys):
                continue
            _k, _r2 = _fit_loglog(xs, [float(y) for y in _ys])
            if _k != _k:
                continue
            _t = _trend(xs, _ys)
            report['arm_growth'][_arm] = {'exponent': round(_k, 3), 'r2': round(_r2, 3),
                                          'values': _ys,
                                          'trend': _t['verdict'] if _t else None,
                                          'local': _t['local'] if _t else None}
            # Flag on the TREND as well as the fit: an arm that is still under the threshold
            # but accelerating is the one worth catching, and it is exactly what a fit over
            # the whole span averages away.
            # THE SAVE-EXCLUDED SERIES IS THE GATE.  `total_s` carries the DB-save section,
            # and one knee there flags nearly every arm at once -- 33 of 34 on the 2026-09-16
            # ladder, against 22 once save was removed.  An arm is only reported when its
            # PLACEMENT cost is what is growing.
            _ex = report['arm_growth_ex_save'].get(_arm)
            # ON THE EXPONENT ONLY, deliberately -- the TREND is biased upward here and
            # must not be part of this gate.  An arm pays a large fixed cost before its
            # batch loop starts (~48 s, measured in COMPLEXITY_ROUND_FINDINGS), so at small
            # rungs that term depresses the early local exponents and ANY arm reads as
            # accelerating while it amortises.  Measured on the 2026-09-16 ladder:
            # `uni_tmin_norsl` is flatly linear with save removed (k = 0.90) and its local
            # exponents still climb 0.73 -> 1.02, a 0.29 rise that clears FLAG_TREND_DELTA
            # on its own.  An arm total is real seconds, not a ratio, so the fit is the
            # right statistic for it.
            _ex_ok = _ex is None or _ex['exponent'] >= FLAG_ARM_EXP
            if _ex_ok and ((_r2 >= MIN_R2 and _k >= FLAG_TIME_EXP)
                           or (_t and _t['verdict'] == 'accelerating')):
                report['offenders'].append(
                    {'kind': 'arm-total', 'name': f'arm:{_arm}', 'last': _ys[-1],
                     'projected': round(_project(_ys, _k), 4), 'units': 'seconds',
                     'exponent': round(_k, 3), 'r2': round(_r2, 3)})

        # A7: COMMENSURABILITY.  `sum(total_s)/workers` is a model of the phase; if it is
        # nowhere near the measured wall then the rows describe a different run than the
        # clock did, and every exponent above is about the wrong thing.  This is the check
        # whose absence let a mean-per-batch be compared against a phase wall for months.
        report['commensurable'] = [
            {'x': r['x'],
             'phase_model_min': round((a['phase_model_s']) / 60, 2),
             'wall_min': round(r['wall_s'] / 60, 2),
             'ratio': round(a['phase_model_s'] / r['wall_s'], 3) if r['wall_s'] else None}
            for r, a in zip(rungs, arms)]

    # ── KNEES ────────────────────────────────────────────────────────────────────
    # Run over every series already fitted above, because a knee can hide in any of them and
    # the r-squared gate suppresses exactly this shape.
    _series = []
    for name, e in report['sections'].items():
        _series.append((f'section:{name}', e['walls']))
    for name, e in report['functions'].items():
        _series.append((f'count:{name}', e['counts']))
    for name, e in report['flows'].items():
        _series.append((f'flow:{name}', e['counts']))
    for name, e in report['arm_totals'].items():
        if name != 'n_bins':
            _series.append((f'arm-total:{name}', e['values']))
    for label, ys in _series:
        k = _knee(xs, ys)
        if k is None:
            continue
        report['knees'][label] = k
        report['offenders'].append({
            'kind': 'knee', 'name': label,
            'exponent': k['last_step_k'], 'r2': 1.0,     # sorts by severity of the jump
            'note': (f"local k {k['earlier_median_k']} -> {k['last_step_k']} at "
                     f"x={k['at_x']:,}; a single fit would smear this away")})

    # Every offender above was appended with a fitted exponent; the series it came from
    # is still in the report, so the trend is a lookup rather than a recomputation.  A knee
    # IS a local-exponent finding already and needs no second one.
    for _o in report['offenders']:
        _name = _o['name']
        _kind = _o.get('kind')
        if _kind == 'arm-total' and _name.startswith('arm:'):
            _kind, _name = 'arm-growth', _name[4:]
        _where = _TREND_SERIES.get(_kind)
        if _where is None:
            continue
        _e = report[_where[0]].get(_name)
        _t = _trend(xs, _e[_where[1]]) if _e else None
        if _t is not None:
            _o['trend'] = _t

    report['offenders'] = _severity_sort(report['offenders'])
    return report


def _growth_png(report: dict, path: str) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    xs = report['xs']
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    for sec, e in sorted(report['sections'].items()):
        ax.loglog(xs, [max(w, 1e-4) for w in e['walls']], marker='o',
                  label=f"{sec} (k={e['exponent']})")
    ax.set_xlabel(report['knob'])
    ax.set_ylabel('untraced wall s')
    ax.set_title('section walls vs size (log-log; k = fitted exponent)')
    ax.legend(fontsize=7)
    ax = axes[1]
    top = sorted(report['functions'].items(), key=lambda kv: -kv[1]['exponent'])[:10]
    for name, e in top:
        ax.loglog(xs, [max(c, 1) for c in e['counts']], marker='o',
                  label=f"{name.split(':')[-1][:34]} (k={e['exponent']})")
    ax.set_xlabel(report['knob'])
    ax.set_ylabel('call count (exact)')
    ax.set_title('steepest call-count growth (the quadratic detector)')
    ax.legend(fontsize=7)
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='size-ladder growth-curve analysis')
    ap.add_argument('--ladder', choices=('meso', 'deep'), default='meso')
    ap.add_argument('--profiles-dir', default=None,
                    help='Catalogue root each DEEP rung binds. Default: whatever '
                         'find_latest_db_pairs picks, which is the most RECENT pair and not '
                         'necessarily one big enough for the top rung -- the ladder refuses '
                         'rather than truncating, and names this flag.')
    ap.add_argument('--profile-run', default=None, metavar='NAME',
                    help='DEEP only: bind this NAMED profile run rather than the newest. '
                         'The newest is not necessarily the biggest, and a rung above the '
                         'catalogue is truncated in silence.')
    ap.add_argument('--knob', choices=tuple(_MESO_LADDERS), default='skus',
                    help='meso only: which input the ladder scales')
    ap.add_argument('--config', choices=tuple(CONFIGS), default='none',
                    help='named scenario configuration layered under every rung; "none" is '
                         'the plain size ladder. See CONFIGS for what each one is.')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--workers', type=int, default=18, help='deep only')
    ap.add_argument('--dry-run', action='store_true', help='deep only: print rungs')
    args = ap.parse_args(argv)

    # A redirected stdout on Windows defaults to cp1252, which cannot encode the U+2248 /
    # U+2265 / U+00B2 in the summary below.  `> ladder.log` therefore used to crash AFTER the
    # artifact was written and recorded, losing the readable summary of a multi-minute run and
    # the PNG with it.  Replace rather than raise: a mojibake character beats a traceback.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):        # not a real stream (pytest capture, a pipe)
        pass

    if args.ladder == 'deep' and args.config != 'none':
        # REFUSE rather than ignore.  The deep tier shells out to `run_simulation`, which takes
        # its put-away configuration from `Optimization/config/settings.py` and not from here,
        # so honouring the flag silently is impossible and dropping it silently is the exact
        # failure `_BUILD_KEYS` exists to prevent.
        ap.error(f'--config {args.config} cannot reach the deep ladder: it launches real '
                 f'run_simulation subprocesses, which read settings.py. Pass the real flags '
                 f'(--put-queue-split, --put-*-staging, --recv-crew-size, --recv-day-seconds, '
                 f'--work-day-seconds) to run_simulation directly, or use --ladder meso.')

    if args.ladder == 'meso':
        print(f'meso ladder over {args.knob} (seed {args.seed}, cfg {args.config}): '
              f'{CONFIGS[args.config].why}')
        ladder = run_meso_ladder(args.knob, args.seed, args.config)
    else:
        print(f'deep ladder ({args.workers} workers)'
              f'{" — DRY RUN" if args.dry_run else " — this is the ~1h session"}:')
        ladder = run_deep_ladder(args.workers, args.dry_run, args.profiles_dir,
                                 args.profile_run)
        if args.dry_run:
            return 0

    report = fit_report(ladder)
    from calltree_store import archive_path, record
    # `cfg` is emitted ALWAYS, including for 'none'.  An untagged artifact is ambiguous
    # forever -- the pre-config entries already in the index cannot be told apart from a
    # deliberate default, and nobody can now recover which they were.
    _tags = {'ladder': args.ladder, 'knob': args.knob, 'seed': args.seed,
             'cfg': args.config}
    out = archive_path('growth', **_tags)
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'ladder': ladder, 'report': report}, fh, indent=1)
    record('growth', out, tags=_tags,
           summary={'exponents': {k: v['exponent'] for k, v in report['sections'].items()},
                    'flows': {k: {'k': v['exponent'], 'last': v['counts'][-1]}
                              for k, v in report['flows'].items()},
                    'offenders': [f"{o['name']} k={o['exponent']}"
                                  for o in report['offenders'][:8]]})

    _sd = report.get('save_decomposition') or {}
    if _sd:
        print('\nsave decomposition [sql+pkl+drn == db; a SUB-partition, never a section]')
        for _n, _d in _sd.items():
            _series = _d.get('walls') or _d.get('ratios') or []
            _unit = 's/row' if _n.endswith('_per_row') else 's'
            print(f'  {_n:26s} k={_d["exponent"]:6.2f} r2={_d["r2"]:.2f}  '
                  f'local={_d.get("local")}  '
                  f'[{", ".join(f"{v:.6g}{_unit}" for v in _series)}]')
        _rws = (_sd.get('t_save_sqlite_per_row') or {}).get('rows')
        if _rws:
            print(f'  {"rows per checkpoint":26s} {_rws}')

    print(f'\nsection exponents [{report.get("sections_units", "?")}] '
          f'(expect ≈1 vs {report["knob"]}; flag ≥ {FLAG_TIME_EXP}):')
    for sec, e in report['sections'].items():
        # The marker is the whole point of carrying `anchor_s`: an unflagged k means "not an
        # offender", which a reader hears as "fine", and a k fitted off a sub-millisecond point
        # is neither.
        _note = (f"   <- NOISE-ANCHORED on {e['anchor_s'] * 1000:.2f} ms, under the "
                 f"{MIN_WALL_S * 1000:.0f} ms floor; do not believe this k"
                 if e.get('noise_anchored') else '')
        print(f"  {sec:10s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}{_note}")
    if report['flows']:
        print(f'\nflow exponents (did the path run, and how fast does its work grow):')
        for name, e in sorted(report['flows'].items()):
            print(f"  {name:20s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}  {e['counts']}")
    elif args.ladder == 'meso':
        print(f'\nno flows recorded — cfg={args.config} does not exercise the put-away or '
              f'receiving path')

    if report['knees']:
        print('\nKNEES (a step change, which a single fit and the r² gate both hide):')
        for label, k in sorted(report['knees'].items(),
                               key=lambda kv: -kv[1]['last_step_k']):
            print(f"  {label}")
            print(f"      local k per step {k['local_exponents']} — last step "
                  f"{k['last_step_k']} vs earlier median {k['earlier_median_k']} "
                  f"at x={k['at_x']:,}")

    if report['arm_totals']:
        print('\nPER-ARM TOTALS (sums over every arm — commensurable with the phase, unlike '
              'the section means above):')
        for name, e in sorted(report['arm_totals'].items()):
            print(f"  {name:18s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}  {e['values']}")
        print('\n  commensurability (Σtotal_s/workers vs the measured wall):')
        for c in report.get('commensurable', []):
            print(f"    x={c['x']:>7,}  model {c['phase_model_min']:>6.1f} min  "
                  f"wall {c['wall_min']:>6.1f} min  ratio {c['ratio']}")

    if report.get('arm_growth'):
        _rank = sorted(report['arm_growth'].items(), key=lambda kv: -kv[1]['exponent'])
        _acc = [kv for kv in _rank if kv[1]['trend'] == 'accelerating']
        print(f"\nPER-ARM growth ({len(report['arm_growth'])} arms; the SUM can be linear "
              f"while one family pulls away):")
        for _n, _e in _rank[:8]:
            _mark = '  <- DIVERGING' if _e['trend'] == 'accelerating' else ''
            _x = report.get('arm_growth_ex_save', {}).get(_n)
            if _x is not None:
                _mark += f"   [ex-save k={_x['exponent']:.2f}]"
            print(f"  {_n:28s} k={_e['exponent']:6.2f}  r\u00b2={_e['r2']:.2f}  "
                  f"{_e['values']}{_mark}")
            if _e['local']:
                print(f"       {'':26s} local k "
                      + ' '.join(f'{k:.2f}' for k in _e['local']))
        if _acc:
            print(f"  {len(_acc)} arm(s) accelerating: "
                  + ', '.join(n for n, _ in _acc[:10]))

    if report['flows_per_placement']:
        print('\nPER-PLACEMENT ratios (work per unit, not unit count — the discriminator):')
        for name, e in sorted(report['flows_per_placement'].items()):
            print(f"  {name:20s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}  {e['ratios']}")

    if report['offenders']:
        print(f'\nOFFENDERS (count k ≥ {FLAG_COUNT_EXP} or wall k ≥ {FLAG_TIME_EXP}, '
              f'r² ≥ {MIN_R2}):')
        for o in report['offenders'][:20]:
            extra = f"  counts={o['counts']}" if 'counts' in o else ''
            print(f"  k={o['exponent']:5.2f}  [{o['kind']}]  {o['name']}{extra}")
            t = o.get('trend')
            if t is not None:
                mark = {'saturating': '  <- CONVERGING toward linear',
                        'settling': f"  <- SETTLED near k={t['last']:.2f} (the fit "
                                    f'overstates the early rungs; this IS the class)',
                        'accelerating': '  <- DIVERGING'}.get(t['verdict'], '')
                locals_ = ' '.join(f'{k:.2f}' for k in t['local'])
                print(f"           local k {locals_}{mark}")
    else:
        print('\nno super-linear offenders flagged at these thresholds')

    png = os.path.splitext(out)[0] + '.png'      # archived beside its JSON, same stamp
    _growth_png(report, png)
    print(f'\nwrote {os.path.relpath(out, _REPO_ROOT)} and {os.path.relpath(png, _REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
