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

The pool/data layer is faked so the control flow is exercised deterministically without a real
ProcessPool or generated DB pairs.

Run:  python -m pytest Tests/test_crash_recovery.py -q
"""
from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace

from Optimization import run_simulation as rs
from Optimization.sim_manifest import _save_resume, _resume_path

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
    units = [(u, {'strategy': u[3]}) for u in uids]
    meta = {gk: {'sim_skeleton': _skeleton(tmp_path, ['a', 'b']),
                 'members': frozenset(uids)}}
    return units, meta, gk, uids


def test_supervise_rebuilds_pool_and_resubmits(monkeypatch, tmp_path):
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, (uid_a, uid_b) = _fake_units(tmp_path)
    monkeypatch.setattr('Optimization.simdriver.supervisor._build_work_units',
                        lambda *a, **k: (units, dict(meta)))
    n = {'calls': 0}

    def fake_run_pool(remaining, meta_, mw, rec, log, done_uids, finalized, cell='', run_root=None):
        n['calls'] += 1
        if n['calls'] == 1:
            done_uids.add(uid_a)                 # one arm lands, then the pool breaks
            return set(), True
        for uid, _sa in remaining:               # rebuild: the rest complete
            done_uids.add(uid)
        rs._finalize_ready_groups(meta_, done_uids, finalized, log)
        return set(), False
    monkeypatch.setattr('Optimization.simdriver.supervisor._run_pool', fake_run_pool)

    rs._supervise([('prof', 'i', 'a')], str(tmp_path), {'prof': {}}, 2, _LOG,
                  log_queue=None, max_tasks_per_child=1, skip_completed=False,
                  max_retries=2, resume_granularity='strategy')

    assert n['calls'] == 2, 'supervisor did not rebuild + resubmit after a broken pool'
    assert (tmp_path / 'sim_meta.json').exists(), 'group not finalized after full recovery'


def test_supervise_quarantines_persistent_failure(monkeypatch, tmp_path):
    _save_resume(str(tmp_path), {'a': 1, 'b': 2}, {'a': 0, 'b': 0})
    units, meta, gk, (uid_a, uid_b) = _fake_units(tmp_path)
    monkeypatch.setattr('Optimization.simdriver.supervisor._build_work_units',
                        lambda *a, **k: (units, dict(meta)))

    def fake_run_pool(remaining, meta_, mw, rec, log, done_uids, finalized, cell='', run_root=None):
        done_uids.add(uid_a)                     # 'a' succeeds; 'b' deterministically fails
        return {uid_b}, False                    # not broke → no retry (deterministic)
    monkeypatch.setattr('Optimization.simdriver.supervisor._run_pool', fake_run_pool)
    warnings = []
    monkeypatch.setattr(_LOG, 'error', lambda m, *a, **k: warnings.append(m))

    rs._supervise([('prof', 'i', 'a')], str(tmp_path), {'prof': {}}, 2, _LOG,
                  log_queue=None, max_tasks_per_child=1, skip_completed=False,
                  max_retries=2, resume_granularity='strategy')

    # the run did NOT abort; the failing arm's resume state is intact and it's reported
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
                        lambda p: calls.append(('init', p)))
    monkeypatch.setattr('Optimization.simdriver.workunits.create_run',
                        lambda p, rt, params, identity=None: calls.append(('create', p)) or 999)
    monkeypatch.setattr('Optimization.simdriver.workunits.reset_strategy_db',
                        lambda rd, db, key: calls.append(('reset', key)))
    monkeypatch.setattr('Optimization.simdriver.workunits.load_worker_checkpoint',
                        lambda rd, key: ckpt['v'])
    s = SimpleNamespace(key='uni', run_type='comparison')

    def plan(gran, prev_id, is_resume):
        calls.clear()
        return rs._plan_strategy_start(str(tmp_path), s, 100, 'uni.db', {}, {},
                                       gran, prev_id, 0, is_resume, _LOG)

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
