"""simdriver.supervisor — the crash-recovery pool driver + finalize gate.

Owns the flat ProcessPoolExecutor lifetime: submit every work unit, isolate ordinary
per-unit failures, rebuild + resubmit on a BrokenProcessPool (hard worker death) up to
max_retries, and finalize a group ONLY when every arm succeeded (so a crashed group
stays resumable).  _run_strategy_worker is the only pickled callable (imported from
strategy_runner, unchanged)."""
from __future__ import annotations

import concurrent.futures
import json
import logging
import logging.handlers
import multiprocessing
import os
from concurrent.futures.process import BrokenProcessPool

from Optimization.sim_manifest import _resume_path
from Optimization.strategy_runner import _cleanup_checkpoints, _run_strategy_worker
from Optimization.simdriver.workunits import _build_work_units


def _finalize_config_run(sim_skeleton: dict) -> dict:
    """Post-completion: remove resume file and write sim_meta.json.

    Returns the sim_result dict (subset of sim_skeleton without inv_db/aff_db).
    """
    run_dir = sim_skeleton['run_dir']
    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)
    _cleanup_checkpoints(run_dir)   # config complete — clear its per-strategy _ckpt_*.pkl
    # Additive runs: if a sim_meta.json already exists (e.g. a --resume run adding
    # a NEW strategy into a prior comparison dir), MERGE the strategy lists instead
    # of overwriting, so run_analysis sees the previously-run strategies plus the
    # new one.  Strategies are de-duplicated by key (the new run wins on collision).
    meta_path = os.path.join(run_dir, 'sim_meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as _f:
                prev = json.load(_f)
        except (OSError, ValueError):
            prev = {}
        new_keys = {s['key'] for s in sim_skeleton.get('strategies', [])}
        kept     = [s for s in prev.get('strategies', []) if s.get('key') not in new_keys]
        sim_skeleton = {**prev, **sim_skeleton,
                        'strategies': kept + sim_skeleton.get('strategies', [])}
    with open(meta_path, 'w') as _f:
        json.dump(sim_skeleton, _f, indent=2)
    return {k: sim_skeleton[k]
            for k in ('name', 'inventory', 'run_dir', 'strategies',
                      'optimal_sigma_fd', 'optimal_work')
            if k in sim_skeleton}


def _tag_of(cell, key):
    """Standardized per-arm/per-group log tag: [cell/pair/config[/channel][/strategy]]."""
    return '/'.join(x for x in ((cell,) + tuple(key)) if x)


def _finalize_ready_groups(meta, done_uids, finalized, log, cell=''):
    """Finalize every group whose members have ALL succeeded and isn't finalized yet."""
    for gk, m in meta.items():
        if gk not in finalized and m['members'] <= done_uids:
            try:
                _finalize_config_run(m['sim_skeleton'])
                finalized.add(gk)
                log.info(f'  [{_tag_of(cell, gk)}] sim_meta.json written')
            except Exception as exc:
                log.error(f'  [{_tag_of(cell, gk)}] finalize FAILED: {exc}', exc_info=True)


def _run_pool(remaining, meta, max_workers, recycle, log, done_uids, finalized, cell=''):
    """One ProcessPoolExecutor lifetime over `remaining` [(uid, args)].  Returns
    (failed_uids, broke).  A genuine success adds uid to done_uids and finalizes its group once
    ALL members succeeded; a hard worker death (BrokenProcessPool) sets broke and abandons the
    rest so the driver can rebuild + resubmit; an ordinary worker Exception is isolated.  `cell`
    is prefixed on every per-arm tag so interleaved multi-cell output is attributable."""
    failed_uids, broke = set(), False
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers, max_tasks_per_child=recycle) as pool:
        futures = {pool.submit(_run_strategy_worker, sa): uid for uid, sa in remaining}
        for fut in concurrent.futures.as_completed(futures):
            uid = futures[fut]
            gk  = uid[:3]
            _tag = _tag_of(cell, uid)
            try:
                res = fut.result()
                log.info(f'  [{_tag}] done  batches={res["done"]}  wall={res["elapsed"]:.1f}s')
                done_uids.add(uid)
            except BrokenProcessPool:
                broke = True
                log.error('  [supervisor] worker pool BROKEN (hard worker death) — abandoning '
                          'this pool; unfinished units will be rebuilt + resubmitted')
                break
            except Exception as exc:
                log.error(f'  [{_tag}] strategy FAILED: {exc}', exc_info=True)
                failed_uids.add(uid)
                continue
            gk_meta = meta.get(gk)
            if gk_meta and gk not in finalized and gk_meta['members'] <= done_uids:
                try:
                    _finalize_config_run(gk_meta['sim_skeleton'])
                    finalized.add(gk)
                    log.info(f'  [{_tag_of(cell, gk)}] sim_meta.json written')
                except Exception as exc:
                    log.error(f'  [{_tag_of(cell, gk)}] finalize FAILED: {exc}', exc_info=True)
    return failed_uids, broke


