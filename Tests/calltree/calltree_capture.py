"""
calltree_capture.py — CLI: run a scenario two-pass and write a calltree-v1 JSON capture.

Two passes, one document:
  pass 1  UNTRACED — fresh assets, plain perf_counter section walls. These are the
          wall-clock truth (regression baselines compare THESE).
  pass 2  TRACED   — fresh assets, same seed, CallTreeTracer. Owns tree shape, exact
          call counts, and relative time attribution. Never a baseline: the document
          records the measured overhead multiplier so nobody mistakes traced seconds
          for real ones.

Usage (from the repo root):
    python Tests/calltree/calltree_capture.py --tier meso --seed 42
    python Tests/calltree/calltree_capture.py --tier meso --skus 2000 --batches 20 \
        -o Tests/calltree/out/a.json
    python Tests/calltree/calltree_capture.py --tier macro                # real run.log
    python Tests/calltree/calltree_capture.py --tier fullfid              # needs profile DBs
    python Tests/calltree/calltree_capture.py --tier meso --cprofile      # tier-B cross-check

Outputs land in Tests/calltree/out/ (gitignored). --speedscope additionally writes a
speedscope-format JSON (open at speedscope.app or a local copy — it is just a file format).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Tests/<sub>/ -> repo root
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import calltree_scenarios as scenarios
import calltree_tracer as tracer_mod
from calltree_tracer import CallTreeTracer, SECTIONS, attribute_sections, to_capture_dict, \
    write_capture

_OUT_DIR = os.path.join(_HERE, 'out')


def _short_commit() -> str:
    try:
        out = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=_REPO_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def _meta(args, sizes: dict, overhead: float | None) -> dict:
    return {
        'scenario': f'{args.tier}',
        'tier': args.tier,
        'seed': args.seed,
        'strategy': args.strategy,
        'sizes': sizes,
        'n_batches': args.batches,
        'python': sys.version.split()[0],
        'commit': _short_commit(),
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'trace_overhead_x': round(overhead, 2) if overhead else None,
    }


def _default_out(args) -> str:
    from calltree_store import archive_path
    return archive_path('capture', tier=args.tier, seed=args.seed)


# ── tiers ─────────────────────────────────────────────────────────────────────

def capture_inproc(args) -> dict:
    """micro/meso: two-pass in-process capture."""
    build = dict(n_skus=args.skus, bins_per_aisle=args.bins_per_aisle,
                 n_pickers=args.pickers, seed=args.seed, strategy=args.strategy)
    n_batches = args.batches if args.tier == 'meso' else min(args.batches, 5)
    runner    = scenarios.run_meso if args.tier == 'meso' else scenarios.run_micro

    # pass 1 — untraced walls
    assets = scenarios.build_assets(**build)
    t0 = time.perf_counter()
    r_u = runner(assets, n_batches=n_batches, seed=args.seed)
    wall_u = time.perf_counter() - t0
    if r_u.placements == 0:
        raise SystemExit('scenario fired zero reorder placements — the measurement would '
                         'silently exclude every assignment function (see README)')

    # pass 2 — traced tree, fresh assets, same seed
    assets = scenarios.build_assets(**build)
    tr = CallTreeTracer(track_c_calls=not args.no_c_calls)
    tr.start()
    t0 = time.perf_counter()
    r_t = runner(assets, n_batches=n_batches, seed=args.seed, tracer=tr)
    wall_t = time.perf_counter() - t0
    tr.stop()
    root = tr.tree()

    if (r_u.picks, r_u.placements) != (r_t.picks, r_t.placements):
        raise SystemExit(f'nondeterministic scenario: untraced pass did '
                         f'{r_u.picks}p/{r_u.placements}pl, traced did '
                         f'{r_t.picks}p/{r_t.placements}pl — fix the seed plumbing first')

    sections_wall = {k: v for k, v in r_u.sections.items()}
    sections_wall.setdefault('t_save', 0.0)
    doc = to_capture_dict(root, meta=_meta(args, dict(assets.sizes,
                                                      picks=r_u.picks,
                                                      placements=r_u.placements,
                                                      reorders=r_u.reorders),
                                           wall_t / max(wall_u, 1e-9)),
                          sections_wall=sections_wall,
                          wall_untraced=wall_u, wall_traced=wall_t)
    return doc


def capture_fullfid(args) -> dict:
    """fullfid: trace sr._run_strategy_worker in-process; sections via SECTION_MAP."""
    # pass 1 — untraced; the worker's own t_* totals are the section walls
    t0 = time.perf_counter()
    res_u = scenarios.run_fullfid(n_batches=args.batches, max_skus=args.skus,
                                  strategy=args.strategy if args.strategy != 'auto' else None)
    wall_u = time.perf_counter() - t0
    worker = res_u.get('worker_result') or {}
    sections_wall = {s: float(worker.get(s, 0.0)) for s in SECTIONS
                     if isinstance(worker.get(s, None), (int, float))}
    if not sections_wall:
        sections_wall = {s: 0.0 for s in SECTIONS}

    # pass 2 — traced
    tr = CallTreeTracer(track_c_calls=not args.no_c_calls)
    t0 = time.perf_counter()
    scenarios.run_fullfid(tracer=tr, n_batches=args.batches, max_skus=args.skus,
                          strategy=args.strategy if args.strategy != 'auto' else None)
    wall_t = time.perf_counter() - t0
    root = tr.tree()

    doc = to_capture_dict(root, meta=_meta(args, {'max_skus': args.skus,
                                                  'arm': res_u.get('arm')},
                                           wall_t / max(wall_u, 1e-9)),
                          sections_wall=sections_wall,
                          wall_untraced=wall_u, wall_traced=wall_t)
    doc['sections_attributed'] = {k: round(v, 6)
                                  for k, v in attribute_sections(root).items()}
    return doc


def capture_macro(args) -> dict:
    """macro: a real run's own section means; empty tree."""
    parsed = scenarios.macro_sections(args.run_log)
    root   = tracer_mod.Node('__root__', kind='root')
    doc = to_capture_dict(root,
                          meta=_meta(args, {'source': parsed['source'],
                                            'checkpoints': parsed['checkpoints']}, None),
                          sections_wall=parsed['sections'],
                          wall_untraced=sum(parsed['sections'].values()),
                          wall_traced=None)
    return doc


