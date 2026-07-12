"""coverage_e2e.py -- single-process full-pipeline exerciser (refactor safety net).

Runs the whole sim+analysis pipeline IN-PROCESS on a toy warehouse across every
strategy (so every assignment fn is hit): build -> stock -> reorder -> pick ->
stats -> save, then analysis (per-config compare/ + cross-profile aggregate).
The real CLI fans strategies through ProcessPoolExecutor, whose subprocesses
coverage.py would miss -- this driver bypasses the pools so plain

    python -m coverage run Tests/bench/coverage_e2e.py && python -m coverage report

captures the substantive code in one process.  Used to confirm nothing breaks
(and to watch dead code disappear) during the structural refactor.
"""
import os, sys, logging, queue, tempfile, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Tests/<sub>/ -> repo root
# repo root on sys.path so package imports resolve when run as a script.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from Optimization import run_simulation as rs
from Optimization import strategy_runner as sr
from Optimization import run_analysis as ra

log = logging.getLogger('cov')


def has_profile_data() -> bool:
    """True when a generated (inventory.db, affinity.db) pair exists to drive the run."""
    try:
        return bool(rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR))
    except Exception:
        return False


def main(base=None):
    """Run the whole sim+analysis pipeline IN-PROCESS on a toy warehouse across every
    strategy, then analysis.  Returns the base run directory.  Importing this module has no
    side effects; the pipeline only runs when main() is called (as a CLI, a refactor safety
    net, or the driver for Tests/test_architecture_coverage.py)."""
    rs.CONFIG['global']['n_batches'] = 4   # toy horizon -- still exercises reorder/reslot/keyframe/steady-state
    logging.basicConfig(level=logging.ERROR, format='%(message)s')

    label, inv_db, aff_db = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)[0]
    if base is None:
        base = tempfile.mkdtemp(prefix='cov_')
    pair_dir = os.path.join(base, label)
    os.makedirs(pair_dir, exist_ok=True)

    shared = rs.build_shared_assets(
        inv_db, aff_db, log,
        max_skus=300, max_bins=20000, min_bins=5000,
        keyframe_interval=2,
        warehouse_db_path=os.path.join(pair_dir, 'warehouse.db'),
    )

    q = queue.Queue()         # worker QueueHandler sink (undrained is fine for coverage)
    _mixed, _channel_runs = rs._channel_runs_for(shared['inventory'])
    for ch, cfg in _channel_runs:
        strategy_args, skeletons = rs._prepare_channel_run(ch, cfg, _mixed, shared, pair_dir, log)
        print(f'config {cfg.get("name")} [{ch.name}]: {len(strategy_args)} strategies')
        for a in strategy_args:
            a['log_queue'] = q
            try:
                sr._run_strategy_worker(a)
            except Exception:
                traceback.print_exc()
        for sk in skeletons:
            rs._finalize_config_run(sk)

    # analysis end-to-end, in-process (workers=1) -- exercises the Performance_Evaluations
    # registry (per-config + cross-profile aggregate).  Two presets so both stats variants
    # (stats.suite and stats.by_initial) are covered.
    ra.run_analysis(base, log, workers=1, preset='E2E_PARITY')
    ra.run_analysis(base, log, workers=1, preset='BY_INITIAL')
    print('DONE', base)
    return base


if __name__ == '__main__':
    main()
