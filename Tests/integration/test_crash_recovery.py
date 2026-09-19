"""test_crash_recovery.py

Locks the automatic crash-recovery of run_simulation.py:

  - FINALIZE GATE (the bug fix): a (pair, config, channel) group is finalized — sim_meta.json
    written, resume.pkl deleted — ONLY when every one of its arms genuinely succeeded.  A group
    with a failed/unreturned arm keeps its resume.pkl, so `--resume` can re-run the dead arm
    instead of skipping it as "complete".
  - SUPERVISOR: a hard worker death that breaks the pool triggers a rebuild + resubmit of the
    unfinished units, up to max_retries; a persistently-failing unit is QUARANTINED (its resume
    state left intact) while the rest finish.
  - _plan_strategy_start: honors resume granularity (strategy = reset partial arm to batch 0;
    batch = continue from checkpoint; done arm = no-op).
  - TORN FINALIZE: _finalize_config_run writes sim_meta.json BEFORE it removes resume.pkl, so a
    kill inside the finalize window leaves a dir that is still resumable (both files present)
    rather than one that is neither resumable nor complete.
  - SECOND RUN IN ONE DB: the fresh-run branch of _plan_strategy_start REFUSES to create_run over
    a db that already holds a run — find_run resolves the OLDEST run, so a second one would
    silently redirect every later query to the abandoned arm.

The pool/data layer is faked so the control flow is exercised deterministically without a real
ProcessPool or generated DB pairs.

Run:  python -m pytest Tests/test_crash_recovery.py -q
"""
from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace

import sqlite3

import pytest

from Optimization import run_simulation as rs
from Optimization.persistence.Picking_Data import create_run, init_run_db
from Optimization.runschema.sim_manifest import _load_resume, _save_resume, _resume_path
from Optimization.simdriver.strategy_runner import save_worker_checkpoint

_LOG = logging.getLogger('test_crash_recovery')


def _skeleton(run_dir, keys):
    return {'run_dir': str(run_dir), 'name': 'cfg', 'inventory': 'prof', 'channel': 'store',
            'strategies': [{'key': k, 'label': k, 'db_path': f'{k}.db', 'run_id': 1} for k in keys],
            'optimal_sigma_fd': 0.0, 'optimal_work': 0.0, 'inv_db': 'i', 'aff_db': 'a'}


# ── finalize gate (the bug fix) ─────────────────────────────────────────────────────────

def test_finalize_gate_skips_group_with_a_failed_arm(tmp_path):
    run_dir = tmp_path / 'store'
    run_dir.mkdir()
    _save_resume(str(run_dir), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})   # resume.pkl present
    gk = ('prof', 'cfg', 'store')
    sk = _skeleton(run_dir, ['a', 'b'])
    meta = {gk: {'sim_skeleton': sk, 'members': frozenset((*gk, k) for k in ('a', 'b'))}}
    finalized = set()

    # only arm 'a' succeeded → group must NOT finalize
    rs._finalize_ready_groups(meta, {(*gk, 'a')}, finalized, _LOG)
    assert not (run_dir / 'sim_meta.json').exists(), 'finalized a group with an unfinished arm'
    assert os.path.exists(_resume_path(str(run_dir))), 'resume.pkl deleted for a crashed group'
    assert gk not in finalized

    # both arms succeeded → group finalizes (sim_meta written, resume.pkl removed)
    rs._finalize_ready_groups(meta, {(*gk, 'a'), (*gk, 'b')}, finalized, _LOG)
    assert (run_dir / 'sim_meta.json').exists()
    assert not os.path.exists(_resume_path(str(run_dir)))
    assert gk in finalized
    assert json.loads((run_dir / 'sim_meta.json').read_text())['name'] == 'cfg'


# ── supervisor: rebuild on broken pool, quarantine on persistent failure ────────────────

def _fake_units(tmp_path):
    gk = ('prof', 'cfg', 'store')
    uids = [(*gk, 'a'), (*gk, 'b')]
    # `group_keys` / `arm_key` as `workunits._stamp_identity` stamps them: the success path
    # reads the unit's identity off its payload, never off a uid slice.
    units = [(u, {'strategy': u[3], 'group_keys': [gk], 'arm_key': u[3]}) for u in uids]
    meta = {gk: {'sim_skeleton': _skeleton(tmp_path, ['a', 'b']),
                 'members': frozenset(uids)}}
    return units, meta, gk, uids


# Driven through `scenario._run_cells` -- the production path -- over a THREAD executor
# (`sc._sim_executor` patched) and a fake worker (`supervisor._run_strategy_worker` patched,
# which `sim_jobs` reads at call time).  `_build_work_units` is faked where `scenario` reads
# it, and the worker-death probe (a subprocess) is stubbed at `workpool`.

