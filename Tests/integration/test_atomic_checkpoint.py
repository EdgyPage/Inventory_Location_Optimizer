"""test_atomic_checkpoint.py

Locks the crash-hardening of the per-strategy checkpoint + strategy-level reset:
  - save_worker_checkpoint writes ATOMICALLY (tmp + os.replace) and leaves no temp file;
  - that replace SURVIVES a transient file lock, and stays fatal when the lock never clears;
  - reset_strategy_db discards a partial arm's sim DB, its keyframe sibling, and its checkpoint
    (so strategy-granularity resume restarts bit-identically from batch 0).

Why the lock case earns its own section
---------------------------------------
`os.replace` raises `PermissionError` (WinError 5) on Windows whenever anything holds the
destination open for a moment — which a virus scanner or the file indexer routinely does just after
a file is written, and the results drive is an external volume where that is likelier still.  This
runs once per checkpoint per arm with up to 18 workers, and the exception propagates straight out
of `_run_strategy_worker`, so the pool records the whole ARM as failed.  Measured on a 6-batch tiny
sweep: two of eight arms died on a momentary lock, and the wreckage failed three separate smoketest
stages downstream — the orphaned `.pkl.tmp.<pid>` as an undeclared path, the un-finalized
`_ckpt_*.pkl` as an artifact the run shape must not have, and the two lost arms as
`channel-run count 6 != expected 8`.

The failure deliberately stays FATAL when the lock never clears.  A checkpoint that silently did not
land would let a resume restart from an earlier batch and re-emit bin-log rows under fresh `seq`
values, duplicating them against the `(run_id, batch_id, seq)` primary key.  Losing the arm is
better than corrupting its log.

Run:  python -m pytest Tests/integration/test_atomic_checkpoint.py -q
"""
from __future__ import annotations

import os
import pickle

import pytest

from Optimization.simdriver import strategy_runner as sr
from Optimization.simdriver.strategy_runner import (
    save_worker_checkpoint, load_worker_checkpoint, reset_strategy_db,
)
from Optimization.persistence.Picking_Data import keyframe_db_path


def _temps(run_dir):
    """Any surviving `*.tmp.<pid>` — the artifact an interrupted replace leaves in the run tree."""
    return [f for f in os.listdir(run_dir) if '.tmp.' in f]


def test_checkpoint_atomic_roundtrip(tmp_path):
    save_worker_checkpoint(str(tmp_path), 'uni', 37)
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 37
    save_worker_checkpoint(str(tmp_path), 'uni', 88)                 # overwrite atomically
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 88
    # atomic write leaves no temp artifact behind
    assert not any(n.startswith('_ckpt_uni.pkl.tmp') for n in os.listdir(tmp_path))
    # absent checkpoint -> 0 (never started / after reset)
    assert load_worker_checkpoint(str(tmp_path), 'never') == 0


# ── a lock that clears ────────────────────────────────────────────────────────

@pytest.mark.parametrize('failures', [1, 2, 4])
def test_a_transient_lock_is_retried_until_it_clears(tmp_path, monkeypatch, failures):
    """A scanner holds the file for a few hundred ms, so every count below the cap must survive —
    not merely a single retry."""
    real_replace = os.replace
    calls = {'n': 0}

    def flaky(src, dst):
        calls['n'] += 1
        if calls['n'] <= failures:
            raise PermissionError(5, 'Access is denied')
        return real_replace(src, dst)

    monkeypatch.setattr(sr.os, 'replace', flaky)
    monkeypatch.setattr(sr.time, 'sleep', lambda _s: None)       # do not actually wait in a test

    save_worker_checkpoint(str(tmp_path), 'uni', 3)

    assert calls['n'] == failures + 1, f'expected {failures + 1} attempts, made {calls["n"]}'
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 3
    assert _temps(str(tmp_path)) == [], 'a retried write left its temp behind'
    with open(os.path.join(str(tmp_path), '_ckpt_uni.pkl'), 'rb') as fh:
        assert pickle.load(fh) == {'next_batch_id': 3}, 'the retried write stored the wrong payload'


def test_the_retry_budget_is_real(tmp_path):
    """Non-vacuity: with a budget of 1, the parametrised test above could not tell a retry from
    luck, and a zero back-off would put every attempt in the same instant as the first."""
    assert sr._CKPT_REPLACE_ATTEMPTS >= 3, (
        f'retry budget is {sr._CKPT_REPLACE_ATTEMPTS}; too small to ride out a scanner')
    assert sr._CKPT_REPLACE_DELAY > 0, 'back-off is zero — all attempts land in the same instant'


# ── a lock that never clears ──────────────────────────────────────────────────

def test_a_permanent_lock_still_raises_and_cleans_up(tmp_path, monkeypatch):
    """Stays fatal (see the module docstring), and leaves no undeclared path behind."""
    calls = {'n': 0}

    def always_denied(src, dst):
        calls['n'] += 1
        raise PermissionError(5, 'Access is denied')

    monkeypatch.setattr(sr.os, 'replace', always_denied)
    monkeypatch.setattr(sr.time, 'sleep', lambda _s: None)

    with pytest.raises(PermissionError):
        save_worker_checkpoint(str(tmp_path), 'uni', 5)

    assert calls['n'] == sr._CKPT_REPLACE_ATTEMPTS, (
        f'gave up after {calls["n"]} attempts, budget is {sr._CKPT_REPLACE_ATTEMPTS}')
    assert _temps(str(tmp_path)) == [], (
        f'orphaned temp left in the run tree: {_temps(str(tmp_path))} — exactly what '
        f'runschema.preflight.verify reports as an undeclared path template')
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 0, 'a failed write created a checkpoint'


def test_an_unrelated_oserror_is_not_swallowed_by_the_retry(tmp_path, monkeypatch):
    """Only PermissionError is the transient being ridden out.  A full disk or a disconnected drive
    must surface at once rather than be retried five times and reported as a lock."""
    calls = {'n': 0}

    def disk_full(src, dst):
        calls['n'] += 1
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(sr.os, 'replace', disk_full)

    with pytest.raises(OSError) as exc:
        save_worker_checkpoint(str(tmp_path), 'uni', 1)

    assert not isinstance(exc.value, PermissionError), 'the test no longer distinguishes the cases'
    assert calls['n'] == 1, f'a non-lock error was retried {calls["n"]} times; it must surface at once'
    # The cleanup lives in a `finally`, so it covers this path too — not just the retried one.
    assert _temps(str(tmp_path)) == [], f'orphaned temp after a non-lock failure: {_temps(str(tmp_path))}'


# ── strategy-level reset ──────────────────────────────────────────────────────

def test_reset_strategy_db_removes_all_outputs(tmp_path):
    db = tmp_path / 'sim_uni.db'
    db.write_bytes(b'partial')
    kf = tmp_path / os.path.basename(keyframe_db_path(str(db)))
    kf.write_bytes(b'partial')
    save_worker_checkpoint(str(tmp_path), 'uni', 42)

    reset_strategy_db(str(tmp_path), str(db), 'uni')

    assert not db.exists(), 'partial sim DB not removed'
    assert not kf.exists(), 'partial keyframe DB not removed'
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 0, 'checkpoint not cleared -> would not restart at 0'


def test_reset_is_a_noop_when_nothing_exists(tmp_path):
    # must not raise when the arm never produced outputs
    reset_strategy_db(str(tmp_path), str(tmp_path / 'sim_missing.db'), 'missing')
