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
import subprocess
import sys
import time
from concurrent.futures.process import BrokenProcessPool

from Optimization.runschema.sim_manifest import _resume_path
from Optimization.simdriver.strategy_runner import _cleanup_checkpoints, _run_strategy_worker
from Optimization.simdriver.workunits import _build_work_units
from Optimization.persistence import runtime_metrics


def _finalize_config_run(sim_skeleton: dict) -> dict:
    """Post-completion: write sim_meta.json, THEN remove resume file + checkpoints.

    THE ORDER IS THE SAFETY.  A kill inside this function must leave a directory that is
    still either resumable or complete — never neither.  The skip guard
    (`workunits._build_work_units`) reads "complete" as *sim_meta.json present AND
    resume.pkl absent*, so writing the marker FIRST makes the crash window hold a dir with
    BOTH files: not complete (so it is re-planned) and still resumable (so `_load_resume`
    answers).  `_plan_strategy_start` then takes its documented "resumed done arm" branch
    — ckpt == n_batches, so reuse prev_id, start n_batches, empty loop, re-finalize.

    The reverse order removed resume.pkl FIRST and left a dir that was NEITHER: the guard
    saw no sim_meta.json and declined to skip, `_load_resume` returned None because the
    file was already gone, and the fresh-run branch therefore ran `create_run` over a
    POPULATED db with no `reset_strategy_db`.  That corruption has no symptom — `find_run`
    resolves `ORDER BY run_id LIMIT 1`, so every run_id-filtered query would answer from
    the ABANDONED run and every unfiltered aggregate over the file would double.
    No new state and no new code path — only the sequence.

    Returns the sim_result dict (subset of sim_skeleton without inv_db/aff_db).
    """
    run_dir = sim_skeleton['run_dir']
    # Additive runs: if a sim_meta.json already exists (e.g. a --resume run adding
    # a NEW strategy into a prior comparison dir), MERGE the strategy lists instead
    # of overwriting, so run_analysis sees the previously-run strategies plus the
    # new one.  Strategies are de-duplicated by key (the new run wins on collision).
    # The read-back stays AHEAD of the write: the reordering moved the two REMOVALS
    # below the merge-and-write pair, it did not touch the pair itself.
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
    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)
    _cleanup_checkpoints(run_dir)   # config complete — clear its per-strategy _ckpt_*.pkl
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