def _supervise(pairs, base_dir, shared_by_pair, max_workers, log, *, log_queue,
               max_tasks_per_child, skip_completed, max_retries, resume_granularity, cell='',
               cell_index=1, cell_total=1):
    """Bounded retry driver: on a hard worker death, rebuild the pool and resubmit the
    unfinished units (each resumes from its on-disk checkpoint), up to max_retries.  Ordinary
    per-unit exceptions are NOT auto-retried (near-always deterministic bad-config).  Quarantine
    + continue: never abort the run, never infinite-loop; unrecovered units keep their resume
    state and are reported with a resume command.  max_tasks_per_child recycles workers so RSS
    is reclaimed between jobs (a clean recycle is NOT a broken pool)."""
    recycle = max_tasks_per_child if max_tasks_per_child and max_tasks_per_child > 0 else None
    done_uids, finalized = set(), set()
    work_units, meta = [], {}
    for attempt in range(max_retries + 1):
        work_units, meta = _build_work_units(
            pairs, base_dir, shared_by_pair, log, log_queue, max_workers,
            skip_completed=(skip_completed or attempt > 0),
            resume_granularity=resume_granularity)
        remaining = [(uid, sa) for uid, sa in work_units if uid not in done_uids]
        if not remaining:
            break
        total = len(remaining)
        # GLOBAL run counter: this cell's jobs occupy slots [base+1 .. base+total] of an estimated
        # cell_total*total whole-run total (arm suite is uniform across cells), so the log shows
        # overall progress, not just position within the current cell.
        gbase, gtot = (cell_index - 1) * total, cell_total * total
        for idx, (uid, sa) in enumerate(remaining, start=1):
            sa['job_index'], sa['job_total'] = idx, total
            sa['cell'] = cell                       # stamped on every worker line (cell/strategy)
            sa['cell_pos'] = f'{cell_index}/{cell_total}'
            sa['gjob']     = f'{gbase + idx}/{gtot}'     # global job index across all cells
            sa['job_tag'] = _tag_of(cell, uid)
        if attempt:
            log.warning(f'  [supervisor] retry {attempt}/{max_retries}: '
                        f'rebuild pool + resubmit {total} unit(s)')
        log.info(f'  Flat pool [cell {cell_index}/{cell_total}'
                 f'{": " + cell if cell else ""}]: {total} job(s) '
                 f'(global {gbase + 1}-{gbase + total}/{gtot}) -> '
                 f'ProcessPoolExecutor({max_workers}, max_tasks_per_child={recycle})')
        _failed, broke = _run_pool(remaining, meta, max_workers, recycle,
                                   log, done_uids, finalized, cell=cell)
        if not broke:
            break            # pool completed; residual failures are deterministic → quarantine
    _finalize_ready_groups(meta, done_uids, finalized, log, cell=cell)   # safety sweep
    all_uids = {uid for uid, _ in work_units} | done_uids
    unfinished = sorted(all_uids - done_uids)
    if unfinished:
        bar = '!' * 72
        log.error(bar)
        log.error(f'  {len(unfinished)}/{len(all_uids)} unit(s) UNRECOVERED after {max_retries} '
                  f'retr{"y" if max_retries == 1 else "ies"}. Resume state left intact.')
        for uid in unfinished:
            log.error('    ' + _tag_of(cell, uid))
        log.error(f'  Resume with:  python Optimization/run_simulation.py --resume {base_dir}')
        log.error(bar)


def _run_workers_flat(
    pairs              : list,
    base_dir           : str,
    shared_by_pair     : dict,
    max_workers        : int,
    log                : logging.Logger,
    cell               : str = '',
    cell_index         : int = 1,
    cell_total         : int = 1,
    max_tasks_per_child: int | None = 1,
    skip_completed     : bool = False,
    max_retries        : int = 2,
    resume_granularity : str = 'strategy',
) -> None:
    """Flat ProcessPoolExecutor pool with automatic crash-recovery.

    All (pair, config, channel, strategy) units share one pool.  A worker that raises is
    isolated; a hard worker death that BREAKS the pool triggers a rebuild + resubmit of the
    unfinished units (each resumes from its on-disk checkpoint), up to max_retries.  A group's
    sim_meta.json is written only when ALL its strategies genuinely succeed, so a crashed arm
    keeps its resume.pkl and stays resumable.  The Manager/QueueListener live out here so worker
    deaths + pool rebuilds don't touch shared logging.

    Graph generation is decoupled: run run_analysis.py <base_dir> afterwards.
    """
    mp_manager = multiprocessing.Manager()
    log_queue  = mp_manager.Queue(-1)
    listener   = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True)
    listener.start()
    log.info('  Log listener started')
    try:
        _supervise(pairs, base_dir, shared_by_pair, max_workers, log,
                   log_queue=log_queue, max_tasks_per_child=max_tasks_per_child,
                   skip_completed=skip_completed, max_retries=max_retries,
                   resume_granularity=resume_granularity, cell=cell,
                   cell_index=cell_index, cell_total=cell_total)
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')
