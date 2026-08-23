"""run_analysis.py — registry-driven graph generator for completed simulation runs.

Reads the sim_meta docs written by run_simulation.py, rebuilds warehouse aisle maps via
build_shared_assets, then runs the graphs/analyses registered under Performance_Evaluations
(selected by a preset).  Each graph is a self-registering module; add/remove/tune graphs by
editing Performance_Evaluations/presets.py — not this file.

Per-leaf filenames (sim_meta / series) and the cross-profile aggregate subtree render through the
run-tree contract (`rt.leaf_path` / `rt.aggregate_dir`), never a basename join — the resolver is
rooted at the RUN root (this module receives a CELL dir from analyze_run), and
Tests/integration/test_writer_paths_golden.py pins the rendered strings to the old literals.

Parallelism is a single FLAT worker pool (mirrors run_simulation): one global job list across
all pairs × configs fed to one ProcessPoolExecutor, then a second flat pool for the
cross-profile aggregate stage.  `--granularity config` (default) = one job per config (context
loaded once, shared across its graphs); `--granularity graph` = one job per (config, graph)
for maximum core utilization on sparse runs.

Usage:
  python run_analysis.py <base_dir>                        # default preset BY_INITIAL
  python run_analysis.py <base_dir> --preset DEFAULT        # uni-only (drops opt_* arms)
  python run_analysis.py <base_dir> --granularity graph --set labor.delta_topn.top_n=3

The default preset is BY_INITIAL (focus=all): it keeps BOTH the uniform (uni_*) and optimum
(opt_*) initial-assignment arms, so the optimum-vs-uniform comparison is produced and the
downstream run_channel_rollup sees the full suite.  Pass --preset DEFAULT for the older
uniform-only view.
"""

import argparse
import concurrent.futures
import json
import logging
import os
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_analysis.py <dir>); `-m` form needs none of this.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Optimization.simdriver.sim_assets import build_shared_assets
from Optimization.config.sim_config import (CONFIG, regime_sizing_from_config, _setup_logging,
                                            _OUTPUT_DIR)
from Optimization.runschema.runlayout import iter_channel_runs

