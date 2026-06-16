"""
run_analysis.py — standalone graph generator for completed simulation runs.

Reads sim_meta.json files written by run_simulation.py, rebuilds warehouse
aisle maps via build_shared_assets, and calls run_config_analysis to produce
all plots.

Usage:
  python run_analysis.py <base_dir>
  python run_analysis.py C:\\...\\comparison_20260605_120000
"""

import argparse
import concurrent.futures
import json
import logging
import os
import sys

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, _HERE)

from run_simulation import (
    build_shared_assets,
    discover_db_pairs,
    find_latest_db_pairs,
    _setup_logging,
    _clean_path,
    _OUTPUT_DIR,
)
from Comparison_Plots import (
    run_config_analysis as _run_config_analysis,
    run_aggregate_analysis as _run_aggregate_analysis,
)


# Keys run_config_analysis actually reads from `shared` — a small, picklable slice
# sent to worker processes (avoids pickling the warehouse / inventory objects).
_SLIM_KEYS = ('aisle_unittype_map', 'aisle_handling_map', 'k_pickers', 'total_bins')


def _analyze_config_worker(sim_result: dict, slim_shared: dict):
    """Picklable unit of work: plot one config in its own process.

    matplotlib's Agg backend + pyplot is process-safe (each process has its own
    pyplot state), so configs fan out cleanly.  Returns (run_dir, error_or_None).
    """
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-cp1252 log chars (e.g. λ)
    except Exception:
        pass
    log = logging.getLogger('analysis')
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    try:
        _run_config_analysis(sim_result, slim_shared, log)
        return (sim_result.get('run_dir'), None)
    except Exception as exc:  # noqa: BLE001 — report, don't kill the pool
        return (sim_result.get('run_dir'), repr(exc))


def _sim_result_from_meta(meta: dict) -> dict:
    sim_result = {k: meta[k] for k in ('name', 'run_dir', 'strategies')}
    sim_result['optimal_sigma_fd'] = meta.get('optimal_sigma_fd', 0.0)
    sim_result['inventory'] = meta.get('inventory', '')
    return sim_result


def _run_aggregate(base_dir: str, top_n: int, top_by: str, log: logging.Logger,
                   no_stats: bool = False) -> None:
    """Cross-profile roll-up: group every config's series.json by pick-config name
    (the leaf dir) across all profiles, then emit one aggregate suite per group under
    base_dir/_aggregate/<pickcfg>/."""
    groups: dict[str, list] = {}
    for prof in sorted(os.listdir(base_dir)):
        prof_dir = os.path.join(base_dir, prof)
        if not os.path.isdir(prof_dir) or prof.startswith('_'):
            continue
        for cfg in sorted(os.listdir(prof_dir)):
            sp = os.path.join(prof_dir, cfg, 'series.json')
            if not os.path.exists(sp):
                continue
            try:
                with open(sp) as f:
                    groups.setdefault(cfg, []).append(json.load(f))
            except (OSError, ValueError) as exc:
                log.error(f'  bad series.json {sp}: {exc}')
    for cfg, plist in groups.items():
        out_dir = os.path.join(base_dir, '_aggregate', cfg)
        try:
            _run_aggregate_analysis(plist, out_dir, top_n, cfg, log, top_by=top_by,
                                    no_stats=no_stats)
        except Exception as exc:
            log.error(f'  aggregate FAILED for {cfg}: {exc}', exc_info=True)


