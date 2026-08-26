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
import subprocess
import sys
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
}


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

        results.append({'x': x, 'kwargs': kwargs, 'wall_s': wall,
                        'sections': r_u.sections, 'picks': r_u.picks,
                        'placements': r_u.placements, 'counts': counts,
                        'flows': flows, 'levels': levels_u, 'levels_traced': levels_t})

        _fl = ' '.join(f'{k}={v:,}' for k, v in flows.items() if v)
        print(f'  rung {knob}={x}: wall={wall:.2f}s picks={r_u.picks:,} '
              f'placements={r_u.placements:,} fns={len(counts)}')
        if _fl:
            print(f'      flows (traced, cumulative): {_fl}')
        else:
            print(f'      flows: ALL ZERO -- the put-away/receiving path did not execute '
                  f'under cfg={config}. Use --config split_staging4 to exercise it.')
        print(f'      levels (untraced, END of run -- a level, not coverage): '
              f"q={levels_u['queue_depth']:,} dock={levels_u['dock_depth']:,} "
              f"held={levels_u['held']:,}")
    return {'knob': knob, 'config': config, 'rungs': results}


# ── deep ladder ──────────────────────────────────────────────────────────────

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
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, cwd=_REPO_ROOT, env=env, capture_output=True,
                              text=True, encoding='utf-8', errors='replace')
        wall = time.perf_counter() - t0
        if proc.returncode != 0:
            print(f'  RUNG FAILED (exit {proc.returncode}); last output:')
            print('\n'.join((proc.stdout or proc.stderr or '').splitlines()[-10:]))
            continue
        try:
            parsed = scenarios.macro_sections()   # newest run.log = the one we just made
        except scenarios.ScenarioUnavailable as e:
            print(f'  rung done but log unparsable: {e}')
            continue
        results.append({'x': kwargs['max_skus'], 'kwargs': kwargs,
                        'wall_s': wall, 'sections': parsed['sections'],
                        'source': parsed['source'], 'counts': {}})
        print(f'  rung done in {wall / 60:.1f} min ({parsed["source"]})')
    return {'knob': 'max_skus(deep)', 'rungs': results}


# ── fitting + report ─────────────────────────────────────────────────────────

def fit_report(ladder: dict) -> dict:
    rungs = ladder['rungs']
    xs = [r['x'] for r in rungs]
    report = {'knob': ladder['knob'], 'config': ladder.get('config', 'none'), 'xs': xs,
              'sections': {}, 'functions': {}, 'flows': {}, 'offenders': []}
    if len(rungs) < 3:
        return report

    for sec in SECTIONS:
        ys = [r['sections'].get(sec, 0.0) for r in rungs]
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

    print(f'\nsection exponents (expect ≈1 vs {report["knob"]}; flag ≥ {FLAG_TIME_EXP}):')
    for sec, e in report['sections'].items():
        print(f"  {sec:10s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}")
    if report['flows']:
        print(f'\nflow exponents (did the path run, and how fast does its work grow):')
        for name, e in sorted(report['flows'].items()):
            print(f"  {name:20s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}  {e['counts']}")
    elif args.ladder == 'meso':
        print(f'\nno flows recorded — cfg={args.config} does not exercise the put-away or '
              f'receiving path')

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
