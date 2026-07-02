"""run_analysis.py — registry-driven graph generator for completed simulation runs.

Reads sim_meta.json files written by run_simulation.py, rebuilds warehouse aisle maps via
build_shared_assets, then runs the graphs/analyses registered under Performance_Evaluations
(selected by a preset).  Each graph is a self-registering module; add/remove/tune graphs by
editing Performance_Evaluations/presets.py — not this file.

Parallelism is a single FLAT worker pool (mirrors run_simulation): one global job list across
all pairs × configs fed to one ProcessPoolExecutor, then a second flat pool for the
cross-profile aggregate stage.  `--granularity config` (default) = one job per config (context
loaded once, shared across its graphs); `--granularity graph` = one job per (config, graph)
for maximum core utilization on sparse runs.

Usage:
  python run_analysis.py <base_dir>
  python run_analysis.py <base_dir> --preset BY_INITIAL --workers 8
  python run_analysis.py <base_dir> --granularity graph --set compare.top_metric.top_n=3
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
    _setup_logging,
    _OUTPUT_DIR,
)

# Importing the package fires every @evaluation (also re-fires in each spawned worker),
# so the registry is populated before any job runs.
import Performance_Evaluations  # noqa: F401  (side effect: populate registry + set Agg backend)
from Performance_Evaluations.core.registry import EVAL_BY_KEY
from Performance_Evaluations.core.context import EvalContext, AggregateContext
from Performance_Evaluations import driver
from Performance_Evaluations.presets import PRESETS


# Keys the context reads from `shared` — a small, picklable slice sent to worker processes
# (avoids pickling the warehouse / inventory objects).
_SLIM_KEYS = ('aisle_unittype_map', 'aisle_handling_map', 'k_pickers', 'total_bins')

# Per-process context caches (graph granularity: co-scheduled graphs of one config/group
# that land on the same worker reuse a single loaded context).
_CFG_CTX: dict = {}
_AGG_CTX: dict = {}


def _worker_log() -> logging.Logger:
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
    return log


def _sim_result_from_meta(meta: dict) -> dict:
    sim_result = {k: meta[k] for k in ('name', 'run_dir', 'strategies')}
    sim_result['optimal_sigma_fd'] = meta.get('optimal_sigma_fd', 0.0)
    sim_result['optimal_work'] = meta.get('optimal_work', 0.0)
    sim_result['inventory'] = meta.get('inventory', '')
    return sim_result


# ── flat-pool worker (handles both stages / both granularities) ─────────────────

def _run_job(job: dict):
    """Picklable unit of work.  job['stage'] is 'config' or 'aggregate'; job['eval_keys']
    is the list of evaluation keys to run against the job's context."""
    log = _worker_log()
    preset = PRESETS[job['preset']]
    # registry must be populated in this (child) process
    assert len(EVAL_BY_KEY) >= len(preset['keys']), 'evaluation registry not populated'
    overrides, cli_set = preset['overrides'], job['set']
    try:
        if job['stage'] == 'config':
            ctx = _CFG_CTX.get(job['run_dir'])
            if ctx is None:
                ctx = EvalContext(job['sim_result'], job['slim'], preset['focus'], log)
                _CFG_CTX[job['run_dir']] = ctx
            for k in job['eval_keys']:
                driver.run_one(ctx, k, overrides, cli_set)
            return (job['run_dir'], None)
        else:
            ctx = _AGG_CTX.get(job['out_dir'])
            if ctx is None:
                ctx = AggregateContext(job['profile_series_list'], job['out_dir'],
                                       job['pickcfg'], preset['focus'], log)
                _AGG_CTX[job['out_dir']] = ctx
            for k in job['eval_keys']:
                driver.run_one(ctx, k, overrides, cli_set)
            return (job['out_dir'], None)
    except Exception as exc:  # noqa: BLE001 — report, don't kill the pool
        return (job.get('run_dir') or job.get('out_dir'), repr(exc))


def _drain(pool, jobs, log):
    """Run jobs on the flat pool (or inline if no pool); log per-job errors."""
    if pool is None:
        for job in jobs:
            tgt, err = _run_job(job)
            if err:
                log.error(f'  Analysis failed for {tgt}: {err}')
        return
    futures = [pool.submit(_run_job, job) for job in jobs]
    log.info(f'  Running {len(futures)} jobs across the pool...')
    for fut in concurrent.futures.as_completed(futures):
        tgt, err = fut.result()
        if err:
            log.error(f'  Analysis failed for {tgt}: {err}')