def _drive(monkeypatch, tmp_path, units, meta, worker, *, max_retries=2, assets=None,
           on_build=None):
    from concurrent.futures import ThreadPoolExecutor
    from Optimization.simdriver import scenario as sc, supervisor as sup, workpool as wp
    from Optimization.simdriver.cells import Cell
    builds = []

    def fake_build(pairs, base, shared, log_, log_queue, workers, **kw):
        builds.append(kw.get('mid_flight', False))
        if on_build is not None:
            on_build(kw)
        return list(units), dict(meta)
    monkeypatch.setattr(sc, '_build_work_units', fake_build)
    monkeypatch.setattr(sc, '_sim_executor', lambda n: ThreadPoolExecutor(max_workers=n))
    monkeypatch.setattr(sup, '_run_strategy_worker', worker)
    monkeypatch.setattr(wp, '_explain_worker_death', lambda log, mod: None)
    left = sc._run_cells(
        str(tmp_path), [('prof', 'i', 'a')], [Cell('k1_off', None, {'enabled': False}, 'round_robin')],
        _LOG, workers=2, assets_for=assets or (lambda cell, cell_dir: {'prof': {}}),
        max_retries=max_retries, resume_granularity='strategy')
    return left, builds


def test_the_pool_rebuilds_and_resubmits_after_a_break(monkeypatch, tmp_path):
    from concurrent.futures.process import BrokenProcessPool
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, (uid_a, uid_b) = _fake_units(tmp_path)
    gen = {'n': 0}

    def worker(sa):
        if sa['strategy'] == 'b' and gen['n'] == 0:
            gen['n'] = 1
            raise BrokenProcessPool('hard worker death')   # one arm lands, then the pool breaks
        return {'done': 1, 'elapsed': 0.0, 'strategy': sa['strategy']}

    left, builds = _drive(monkeypatch, tmp_path, units, meta, worker)
    assert left == {}, left
    assert builds == [False, True], 'the pool did not rebuild + resubmit after a broken pool'
    assert (tmp_path / 'sim_meta.json').exists(), 'group not finalized after full recovery'


def test_a_persistent_failure_is_quarantined(monkeypatch, tmp_path):
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, (uid_a, uid_b) = _fake_units(tmp_path)

    def worker(sa):
        if sa['strategy'] == 'b':
            raise RuntimeError('deterministic bad config')    # not broke -> no retry
        return {'done': 1, 'elapsed': 0.0, 'strategy': sa['strategy']}
    warnings = []
    monkeypatch.setattr(_LOG, 'error', lambda m, *a, **k: warnings.append(m))

    left, builds = _drive(monkeypatch, tmp_path, units, meta, worker)

    # the run did NOT abort; the failing arm's resume state is intact and it's reported
    assert builds == [False], 'a deterministic failure must never trigger a rebuild'
    assert left == {'k1_off': [uid_b]}
    assert os.path.exists(_resume_path(str(tmp_path))), 'quarantined arm lost its resume state'
    assert not (tmp_path / 'sim_meta.json').exists(), 'finalized a group with a quarantined arm'
    assert any('UNRECOVERED' in str(m) for m in warnings), 'no quarantine report emitted'
    assert any('--resume' in str(m) for m in warnings), 'quarantine report lacks a resume command'


# ── _plan_strategy_start branches (granularity) ─────────────────────────────────────────

def test_plan_strategy_start_all_branches(monkeypatch, tmp_path):
    calls, ckpt = [], {'v': 0}
    # _plan_strategy_start now lives in simdriver.workunits and resolves these via that module's
    # globals — patch there, not on the run_simulation re-export.
    monkeypatch.setattr('Optimization.simdriver.workunits.init_run_db',
                        lambda p, **kw: calls.append(('init', p)))   # **kw: defer_indices
    monkeypatch.setattr('Optimization.simdriver.workunits.create_run',
                        lambda p, rt, params, identity=None: calls.append(('create', p)) or 999)
    monkeypatch.setattr('Optimization.simdriver.workunits.reset_strategy_db',
                        lambda rd, db, key: calls.append(('reset', key)))
    monkeypatch.setattr('Optimization.simdriver.workunits.load_worker_checkpoint',
                        lambda rd, key: ckpt['v'])
    s = SimpleNamespace(key='uni', run_type='comparison')

    def plan(gran, prev_id, is_resume, roll_over=False):
        calls.clear()
        return rs._plan_strategy_start(str(tmp_path), s, 100, 'uni.db', {}, {},
                                       gran, prev_id, 0, is_resume, _LOG,
                                       roll_over=roll_over)

    # fresh run → create, start 0
    ckpt['v'] = 0
    assert plan('strategy', None, False) == (999, 0) and ('create', 'uni.db') in calls
    # resume, partial arm, strategy granularity → reset + fresh run_id + 0
    ckpt['v'] = 30
    assert plan('strategy', 7, True) == (999, 0) and ('reset', 'uni') in calls
    # resume, partial arm, batch granularity → keep run_id + continue at checkpoint
    ckpt['v'] = 30
    assert plan('batch', 7, True) == (7, 30) and ('reset', 'uni') not in calls
    # resume, done arm (ckpt >= n_batches) → keep run_id, start n_batches (no-op loop)
    ckpt['v'] = 100
    assert plan('strategy', 7, True) == (7, 100) and ('reset', 'uni') not in calls


