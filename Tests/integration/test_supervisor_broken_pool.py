"""test_supervisor_broken_pool.py -- a pool whose every worker dies before it works must
RETRY, say WHY, and RETURN -- not hold the machine.

Seen 2026-09-18 on the phase-2 launch (`.scratch/phase-2-campaign/issues/01`): twelve spawned
workers died at import within three seconds, `_run_pool` logged "worker pool BROKEN" and
`break`-ed -- inside `with ProcessPoolExecutor(...)`, whose exit is `shutdown(wait=True)` --
and the driver sat at 0.00 s CPU for 37 minutes.  The manager thread was joining the call
queue's feeder thread, and the feeder was blocked in `send_bytes` on a pipe no live worker
would read (CPython gh-107219; this interpreter, 3.11.4, predates the 3.11.5 fix).
`test_crash_recovery.py` fakes `_run_pool` at every call site, so nothing before this drove a
REAL pool with a worker that cannot import -- and a fixture with SMALL arguments does not see
it either: measured, 2 workers x 2 units hang at a 16 KiB argument and return at 0.

The reproduction runs in a SUBPROCESS (`_broken_pool_driver.py`) under two watchdogs: the
driver's own thread turns a hang into exit 3, and this test kills the process tree if even
that fails to fire.  A hang is therefore a failed assertion, never a hung suite.

Run:  python -m pytest Tests/integration/test_supervisor_broken_pool.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
_DRIVER = os.path.join(_HERE, '_broken_pool_driver.py')

_INNER_BUDGET_S = 120.0       # the driver's own watchdog (three spawn pools = a few seconds)
_OUTER_BUDGET_S = 180.0       # this test's last resort: kill the tree


def _kill_tree(pid: int) -> None:
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def _drive(tmp_path, *args: str) -> tuple[dict, str]:
    proc = subprocess.Popen(
        [sys.executable, _DRIVER, str(tmp_path / 'store'), '--budget', str(_INNER_BUDGET_S),
         *args],
        cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='replace')
    try:
        out, _ = proc.communicate(timeout=_OUTER_BUDGET_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        out, _ = proc.communicate()
        pytest.fail(f'the driver hung past {_OUTER_BUDGET_S:.0f} s and its own watchdog never '
                    f'fired; killed the tree.\n{out}')
    assert proc.returncode != 3, f'the driver HUNG (its watchdog fired):\n{out}'
    assert proc.returncode == 0, f'driver exit {proc.returncode}:\n{out}'
    tail = [ln for ln in out.splitlines() if ln.startswith('{')]
    assert tail, f'no result line from the driver:\n{out}'
    return json.loads(tail[-1]), out


# The two death points x the shape that hangs the unfixed supervisor.  `main` with 2 workers
# and a 16 KiB argument is the smallest measured hang; `target` at the launch's own width.
@pytest.mark.parametrize('die, workers, units, payload_kb', [
    ('main', 2, 2, 16),
    ('target', 12, 24, 64),
])
def test_a_pool_whose_workers_die_retries_explains_and_returns(tmp_path, die, workers, units,
                                                              payload_kb):
    res, out = _drive(tmp_path, '--die', die, '--workers', str(workers), '--units', str(units),
                      '--payload-kb', str(payload_kb))
    # non-vacuity: the pool really broke, every attempt
    assert res['broken_lines'] == 3, f'expected a BROKEN line per attempt (1 + 2 retries): {res}\n{out}'
    # the retry path RAN -- the line the 2026-09-18 log never showed
    assert res['retries'] == 2, f'rebuild + resubmit did not run to max_retries: {res}\n{out}'
    # the WHY, once, from the import probe: the fixture's own RuntimeError text, in the log
    assert out.count('import probe: the worker FAILS to import') == 1, out
    assert 'refuses to import in a spawned child' in out
    # and the outcome is the honest one: every unit unfinished, handed back for the exit path
    assert res['unfinished'] == units, res
    assert 'UNRECOVERED' in out


def test_small_arguments_never_hung_and_still_do_not(tmp_path):
    """The control: the shape every earlier fixture had.  It returned before the fix and must
    still return after it, or the workaround broke the case that worked."""
    res, out = _drive(tmp_path, '--die', 'main', '--workers', '2', '--units', '2')
    assert res['payload_kb'] == 0 and res['retries'] == 2 and res['unfinished'] == 2, res
