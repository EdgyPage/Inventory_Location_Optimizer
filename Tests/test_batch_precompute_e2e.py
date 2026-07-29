"""test_batch_precompute_e2e.py — full-pipeline proof that the batch-precompute dedup feeds each arm
the SAME batches it would have sampled inline, through the real run_simulation wiring.

Why not compare sim outputs?  The simulation is intentionally not bit-reproducible run-to-run (placement
iterates over object-identity-keyed structures, so two runs of one arm already differ) — see the repo's
cross-arm comparison, which is statistical.  What the dedup must preserve is the *input*: the exact
per-batch (num_skus, items) sequence each worker feeds into Task.from_batch.  So this test captures that
sequence directly (Task.from_batch is stubbed to record the batch and skip the heavy sim) and asserts:

  * the precompute-wired run and the forced-inline run feed IDENTICAL batch sequences, and
  * the precompute run's sequence equals the on-disk precomputed file (i.e. the worker really loaded and
    used it via the real fingerprint check — not a silent inline fallback that would make this vacuous).

Skips when no generated inventory/affinity DB pair is present (clean checkout / CI).
Run: python -m pytest Tests/test_batch_precompute_e2e.py
"""
from __future__ import annotations

import glob
import logging
import os
import queue
import sqlite3
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


from Optimization import run_simulation as rs                     # noqa: E402
from Optimization.simdriver import strategy_runner as sr                    # noqa: E402
from Optimization.simdriver import batch_precompute as BP                   # noqa: E402

_N_E2E_BATCHES = 6


def _store_only_current_schema(inv_db):
    """True if inv_db is loadable by the current code (has relative_frequency) AND store-only
    (no fulfillment SKUs).  The precompute dedup is a single-channel/store-only optimization —
    a fulfillment dataset runs multi-channel and samples inline (no shared batches file), so this
    e2e only applies to store-only pairs.  Pre-rename DBs (demand_frequency) aren't loadable."""
    try:
        c = sqlite3.connect(inv_db)
        cols = [r[1] for r in c.execute('PRAGMA table_info(cartons)')]
        if 'relative_frequency' not in cols:
            return False
        ff = c.execute("SELECT COUNT(*) FROM cartons WHERE handling='fulfillment'").fetchone()[0]
        c.close()
        return ff == 0
    except Exception:
        return False


def _pair_or_skip():
    try:
        pairs = rs.discover_db_pairs(rs._DEFAULT_PROFILES_DIR)
    except Exception:
        pairs = []
    for pair in pairs:
        if _store_only_current_schema(pair[1]):
            return pair
    pytest.skip('no store-only, current-schema inventory/affinity pair for the precompute e2e')


def _store_channel_run(shared, pair_dir, cfg, log, workers=1):
    """Prepare the single store channel-run for `cfg` (this e2e uses a store-only pair, so the
    catalog is not mixed and the run collapses to the legacy <config>/ layout)."""
    from Optimization.config.channels import make_channel
    from Warehouse.kernel.regime import STORE
    pc = rs._build_pick_cfg(cfg, num_pickers=rs.K_PICKERS)
    ch = make_channel('store', STORE, pc, rs.K_PICKERS, restocks=rs.STORE_RESTOCKS)
    mixed, _ = rs._channel_runs_for(shared['inventory'])       # store-only → False
    return rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=workers)


def _capture_batch_sequence(shared, pair_dir, cfg, log, *, force_inline):
    """Run the first arm with Task.from_batch stubbed to record each batch (and skip the sim).
    Returns (captured_seq, strategy_args[0]).  captured_seq[i] = (num_skus, sorted items tuple)."""
    os.makedirs(pair_dir, exist_ok=True)
    strategy_args, _ = _store_channel_run(shared, pair_dir, cfg, log, workers=1)
    a = strategy_args[0]
    a['log_queue'] = queue.Queue()
    if force_inline:
        a['batches_path'] = None
        a['batches_fingerprint'] = None

    captured: list = []
    orig = sr.Task.from_batch

    def _spy(batch, warehouse, manager=None):
        captured.append((batch.num_skus, tuple(sorted(batch.items.items()))))
        return []                                  # empty tasks -> loop skips the heavy sim, fast

    sr.Task.from_batch = staticmethod(_spy)
    try:
        sr._run_strategy_worker(a)
    finally:
        sr.Task.from_batch = orig
    return captured, a


def test_e2e_worker_consumes_identical_batches(tmp_path):
    label, inv_db, aff_db = _pair_or_skip()
    log = logging.getLogger('e2e'); log.setLevel(logging.ERROR)
    rs.CONFIG['global']['n_batches'] = _N_E2E_BATCHES

    build_pair = str(tmp_path / 'build' / label)
    os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=250, max_bins=20000, min_bins=5000,
        keyframe_interval=2, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))
    cfg = rs.REGRESSION_CONFIGS[0]

    pre_seq, pre_args = _capture_batch_sequence(
        shared, str(tmp_path / 'pre' / label), cfg, log, force_inline=False)
    inl_seq, _ = _capture_batch_sequence(
        shared, str(tmp_path / 'inl' / label), cfg, log, force_inline=True)

    # Non-vacuous: the precompute path was actually wired and a file produced.
    assert pre_args.get('batches_path') and os.path.exists(pre_args['batches_path']), \
        'precompute file not wired into worker args'
    assert glob.glob(str(tmp_path / 'pre' / label / '_batches_*.pkl')), 'no precomputed batch file'

    # The worker actually LOADED and used the precomputed file (not a silent inline fallback):
    # the captured sequence must equal the on-disk precomputed batches.
    on_disk = BP.load_batches(pre_args['batches_path'], pre_args['batches_fingerprint'])
    assert on_disk is not None and len(on_disk) >= _N_E2E_BATCHES
    on_disk_seq = [(b.num_skus, tuple(sorted(b.items.items()))) for b in on_disk[:len(pre_seq)]]
    assert pre_seq == on_disk_seq, 'worker did not feed the precomputed batches into the loop'

    # The core equivalence: precompute-wired vs forced-inline feed identical batches.
    assert len(pre_seq) == len(inl_seq) == _N_E2E_BATCHES
    assert pre_seq == inl_seq, 'precompute vs inline batch sequences differ'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v', '-s']))