# ── job-list construction ────────────────────────────────────────────────────────

def _config_jobs(base_dir, preset_name, granularity, cli_set, log):
    """Parent pre-pass: build slim shared assets per pair, prepare each config's output
    dirs once, and emit the flat config-stage job list."""
    preset = PRESETS[preset_name]
    cfg_keys = driver.config_keys(preset)
    jobs = []
    for pair_name in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair_name)
        if not os.path.isdir(pair_dir):
            continue
        config_metas, inv_db, aff_db = [], None, None
        for cfg_name in sorted(os.listdir(pair_dir)):
            cfg_dir = os.path.join(pair_dir, cfg_name)
            if not os.path.isdir(cfg_dir):
                continue
            # Store-only writes <config>/sim_meta.json; a mixed run writes one per channel at
            # <config>/<channel>/sim_meta.json.  Discover both — each meta carries its own
            # run_dir, so the whole plot suite replicates per channel with no plot changes.
            meta_paths = []
            direct = os.path.join(cfg_dir, 'sim_meta.json')
            if os.path.exists(direct):
                meta_paths.append(direct)
            else:
                for sub in sorted(os.listdir(cfg_dir)):
                    mp = os.path.join(cfg_dir, sub, 'sim_meta.json')
                    if os.path.exists(mp):
                        meta_paths.append(mp)
            for meta_path in meta_paths:
                with open(meta_path) as f:
                    meta = json.load(f)
                config_metas.append(meta)
                if inv_db is None:
                    inv_db, aff_db = meta.get('inv_db'), meta.get('aff_db')
        if not config_metas or inv_db is None or aff_db is None:
            continue
        log.info(f'  Pair: {pair_name}  ({len(config_metas)} config(s))')
        try:
            shared = build_shared_assets(inv_db, aff_db, log)
        except Exception as exc:
            log.error(f'  build_shared_assets failed for {pair_name}: {exc}', exc_info=True)
            continue
        slim = {k: shared.get(k) for k in _SLIM_KEYS}   # small picklable subset per pair
        for meta in config_metas:
            sim_result = _sim_result_from_meta(meta)
            run_dir = sim_result['run_dir']
            driver.prepare_config_dirs(run_dir)         # wipe shared dirs ONCE (no worker race)
            common = dict(stage='config', preset=preset_name, set=cli_set,
                          sim_result=sim_result, slim=slim, run_dir=run_dir)
            if granularity == 'graph':
                for k in cfg_keys:
                    jobs.append({**common, 'eval_keys': [k]})
            else:
                jobs.append({**common, 'eval_keys': cfg_keys})
    return jobs


def _aggregate_jobs(base_dir, preset_name, granularity, cli_set, log):
    """Group every config's series.json by leaf pick-config name across profiles, prepare
    each _aggregate/<pickcfg>/ dir once, and emit the flat aggregate-stage job list."""
    preset = PRESETS[preset_name]
    agg_keys = driver.aggregate_keys(preset)
    if not agg_keys:
        return []
    groups: dict = {}
    for prof in sorted(os.listdir(base_dir)):
        prof_dir = os.path.join(base_dir, prof)
        if not os.path.isdir(prof_dir) or prof.startswith('_'):
            continue
        for cfg in sorted(os.listdir(prof_dir)):
            cfg_dir = os.path.join(prof_dir, cfg)
            if not os.path.isdir(cfg_dir):
                continue
            # store-only: <config>/series.json (group by config); mixed:
            # <config>/<channel>/series.json (group by config/channel so the cross-profile
            # aggregate stays within one channel).
            found = []
            direct = os.path.join(cfg_dir, 'series.json')
            if os.path.exists(direct):
                found.append((cfg, direct))
            else:
                for sub in sorted(os.listdir(cfg_dir)):
                    sp = os.path.join(cfg_dir, sub, 'series.json')
                    if os.path.exists(sp):
                        found.append((os.path.join(cfg, sub), sp))
            for gkey, sp in found:
                try:
                    with open(sp) as f:
                        groups.setdefault(gkey, []).append(json.load(f))
                except (OSError, ValueError) as exc:
                    log.error(f'  bad series.json {sp}: {exc}')
    jobs = []
    for cfg, plist in groups.items():
        out_dir = os.path.join(base_dir, '_aggregate', cfg)
        driver.prepare_aggregate_dir(out_dir)
        common = dict(stage='aggregate', preset=preset_name, set=cli_set,
                      profile_series_list=plist, out_dir=out_dir, pickcfg=cfg)
        if granularity == 'graph':
            for k in agg_keys:
                jobs.append({**common, 'eval_keys': [k]})
        else:
            jobs.append({**common, 'eval_keys': agg_keys})
    return jobs