def run_analysis(base_dir: str, log: logging.Logger, workers: int = 1,
                 top_n: int = 1, top_by: str = 'global', no_stats: bool = False) -> None:
    """Re-run analysis (plots + CSV summaries) on all completed sims under *base_dir*.

    Scans base_dir/pair_label/config_name/sim_meta.json.  build_shared_assets runs
    once per pair (main process, sequential).  The per-config plotting — the heavy
    part, especially with many strategies — is fanned out across `workers` processes
    when workers > 1, overlapping with the next pair's shared build.  workers=1 keeps
    the original sequential behaviour.  After every config is plotted, a cross-profile
    aggregate suite is emitted per pick-config.
    """
    pool = (concurrent.futures.ProcessPoolExecutor(max_workers=workers)
            if workers and workers > 1 else None)
    futures = []
    try:
        for pair_name in sorted(os.listdir(base_dir)):
            pair_dir = os.path.join(base_dir, pair_name)
            if not os.path.isdir(pair_dir):
                continue

            config_metas: list[dict] = []
            inv_db = aff_db = None
            for cfg_name in sorted(os.listdir(pair_dir)):
                meta_path = os.path.join(pair_dir, cfg_name, 'sim_meta.json')
                if not os.path.exists(meta_path):
                    continue
                with open(meta_path) as f:
                    meta = json.load(f)
                config_metas.append(meta)
                if inv_db is None:
                    inv_db = meta.get('inv_db')
                    aff_db = meta.get('aff_db')

            if not config_metas or inv_db is None or aff_db is None:
                continue

            log.info(f'  Pair: {pair_name}  ({len(config_metas)} config(s))')
            try:
                shared = build_shared_assets(inv_db, aff_db, log)
            except Exception as exc:
                log.error(f'  build_shared_assets failed for {pair_name}: {exc}',
                          exc_info=True)
                continue
            slim = {k: shared.get(k) for k in _SLIM_KEYS}  # picklable subset per pair
            slim['top_n'] = top_n
            slim['top_by'] = top_by
            slim['no_stats'] = no_stats

            for meta in config_metas:
                sim_result = _sim_result_from_meta(meta)
                if pool is not None:
                    futures.append(pool.submit(_analyze_config_worker, sim_result, slim))
                else:
                    _, err = _analyze_config_worker(sim_result, slim)
                    if err:
                        log.error(f"  Analysis failed for {meta.get('name', '?')}: {err}")

        if pool is not None:
            log.info(f'  Plotting {len(futures)} configs across {workers} processes...')
            for fut in concurrent.futures.as_completed(futures):
                run_dir, err = fut.result()
                if err:
                    log.error(f'  Analysis failed for {run_dir}: {err}')
                else:
                    log.info(f'  Done: {run_dir}')
    finally:
        if pool is not None:
            pool.shutdown()

    # ── cross-profile aggregate (main process; needs all series.json on disk) ──
    log.info('  Building cross-profile aggregate suites...')
    _run_aggregate(base_dir, top_n, top_by, log, no_stats=no_stats)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate plots and CSV summaries from completed simulation runs.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        'base_dir',
        nargs='?',
        default=None,
        help='Comparison output directory (e.g. comparison_20260605_120000). '
             'Relative paths are resolved under COMPARISON_OUTPUT_DIR.',
    )
    parser.add_argument(
        '--workers', type=int, default=1,
        help='Parallel processes for graph generation (1 = sequential). '
             'Each config is plotted in its own process.',
    )
    parser.add_argument(
        '--top-n', type=int, default=1, dest='top_n',
        help='Number of best strategies (by steady-state throughput) to overlay in '
             'the compare/top/ plots.',
    )
    parser.add_argument(
        '--top-by', default='global', dest='top_by',
        choices=('global', 'initial', 'assignment', 'reslot'),
        help="How the compare/top/ plot picks its strategies: 'global' = top-N overall "
             "(tends to be dominated by optimal-start); a dimension = top-N WITHIN each "
             "value of it (e.g. 'initial' overlays the best Uniform vs best Optimal).",
    )
    parser.add_argument(
        '--no-stats', action='store_true', dest='no_stats',
        help='Skip the statistical significance suite (stats/ dirs); only descriptive '
             'plots + series.json are produced.',
    )
    args = parser.parse_args()

    if args.base_dir is None:
        parser.print_help()
        sys.exit(1)

    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-cp1252 log chars (e.g. λ)
    except Exception:
        pass

    base_dir = (args.base_dir if os.path.isabs(args.base_dir)
                else os.path.join(_OUTPUT_DIR, args.base_dir))
    if not os.path.isdir(base_dir):
        sys.exit(f'Directory not found: {base_dir}')

    log = _setup_logging(os.path.join(base_dir, 'analysis.log'))
    log.info(f'run_analysis  dir: {base_dir}  (workers={args.workers}, '
             f'top_n={args.top_n}, top_by={args.top_by}, stats={not args.no_stats})')
    run_analysis(base_dir, log, workers=args.workers, top_n=args.top_n,
                 top_by=args.top_by, no_stats=args.no_stats)
    log.info('Done.')


if __name__ == '__main__':
    main()