def _run_pool(remaining, meta, max_workers, recycle, log, done_uids, finalized, cell='', run_root=None):
    """One ProcessPoolExecutor lifetime over `remaining` [(uid, args)].  Returns
    (failed_uids, broke).  A genuine success adds uid to done_uids and finalizes its group once
    ALL members succeeded; a hard worker death (BrokenProcessPool) sets broke and abandons the
    rest so the driver can rebuild + resubmit; an ordinary worker Exception is isolated.  `cell`
    is prefixed on every per-arm tag so interleaved multi-cell output is attributable."""
    failed_uids, broke = set(), False
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers, max_tasks_per_child=recycle) as pool:
        futures = {pool.submit(_run_strategy_worker, sa): (uid, sa) for uid, sa in remaining}
        for fut in concurrent.futures.as_completed(futures):
            uid, sa = futures[fut]
            _tag = _tag_of(cell, uid)
            try:
                res = fut.result()
                # ONE RESULT PER LEAF.  A one-leaf unit returns its leaf's dict at top level
                # (every run today); a coupled unit returns `leaves` and NOTHING at top level,
                # so a reader that took `res['done']` from it would be quoting one leaf's
                # number as the unit's.  `group_keys` is positional with `leaves`, so the two
                # zip and no slot has to be inferred.
                _results = res.get('leaves') or [res]
                _gks = sa['group_keys']
                if len(_results) != len(_gks):
                    raise ValueError(
                        f'unit returned {len(_results)} leaf result(s) but states '
                        f'{len(_gks)} group key(s); the two are positional')
                # Both ledgers already produce a `log.error` inside the worker, so a
                # break is not silent -- but the parent's per-arm line is what a reader
                # scans, and a break belongs on it rather than only in the worker stream a
                # hundred lines up.  Zero is not printed: a clean line stays clean.
                for _gk, _r in zip(_gks, _results):
                    _lt = _tag if len(_results) == 1 else _tag_of(cell, (*_gk, _r['strategy']))
                    _cb = int(_r.get('cons_breaks') or 0)
                    _db = int(_r.get('demand_breaks') or 0)
                    _ledger = f'  LEDGER cons={_cb} demand={_db}' if (_cb or _db) else ''
                    log.info(f'  [{_lt}] done  batches={_r["done"]}  '
                             f'wall={_r["elapsed"]:.1f}s{_ledger}')
                    if _cb or _db:
                        log.error(f'  [{_lt}] LEDGER BROKE: {_cb} conservation, {_db} demand '
                                  f'-- the numbers for this arm are suspect; see the worker '
                                  f'log for the batch that broke first.')
                done_uids.add(uid)
                # The arm's expected day under its own initial placement (strategy_runner
                # `_arm_expected_pick`): onto the skeleton's strategy entry, so the group's meta
                # document and hence sim_result['strategies'] carry it to the throughput audit.  Absent
                # on a flag-off arm, and then nothing is written -- byte-identical.
                #
                # `sa['group_keys']` / `sa['arm_key']`, never `uid[:3]` / `uid[3]`: this block
                # sliced the uid INDEPENDENTLY of the finalize gate below, so a coupled unit's
                # carried group keys would not have reached it and `meta[(label, 'coupled',
                # arm_store)]` raises KeyError -- inside the success `try`, which turns a unit
                # that SUCCEEDED into a logged `strategy FAILED`.  Not silent, but mis-attributed
                # is its own failure: a reader scanning run.log hunts a simulation bug that is
                # not there.  One leaf today, so the loop runs once over exactly `[uid[:3]]`.
                #
                # A leaf's own `expected_pick` reaches its own group. The refusal site-dock 11
                # recorded -- one value copied onto both arms -- is gone because the value is
                # no longer one: each leaf returns its own, positionally with `group_keys`.
                for _gk, _r in zip(_gks, _results):
                    if _r.get('expected_pick') is None:
                        continue
                    for _s in meta[_gk]['sim_skeleton'].get('strategies', []):
                        if _s.get('key') == _r['strategy']:
                            _s['expected_pick'] = _r['expected_pick']
                if run_root:                         # parent-side runtime-metrics DB (best-effort)
                    # PER LEAF, from the leaf's OWN group key and arm. Never from the uid:
                    # its four slots mean `(pair, config, channel, arm)` on a one-leaf unit
                    # and `(pair, 'coupled', arm_store, arm_ful)` on a coupled one, so a
                    # positional read would file both leaves' metrics under a config named
                    # 'coupled' with an arm key that is the other leaf's.
                    for _gk, _r in zip(_gks, _results):
                        try:
                            runtime_metrics.record_arm(
                                run_root, cell, _r,
                                pair=_gk[0], config=_gk[1], channel=_gk[2],
                                arm=_r['strategy'])
                        except Exception as exc:     # noqa: BLE001 — never let metrics sink a run
                            log.warning(f'  [{_tag}] runtime-metrics record failed: {exc!r}')
            except BrokenProcessPool:
                broke = True
                log.error('  [supervisor] worker pool BROKEN (hard worker death) — abandoning '
                          'this pool; unfinished units will be rebuilt + resubmitted')
                _unblock_broken_pool(pool, log)
                break
            except Exception as exc:
                log.error(f'  [{_tag}] strategy FAILED: {exc}', exc_info=True)
                failed_uids.add(uid)
                continue
            # EVERY group this unit finalizes, from `group_keys` rather than `uid[:3]`: a
            # coupled unit's two leaves live in two different config subtrees (its config slot
            # is the literal 'coupled', which is no directory), so the uid names neither.
            for gk in sa['group_keys']:
                gk_meta = meta.get(gk)
                if gk_meta and gk not in finalized and gk_meta['members'] <= done_uids:
                    try:
                        _finalize_config_run(gk_meta['sim_skeleton'])
                        finalized.add(gk)
                        log.info(f'  [{_tag_of(cell, gk)}] sim_meta.json written')
                    except Exception as exc:
                        log.error(f'  [{_tag_of(cell, gk)}] finalize FAILED: {exc}', exc_info=True)
    return failed_uids, broke