def run_analysis(base_dir: str, log: logging.Logger, workers: int = 1,
                 preset: str = 'DEFAULT', granularity: str = 'config',
                 cli_set: dict | None = None) -> None:
    """Re-run analysis on all completed sims under *base_dir* via the registry.

    Two sequential flat-pool stages (config, then cross-profile aggregate); each stage is a
    single ProcessPoolExecutor sized by `workers` (workers<=1 runs inline)."""
    cli_set = cli_set or {}
    if preset not in PRESETS:
        raise ValueError(f'unknown preset {preset!r}; choices: {sorted(PRESETS)}')

    pool = (concurrent.futures.ProcessPoolExecutor(max_workers=workers)
            if workers and workers > 1 else None)
    try:
        cfg_jobs = _config_jobs(base_dir, preset, granularity, cli_set, log)
        log.info(f'  Config stage: {len(cfg_jobs)} job(s)  '
                 f'(preset={preset}, granularity={granularity}, workers={workers})')
        _drain(pool, cfg_jobs, log)

        # aggregate stage needs every series.json on disk first
        log.info('  Building cross-profile aggregate suites...')
        agg_jobs = _aggregate_jobs(base_dir, preset, granularity, cli_set, log)
        _drain(pool, agg_jobs, log)
    finally:
        if pool is not None:
            pool.shutdown()


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _parse_set(items: list) -> dict:
    """Parse repeated --set KEY.PARAM=VALUE into {eval_key: {param: coerced_value}}."""
    out: dict = {}
    for raw in items or []:
        if '=' not in raw or '.' not in raw.split('=', 1)[0]:
            raise ValueError(f"--set must be KEY.PARAM=VALUE, got {raw!r}")
        lhs, val = raw.split('=', 1)
        ev_key, param = lhs.rsplit('.', 1)
        out.setdefault(ev_key, {})[param] = _coerce(val)
    return out


def _coerce(v: str):
    low = v.strip().lower()
    if low in ('true', 'false'):
        return low == 'true'
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate plots/analyses from completed simulation runs (registry-driven).',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('base_dir', nargs='?', default=None,
                        help='Comparison output directory (e.g. comparison_20260605_120000). '
                             'Relative paths are resolved under COMPARISON_OUTPUT_DIR.')
    parser.add_argument('--preset', default='DEFAULT', choices=sorted(PRESETS),
                        help='Which set of graphs to run (see Performance_Evaluations/presets.py).')
    parser.add_argument('--workers', type=int, default=1,
                        help='Flat-pool worker processes (1 = inline/sequential).')
    parser.add_argument('--granularity', default='config', choices=('config', 'graph'),
                        help="Job unit: 'config' (context loaded once per config, shared across "
                             "its graphs) or 'graph' (one job per graph — max parallelism).")
    parser.add_argument('--set', action='append', default=[], dest='set',
                        metavar='KEY.PARAM=VALUE',
                        help='Ad-hoc per-graph param override, e.g. compare.top_metric.top_n=3.')
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

    cli_set = _parse_set(args.set)
    log = _setup_logging(os.path.join(base_dir, 'analysis.log'))
    log.info(f'run_analysis  dir: {base_dir}  (preset={args.preset}, workers={args.workers}, '
             f'granularity={args.granularity})')
    run_analysis(base_dir, log, workers=args.workers, preset=args.preset,
                 granularity=args.granularity, cli_set=cli_set)
    log.info('Done.')


if __name__ == '__main__':
    main()
