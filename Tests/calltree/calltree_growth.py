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

Usage:
    python Tests/calltree/calltree_growth.py --ladder meso --seed 42
    python Tests/calltree/calltree_growth.py --ladder meso --knob batches
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

def run_meso_ladder(knob: str, seed: int) -> dict:
    rungs = _MESO_LADDERS[knob]
    results = []
    for kwargs in rungs:
        build = dict(n_skus=kwargs.get('n_skus', 2_000),
                     bins_per_aisle=kwargs.get('bins_per_aisle', 100),
                     n_pickers=kwargs.get('n_pickers', 10),
                     seed=seed)
        n_batches = kwargs.get('n_batches', 20)

        # untraced walls
        assets = scenarios.build_assets(**build)
        x = {'skus': assets.sizes['n_skus_sampled'], 'bins': assets.sizes['n_bins'],
             'batches': n_batches, 'pickers': build['n_pickers']}[knob]
        t0 = time.perf_counter()
        r_u = scenarios.run_meso(assets, n_batches=n_batches, seed=seed)
        wall = time.perf_counter() - t0

        # traced counts
        assets = scenarios.build_assets(**build)
        tr = CallTreeTracer(track_c_calls=False)     # counts of project fns; lower overhead
        tr.start()
        scenarios.run_meso(assets, n_batches=n_batches, seed=seed, tracer=tr)
        tr.stop()
        counts = _flat_counts(tr.tree().to_dict())

        results.append({'x': x, 'kwargs': kwargs, 'wall_s': wall,
                        'sections': r_u.sections, 'picks': r_u.picks,
                        'placements': r_u.placements, 'counts': counts})
        print(f'  rung {knob}={x}: wall={wall:.2f}s picks={r_u.picks:,} '
              f'placements={r_u.placements:,} fns={len(counts)}')
    return {'knob': knob, 'rungs': results}


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
        proc = subprocess.run(cmd, cwd=_REPO_ROOT, env=env,
                              capture_output=True, text=True)
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
    report = {'knob': ladder['knob'], 'xs': xs, 'sections': {}, 'functions': {},
              'offenders': []}
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
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--workers', type=int, default=18, help='deep only')
    ap.add_argument('--dry-run', action='store_true', help='deep only: print rungs')
    args = ap.parse_args(argv)

    if args.ladder == 'meso':
        print(f'meso ladder over {args.knob} (seed {args.seed}):')
        ladder = run_meso_ladder(args.knob, args.seed)
    else:
        print(f'deep ladder ({args.workers} workers)'
              f'{" — DRY RUN" if args.dry_run else " — this is the ~1h session"}:')
        ladder = run_deep_ladder(args.workers, args.dry_run)
        if args.dry_run:
            return 0

    report = fit_report(ladder)
    out = os.path.join(_OUT_DIR, f'growth_{args.ladder}_{args.knob}_s{args.seed}.json')
    os.makedirs(_OUT_DIR, exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'ladder': ladder, 'report': report}, fh, indent=1)

    print(f'\nsection exponents (expect ≈1 vs {report["knob"]}; flag ≥ {FLAG_TIME_EXP}):')
    for sec, e in report['sections'].items():
        print(f"  {sec:10s} k={e['exponent']:6.2f}  r²={e['r2']:.2f}")
    if report['offenders']:
        print(f'\nOFFENDERS (count k ≥ {FLAG_COUNT_EXP} or wall k ≥ {FLAG_TIME_EXP}, '
              f'r² ≥ {MIN_R2}):')
        for o in report['offenders'][:20]:
            extra = f"  counts={o['counts']}" if 'counts' in o else ''
            print(f"  k={o['exponent']:5.2f}  [{o['kind']}]  {o['name']}{extra}")
    else:
        print('\nno super-linear offenders flagged at these thresholds')

    render_dir = os.path.join(_OUT_DIR, 'render')
    os.makedirs(render_dir, exist_ok=True)
    png = os.path.join(render_dir, f'growth_{args.ladder}_{args.knob}_s{args.seed}.png')
    _growth_png(report, png)
    print(f'\nwrote {os.path.relpath(out, _REPO_ROOT)} and {os.path.relpath(png, _REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
