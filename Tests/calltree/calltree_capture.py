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
        # A capture without its configuration is unreadable: `cfg=none` and `cfg=inbound_yard`
        # produce different trees from the same flags, and the difference is the whole point.
        'config': getattr(args, 'config', 'none'),
        # Coupled and uncoupled are different EXPERIMENTS, not two runs of one -- see
        # capture_fullfid's docstring. Recording it beside `config` for the same reason:
        # a capture whose mode is not on the record is unreadable a week later.
        'coupled': bool(getattr(args, 'coupled', False)),
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
    # `cfg` belongs in the FILENAME as well as the index tags, the way `calltree_growth` writes
    # `growth__cfg-<name>_knob-<knob>_...`.  Two captures of different configurations are not
    # comparable and must not be distinguishable only by opening them.
    mode = 'coupled' if getattr(args, 'coupled', False) else getattr(args, 'config', 'none')
    return archive_path('capture', cfg=mode, tier=args.tier, seed=args.seed)


# ── tiers ─────────────────────────────────────────────────────────────────────

def capture_inproc(args) -> dict:
    """micro/meso: two-pass in-process capture.

    `--config` names a `calltree_growth.CONFIGS` cell, the SAME registry the ladder uses, so a
    capture and a ladder rung cannot drift on what a configuration means.  Without it this tier
    could only ever capture `cfg=none`: `build_assets`'s put-away, receiving and inbound
    parameters all default to off, so `SiteReceiving`, `YardTransit` and every symbol in
    `Inbound/gain.py` were unreachable from a TREE -- the ladder could fit their exponents and
    nothing could show where the time went.

    The overlay is merged UNDER the CLI, matching `run_meso_ladder`: an explicit `--skus` owns
    the axis it names, exactly as a rung does.
    """
    import calltree_growth as growth          # acyclic: growth imports scenarios, not capture

    cfg = growth.CONFIGS[args.config]
    merged = dict(cfg.overlay)
    merged.update(dict(n_skus=args.skus, bins_per_aisle=args.bins_per_aisle,
                       n_pickers=args.pickers, strategy=args.strategy,
                       n_batches=args.batches))
    build, run_kw, n_batches = growth._split_kwargs(merged, args.seed)
    if args.tier != 'meso':
        n_batches = min(n_batches, 5)
    runner = scenarios.run_meso if args.tier == 'meso' else scenarios.run_micro
    if run_kw and args.tier != 'meso':
        run_kw = {}                            # run_micro takes no deadlines

    # pass 1 — untraced walls
    assets = scenarios.build_assets(**build)
    t0 = time.perf_counter()
    r_u = runner(assets, n_batches=n_batches, seed=args.seed, **run_kw)
    wall_u = time.perf_counter() - t0
    if r_u.placements == 0 and not build.get('inbound'):
        raise SystemExit('scenario fired zero reorder placements — the measurement would '
                         'silently exclude every assignment function (see README)')
    if build.get('inbound') and r_u.placements == 0 and getattr(r_u, 'drains', 0) == 0:
        # An INBOUND cell may legitimately place little while trailers stand, but a cell that
        # neither placed nor drained ran nothing at all, and that must not read as a result.
        raise SystemExit('inbound cell fired zero placements AND zero drains — nothing ran')

    # pass 2 — traced tree, fresh assets, same seed
    assets = scenarios.build_assets(**build)
    tr = CallTreeTracer(track_c_calls=not args.no_c_calls)
    tr.start()
    t0 = time.perf_counter()
    r_t = runner(assets, n_batches=n_batches, seed=args.seed, tracer=tr, **run_kw)
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
    """fullfid: trace sr._run_strategy_worker in-process; sections via SECTION_MAP.

    `--coupled` routes through `_prepare_site_run` so BOTH leaves run as one site unit.
    It is not a variant of the same measurement: uncoupled takes `_channel_runs[0]`, which
    workunits.py guarantees is the STORE, and the store leaf's yard barely stands (T
    1.14-1.30 across the whole uncoupled ladder) while the coupled site's reaches 2.25 at
    40k SKUs. Every cost that scales with yard depth -- `plan_order` is O(T^2) in it -- is
    therefore invisible in an uncoupled tree, which is a demonstrated fact about this tier
    rather than a caution. PutawayPool, the two-leaf compose_site_view and _unload_split's
    door teams exist only on this path too.
    """
    # pass 1 — untraced; the worker's own t_* totals are the section walls
    t0 = time.perf_counter()
    res_u = scenarios.run_fullfid(n_batches=args.batches, max_skus=args.skus,
                                  strategy=args.strategy if args.strategy != 'auto' else None,
                                  coupled=args.coupled)
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
                          strategy=args.strategy if args.strategy != 'auto' else None,
                          coupled=args.coupled)
    wall_t = time.perf_counter() - t0
    root = tr.tree()

    doc = to_capture_dict(root, meta=_meta(args, {'max_skus': args.skus,
                                                  'arm': res_u.get('arm'),
                                                  'coupled': bool(args.coupled)},
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
    # The SAME registry the ladder uses, so a capture and a rung cannot mean different
    # things by one name.  Choices are read from it rather than restated.
    import calltree_growth as _growth
    ap.add_argument('--config', choices=tuple(_growth.CONFIGS), default='none',
                    help='named scenario configuration, layered UNDER the flags below. '
                         '"none" is the plain capture. Without this the inbound and '
                         'put-away machinery never executes and the tree cannot show it.')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--skus', type=int, default=2_000)
    ap.add_argument('--bins-per-aisle', type=int, default=100)
    ap.add_argument('--batches', type=int, default=20)
    ap.add_argument('--pickers', type=int, default=10)
    ap.add_argument('--strategy', default=scenarios.DEFAULT_STRATEGY)
    ap.add_argument('--run-log', default=None, help='macro: explicit run.log path')
    ap.add_argument('--no-c-calls', action='store_true',
                    help='skip <c>: leaf counting (lower overhead, fewer counts)')
    ap.add_argument('--coupled', action='store_true',
                    help='fullfid only: run BOTH leaves as one site unit via _prepare_site_run. '
                         'The uncoupled default traces the STORE leaf, whose yard barely stands, '
                         'so every yard-depth-driven cost is invisible without this.')
    ap.add_argument('--cprofile', action='store_true', help='also print tier-B cross-check')
    ap.add_argument('--speedscope', action='store_true')
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args(argv)
    if args.coupled and args.tier != 'fullfid':
        # Silently ignoring it would archive an UNCOUPLED tree tagged coupled=True, and a
        # tag value is permanent in out/index.json. A refusal costs one retype.
        ap.error(f'--coupled is a fullfid-tier flag; --tier {args.tier} has no site unit')

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
        # `cfg` in the TAGS, not just in the doc: two captures of different configurations
        # that share a tag set merge in `out/index.json` forever, and a tag value is
        # permanent.  `calltree_growth` records the same lesson at its own archive site.
        record('capture', out, tags={'tier': args.tier, 'seed': args.seed,
                                     'cfg': args.config,
                                     'coupled': bool(args.coupled)},
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
