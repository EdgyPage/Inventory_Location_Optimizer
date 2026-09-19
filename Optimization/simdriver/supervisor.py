"""simdriver.supervisor — the sim driver's books behind the work pool, and the finalize gate.

The pool itself is `simdriver.workpool.WorkPool` (one executor for every cell of a run, its
own dispatch queue, the broken-pool recovery policy).  This module is what the SIM driver
hangs on it: `_sim_executor` builds the spawn pool with worker recycling pinned at 1;
`sim_jobs` turns a cell's `(uid, args)` work units into the pool's `Job`s, stamped the way the
worker's log line expects; `SimBooks` is the per-cell bookkeeping the pool's `on_success` /
`on_failure` callbacks land in -- the per-leaf `done` line, the runtime-metrics row, the
expected-pick attach, and the FINALIZE GATE: a group's `sim_meta.json` is written ONLY when
every one of its member units succeeded, so a crashed arm keeps its resume state and stays
resumable.  `_run_strategy_worker` is the only pickled callable (imported from
strategy_runner, unchanged) and is looked up here at submit time, which is how the
real-spawn fixture (`Tests/integration/_broken_pool_driver.py`) swaps it for a worker that
dies.

Until 2026-09-19 this module also owned the executor, one per cell (`_run_workers_flat` ->
`_supervise` -> `_run_pool`), and the driver waited for a cell's last unit before the next
cell's setup.  Those three are gone; their bookkeeping is `_absorb_success` and
`_finalize_unit_groups` below, and their recovery policy is the pool's `finish`."""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os

from Optimization.runschema.sim_manifest import _resume_path
from Optimization.simdriver.strategy_runner import _cleanup_checkpoints, _run_strategy_worker
from Optimization.simdriver.workpool import Job, _tag_of
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


def _absorb_success(res, uid, sa, meta, done_uids, log, cell, run_root):
    """A unit's SUCCESS absorbed into the parent's books: the per-leaf `done` line (with its
    ledger breaks), `done_uids`, the arm's expected pick onto its group's skeleton, and the
    runtime-metrics row.  Raises (a leaf-count mismatch) into the pool's success handling,
    which books the unit as a `strategy FAILED`, as before."""
    _tag = _tag_of(cell, uid)
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
    # `sa['group_keys']` / `sa['arm_key']`, never `uid[:3]` / `uid[3]`: this block once
    # sliced the uid INDEPENDENTLY of the finalize gate below, so a coupled unit's
    # carried group keys would not have reached it and `meta[(label, 'coupled',
    # arm_store)]` raises KeyError -- inside the success path, which turns a unit
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


def _finalize_unit_groups(sa, meta, done_uids, finalized, log, cell):
    """Finalize every group this unit completes, from `group_keys`."""
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


# ── the sim driver's side of the pool ───────────────────────────────────────────────────

def _sim_executor(max_workers: int):
    """The sim pool's executor: a spawn `ProcessPoolExecutor` with WORKER RECYCLING PINNED
    AT 1 -- one fresh process per unit.

    The value is fixed HERE, in the one place the executor is built, rather than plumbed
    from the CLI (`--max-tasks-per-child` is accepted and warned about, never honoured).
    The flag was NOT forwarded for months (`_run_whatif_matrix` dropped it, and every run is
    a matrix), so every run in this repo's history has in fact recycled at 1.  The first run
    that actually honoured a larger value DEADLOCKED at the cell boundary: cell 1 finished
    all 244 arms, then the pool sat at zero CPU with one live worker of eighteen and never
    shut down (comparison_whatif_20260815_234253, recycle=6).  The identical profile at
    recycle=1 the day before completed both cells (comparison_whatif_20260814_083723).  A
    worker holding a queue of jobs is a bad trade in multi-hour code regardless: the
    measured saving is ~10 s of spawn per job against a job that runs for minutes, and
    workers reload their assets per job anyway (the per-arm startup term is catalogue
    LOADING, not spawn -- memory `per-arm-startup-is-catalogue-loading`).  The analysis
    stage builds its own executor WITHOUT this pin (`run_analysis._analysis_executor`): its
    jobs are seconds long and reuse a loaded context across co-scheduled jobs.
    `Tests/unit/test_worker_recycling_pin.py` pins the literal below by source."""
    recycle = 1
    return concurrent.futures.ProcessPoolExecutor(max_workers=max_workers,
                                                  max_tasks_per_child=recycle)


def sim_jobs(cell, units, *, cell_index=1, cell_total=1):
    """One cell's `(uid, args)` work units as the pool's `Job`s, stamped with what the
    worker's log line reads: `cell` (the logger name and every per-arm line), `job_tag`,
    the per-cell `job_index/job_total`, and `cell_pos` (this cell's position in the spec).
    There is no whole-run job counter any more: the old `gjob` assumed every cell had the
    same unit count and was wrong on resume and after a retry; matrix progress is now the
    pool's own `[pool] N unit(s) done` line.

    `_run_strategy_worker` is read from THIS module at call time so a fixture can rebind it
    before the driver runs (`_broken_pool_driver.py` does, for a worker that cannot import)."""
    total = len(units)
    jobs = []
    for idx, (uid, sa) in enumerate(units, start=1):
        sa['job_index'], sa['job_total'] = idx, total
        sa['cell'] = cell                       # stamped on every worker line (cell/strategy)
        sa['cell_pos'] = f'{cell_index}/{cell_total}'
        sa['job_tag'] = _tag_of(cell, uid)
        jobs.append(Job(key=uid, fn=_run_strategy_worker, payload=sa))
    return jobs


class SimBooks:
    """The sim driver's per-cell books behind `WorkPool`'s callbacks.

    Per cell, in the shape the old `_run_pool` kept: `meta[group_key] = {sim_skeleton,
    members}`, `done` uids, `finalized` group keys.  Per cell because uids and group keys
    repeat across cells.  `on_success` absorbs a unit and finalizes every group it
    completes; `on_failure` is the `strategy FAILED` line; `sweep` is the safety pass the
    old `_supervise` ran at the end (a group whose last member landed but whose finalize
    raised gets one more chance)."""

    def __init__(self, log: logging.Logger, run_root: str | None):
        self.log = log
        self.run_root = run_root
        self.cells: dict = {}       # cell -> {'meta', 'done', 'finalized'}

    def _state(self, cell) -> dict:
        st = self.cells.get(cell)
        if st is None:
            st = self.cells[cell] = {'meta': {}, 'done': set(), 'finalized': set()}
        return st

    def register(self, cell, meta: dict) -> None:
        """A cell's group meta from `_build_work_units` (merged: a rebuild re-registers)."""
        self._state(cell)['meta'].update(meta)

    def on_success(self, cell, uid, sa, res) -> None:
        st = self._state(cell)
        _absorb_success(res, uid, sa, st['meta'], st['done'], self.log, cell, self.run_root)
        _finalize_unit_groups(sa, st['meta'], st['done'], st['finalized'], self.log, cell)

    def on_failure(self, cell, uid, sa, exc) -> None:
        self.log.error(f'  [{_tag_of(cell, uid)}] strategy FAILED: {exc}', exc_info=exc)

    def sweep(self) -> None:
        for cell, st in self.cells.items():
            _finalize_ready_groups(st['meta'], st['done'], st['finalized'], self.log, cell=cell)

    def done(self, cell) -> set:
        return self._state(cell)['done']