def test_batch_resume_is_refused_while_the_carry_is_on(monkeypatch, tmp_path):
    """Batch-level resume LOSES DEMAND once unpicked work rolls over, and that is a
    different failure from the one its warning describes.

    `_pending` — the units a day cut or a stock clamp rolled into the next batch — lives in
    the worker's locals and is in no checkpoint. Resuming at batch N discards everything the
    pre-crash run carried, and the resumed stream never asks for it again, so the run reports
    throughput it did not earn. "Not bit-identical" reads as a rounding difference; this is a
    conservation break, so it raises.

    The three cases that must still work are asserted alongside, because a refusal that also
    breaks the default path is worse than the silence it replaces.
    """
    import pytest
    calls, ckpt = [], {'v': 30}
    monkeypatch.setattr('Optimization.simdriver.workunits.init_run_db',
                        lambda p, **kw: None)   # **kw: init_run_db takes defer_indices
    monkeypatch.setattr('Optimization.simdriver.workunits.create_run',
                        lambda p, rt, params, identity=None: 999)
    monkeypatch.setattr('Optimization.simdriver.workunits.reset_strategy_db',
                        lambda rd, db, key: calls.append(('reset', key)))
    monkeypatch.setattr('Optimization.simdriver.workunits.load_worker_checkpoint',
                        lambda rd, key: ckpt['v'])
    s = SimpleNamespace(key='uni', run_type='comparison')

    def plan(gran, prev_id, is_resume, roll_over):
        return rs._plan_strategy_start(str(tmp_path), s, 100, 'uni.db', {}, {},
                                       gran, prev_id, 0, is_resume, _LOG,
                                       roll_over=roll_over)

    with pytest.raises(RuntimeError, match='batch-level resume'):
        plan('batch', 7, True, roll_over=True)

    # ...and the default is untouched: strategy granularity replays from batch 0, where a
    # carry that never happened cannot be lost.
    assert plan('strategy', 7, True, roll_over=True) == (999, 0)
    # a DONE arm never resumes into anything, carry or no carry
    ckpt['v'] = 100
    assert plan('batch', 7, True, roll_over=True) == (7, 100)
    # and a fresh run has no checkpoint to refuse
    ckpt['v'] = 0
    assert plan('batch', None, False, roll_over=True) == (999, 0)


# ── the torn-finalize window (ordering inside _finalize_config_run) ─────────────────────

def _one_run_db(path, key):
    """A real sim_<key>.db holding exactly one run — the state a finished arm leaves."""
    init_run_db(str(path))
    return create_run(str(path), 'comparison', {}, identity={'strategy_key': key})


def _n_runs(path):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
    finally:
        con.close()