# Importing the package fires every @evaluation (also re-fires in each spawned worker),
# so the registry is populated before any job runs.
from Optimization import Performance_Evaluations  # noqa: F401  (side effect: populate registry + set Agg backend)
from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
from Optimization.Performance_Evaluations.core.context import EvalContext, AggregateContext
from Optimization.Performance_Evaluations.core import requests
from Optimization.Performance_Evaluations import driver
from Optimization.Performance_Evaluations.presets import PRESETS


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
    is the list of evaluation keys to run against the job's context.  Returns
    (target, error, access_tally) — the tally is this job's broker grant/denial counts,
    carried back to the parent for the run-end `[access]` summary."""
    log = _worker_log()
    preset = PRESETS[job['preset']]
    # registry must be populated in this (child) process
    assert len(EVAL_BY_KEY) >= len(preset['keys']), 'evaluation registry not populated'
    overrides, cli_set = preset['overrides'], job['set']
    requests.tally_snapshot(reset=True)                 # this job's counts only
    try:
        if job['stage'] == 'config':
            ctx = _CFG_CTX.get(job['run_dir'])
            if ctx is None:
                ctx = EvalContext(job['sim_result'], job['slim'], preset['focus'], log)
                _CFG_CTX[job['run_dir']] = ctx
            for k in job['eval_keys']:
                driver.run_one(ctx, k, overrides, cli_set)
            return (job['run_dir'], None, requests.tally_snapshot())
        else:
            ctx = _AGG_CTX.get(job['out_dir'])
            if ctx is None:
                ctx = AggregateContext(job['profile_series_list'], job['out_dir'],
                                       job['pickcfg'], preset['focus'], log)
                _AGG_CTX[job['out_dir']] = ctx
            for k in job['eval_keys']:
                driver.run_one(ctx, k, overrides, cli_set)
            return (job['out_dir'], None, requests.tally_snapshot())
    except Exception as exc:  # noqa: BLE001 — report, don't kill the pool
        return (job.get('run_dir') or job.get('out_dir'), repr(exc),
                requests.tally_snapshot())


def _merge_tally(total: dict, part: dict) -> None:
    for bucket in ('granted', 'denied'):
        for key, n in part.get(bucket, {}).items():
            total[bucket][key] = total[bucket].get(key, 0) + n


def _drain(pool, jobs, log) -> dict:
    """Run jobs on the flat pool (or inline if no pool); log per-job errors.  Returns the
    merged access tally {'granted': {eval: n}, 'denied': {eval: n}} across all jobs."""
    tally = {'granted': {}, 'denied': {}}
    if pool is None:
        for job in jobs:
            tgt, err, part = _run_job(job)
            _merge_tally(tally, part)
            if err:
                log.error(f'  Analysis failed for {tgt}: {err}')
        return tally
    futures = [pool.submit(_run_job, job) for job in jobs]
    log.info(f'  Running {len(futures)} jobs across the pool...')
    for fut in concurrent.futures.as_completed(futures):
        tgt, err, part = fut.result()
        _merge_tally(tally, part)
        if err:
            log.error(f'  Analysis failed for {tgt}: {err}')
    return tally


# ── job-list construction ────────────────────────────────────────────────────────

def _tree_for(cell_dir: str):
    """(RunTree, cell_name) for the run this CELL directory belongs to.

    run_analysis receives a CELL dir (analyze_run calls it once per cell), so the resolver is
    rooted at the PARENT — where the run descriptor lives — and the cell name is the dir's own
    basename.  A tree with NO descriptor (the e2e scratch trees, ad-hoc analyses) falls back to
    the HEAD contract rooted at the same parent: the head templates render exactly the strings
    the old hand-joins assumed, so the fallback is behavior-preserving while keeping the tree
    shape declared in one place.
    """
    from Optimization import runschema
    from Optimization.runschema import contract as _rt_contract
    from Optimization.runschema.resolver import RunTree
    cell_dir = os.path.abspath(cell_dir)
    root, cell = os.path.dirname(cell_dir), os.path.basename(cell_dir)
    try:
        return runschema.resolver_for(root), cell
    except runschema.UnsupportedRunTree:
        head = _rt_contract.head()
        doc = _rt_contract.load(head) if head else None
        if doc is None:
            raise
        return RunTree(root, doc, layout={}), cell


def _config_jobs(base_dir, rt, preset_name, granularity, cli_set, log, max_skus=None):
    """Parent pre-pass: build slim shared assets per pair, prepare each config's output
    dirs once, and emit the flat config-stage job list."""
    preset = PRESETS[preset_name]
    cfg_keys = driver.config_keys(preset)
    jobs = []
    # Store-only writes its sim_meta at <config>/; a mixed run writes one per channel at
    # <config>/<channel>/.  iter_channel_runs discovers both (its default marker is the
    # sim_meta basename) — each meta carries its own run_dir, so the whole plot suite
    # replicates per channel with no plot changes.  Group by pair (shared assets are per-pair).
    metas_by_pair: dict[str, list] = {}
    for run in iter_channel_runs(base_dir):
        with open(rt.leaf_path(run, 'sim_meta')) as f:
            metas_by_pair.setdefault(run.pair, []).append(json.load(f))
    for pair_name, config_metas in metas_by_pair.items():
        inv_db = next((m.get('inv_db') for m in config_metas if m.get('inv_db')), None)
        aff_db = next((m.get('aff_db') for m in config_metas if m.get('aff_db')), None)
        if inv_db is None or aff_db is None:
            continue
        log.info(f'  Pair: {pair_name}  ({len(config_metas)} config(s))')
        try:
            # Rebuild the warehouse SHAPE with the SAME per-regime sizing the run used, so the
            # fulfillment aisle layout + total_bins match (fixed ff distribution, not demand).
            # max_skus AND the per-regime sizing both come from the run's own run_spec
            # (see _apply_run_shape) — sizing this differently from the run is the silent
            # wrong-warehouse bug this argument exists to close.
            shared = build_shared_assets(inv_db, aff_db, log, max_skus=max_skus,
                                         regime_sizing=regime_sizing_from_config())
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


def _aggregate_jobs(base_dir, rt, cell, preset_name, granularity, cli_set, log):
    """Group every config's series doc by leaf pick-config name across profiles, prepare
    each aggregate group dir once, and emit the flat aggregate-stage job list."""
    preset = PRESETS[preset_name]
    agg_keys = driver.aggregate_keys(preset)
    if not agg_keys:
        return []
    # store-only: the series doc sits at <config>/ (group by config); mixed: at
    # <config>/<channel>/ (group by config/channel so the cross-profile aggregate stays
    # within one channel).  ChannelRun.group_key encodes exactly that.  The walk marks on
    # sim_meta (the default) and skips leaves whose series doc was never written — the same
    # set the old marker='series' walk yielded, without spelling the basename here.
    groups: dict = {}
    for run in iter_channel_runs(base_dir):
        sp = rt.leaf_path(run, 'series_json')
        if not os.path.exists(sp):
            continue
        try:
            with open(sp) as f:
                groups.setdefault(run.group_key, []).append(json.load(f))
        except (OSError, ValueError) as exc:
            log.error(f'  bad series doc {sp}: {exc}')
    jobs = []
    for cfg, plist in groups.items():
        # group_key already folds the optional channel level in (`<config>` store-only,
        # `<config>/<channel>` mixed) — aggregate_dir splits it back onto the template.
        out_dir = rt.aggregate_dir(cell, cfg)
        driver.prepare_aggregate_dir(out_dir)
        common = dict(stage='aggregate', preset=preset_name, set=cli_set,
                      profile_series_list=plist, out_dir=out_dir, pickcfg=cfg)
        if granularity == 'graph':
            for k in agg_keys:
                jobs.append({**common, 'eval_keys': [k]})
        else:
            jobs.append({**common, 'eval_keys': agg_keys})
    return jobs


def _apply_run_shape(base_dir: str, log: logging.Logger) -> int | None:
    """Restore the RUN's own run-shaping params onto CONFIG before rebuilding its warehouse.

    A standalone re-analysis used to size the warehouse from whatever CONFIG this checkout
    happens to hold — no max_skus, this build's fill — while the comment at the rebuild claimed
    it used "the SAME per-regime sizing the run used".  For any capped run that silently
    rebuilt a DIFFERENT warehouse than the sim ran on: wrong aisle count, wrong total_bins,
    and (per `regime_sizing_from_config`'s own docstring) misgrouped ff aisle stats and skewed
    churn %.  The in-process analysis never had the bug because `run_simulation` had already
    mutated CONFIG in the same process; only the standalone path was affected.

    Returns the run's `max_skus` (None when unrecorded).  A run with no recorded spec — every
    run before that file existed — warns loudly and keeps the old behaviour, which is the
    honest answer: those runs never recorded what they were shaped with.
    """
    from Optimization.runschema.sim_manifest import _load_run_spec
    # `base_dir` is a CELL dir (analyze_run calls this once per cell) but the run spec lives at
    # the RUN ROOT — the same parent-vs-self distinction _tree_for documents.  Checking only the
    # cell dir found nothing and silently sized from this checkout: the resumed rehearsal loaded
    # 150,000 orders where its own sim had loaded 8,000.
    spec = _load_run_spec(base_dir) or _load_run_spec(os.path.dirname(os.path.abspath(base_dir)))
    if not spec:
        log.warning('  no run_spec.json at the run root — sizing the warehouse from THIS '
                    "checkout's CONFIG, which may not match what the run used (pre-run_spec "
                    'run). Aisle counts and ff aisle stats may not line up.')
        return None
    g = CONFIG['global']
    for key in ('n_batches', 'keyframe_interval', 'checkpoint_frac'):
        if spec.get(key) is not None:
            g[key] = spec[key]
    # Batch-sampler era: a pre-field run_spec predates v2's adoption, so its absence
    # means that run's batches were drawn with v1 — never this checkout's default.
    g['sampler'] = spec.get('sampler') or 'v1'
    if spec.get('max_skus') is not None:
        g['max_skus'] = spec['max_skus']
    for ch, key in (('store', 'store_fill'), ('fulfillment', 'ff_fill')):
        if spec.get(key) is not None:
            CONFIG['channels'][ch]['fill'] = spec[key]
    CONFIG['channels']['store']['sizing'].update(
        max_aisles=spec.get('s_max_aisles'), max_bins=spec.get('s_max_bins'),
        min_bins=spec.get('s_min_bins'), composition=spec.get('s_composition'))
    CONFIG['channels']['fulfillment']['sizing'].update(
        max_aisles=spec.get('ff_max_aisles'), max_bins=spec.get('ff_max_bins'),
        min_bins=spec.get('ff_min_bins'))
    log.info(f"  run_spec applied: max_skus={spec.get('max_skus')} "
             f"s_max_bins={spec.get('s_max_bins')} ff_max_bins={spec.get('ff_max_bins')} "
             f"store_fill={spec.get('store_fill')} ff_fill={spec.get('ff_fill')}")
    return spec.get('max_skus')


def run_analysis(base_dir: str, log: logging.Logger, workers: int = 1,
                 preset: str = 'BY_INITIAL', granularity: str = 'config',
                 cli_set: dict | None = None) -> None:
    """Re-run analysis on all completed sims under *base_dir* via the registry.

    Two sequential flat-pool stages (config, then cross-profile aggregate); each stage is a
    single ProcessPoolExecutor sized by `workers` (workers<=1 runs inline)."""
    cli_set = cli_set or {}
    if preset not in PRESETS:
        raise ValueError(f'unknown preset {preset!r}; choices: {sorted(PRESETS)}')

    rt, cell = _tree_for(base_dir)
    # THE fix for the standalone path: restore the run's own shaping params before any
    # warehouse is rebuilt.  Harmless in-process (run_simulation already set the same values).
    max_skus = _apply_run_shape(base_dir, log)
    pool = (concurrent.futures.ProcessPoolExecutor(max_workers=workers)
            if workers and workers > 1 else None)
    tally = {'granted': {}, 'denied': {}}
    try:
        cfg_jobs = _config_jobs(base_dir, rt, preset, granularity, cli_set, log, max_skus)
        log.info(f'  Config stage: {len(cfg_jobs)} job(s)  '
                 f'(preset={preset}, granularity={granularity}, workers={workers})')
        _merge_tally(tally, _drain(pool, cfg_jobs, log))

        # aggregate stage needs every series doc on disk first
        log.info('  Building cross-profile aggregate suites...')
        agg_jobs = _aggregate_jobs(base_dir, rt, cell, preset, granularity, cli_set, log)
        _merge_tally(tally, _drain(pool, agg_jobs, log))
    finally:
        if pool is not None:
            pool.shutdown()

    # Run-end access summary — "did every consumer get what it asked for", from the log alone.
    n_granted = sum(tally['granted'].values())
    n_denied = sum(tally['denied'].values())
    if n_denied:
        per_eval = ', '.join(f'{k} x{n}' for k, n in sorted(tally['denied'].items()))
        log.warning(f'[access] run summary: {n_granted} granted, {n_denied} DENIED '
                    f'({per_eval}) — see the DENIED lines above for reasons')
    else:
        log.info(f'[access] run summary: all {n_granted} evaluation requests granted, 0 denials')


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
    parser.add_argument('--preset', default='BY_INITIAL', choices=sorted(PRESETS),
                        help='Which set of graphs to run (see Performance_Evaluations/presets.py). '
                             'Default BY_INITIAL (focus=all) keeps both uni_* and opt_* arms so the '
                             'optimum-vs-uniform comparison is produced; pass DEFAULT for uni-only.')
    parser.add_argument('--workers', type=int, default=1,
                        help='Flat-pool worker processes (1 = inline/sequential).')
    parser.add_argument('--granularity', default='config', choices=('config', 'graph'),
                        help="Job unit: 'config' (context loaded once per config, shared across "
                             "its graphs) or 'graph' (one job per graph — max parallelism).")
    parser.add_argument('--set', action='append', default=[], dest='set',
                        metavar='KEY.PARAM=VALUE',
                        help='Ad-hoc per-graph param override, e.g. labor.delta_topn.top_n=3.')
    args = parser.parse_args()

    if args.base_dir is None:
        parser.print_help()
        sys.exit(1)

    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-cp1252 log chars (e.g. λ)
    except Exception:
        pass

    from Optimization.runschema import resolve_base_dir
    base_dir = resolve_base_dir(args.base_dir)
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
