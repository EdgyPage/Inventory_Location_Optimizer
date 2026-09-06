"""run_reference.py — take the REFERENCE RUN and write the calibration record.

The one run whose measured labour per unit prices the travel-bearing departments for a
catalogue (glossary: *Reference run*, *Calibration record*).  This CLI drives the procedure
"Choose the calibration procedure" decided (.scratch/department-calibration): it launches
passes of

    python -m Optimization.run_simulation --spec calibration_reference --n-batches 40
                                          --calibration-record <candidate> --no-analyze

as SUBPROCESSES (the spawn pool must be launched from a real module, never a heredoc:
memory `heredoc-python-breaks-the-spawn-pool`), measures days 20-39 of each pass under the
equilibrium check as the window precondition, and iterates to the fixed point
(`Optimization/simconfig/reference.py`).  Nothing here edits the committed record: the
candidate lands in the output directory, and `--install` copies it over
`Optimization/simconfig/calibration_record.json` only when its constants are MEASURED
(a re-seed from a discarded window is refused).  Committing the installed record to git is
the human's act.

Two modes:

    python -m Optimization.run_reference                         # launch passes (the default)
        [--n-batches 40] [--window 20 39] [--max-passes 2] [--workers N]
        [--seed-record PATH] [--out DIR] [--install] [-- <run_simulation flags...>]
    python -m Optimization.run_reference --measure RUN_ROOT      # judge + measure a finished
        [--window 20 39] [--out DIR]                            # run, no launch

Flags after a bare `--` are passed through to run_simulation untouched (`--profiles-dir`,
`--seed-batches`, `--store-pickers`, ...).  Run output stays out of git (CLAUDE.md §2); only
the record is committed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_reference.py); `-m` form needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Optimization.simconfig import calibration as _cal            # noqa: E402
from Optimization.simconfig import reference as _ref              # noqa: E402

#: The run root as run_simulation announces it, on either of its two log lines.
_ROOT_LINE = re.compile(r'(?:All simulations complete\.\s+Root:|Output directory\s*:)\s*(.+?)\s*$')


def _log() -> logging.Logger:
    log = logging.getLogger('reference')
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


def _default_out() -> str:
    from Optimization.config.sim_config import _OUTPUT_DIR
    return os.path.join(_OUTPUT_DIR, f'calibration_reference_{datetime.now():%Y%m%d_%H%M%S}')


def _launcher(args, passthrough: list, out_dir: str, log: logging.Logger):
    """`launch(record_path, pass_no) -> run_root`: one run_simulation subprocess per pass,
    its output teed to `<out_dir>/pass<n>.log`, the run root parsed off the log line."""
    def launch(record_path: str, pass_no: int) -> str:
        cmd = [sys.executable, '-m', 'Optimization.run_simulation',
               '--spec', 'calibration_reference', '--n-batches', str(args.n_batches),
               '--calibration-record', record_path, '--workers', str(args.workers),
               '--no-analyze', *passthrough]
        log_path = os.path.join(out_dir, f'pass{pass_no}.log')
        log.info(f'[reference] pass {pass_no}: {" ".join(cmd)}')
        root = None
        with open(log_path, 'w', encoding='utf-8') as fh:
            proc = subprocess.Popen(cmd, cwd=_REPO_ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding='utf-8', errors='replace')
            for line in proc.stdout:
                fh.write(line)
                sys.stdout.write(line)
                m = _ROOT_LINE.search(line)
                if m:
                    root = m.group(1).strip()
            rc = proc.wait()
        if rc != 0:
            raise _ref.ReferenceRunError(f'pass {pass_no}: run_simulation exited {rc}; '
                                         f'see {log_path}')
        if not root or not os.path.isdir(root):
            raise _ref.ReferenceRunError(f'pass {pass_no}: could not find the run root in '
                                         f'{log_path} (no "Root:" line)')
        # Pool run swallows dead arms (memory): a worker can die and the run still exits 0.
        with open(log_path, encoding='utf-8') as fh:
            text = fh.read()
        for marker in ('Traceback', 'produced no data'):
            if marker in text:
                raise _ref.ReferenceRunError(f'pass {pass_no}: {marker!r} in {log_path}; '
                                             f'the run is not trustworthy')
        return root
    return launch


def _measure_only(args, log: logging.Logger) -> int:
    out_dir = args.out or _default_out()
    os.makedirs(out_dir, exist_ok=True)
    lo, hi = args.window
    rc = 0
    for root in args.measure:
        try:
            result = _ref.measure_pass(root, day_lo=lo, day_hi=hi, recv_tol=args.recv_tol,
                                       log=log)
        except _ref.ReferenceRunError as exc:
            log.error(f'[reference] {root}: {exc}')
            rc = 2
            continue
        log.info(f'[reference] {root}: window {"PASSED" if result["passed"] else "FAILED"}')
        for r in result['reasons']:
            log.info(f'    {r}')
        for ch, m in result['s_pick'].items():
            log.info(f'    s_pick[{ch}] = {m["value"]} s/unit (analytic {m["analytic"]:.4f}, '
                     f'travel share {m["travel_share"]})  k_max={result["k_max"].get(ch)}')
        log.info(f'    s_put = {result["s_put"]["value"]} s/unit '
                 f'(analytic {result["s_put"]["analytic"]:.4f}, per leaf '
                 f'{result["s_put"]["per_leaf"]})')
        rcv = result['recv_check']
        log.info(f'    receiving self-check: charged {rcv["measured_s"]:,.1f} s vs exact '
                 f'{rcv["expected_s"]:,.1f} s over {rcv["packs"]} packs -> '
                 f'{"ok" if rcv["passed"] else "FAIL"} (script s_recv {rcv["script_s_recv"]}, '
                 f'measured {rcv["measured_s_per_pack"]} s/pack)')
        seed = _cal.load_record(args.seed_record)
        rec = _ref.candidate_record(seed, result, pass_no=0)
        kind = 'measured' if result['passed'] else 'reseed'
        path = _ref.write_record(rec, os.path.join(
            out_dir, f'calibration_record.{kind}.{os.path.basename(root)}.json'))
        log.info(f'    candidate written ({kind}): {path}')
    return rc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Take the reference run and write the calibration record.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--measure', nargs='+', metavar='RUN_ROOT', default=None,
                        help='Judge and measure these FINISHED run(s) instead of launching.')
    parser.add_argument('--n-batches', type=int, default=40,
                        help='Days per pass (one batch is one day under the era).')
    parser.add_argument('--window', type=int, nargs=2, default=(20, 39),
                        metavar=('DAY_LO', 'DAY_HI'), help='Measured days, inclusive.')
    parser.add_argument('--max-passes', type=int, default=_ref.MAX_PASSES)
    parser.add_argument('--demand-tol', type=float, default=_ref.DEMAND_TOL,
                        help='Relative move in derived daily demand below which the fixed '
                             'point has closed.')
    parser.add_argument('--recv-tol', type=float, default=_ref.RECV_TOL,
                        help='Relative tolerance of the receiving self-check.')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--seed-record', default=_cal.RECORD_PATH,
                        help='The record pass 0 runs under.')
    parser.add_argument('--out', default=None,
                        help='Where candidate records and pass logs go (default: a '
                             'calibration_reference_<ts> dir under COMPARISON_OUTPUT_DIR).')
    parser.add_argument('--install', action='store_true',
                        help='Copy the final record over the committed one when it is '
                             'MEASURED (refused for a re-seed).')
    args, passthrough = parser.parse_known_args(argv)
    if passthrough and passthrough[0] == '--':
        passthrough = passthrough[1:]
    log = _log()
    lo, hi = args.window
    if hi < lo or lo < 0:
        parser.error(f'--window {lo} {hi} is not a day range')
    if args.measure:
        return _measure_only(args, log)
    if hi >= args.n_batches:
        parser.error(f'--window ends at day {hi} but the pass has only {args.n_batches} days')

    out_dir = args.out or _default_out()
    os.makedirs(out_dir, exist_ok=True)
    seed = _cal.load_record(args.seed_record)
    log.info(f'[reference] seed record: {seed.get("_path")} (pass {seed.get("pass")}, '
             f'{"measured" if _cal.is_measured(seed) else "seed"})')
    log.info(f'[reference] output: {out_dir}')
    outcome = _ref.fixed_point(
        launch=_launcher(args, passthrough, out_dir, log),
        measure=lambda root: _ref.measure_pass(root, day_lo=lo, day_hi=hi,
                                               recv_tol=args.recv_tol, log=log),
        seed_record=seed, out_dir=out_dir, max_passes=args.max_passes,
        tol=args.demand_tol, log=log)
    rec = outcome['record']
    log.info(f'[reference] done: {len(outcome["passes"])} pass(es), '
             f'{"MEASURED" if outcome["measured"] else "RE-SEED ONLY (no window passed)"}, '
             f'{"converged" if outcome["converged"] else "cut off"} -> {outcome["record_path"]}')
    for ch, e in rec['constants']['s_pick'].items():
        log.info(f'    s_pick[{ch}] = {e.get("value")} ({e["provenance"]}, travel share '
                 f'{e.get("travel_share")})')
    log.info(f'    s_put = {rec["constants"]["s_put"].get("value")} '
             f'({rec["constants"]["s_put"]["provenance"]})  k_max = {rec["constants"].get("k_max")}')
    if args.install:
        if not outcome['measured']:
            log.error('[reference] --install refused: the final record is a re-seed, not a '
                      'measurement')
            return 2
        shutil.copyfile(outcome['record_path'], _cal.RECORD_PATH)
        _cal.load_record(_cal.RECORD_PATH)                 # re-validate what was installed
        log.info(f'[reference] installed {_cal.RECORD_PATH}; commit it to make it the record')
    return 0 if outcome['measured'] else 1


if __name__ == '__main__':
    sys.exit(main())