def test_a_kill_inside_finalize_leaves_the_dir_resumable(monkeypatch, tmp_path):
    """A kill between the marker write and the resume-file removal must leave BOTH files.

    The finalize window is the one place a run dir can end up NEITHER resumable NOR complete:
    the skip guard reads "complete" as *sim_meta.json present AND resume.pkl absent*, so a dir
    missing both markers is re-planned as a FRESH run — `create_run` over a populated db, with
    no `reset_strategy_db`.  `find_run` then resolves `ORDER BY run_id LIMIT 1` and every later
    query answers from the abandoned run.  No exception, no log line, wrong numbers.

    Writing sim_meta.json FIRST makes the window hold both files instead: not complete, still
    resumable, and `_plan_strategy_start` takes its documented done-arm branch.  This test is a
    mutation detector for the ordering — revert it and the removal raises before the marker is
    ever written, so the `sim_meta.json` assertion below fails.
    """
    run_dir = tmp_path / 'store'
    run_dir.mkdir()
    db_path = run_dir / 'sim_uni.db'
    n_batches = 100
    prev_id = _one_run_db(db_path, 'uni')
    save_worker_checkpoint(str(run_dir), 'uni', n_batches)      # the arm genuinely finished
    _save_resume(str(run_dir), {'uni': prev_id}, {'uni': n_batches})

    _real_remove = os.remove

    def _die_on_resume_removal(path, *a, **k):
        if str(path).endswith('resume.pkl'):
            raise OSError('killed inside the finalize window')
        return _real_remove(path, *a, **k)
    monkeypatch.setattr(os, 'remove', _die_on_resume_removal)

    with pytest.raises(OSError, match='finalize window'):
        rs._finalize_config_run(_skeleton(run_dir, ['uni']))
    monkeypatch.undo()

    # BOTH markers present: not complete (so the guard re-plans it), still resumable.
    assert (run_dir / 'sim_meta.json').exists(), \
        'the completeness marker was not written before the resume file was removed'
    assert os.path.exists(_resume_path(str(run_dir))), 'resume.pkl went first after all'

    # ...and the resume over that dir is a no-op that reuses the run, not a second create_run.
    resume = _load_resume(str(run_dir))
    s = SimpleNamespace(key='uni', run_type='comparison')
    assert rs._plan_strategy_start(
        str(run_dir), s, n_batches, str(db_path), {}, {}, 'strategy',
        resume['run_ids']['uni'], resume['next_batch']['uni'], True, _LOG) == (prev_id, n_batches)
    assert _n_runs(db_path) == 1, 'the resume opened a second run in the arm db'


# ── a second run in one db is refused, never created ───────────────────────────────────

def test_fresh_branch_refuses_to_create_a_second_run(tmp_path):
    """The corruption has no symptom, so the refusal is the only place it can be named.

    `find_run` resolves `ORDER BY run_id LIMIT 1` — the OLDEST run — so a db that acquired a
    second one answers every run_id-filtered query from the ABANDONED one and doubles every
    unfiltered aggregate over the file.  Nothing reaches the fresh branch in that state today;
    this makes the invariant a fact rather than an argument.
    """
    run_dir = tmp_path / 'store'
    run_dir.mkdir()
    db_path = run_dir / 'sim_uni.db'
    prev_id = _one_run_db(db_path, 'uni')
    s = SimpleNamespace(key='uni', run_type='comparison')

    with pytest.raises(RuntimeError) as exc:
        rs._plan_strategy_start(str(run_dir), s, 100, str(db_path), {}, {}, 'strategy',
                                None, 0, False, _LOG)
    msg = str(exc.value)
    assert 'sim_uni.db' in msg and 'uni' in msg and str(run_dir) in msg, \
        f'refusal names neither the db, the strategy nor the run dir: {msg}'
    assert f'run_id={prev_id}' in msg, f'refusal does not name the existing run: {msg}'
    assert _n_runs(db_path) == 1, 'the refusal still left a second run behind'


def test_fresh_branch_is_unchanged_on_an_empty_or_absent_db(tmp_path):
    """The refusal must not cost the ordinary fresh start — neither shape of a virgin arm."""
    s = SimpleNamespace(key='uni', run_type='comparison')

    absent = tmp_path / 'absent' / 'sim_uni.db'
    absent.parent.mkdir()
    rid, start = rs._plan_strategy_start(str(absent.parent), s, 100, str(absent), {}, {},
                                         'strategy', None, 0, False, _LOG)
    assert isinstance(rid, int) and start == 0

    empty_dir = tmp_path / 'empty'
    empty_dir.mkdir()
    empty = empty_dir / 'sim_uni.db'
    init_run_db(str(empty))                      # schema present, no runs
    rid2, start2 = rs._plan_strategy_start(str(empty_dir), s, 100, str(empty), {}, {},
                                           'strategy', None, 0, False, _LOG)
    assert isinstance(rid2, int) and start2 == 0
    assert _n_runs(empty) == 1, 'the fresh branch did not create the run it was asked for'


# ── the unrecovered list reaches the exit status (2026-09-18) ─────────────────────
#
# `_supervise` has always DETECTED the units it could not recover and logged them at ERROR with
# a resume command.  It returned nothing, `_run_workers_flat`, `_run_scenario` and
# `_run_whatif_matrix` returned nothing about it, and `main` went on to run the analysis stage
# and print "All simulations complete." -- exit 0 with every arm dead
# (`pool-run-swallows-dead-arms`, 2026-09-05).  The tests below pin each hop of the propagation
# and the refusal at the top, on the same faked pool the quarantine test above uses.