# ── tier B: cProfile cross-check ─────────────────────────────────────────────

def cprofile_crosscheck(args) -> str:
    """cProfile the same scenario (thread-blind, pair-approximated) and return the
    top-30 project-filtered cumulative report as text (profile_lifecycle idiom)."""
    import cProfile
    import io
    import pstats

    build = dict(n_skus=args.skus, bins_per_aisle=args.bins_per_aisle,
                 n_pickers=args.pickers, seed=args.seed, strategy=args.strategy)
    assets = scenarios.build_assets(**build)
    pr = cProfile.Profile()
    pr.enable()
    scenarios.run_meso(assets, n_batches=args.batches, seed=args.seed)
    pr.disable()
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf).sort_stats('cumulative')
    stats.print_stats(r'Warehouse|Optimization|Schema', 30)
    return buf.getvalue()


# ── speedscope export (optional; just a file format, zero deps) ──────────────

def to_speedscope(doc: dict) -> dict:
    """Synthesize an 'evented' speedscope profile from the aggregated tree: each node
    becomes one open/close pair of duration cum_s at its path position."""
    frames: list[dict] = []
    frame_ix: dict[str, int] = {}
    events: list[dict] = []
    clock = [0.0]

    def fidx(name: str) -> int:
        if name not in frame_ix:
            frame_ix[name] = len(frames)
            frames.append({'name': name})
        return frame_ix[name]

    def walk(node: dict) -> None:
        for c in node.get('children', []):
            i  = fidx(c['name'])
            at = clock[0]
            events.append({'type': 'O', 'frame': i, 'at': at})
            walk(c)
            clock[0] = max(clock[0], at + c['cum_s'])
            events.append({'type': 'C', 'frame': i, 'at': clock[0]})

    walk(doc['tree'])
    total = clock[0]
    return {
        '$schema': 'https://www.speedscope.app/file-format-schema.json',
        'shared': {'frames': frames},
        'profiles': [{'type': 'evented', 'name': doc['meta']['scenario'],
                      'unit': 'seconds', 'startValue': 0, 'endValue': total,
                      'events': events}],
        'name': doc['meta']['scenario'],
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='two-pass call-tree capture')
    ap.add_argument('--tier', choices=('micro', 'meso', 'fullfid', 'macro'), default='meso')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--skus', type=int, default=2_000)
    ap.add_argument('--bins-per-aisle', type=int, default=100)
    ap.add_argument('--batches', type=int, default=20)
    ap.add_argument('--pickers', type=int, default=10)
    ap.add_argument('--strategy', default=scenarios.DEFAULT_STRATEGY)
    ap.add_argument('--run-log', default=None, help='macro: explicit run.log path')
    ap.add_argument('--no-c-calls', action='store_true',
                    help='skip <c>: leaf counting (lower overhead, fewer counts)')
    ap.add_argument('--cprofile', action='store_true', help='also print tier-B cross-check')
    ap.add_argument('--speedscope', action='store_true')
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args(argv)

    try:
        if args.tier in ('micro', 'meso'):
            doc = capture_inproc(args)
        elif args.tier == 'fullfid':
            doc = capture_fullfid(args)
        else:
            doc = capture_macro(args)
    except scenarios.ScenarioUnavailable as e:
        print(f'unavailable: {e}')
        return 2

    out = args.out or _default_out(args)
    write_capture(doc, out)
    if args.out is None:                       # archived default -> index it
        from calltree_store import record
        record('capture', out, tags={'tier': args.tier, 'seed': args.seed},
               summary={'fingerprint': doc['counts_fingerprint'][:16],
                        'wall_untraced_s': doc['wall_s']['untraced'],
                        'sections': {s['name']: s['wall_s'] for s in doc['sections']
                                     if s['wall_s'] > 0}})
    rel = os.path.relpath(out, _REPO_ROOT)
    w = doc['wall_s']
    print(f'wrote {rel}')
    print(f"  wall: untraced={w['untraced']}s traced={w['traced']}s"
          f" overhead={doc['meta']['trace_overhead_x']}x")
    for s in doc['sections']:
        if s['wall_s'] > 0:
            print(f"  {s['name']:10s} {s['wall_s']:8.3f}s  {s['share']:6.1%}")
    print(f"  fingerprint: {doc['counts_fingerprint'][:16]}")

    if args.speedscope:
        ss_path = os.path.splitext(out)[0] + '.speedscope.json'
        with open(ss_path, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(to_speedscope(doc), fh)
        print(f'wrote {os.path.relpath(ss_path, _REPO_ROOT)}')

    if args.cprofile and args.tier in ('micro', 'meso'):
        print('\n── tier-B cProfile cross-check (thread-blind; pairs, not paths) ──')
        print(cprofile_crosscheck(args))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
