"""test_atomic_checkpoint.py

Locks the crash-hardening of the per-strategy checkpoint + strategy-level reset:
  - save_worker_checkpoint writes ATOMICALLY (tmp + os.replace) and leaves no temp file;
  - reset_strategy_db discards a partial arm's sim DB, its keyframe sibling, and its checkpoint
    (so strategy-granularity resume restarts bit-identically from batch 0).

Run:  python -m pytest Tests/test_atomic_checkpoint.py -q
"""
from __future__ import annotations

import os

from Optimization.strategy_runner import (
    save_worker_checkpoint, load_worker_checkpoint, reset_strategy_db,
)
from Optimization.Picking_Data import keyframe_db_path


def test_checkpoint_atomic_roundtrip(tmp_path):
    save_worker_checkpoint(str(tmp_path), 'uni', 37)
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 37
    save_worker_checkpoint(str(tmp_path), 'uni', 88)                 # overwrite atomically
    assert load_worker_checkpoint(str(tmp_path), 'uni') == 88
    # atomic write leaves no temp artifact behind
    assert not any(n.startswith('_ckpt_uni.pkl.tmp') for n in os.listdir(tmp_path))
    # absent checkpoint -> 0 (never started / after reset)
    assert load_worker_checkpoint(str(tmp_path), 'never') == 0


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