def _unblock_broken_pool(pool, log, *, timeout_s: float = 60.0) -> int:
    """Make the `with ProcessPoolExecutor` exit RETURN after a hard worker death, by reading
    the unit payloads the dead workers never took off the call queue.  Returns the count.

    THE HANG (2026-09-18, the phase-2 launch; `.scratch/phase-2-campaign/issues/01`): every
    child died at import, `_run_pool` logged BROKEN and `break`-ed, and the driver sat at
    0.00 s CPU for 37 minutes.  Thread dump, taken on the reproduction: the `with` exit is
    `shutdown(wait=True)`, joining the executor's manager thread; that thread, in
    `terminate_broken -> join_executor_internals -> call_queue.join_thread()`, is joining the
    call queue's FEEDER thread; and the feeder is inside `PipeConnection._send_bytes`,
    waiting on an overlapped WriteFile that no live worker will ever read.  A Windows pipe
    buffer is 8 KiB; one 16 KiB unit argument with TWO workers is enough
    (`Tests/integration/_broken_pool_driver.py`), and the real payload -- the CONFIG snapshot
    and the shared paths, times twelve queued units -- is well past it.  With small arguments
    the writes fit the buffer and nothing hangs, which is why `test_crash_recovery.py` (which
    fakes `_run_pool` anyway) and every earlier fixture were blind to it.

    This is CPython gh-107219, fixed in 3.11.5 / 3.12 by closing the queue's connections from
    `Queue._terminate_broken`; the machine that hung runs 3.11.4.  That fix is NOT what this
    does, because it was measured not to work here: closing the WRITER from another thread
    leaves the overlapped write pending (probe, 2026-09-18: feeder still alive after 5 s), and
    closing the READER ends the pipe in isolation (`BrokenPipeError` at once) but not under
    the pool -- a child that dies at bootstrap never steals the handle duplicates
    `reduction.DupHandle` made for it in THIS process, so the parent still holds a live
    reader and the pipe is not ended.  What always works is the third thing the probe tried:
    READ the parent's end.  Each `recv_bytes` completes one blocked write, the feeder moves
    to the next item, the manager thread's `close()` appends the sentinel, the feeder exits,
    `join_thread` returns, `shutdown(wait=True)` returns, and `_supervise`'s retry runs.
    Bounded by `timeout_s` so this can never be the thing that hangs.  Private attributes,
    deliberately: there is no public surface for this, and the alternative was a driver that
    holds the machine."""
    cq = getattr(pool, '_call_queue', None)
    reader = getattr(cq, '_reader', None)
    feeder = getattr(cq, '_thread', None)          # None when nothing was ever submitted
    if cq is None or reader is None:
        log.warning('  [supervisor] this executor has no call-queue reader; cannot drain its '
                    f'feeder thread (Python {sys.version.split()[0]}) -- if the pool exit '
                    'hangs, that is why')
        return 0
    drained = 0
    deadline = time.monotonic() + timeout_s
    while feeder is not None and feeder.is_alive():
        if time.monotonic() > deadline:
            log.warning(f'  [supervisor] call-queue feeder still alive after {timeout_s:.0f} s '
                        f'and {drained} payload(s) drained -- the pool exit may hang')
            return drained
        try:
            if reader.poll(0.05):
                reader.recv_bytes()
                drained += 1
        except (EOFError, OSError):
            # THE NORMAL END: once the feeder has sent its last item the manager thread
            # closes the queue, and the reader we are polling goes with it.
            break
    log.info(f'  [supervisor] drained {drained} queued unit payload(s) the dead workers never '
             'read; the call queue is closed and the pool can exit')
    return drained


def _explain_worker_death(log, *, timeout_s: float = 300.0) -> str | None:
    """Say WHY the workers died, in the parent's log, the first time a pool breaks.

    A child that dies at import writes its traceback to a stderr nobody has under a
    scheduled task (memory `launch-long-drivers-detached`); the parent knows only "hard
    death".  So: one fresh interpreter, no multiprocessing, that does what a spawned child
    does first -- re-run the main module as `__mp_main__` (by name under `-m`, by path
    otherwise, mirroring `multiprocessing.spawn.get_preparation_data`), then import the
    worker target's module -- with its stderr CAPTURED.  A failure prints the traceback's
    tail here, naming the exception a broken working tree raises; a success says the death
    is not an import failure (out of memory, a hard crash inside a worker, a kill).

    Returns 'import-failure', 'clean' or None (probe timed out), for a caller that wants the
    verdict; the log lines are the point."""
    main = sys.modules.get('__main__')
    spec = getattr(main, '__spec__', None)
    lines = ['import importlib, runpy, sys']
    if spec is not None and getattr(spec, 'name', None):
        lines.append(f'runpy.run_module({spec.name!r}, run_name="__mp_main__", alter_sys=True)')
    elif getattr(main, '__file__', None):
        lines.append(f'runpy.run_path({os.path.abspath(main.__file__)!r}, run_name="__mp_main__")')
    lines.append(f'importlib.import_module({_run_strategy_worker.__module__!r})')
    lines.append('print("WORKER IMPORT OK")')
    try:
        r = subprocess.run([sys.executable, '-c', '\n'.join(lines)], capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=timeout_s,
                           cwd=os.getcwd(), env=os.environ.copy())
    except subprocess.TimeoutExpired:
        log.error(f'  [supervisor] import probe: no verdict within {timeout_s:.0f} s')
        return None
    if r.returncode == 0 and 'WORKER IMPORT OK' in r.stdout:
        log.error('  [supervisor] import probe: a fresh interpreter imports the main module and '
                  f'{_run_strategy_worker.__module__} cleanly -- the death is NOT an import '
                  'failure (out of memory, a hard crash inside a worker, or a kill)')
        return 'clean'
    tail = (r.stderr or r.stdout).strip().splitlines()[-12:]
    log.error(f'  [supervisor] import probe: the worker FAILS to import in a fresh interpreter '
              f'(exit {r.returncode}) -- this is why every worker died:')
    for ln in tail:
        log.error('      ' + ln)
    return 'import-failure'


