"""coverage_e2e.py -- single-process full-pipeline exerciser (refactor safety net).

Runs the whole sim+analysis pipeline IN-PROCESS on a toy warehouse across every
strategy (so every assignment fn is hit): build -> stock -> reorder -> pick ->
stats -> save, then analysis (per-config compare/ + cross-profile aggregate).
The real CLI fans strategies through ProcessPoolExecutor, whose subprocesses
coverage.py would miss -- this driver bypasses the pools so plain

    python -m coverage run Tests/coverage_e2e.py && python -m coverage report

captures the substantive code in one process.  Used to confirm nothing breaks
(and to watch dead code disappear) during the structural refactor.
"""
import os, sys, logging, queue, tempfile, traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:0] = [os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization')]

import run_simulation as rs
import strategy_runner as sr
import run_analysis as ra

rs.N_BATCHES = 4          # toy horizon -- still exercises reorder/reslot/keyframe/steady-state

logging.basicConfig(level=logging.ERROR, format='%(message)s')
log = logging.getLogger('cov')

label, inv_db, aff_db = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)[0]
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
for cfg in rs.REGRESSION_CONFIGS:
    strategy_args, skeleton = rs._prepare_config_run(cfg, shared, pair_dir, log)
    print(f'config {cfg.get("name")}: {len(strategy_args)} strategies')
    for a in strategy_args:
        a['log_queue'] = q
        try:
            sr._run_strategy_worker(a)
        except Exception:
            traceback.print_exc()
    rs._finalize_config_run(skeleton)

# analysis end-to-end, in-process (workers=1) -- exercises the Performance_Evaluations
# registry (per-config + cross-profile aggregate).  Two presets so both stats variants
# (stats.suite and stats.by_initial) are covered.
ra.run_analysis(base, log, workers=1, preset='E2E_PARITY')
ra.run_analysis(base, log, workers=1, preset='BY_INITIAL')
print('DONE', base)
