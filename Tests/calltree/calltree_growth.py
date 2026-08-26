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
}
_DEEP_LADDER = [   # run_simulation args per rung; sized so 5 rungs fit ~an hour at 18 workers
    dict(max_skus=10_000, s_max_bins=15_000, ff_max_bins=20_000, n_batches=15),
    dict(max_skus=20_000, s_max_bins=25_000, ff_max_bins=33_000, n_batches=15),
    dict(max_skus=40_000, s_max_bins=50_000, ff_max_bins=66_000, n_batches=15),
    dict(max_skus=80_000, s_max_bins=100_000, ff_max_bins=132_000, n_batches=15),
]

# Offender thresholds: exponent above which a fit is flagged, per instrument.
FLAG_COUNT_EXP = 1.30
FLAG_TIME_EXP  = 1.50
MIN_R2         = 0.90       # don't flag garbage fits
MIN_CALLS      = 200        # ignore trivial functions at the largest rung


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
               'strategy', 'coverage', 'safety', 'target_fill')
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


def _flows(tree: dict, flat: dict[str, int]) -> dict[str, int]:
    """Every `_FLOW_COUNTS` entry, resolved against one traced tree."""
    under: dict[str, dict[str, int]] = {}
    out: dict[str, int] = {}
    for key, (name, parent) in _FLOW_COUNTS.items():
        if parent is None:
            out[key] = flat.get(name, 0)
        else:
            if parent not in under:
                under[parent] = _counts_under(tree, parent)
            out[key] = under[parent].get(name, 0)
    return out


def _levels(mgr) -> dict[str, int]:
    """The backlog where it FINISHED.  A level -- never a statement about what ran."""
    return {'queue_depth': mgr.queue_depth, 'dock_depth': mgr.dock_depth,
            'held': len(mgr._held)}


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
        x = {'skus': assets.sizes['n_skus_sampled'], 'bins': assets.sizes['n_bins'],
             'batches': n_batches, 'pickers': build['n_pickers']}[knob]
        t0 = time.perf_counter()
        r_u = scenarios.run_meso(assets, n_batches=n_batches, seed=seed, **run_kw)
        wall = time.perf_counter() - t0
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
        results.append({'x': x, 'kwargs': kwargs, 'wall_s': wall,
                        'sections': r_u.sections, 'picks': r_u.picks,
                        'placements': r_u.placements, 'counts': counts,
                        'flows': flows, 'flows_per_placement': per_pl,
                        'levels': levels_u, 'levels_traced': levels_t})

        _fl = ' '.join(f'{k}={v:,}' for k, v in flows.items() if v)
        print(f'  rung {knob}={x}: wall={wall:.2f}s picks={r_u.picks:,} '
              f'placements={r_u.placements:,} fns={len(counts)}')
        if _fl:
            print(f'      flows (traced, cumulative): {_fl}')
            if per_pl:
                print('      per placement: '
                      + ' '.join(f'{k}={v:.2f}' for k, v in sorted(per_pl.items())))
        else:
            print(f'      flows: ALL ZERO -- the put-away/receiving path did not execute '
                  f'under cfg={config}. Use --config split_staging4 to exercise it.')
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
        'peak_rss_mib_max': round(max(peaks), 1) if peaks else None,
        # A4: the SECOND axis.  The ladder scales SKUs and bins together, so every per-bin
        # cost is charged to the SKU exponent unless the bin count is carried alongside.
        'n_bins': int(_f(slowest, 'n_bins')) or None,
        'n_aisles': int(_f(slowest, 'n_aisles')) or None,
    }


def run_deep_ladder(workers: int, dry_run: bool) -> dict:
    """Real run_simulation per rung; sections parsed from each run's own log."""
    results = []
    for kwargs in _DEEP_LADDER:
        cmd = [sys.executable, '-m', 'Optimization.run_simulation',
               '--workers', str(workers), '--spec', 'single',
               '--n-batches', str(kwargs['n_batches']),
               '--max-skus', str(kwargs['max_skus']),
               '--s-max-bins', str(kwargs['s_max_bins']),
               '--ff-max-bins', str(kwargs['ff_max_bins']),
               '--keyframe-interval', '0']
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
            continue
        try:
            parsed = scenarios.macro_sections()   # newest run.log = the one we just made
        except scenarios.ScenarioUnavailable as e:
            print(f'  rung done but log unparsable: {e}')
            parsed = {'sections': {}, 'source': 'unparsable'}

        run_root = _run_root_from(out_txt)
        arms = _arm_rollup(run_root, workers) if run_root else {}

        results.append({'x': kwargs['max_skus'], 'kwargs': kwargs,
                        'wall_s': wall,
                        # MEANS PER BATCH, averaged over every arm -- NOT a share of the wall.
                        # Kept for continuity with archived deep artifacts and renamed in the
                        # report so nothing sums them against a phase again.
                        'sections_mean_per_batch': parsed['sections'],
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
        else:
            print('      NO runtime_metrics rows — the per-arm instrument is unavailable, so '
                  'this rung has only a wall.')
    return {'knob': 'max_skus(deep)', 'rungs': results}


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
              'flows_per_placement': {}, 'arm_totals': {}, 'knees': {},
              'offenders': []}
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
        report['sections'][sec] = {'exponent': round(slope, 3), 'r2': round(r2, 3),
                                   'walls': [round(y, 4) for y in ys]}
        if r2 >= MIN_R2 and slope >= FLAG_TIME_EXP:
            report['offenders'].append({'kind': 'section-wall', 'name': sec,
                                        'exponent': round(slope, 3), 'r2': round(r2, 3)})

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
                                        'counts': ys})

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
                report['offenders'].append({'kind': 'arm-total', 'name': name,
                                            'exponent': round(slope, 3), 'r2': round(r2, 3)})
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

    report['offenders'].sort(key=lambda o: -o['exponent'])
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
        ladder = run_deep_ladder(args.workers, args.dry_run)
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

    print(f'\nsection exponents [{report.get("sections_units", "?")}] '
          f'(expect ≈1 vs {report["knob"]}; flag ≥ {FLAG_TIME_EXP}):')
    for sec, e in report['sections'].items():
        print(f"  {sec:10s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}")
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
    else:
        print('\nno super-linear offenders flagged at these thresholds')

    png = os.path.splitext(out)[0] + '.png'      # archived beside its JSON, same stamp
    _growth_png(report, png)
    print(f'\nwrote {os.path.relpath(out, _REPO_ROOT)} and {os.path.relpath(png, _REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
