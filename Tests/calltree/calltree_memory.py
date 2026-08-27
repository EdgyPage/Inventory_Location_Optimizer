"""
calltree_memory.py — the memory dimension: per-section allocation peaks, retained growth,
top allocation sites, and true-scale worker RSS.

Two tiers, mirroring the wall-time design (each trusted for what it measures):

  meso (tracemalloc, in-process)
      MemTracker quacks like CallTreeTracer's section() interface, so the UNMODIFIED
      calltree_scenarios.run_meso loop drives it. Per section: peak allocation delta
      (tracemalloc.reset_peak per entry) and net retained bytes; per batch: end-of-batch
      retained size (a rising staircase = an accumulator/leak, flat = churn only). At
      stop(): top project allocation sites from a traceback snapshot. Overhead ~2-5x wall
      — NEVER quote wall times from a mem pass.

  deep (psutil RSS, subprocess)
      Launches real `run_simulation` rungs and samples the whole process TREE's RSS from
      the parent every 2s — peak total and peak single worker. Zero product edits; the
      cost of non-invasiveness is granularity (RSS is the OS's opinion, includes
      interpreter+numpy baselines per spawned worker).

  --ladder skus fits log-log exponents on the meso per-section peaks (k_mem), same
  method and thresholds-by-noise philosophy as calltree_growth.

Usage:
    python Tests/calltree/calltree_memory.py --tier meso --skus 2000 --batches 20
    python Tests/calltree/calltree_memory.py --ladder skus --seed 42
    python Tests/calltree/calltree_memory.py --deep --workers 18      # 2 rungs, ~1h

Not collected by pytest. Outputs land in out/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tracemalloc
from contextlib import contextmanager

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import calltree_scenarios as scenarios
from calltree_growth import CONFIGS, _MESO_LADDERS, _fit_loglog, _split_kwargs

_OUT_DIR = os.path.join(_HERE, 'out')
_PROJECT_PREFIXES = tuple(os.path.join(_REPO_ROOT, p) + os.sep
                          for p in ('Warehouse', 'Inbound', 'Optimization', 'Schema'))


class MemTracker:
    """Duck-typed stand-in for CallTreeTracer: section() measures ALLOCATION, not time.

    Also tracks GC pressure (collection counts + summed pause wall via gc.callbacks) and
    the live-object census — the two CPython mechanisms by which memory GROWTH becomes
    wall-time SLOWDOWN: gen-2 pause cost scales with live objects, pause frequency with
    allocation rate. A section whose wall exponent exceeds its count exponent is the
    fingerprint; these numbers say whether GC is the culprit."""

    def __init__(self, top_n: int = 25, trace_frames: int = 20) -> None:
        self.top_n        = top_n
        self.trace_frames = trace_frames
        self.section_peak: dict[str, int] = {}     # max peak-delta over all entries
        self.section_net:  dict[str, int] = {}     # summed net retained bytes
        self.batch_retained: list[int] = []        # end-of-section-cycle current size
        self.top_sites: list[dict] = []
        self._last_current = 0
        self.gc_pause_s   = 0.0
        self.gc_collects  = [0, 0, 0]              # per generation
        self._gc_t0       = 0.0

    def _gc_cb(self, phase: str, info: dict) -> None:
        if phase == 'start':
            self._gc_t0 = time.perf_counter()
        else:
            self.gc_pause_s += time.perf_counter() - self._gc_t0
            self.gc_collects[info.get('generation', 0)] += 1

    def start(self) -> None:
        import gc
        gc.callbacks.append(self._gc_cb)
        self.live_objects_start = len(gc.get_objects())
        tracemalloc.start(self.trace_frames)

    @contextmanager
    def section(self, name: str):
        base_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        try:
            yield
        finally:
            current, peak = tracemalloc.get_traced_memory()
            delta_peak = peak - base_current
            if delta_peak > self.section_peak.get(name, -1):
                self.section_peak[name] = delta_peak
            self.section_net[name] = self.section_net.get(name, 0) + (current - base_current)
            # one retained sample per t_extract close = once per batch in the meso loop
            if name == 't_extract':
                self.batch_retained.append(current)
            self._last_current = current

    def stop(self) -> None:
        import gc
        try:
            gc.callbacks.remove(self._gc_cb)
        except ValueError:
            pass
        self.live_objects_end = len(gc.get_objects())
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics('lineno')
        rows = []
        for st in stats:
            fr = st.traceback[0]
            if not fr.filename.startswith(_PROJECT_PREFIXES):
                continue
            rel = os.path.relpath(fr.filename, _REPO_ROOT).replace(os.sep, '/')
            rows.append({'site': f'{rel}:{fr.lineno}', 'kib': round(st.size / 1024, 1),
                         'count': st.count})
            if len(rows) >= self.top_n:
                break
        self.top_sites = rows
        tracemalloc.stop()

    def report(self) -> dict:
        retained = self.batch_retained
        slope = 0.0
        if len(retained) >= 3:      # bytes retained per batch (linear fit, plain axes)
            n = len(retained)
            xs = list(range(n))
            mx, my = sum(xs) / n, sum(retained) / n
            sxx = sum((x - mx) ** 2 for x in xs) or 1.0
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, retained)) / sxx
        return {
            'section_peak_kib': {k: round(v / 1024, 1) for k, v in self.section_peak.items()},
            'section_net_kib':  {k: round(v / 1024, 1) for k, v in self.section_net.items()},
            'retained_end_kib': round(retained[-1] / 1024, 1) if retained else 0,
            'retained_per_batch_kib': round(slope / 1024, 2),
            'gc_pause_s': round(self.gc_pause_s, 3),
            'gc_collects': list(self.gc_collects),
            'live_objects': [self.live_objects_start, self.live_objects_end],
            'top_sites': self.top_sites,
        }


# ── meso tier ────────────────────────────────────────────────────────────────

def run_meso_mem(*, n_skus: int, bins_per_aisle: int = 100, n_pickers: int = 10,
                 n_batches: int = 20, seed: int = 42, config: str = 'none') -> dict:
    """One traced-for-allocation arm.  `config` is a `calltree_growth.CONFIGS` name.

    The overlay goes through the SAME `_split_kwargs` the growth ladder uses rather than a
    second copy -- two copies of "what a rung means" would drift, and the whole point of
    running both tools is that `k_mem` and `k_count` describe the same scenario.

    No extra instrumentation is needed to see a data-structure change: `MemTracker.stop()`
    already reports per-line allocation for `Warehouse/`, so a per-queue partition of the held
    list shows up in `top_sites` on its own -- but only once a configuration can reach it,
    which before this argument nothing could.
    """
    kwargs = dict(CONFIGS[config].overlay,
                  n_skus=n_skus, bins_per_aisle=bins_per_aisle, n_pickers=n_pickers)
    build, run_kw, _nb = _split_kwargs(kwargs, seed)
    assets = scenarios.build_assets(**build)
    mt = MemTracker()
    mt.start()
    r = scenarios.run_meso(assets, n_batches=n_batches, seed=seed, tracer=mt, **run_kw)
    mt.stop()
    doc = mt.report()
    # `assets.sizes` already carries put_timing / put_split / put_staging / recv_crew, so the
    # artifact self-documents which configuration produced it.
    doc['sizes'] = dict(assets.sizes, n_batches=n_batches, config=config,
                        picks=r.picks, placements=r.placements)
    return doc


def ladder_mem(seed: int, config: str = 'none') -> dict:
    rungs = []
    # The SAME rungs the growth ladder uses for this configuration.  Fitting k_mem over
    # different x values than k_count makes the two incomparable, and comparing them is the
    # reason both tools exist.
    _rungs = CONFIGS[config].rungs.get('skus') or _MESO_LADDERS['skus']
    for _r in _rungs:
        n = _r['n_skus']
        doc = run_meso_mem(n_skus=n, seed=seed, config=config)
        rungs.append({'x': doc['sizes']['n_skus_sampled'], 'doc': doc})
        print(f"  rung skus={n}: peak by section (KiB) "
              f"{ {k: v for k, v in sorted(doc['section_peak_kib'].items(), key=lambda kv: -kv[1])[:3]} }"
              f" retained/batch={doc['retained_per_batch_kib']} KiB"
              f" gc_pause={doc['gc_pause_s']}s gen2={doc['gc_collects'][2]}"
              f" live_obj={doc['live_objects'][1]:,}")
    xs = [r['x'] for r in rungs]
    # Persist the FULL per-rung docs — gc_pause/gc_collects/live_objects once went only to
    # stdout and were lost to a tail-truncated log; evidence lands in the archive, always.
    report = {'xs': xs, 'rungs': [r['doc'] for r in rungs], 'sections': {}, 'offenders': []}
    names = set()
    for r in rungs:
        names.update(r['doc']['section_peak_kib'])
    for name in sorted(names):
        ys = [r['doc']['section_peak_kib'].get(name, 0.0) for r in rungs]
        slope, r2 = _fit_loglog(xs, [max(y, 0.001) for y in ys])
        report['sections'][name] = {'k_mem': round(slope, 3), 'r2': round(r2, 3),
                                    'peaks_kib': ys}
        if r2 >= 0.9 and slope >= 1.3:
            report['offenders'].append({'name': name, 'k_mem': round(slope, 3),
                                        'peaks_kib': ys})
    # retained-growth check at the largest rung: a staircase is a leak candidate
    report['retained_per_batch_kib_at_max'] = rungs[-1]['doc']['retained_per_batch_kib']
    report['top_sites_at_max'] = rungs[-1]['doc']['top_sites']
    return report


# ── deep tier: worker-tree RSS from the parent ───────────────────────────────

def deep_rss(workers: int, rungs: tuple[dict, ...]) -> list[dict]:
    import psutil   # hand-run tool: hard import, named failure is fine

    results = []
    for kw in rungs:
        cmd = [sys.executable, '-m', 'Optimization.run_simulation',
               '--workers', str(workers), '--spec', 'single',
               '--n-batches', str(kw['n_batches']),
               '--max-skus', str(kw['max_skus']),
               '--s-max-bins', str(kw['s_max_bins']),
               '--ff-max-bins', str(kw['ff_max_bins']),
               '--keyframe-interval', '0']
        print(f"  rung max_skus={kw['max_skus']:,}: sampling RSS "
              f'({workers} workers)...', flush=True)
        env = dict(os.environ, MPLBACKEND='Agg')
        proc = subprocess.Popen(cmd, cwd=_REPO_ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ps = psutil.Process(proc.pid)
        peak_total = peak_worker = 0
        samples = 0
        t0 = time.perf_counter()
        while proc.poll() is None:
            try:
                procs = [ps] + ps.children(recursive=True)
                rss = [p.memory_info().rss for p in procs]
                peak_total  = max(peak_total, sum(rss))
                # children only — the parent holds shared assets and would mask workers
                if len(rss) > 1:
                    peak_worker = max(peak_worker, max(rss[1:]))
                samples += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(2.0)
        wall = time.perf_counter() - t0
        results.append({'max_skus': kw['max_skus'], 'wall_s': round(wall, 1),
                        'samples': samples,
                        'peak_total_gib': round(peak_total / 2**30, 2),
                        'peak_worker_gib': round(peak_worker / 2**30, 2)})
        print(f"    done {wall/60:.1f} min: peak tree {peak_total/2**30:.2f} GiB, "
              f"peak single worker {peak_worker/2**30:.2f} GiB")
    return results


_DEEP_RUNGS = (
    dict(max_skus=10_000, s_max_bins=15_000, ff_max_bins=20_000, n_batches=15),
    dict(max_skus=80_000, s_max_bins=100_000, ff_max_bins=132_000, n_batches=15),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='memory measurement: tracemalloc + RSS tiers')
    ap.add_argument('--tier', choices=('meso',), default=None)
    ap.add_argument('--ladder', choices=('skus',), default=None)
    ap.add_argument('--config', choices=tuple(CONFIGS), default='none',
                    help='named scenario configuration, shared with calltree_growth. NOT '
                         'honoured by --deep, which reads settings.py instead.')
    ap.add_argument('--deep', action='store_true')
    ap.add_argument('--skus', type=int, default=2_000)
    ap.add_argument('--batches', type=int, default=20)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--workers', type=int, default=18)
    args = ap.parse_args(argv)
    # cp1252 cannot encode the r-squared superscript in the summary below, so a redirected
    # stdout crashed AFTER the artifact was written.  Same fix as calltree_growth.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass
    os.makedirs(_OUT_DIR, exist_ok=True)

    if args.deep:
        if args.config != 'none':
            ap.error('--config does not reach --deep: the deep RSS tier launches real '
                     'run_simulation subprocesses, which read Optimization/config/settings.py. '
                     'Tagging the artifact with a cfg it did not use would be a false claim.')
        from calltree_store import archive_path, record
        res = deep_rss(args.workers, _DEEP_RUNGS)
        out = archive_path('memory', tier='deep-rss', workers=args.workers)
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1)
        record('memory', out, tags={'tier': 'deep-rss', 'workers': args.workers},
               summary={f"skus{r['max_skus']}": {'peak_total_gib': r['peak_total_gib'],
                                                 'peak_worker_gib': r['peak_worker_gib']}
                        for r in res})
        if len(res) == 2 and res[0]['peak_worker_gib'] > 0:
            ratio = res[1]['peak_worker_gib'] / max(res[0]['peak_worker_gib'], 1e-9)
            import math
            k = math.log(ratio) / math.log(res[1]['max_skus'] / res[0]['max_skus'])
            print(f'  worker-RSS scaling exponent k≈{k:.2f} over the 8x rung pair')
        print(f'wrote {os.path.relpath(out, _REPO_ROOT)}')
        return 0

    if args.ladder:
        from calltree_store import archive_path, record
        print(f'meso memory ladder over skus (seed {args.seed}, cfg {args.config}): '
              f'{CONFIGS[args.config].why}')
        report = ladder_mem(args.seed, args.config)
        _tags = {'tier': 'meso', 'knob': 'skus', 'seed': args.seed, 'cfg': args.config}
        out = archive_path('memory', **_tags)
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=1)
        record('memory', out, tags=_tags,
               summary={'k_mem': {k: v['k_mem'] for k, v in report['sections'].items()},
                        'retained_per_batch_kib': report['retained_per_batch_kib_at_max']})
        print(f"\nsection k_mem (peak allocation vs skus; flag >= 1.3):")
        for name, e in sorted(report['sections'].items()):
            print(f"  {name:10s} k_mem={e['k_mem']:6.2f}  r²={e['r2']:.2f}  "
                  f"peaks={e['peaks_kib']}")
        # NOT "8k": the top rung depends on the CONFIGURATION -- a staged config stops
        # at 2,400 because a floor makes each rung far more expensive.  The hardcoded
        # label claimed a size the run never reached.
        _top = report['xs'][-1]
        print(f"\nretained/batch at the {_top:,}-sku rung: "
              f"{report['retained_per_batch_kib_at_max']} KiB")
        print(f'top allocation sites at the {_top:,}-sku rung:')
        for s in report['top_sites_at_max'][:12]:
            print(f"  {s['kib']:>10.1f} KiB  x{s['count']:<8,} {s['site']}")
        print(f'wrote {os.path.relpath(out, _REPO_ROOT)}')
        return 0

    doc = run_meso_mem(n_skus=args.skus, n_batches=args.batches, seed=args.seed,
                       config=args.config)
    print(json.dumps(doc, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
