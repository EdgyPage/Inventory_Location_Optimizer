"""_broken_pool_driver.py -- drive `scenario._run_cells` over a REAL spawn pool whose every
worker dies before it does any work, under a watchdog that turns a hang into exit status 3.

Run by `test_supervisor_broken_pool.py` as a subprocess, never in the pytest process: a pool
that hangs on its `with` exit keeps a non-daemon manager thread alive, and pytest itself
would then hang at interpreter exit -- the very failure this exists to catch.  As a child
process, a hang is a killed process and a failed assertion.

Two places a child can die, selectable with `--die`, because they are different failures:

  target  the child bootstraps fine and dies unpickling the call item, when the target's
          module (`_worker_dies_at_import`) refuses to import.  `_process_worker` is running.
  main    the child dies INSIDE spawn's bootstrap, re-importing THIS script as `__mp_main__`
          (the guard at the top of this file) -- before `_process_worker` ever starts.  This
          is how the 2026-09-18 launch died: `run_simulation` imports `strategy_runner` at
          module level, so a broken tree kills the child while it is still preparing.

Prints one JSON line at the end: {"retries": n, "unfinished": n, "broken_lines": n, ...}.
Exit 0 when the driver returned; 3 when the watchdog fired.

Usage:  python Tests/integration/_broken_pool_driver.py <base_dir> --die main --workers 12
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# `--die main`: the parent sets this in the child's environment before the pool spawns, and
# the child trips on it while spawn re-imports this script as `__mp_main__`.  Detected by
# `__name__`, not `multiprocessing.parent_process()`: that is still None while `prepare()`
# runs the main module -- it is bound later, in `_bootstrap`, which a child that dies here
# never reaches.  (The first draft used it and the children lived.)
if __name__ == '__mp_main__' and os.environ.get('BROKEN_POOL_DIE_IN_MAIN'):
    raise RuntimeError('_broken_pool_driver: __mp_main__ refuses to import in a spawned child '
                       '(test fixture standing in for a broken working tree)')


def _healthy_worker(sa):
    """A target that would succeed; under `--die main` no child lives to run it."""
    return {'done': True, 'strategy': sa['strategy']}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('base_dir')
    ap.add_argument('--budget', type=float, default=120.0, help='watchdog seconds')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--units', type=int, default=2)
    ap.add_argument('--die', choices=('target', 'main'), default='main')
    ap.add_argument('--retries', type=int, default=2)
    ap.add_argument('--payload-kb', type=int, default=0,
                    help='bytes of ballast on every unit argument (the real payload carries '
                         'the CONFIG snapshot and shared paths; a Windows pipe buffer is 8 KiB)')
    a = ap.parse_args()
    os.makedirs(a.base_dir, exist_ok=True)

    # The watchdog is the FIRST thing up: a hang anywhere after this line is exit 3, not a
    # process somebody has to find and kill.
    def watchdog():
        time.sleep(a.budget)
        print(f'WATCHDOG: the driver did not finish within {a.budget:.0f} s -- HUNG '
              f'(die={a.die} workers={a.workers} units={a.units} payload_kb={a.payload_kb})',
              flush=True)
        # WHERE it hangs, every thread: the executor's manager thread and the call queue's
        # feeder are the two that matter, and a hang report without their frames is a guess.
        import faulthandler
        faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
        sys.stdout.flush()
        os._exit(3)
    threading.Thread(target=watchdog, daemon=True).start()

    from Optimization.simdriver import scenario as sc
    from Optimization.simdriver import supervisor as sup
    from Optimization.simdriver.cells import Cell
    if a.die == 'target':
        import _worker_dies_at_import as dying
        # pickle-by-reference resolves `dying.worker` through ITS module in the child, which
        # is where the import raises; `sup._run_strategy_worker` is only the name submitted.
        sup._run_strategy_worker = dying.worker
        os.environ['BROKEN_POOL_DIE_IN_TARGET'] = '1'      # after OUR import; see the module
    else:
        sup._run_strategy_worker = _healthy_worker

    records: list[str] = []
    log = logging.getLogger('broken_pool_driver')
    log.setLevel(logging.DEBUG)
    log.propagate = False

    class _Capture(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            records.append(msg)
            print(msg, flush=True)
    log.addHandler(_Capture())

    gk = ('prof', 'cfg', 'store')
    keys = [f's{i:02d}' for i in range(a.units)]
    uids = [(*gk, k) for k in keys]
    skeleton = {'run_dir': a.base_dir, 'name': 'cfg', 'inventory': 'prof', 'channel': 'store',
                'strategies': [{'key': k, 'label': k, 'db_path': f'{k}.db', 'run_id': 1}
                               for k in keys],
                'optimal_sigma_fd': 0.0, 'optimal_work': 0.0, 'inv_db': 'i', 'aff_db': 'a'}
    ballast = b'x' * (a.payload_kb * 1024)
    units = [(u, {'strategy': u[3], 'group_keys': [gk], 'ballast': ballast}) for u in uids]
    meta = {gk: {'sim_skeleton': skeleton, 'members': frozenset(uids)}}
    # The real driver hands every worker a Manager Queue proxy for its log records; the
    # ticket names the children's handles on it as part of why the shutdown never returned,
    # and the pool's own Manager (`WorkPool.__enter__`) carries it.  So the tree is "broken"
    # only once that Manager is up: a Manager whose server child dies at bootstrap hangs
    # `Manager()` itself, forever, in `start()` waiting for the server's address (measured
    # 2026-09-18 -- the first draft of this fixture did exactly that, and its watchdog had not
    # started yet).  `assets_for` runs inside the pool context, after the Manager and before
    # the first submit -- the launch's Manager was up before the working tree changed under
    # it, so this is the faithful order.
    def _assets(_cell, _dir):
        if a.die == 'main':
            os.environ['BROKEN_POOL_DIE_IN_MAIN'] = '1'
        return {'prof': {}}

    def _build(pairs, base_dir, shared, log_, log_queue, workers, **kw):
        for _u, sa in units:
            sa['log_queue'] = log_queue
        return list(units), dict(meta)
    sc._build_work_units = _build

    t0 = time.perf_counter()
    unfinished = sc._run_cells(a.base_dir, [('prof', 'i', 'a')],
                               [Cell('', None, {'enabled': False}, 'round_robin')], log,
                               workers=a.workers, assets_for=_assets, max_retries=a.retries,
                               resume_granularity='strategy')
    wall = time.perf_counter() - t0
    out = {'die': a.die, 'workers': a.workers, 'units': a.units, 'payload_kb': a.payload_kb,
           'retries': sum('] retry ' in m for m in records),
           'unfinished': sum(len(v) for v in (unfinished or {}).values()),
           'broken_lines': sum('worker pool BROKEN' in m for m in records),
           'wall_s': round(wall, 1)}
    print(json.dumps(out), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