def test_a_clean_run_comes_back_empty(monkeypatch, tmp_path):
    """NON-VACUITY for the refusal: a clean run must come back empty, or every run exits 1."""
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, (uid_a, uid_b) = _fake_units(tmp_path)
    left, _builds = _drive(monkeypatch, tmp_path, units, meta,
                           lambda sa: {'done': 1, 'elapsed': 0.0, 'strategy': sa['strategy']})
    assert left == {}
    assert (tmp_path / 'sim_meta.json').exists()


def test_a_cell_whose_setup_raises_is_reported_not_raised(monkeypatch, tmp_path):
    """A campaign pin that does not match, a derivation that disagrees with the record: the
    setup raises, and with other cells' units in flight the exception must not unwind into
    the pool (its exit would wait for them and book nothing).  Reported per cell instead."""
    units, meta, gk, _ = _fake_units(tmp_path)

    def bad_assets(cell, cell_dir):
        raise RuntimeError('the derivation disagrees with the record')
    left, builds = _drive(monkeypatch, tmp_path, units, meta,
                          lambda sa: {'done': 1, 'elapsed': 0.0, 'strategy': sa['strategy']},
                          assets=bad_assets)
    assert left == {'k1_off': [('setup',)]}, left
    assert builds == [], 'units were built for a cell whose assets never existed'


def test_a_leaf_whose_prepare_failed_is_reported(monkeypatch, tmp_path):
    """A leaf whose `_prepare_channel_run` raised becomes NO unit, so nothing downstream could
    report it and the run finished, exited 0 and simply lacked an arm.  The builder now hands
    such leaves back through `failed=`, and they ride the unfinished report."""
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, _ = _fake_units(tmp_path)
    left, _builds = _drive(monkeypatch, tmp_path, units, meta,
                           lambda sa: {'done': 1, 'elapsed': 0.0, 'strategy': sa['strategy']},
                           on_build=lambda kw: kw['failed'].append('prof/cfg2/store'))
    assert left == {'k1_off': [('prepare', 'prof/cfg2/store')]}, left
    assert (tmp_path / 'sim_meta.json').exists(), 'the leaves that DID prepare must still finish'


def test_run_whatif_matrix_reports_unfinished_per_cell(monkeypatch, tmp_path):
    """The matrix hands `_run_cells` every cell not already complete and returns what it
    left, keyed by cell -- so `info['unfinished']` is empty exactly when the matrix is clean."""
    from Optimization.simdriver import scenario as sc
    from Optimization.config.whatif_config import SPECS
    seen = []

    def fake_cells(base_dir, pairs, cells, log, **kw):
        seen.extend(c.name for c in cells)
        return {c.name: [('u', c.name)] for c in cells if c.name.endswith('_off')}
    monkeypatch.setattr(sc, '_run_cells', fake_cells)
    monkeypatch.setattr(sc, 'build_shared_assets', lambda *a, **k: {'planned_inv_db': 'p.db'})
    monkeypatch.setattr(sc, '_record_coverage', lambda *a, **k: None)

    info = sc._run_whatif_matrix(str(tmp_path), [('lbl', 'i.db', 'a.db')], _LOG, SPECS['single'])
    assert seen == ['k1_off'], 'the single cell never reached the pool driver'
    assert set(info) >= {'cells', 'reference', 'unfinished'}
    assert info['unfinished'] == {'k1_off': [('u', 'k1_off')]}, info


def test_refuse_incomplete_exits_one_with_the_resume_command(tmp_path):
    errors = []
    log = SimpleNamespace(error=lambda m, *a, **k: errors.append(str(m)),
                          info=lambda *a, **k: None)
    with pytest.raises(SystemExit) as ex:
        rs._refuse_incomplete({'cells': ['k1_off'], 'unfinished': {'k1_off': [('a',), ('b',)]}},
                              str(tmp_path), log)
    assert ex.value.code == 1
    assert any('UNRECOVERED' in m for m in errors), errors
    assert any('--resume' in m and str(tmp_path) in m for m in errors), errors
    assert any('skipped' in m for m in errors), 'it must say the analysis was not run'


def test_refuse_incomplete_is_silent_on_a_clean_matrix(tmp_path):
    """NON-VACUITY: no unfinished units, no exit, no error line."""
    errors = []
    log = SimpleNamespace(error=lambda m, *a, **k: errors.append(str(m)),
                          info=lambda *a, **k: None)
    assert rs._refuse_incomplete({'cells': ['k1_off'], 'unfinished': {}}, str(tmp_path), log) is None
    assert rs._refuse_incomplete({'cells': ['k1_off']}, str(tmp_path), log) is None
    assert errors == []
