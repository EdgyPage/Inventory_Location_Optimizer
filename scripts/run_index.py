"""run_index.py — which runs happened, which phase each belonged to, and did they finish.

Before the launch record there was no cross-run index anywhere: the directory listing WAS
the index, and answering "which of these forty roots were phase 2, and did they complete?"
meant opening forty `run_spec.json` files one at a time — each of which is written once at
launch and never rewritten, so none of them could answer the second half of the question at
all.

Two sources, and the difference matters:

  * `run_index.jsonl`, one line per launch and per completion, appended OUTSIDE every run
    root. Cheap, and it survives a root being archived or deleted. Best-effort on the write
    side, so it can be incomplete.
  * `<root>/run_history.json`, the per-run ledger. The AUTHORITY: one record per launch
    including every resume, with the construction and the outcome. Read with `--deep`, which
    opens every root it lists.

A record whose `status` is null means that launch never reported back — killed, crashed, or
the machine went down. That is a different fact from a launch that failed, and the two are
never merged here.

    python scripts/run_index.py                        # every run under the output dir
    python scripts/run_index.py --phase 2              # just the phase-2 runs
    python scripts/run_index.py --status incomplete    # what needs resuming
    python scripts/run_index.py --deep --run comparison_whatif_20260919_222506
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Optimization.runschema.sim_manifest import (                        # noqa: E402
    read_run_history, read_run_index)


def _out_dir() -> str:
    """The configured run-output directory, through the ONE key that names it.

    Imported for its side effect (`.env` into `os.environ`) exactly as the archiver does,
    and never hardwired: the drive this lands on has moved more than once.
    """
    from Optimization.config import sim_config as sc                     # noqa: F401
    return os.environ.get('COMPARISON_OUTPUT_DIR') or os.getcwd()


def _roots(out_dir: str) -> list:
    """Run roots on disk, newest first — the fallback when the ledger has no line for one.

    A root that predates the ledger (every root before 2026-09-19) is still listed, with
    whatever its own history file says, which for those is nothing. Listing it as UNKNOWN is
    the honest answer; omitting it would make the tool quietly describe a smaller archive
    than the one in front of you.
    """
    if not os.path.isdir(out_dir):
        return []
    hits = [d for d in os.listdir(out_dir)
            if d.startswith('comparison') and os.path.isdir(os.path.join(out_dir, d))]
    return sorted(hits, reverse=True)


def _fold(lines) -> dict:
    """Ledger lines -> `{run: {...}}`, last write wins per field."""
    by_run: dict = {}
    for ln in lines:
        run = ln.get('run')
        if not run:
            continue
        rec = by_run.setdefault(run, {'run': run, 'root': ln.get('root'),
                                      'launches': 0, 'spec': None, 'phase': None,
                                      'status': None, 'last': None})
        rec['last'] = ln.get('at') or rec['last']
        if ln.get('event') == 'launched':
            rec['launches'] += 1
            rec['spec'] = ln.get('spec') or rec['spec']
            rec['phase'] = ln.get('phase') if ln.get('phase') is not None else rec['phase']
            # A new launch REOPENS the run: the previous attempt's outcome no longer
            # describes what is on disk, and a stale `complete` here is how a half-resumed
            # run gets read as finished.
            rec['status'] = None
        elif ln.get('event') == 'finished':
            rec['status'] = ln.get('status') or rec['status']
    return by_run


def rows(out_dir: str, *, deep: bool = False) -> list:
    """One row per run root, newest first.  `deep` opens each root's own history file."""
    folded = _fold(read_run_index(out_dir))
    out = []
    for run in _roots(out_dir):
        rec = folded.get(run) or {'run': run, 'root': os.path.join(out_dir, run),
                                  'launches': 0, 'spec': None, 'phase': None,
                                  'status': None, 'last': None}
        if deep:
            hist = read_run_history(rec['root'])
            if hist:
                last = hist[-1]
                rec['launches'] = len(hist)
                rec['spec'] = last.get('spec') or rec['spec']
                rec['phase'] = last.get('phase') if last.get('phase') is not None else rec['phase']
                rec['status'] = last.get('status')
                rec['last'] = last.get('ended') or last.get('started') or rec['last']
                rec['cells'] = last.get('cells')
                rec['unfinished'] = last.get('unfinished')
                rec['commit'] = last.get('repo_commit')
        out.append(rec)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out-dir', default=None,
                    help='the run-output directory (default: the configured one)')
    ap.add_argument('--phase', type=int, default=None, help='only this funnel phase')
    ap.add_argument('--spec', default=None, help='only runs of this spec')
    ap.add_argument('--status', default=None,
                    help="only this outcome: complete / incomplete / unknown (a launch that "
                         "never reported back)")
    ap.add_argument('--run', default=None, help='only this run root (substring match)')
    ap.add_argument('--deep', action='store_true',
                    help="open every root's own run_history.json — the authority, and slower")
    ap.add_argument('-n', type=int, default=40, metavar='N', help='most recent N (default 40)')
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:                                  # noqa: BLE001
        pass

    out_dir = a.out_dir or _out_dir()
    got = rows(out_dir, deep=a.deep)
    if a.phase is not None:
        got = [r for r in got if r.get('phase') == a.phase]
    if a.spec:
        got = [r for r in got if (r.get('spec') or '') == a.spec]
    if a.run:
        got = [r for r in got if a.run in r['run']]
    if a.status:
        want = None if a.status == 'unknown' else a.status
        got = [r for r in got if r.get('status') == want]
    got = got[:a.n]

    print(f'{out_dir}   {len(got)} run(s)'
          + ('' if a.deep else '   (ledger only; --deep reads each run\'s own history)'))
    print(f'{"run":40s} {"spec":22s} {"ph":>3s} {"try":>4s} {"status":>11s}  last')
    for r in got:
        status = r.get('status') or 'unknown'
        print(f'{r["run"]:40s} {str(r.get("spec") or "-"):22s} '
              f'{str(r.get("phase") if r.get("phase") is not None else "-"):>3s} '
              f'{r.get("launches") or 0:>4d} {status:>11s}  {r.get("last") or "-"}')
        if a.deep and r.get('unfinished'):
            print(f'    unfinished: {r["unfinished"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