def _supervise(pairs, base_dir, shared_by_pair, max_workers, log, *, log_queue,
               max_tasks_per_child, skip_completed, max_retries, resume_granularity, cell='',
               cell_index=1, cell_total=1):
    """Bounded retry driver: on a hard worker death, rebuild the pool and resubmit the
    unfinished units (each resumes from its on-disk checkpoint), up to max_retries.  Ordinary
    per-unit exceptions are NOT auto-retried (near-always deterministic bad-config).  Quarantine
    + continue: never abort the run, never infinite-loop; unrecovered units keep their resume
    state and are reported with a resume command.

    RETURNS THE UNRECOVERED UNIT IDS, sorted; empty on a clean run.  Until 2026-09-18 the list
    was logged at ERROR here and returned to nobody, so a run whose EVERY worker died still
    ran the analysis stage over an empty leaf set and exited 0 (`pool-run-swallows-dead-arms`:
    68 arms dead on a KeyError, "All simulations complete", exit 0).  The detection was never
    the missing piece; the propagation was.  `_run_workers_flat` -> `_run_scenario` ->
    `_run_whatif_matrix` carry this list up, and `run_simulation._refuse_incomplete` turns it
    into exit status 1 with no analysis.

    WORKER RECYCLING IS PINNED AT 1 — one fresh process per job.  `max_tasks_per_child` was
    accepted but never forwarded (`_run_whatif_matrix` dropped it, and every run is a matrix),
    so every run in this repo's history has in fact recycled at 1.  The first run that actually
    honoured a larger value DEADLOCKED at the cell boundary: cell 1 finished all 244 arms, then
    the pool sat at zero CPU with one live worker of eighteen and never shut down
    (comparison_whatif_20260815_234253, recycle=6).  The identical profile at recycle=1 the day
    before completed both cells (comparison_whatif_20260814_083723).  A worker holding a queue
    of jobs is a bad trade in multi-hour code regardless: the measured saving is ~10 s of spawn
    per job against a job that runs for minutes, and workers reload their assets per job anyway.
    So the value is fixed here rather than plumbed, and the CLI flag warns if asked for more."""
    recycle = 1
    done_uids, finalized = set(), set()
    work_units, meta = [], {}
    # runtime_metrics.db lives at the RUN ROOT (spans every cell): base_dir is this cell's dir, so
    # the root is its parent when running under a cell (else base_dir itself for a legacy flat run).
    run_root = os.path.dirname(base_dir) if cell else base_dir
    for attempt in range(max_retries + 1):
        work_units, meta = _build_work_units(
            pairs, base_dir, shared_by_pair, log, log_queue, max_workers,
            skip_completed=(skip_completed or attempt > 0),
            resume_granularity=resume_granularity,
            # THE POOL IS STILL UP on every attempt after the first, and `done_uids` already
            # holds the units that finished before the break -- they are filtered out of
            # `remaining` below and will never be resubmitted.  The coupled reconciler needs
            # to know that: a torn pair seen HERE would be repaired by discarding the output
            # of exactly those units.  A `--resume` of a dead run has no done_uids and is the
            # state the repair is written for (site-dock 10 / 22).
            mid_flight=attempt > 0)
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
                                   log, done_uids, finalized, cell=cell, run_root=run_root)
        if not broke:
            break            # pool completed; residual failures are deterministic → quarantine
        if attempt == 0:
            _explain_worker_death(log)      # once: the WHY the 2026-09-18 log never had
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
    return unfinished


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
) -> list:
    """Flat ProcessPoolExecutor pool with automatic crash-recovery.

    Returns `_supervise`'s list of unrecovered unit ids (empty on a clean run) -- see there for
    why the list is returned and not only logged.

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
        unfinished = _supervise(
            pairs, base_dir, shared_by_pair, max_workers, log,
            log_queue=log_queue, max_tasks_per_child=max_tasks_per_child,
            skip_completed=skip_completed, max_retries=max_retries,
            resume_granularity=resume_granularity, cell=cell,
            cell_index=cell_index, cell_total=cell_total)
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')
    return unfinished
