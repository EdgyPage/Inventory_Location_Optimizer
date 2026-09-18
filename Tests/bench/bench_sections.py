"""bench_sections.py - Amdahl baseline: where wall-time actually goes per batch, parsed from
the newest comparison run.log (real full-scale data on THIS machine).  Each GPU candidate's
whole-run impact is bounded by its section's share, so the runner weights speedups by these.

The strategy_runner checkpoint line carries per-section wall:
    | reord=..s build=..s pre=..s sim=..s extr=..s inv=..s     (+ db=..s wall=..s)
We average those across all checkpoints (and split early vs late, since reord grows as queues
fill).  Run: python Tests/bench/bench_sections.py
"""
from __future__ import annotations

import glob
import os
import re
import statistics as st

# build now logs an optional sub-split `(smpl=..s task=..s)` between build= and pre=; the
# group is optional so older logs (without it) still parse.  The last section logged as
# `inv=` until the conservation-ledger relabel made it `cons=`; both spellings parse so
# neither vintage of run.log silently drops to zero rows (Tests/calltree's anchors test
# pins this against strategy_runner's emitted line).
_SEC_RE = re.compile(r'reord=(?P<reord>[\d.]+)s build=(?P<build>[\d.]+)s'
                     r'(?:\s*\(smpl=(?P<smpl>[\d.]+)s task=(?P<task>[\d.]+)s\))?'
                     r' pre=(?P<pre>[\d.]+)s sim=(?P<sim>[\d.]+)s '
                     r'extr=(?P<extr>[\d.]+)s (?:inv|cons)=(?P<inv>[\d.]+)s')
_DB_RE = re.compile(r'\bdb=([\d.]+)s')
# Overlay tokens appended after the partition set (2026-08-19): kf ⊂ pre (the keyframe
# sqlite write), gc overlaps every section (GC pause).  Independent optional regexes on
# the _DB_RE pattern — older logs without them still parse to zero rows of overlay, and
# _SEC_RE stays untouched (it is an unanchored search; appended tokens are invisible).
_KF_RE = re.compile(r'\bkf=([\d.]+)s')
_GC_RE = re.compile(r'\bgc=([\d.]+)s')
# The save DECOMPOSITION (2026-09-17), appended after the overlay for the same reason it was:
# independent optional regexes, invisible to the unanchored _SEC_RE, zero on older logs.
# sql + pkl + drn == db.  They are a SUB-partition of `db` and belong to no section set --
# adding them beside `db` would double-count the whole of it, which is the mistake `build`
# is dropped from _MACRO_KEYMAP to avoid.  `save_s` was one stopwatch over a Python drain, a
# SQLite flush and a pickle write with a retry loop, and 48.6% of the deep tier sat under it.
_SQL_RE = re.compile(r'\bsql=([\d.]+)s')
_PKL_RE = re.compile(r'\bpkl=([\d.]+)s')
_DRN_RE = re.compile(r'\bdrn=([\d.]+)s')
# The DENOMINATOR.  Eight arms of one toy run wrote the same rows (1.00x spread) while their
# save_s varied 2.8x -- cost per row, not volume -- and nothing recorded the row count.
_ROWS_RE  = re.compile(r'\brows=(\d+)')
_DBMB_RE  = re.compile(r'\bdbmb=([\d.]+)')
_WALMB_RE = re.compile(r'\bwalmb=([\d.]+)')
_BATCH_RE = re.compile(r'Batch\s+(\d+)/')
# The two PER-ARM save lines (2026-09-18).  They are not checkpoint lines, so `parse` below
# never sees them -- it only makes a row when `_SEC_RE` matches.  They are read by `save_tail`.
#
# WHY THEY MATTER: the runner charges both to the `save` section, so `runtime_metrics.save_s`
# contains them.  The ladder's `t_save` is a mean over CHECKPOINT lines and does not.  Without
# these, the two instruments named `save` measure different work and neither says so.
_IDX_BUILD_RE = re.compile(r'\[save\] index build ([\d.]+)s')
_RUN_END_RE = re.compile(r'\[save\] run-end close ([\d.]+)s')
_TS_RE = re.compile(r'^(\d\d):(\d\d):(\d\d)\s')
_SECTIONS = ['build', 'reord', 'pre', 'sim', 'extr', 'inv', 'db']


def _roots():
    """Where to look for a comparison run.log, newest wins.

    Reads COMPARISON_OUTPUT_DIR from the environment (sim_config loads .env into it), which is
    the only place the output drive is recorded on a given machine.  This used to be a pair of
    hardcoded drive letters; those are machine-local paths and must not live in a tracked file
    (CLAUDE.md section 5, enforced by context/guards/path_guard.py).
    """
    roots = []
    try:
        from Optimization.config.sim_config import CONFIG    # noqa: F401 - loads .env as a side effect
    except Exception:
        pass
    for key in ('COMPARISON_OUTPUT_DIR', 'PROFILE_INPUT_DIR'):
        val = (os.environ.get(key) or '').strip()
        if val:
            roots.append(val)
    roots.append(os.getcwd())
    return roots


def _latest_log():
    logs = []
    for r in _roots():
        logs += glob.glob(os.path.join(r, 'comparison_*', 'run.log'))
    return max(logs, key=os.path.getmtime) if logs else None


def parse(log_path):
    """Return per-section mean seconds (all checkpoints) + early/late splits."""
    rows = []   # (batch, dict)
    with open(log_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _SEC_RE.search(line)
            if not m:
                continue
            d = {'reord': float(m['reord']), 'build': float(m['build']),
                 'pre': float(m['pre']), 'sim': float(m['sim']),
                 'extr': float(m['extr']), 'inv': float(m['inv']),
                 'smpl': float(m['smpl']) if m['smpl'] else 0.0,
                 'task': float(m['task']) if m['task'] else 0.0}
            db = _DB_RE.search(line)
            d['db'] = float(db[1]) if db else 0.0
            kf = _KF_RE.search(line)
            d['kf'] = float(kf[1]) if kf else 0.0     # overlay: subset of 'pre'
            gc = _GC_RE.search(line)
            d['gc'] = float(gc[1]) if gc else 0.0     # overlay: overlaps everything
            for _key, _re in (('sql', _SQL_RE), ('pkl', _PKL_RE), ('drn', _DRN_RE),
                              ('dbmb', _DBMB_RE), ('walmb', _WALMB_RE)):
                _m = _re.search(line)
                d[_key] = float(_m[1]) if _m else 0.0   # sub-partition of 'db'; 0.0 pre-2026-09-17
            _r = _ROWS_RE.search(line)
            d['rows'] = float(_r[1]) if _r else 0.0
            b = _BATCH_RE.search(line)
            rows.append((int(b[1]) if b else -1, d))
    return rows


def save_tail(log_path):
    """The per-arm save costs a checkpoint line cannot carry, plus the wall split.

    Returns `{index_build_s, run_end_close_s, arms, span_s, sim_s, analysis_s}` -- means over
    arms for the two costs, and seconds for the split.

    THE SPLIT'S BOUNDARY is the last line that is either a checkpoint or a per-arm save line:
    everything after it is the analysis half, which runs in the same subprocess. That is what
    makes the analysis half measurable without a second run -- it was already in the log.
    """
    import statistics as _st
    builds, closes, stamps, last_sim = [], [], [], None
    with open(log_path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = _TS_RE.match(line)
            if m:
                t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                stamps.append(t)
                if _SEC_RE.search(line) or '[save] ' in line:
                    last_sim = t
            b = _IDX_BUILD_RE.search(line)
            if b:
                builds.append(float(b[1]))
            c = _RUN_END_RE.search(line)
            if c:
                closes.append(float(c[1]))
    if not stamps:
        return {}
    span = stamps[-1] - stamps[0]
    sim = (last_sim - stamps[0]) if last_sim is not None else span
    return {'index_build_s': _st.fmean(builds) if builds else 0.0,
            'run_end_close_s': _st.fmean(closes) if closes else 0.0,
            'arms': len(closes),
            'span_s': float(span), 'sim_s': float(sim),
            'analysis_s': float(span - sim)}


def _means(rows):
    return {s: st.fmean(d[s] for _, d in rows) for s in _SECTIONS} if rows else {}


def section_shares():
    """Normalized GPU-addressable share per section (for Amdahl weighting).  {} if no log."""
    log = _latest_log()
    if not log:
        return {}
    means = _means(parse(log))
    tot = sum(means.values()) or 1.0
    return {s: means[s] / tot for s in _SECTIONS}


def run():
    log = _latest_log()
    if not log:
        print('  no comparison run.log found under COMPARISON_OUTPUT_DIR/PROFILE_INPUT_DIR/cwd; '
              'skipping section baseline')
        return {}
    rows = parse(log)
    if not rows:
        print(f'  no per-section timer lines in {log}')
        return {}
    allm = _means(rows)
    early = _means([r for r in rows if 0 <= r[0] <= 30])
    late = _means([r for r in rows if r[0] >= 70])
    tot = sum(allm.values()) or 1.0
    print(f'  source: {log}   ({len(rows)} checkpoints)')
    print(f'  {"section":8} {"mean s":>8} {"share":>7}   {"early s":>8} {"late s":>8}')
    for s in _SECTIONS:
        print(f'  {s:8} {allm[s]:8.1f} {allm[s] / tot:6.1%}   '
              f'{early.get(s, 0):8.1f} {late.get(s, 0):8.1f}')
    print(f'  {"TOTAL":8} {tot:8.1f}')
    # build sub-split (sampling vs task construction) — grounds the precompute/dedup ceiling.
    smpl = st.fmean(d['smpl'] for _, d in rows)
    task = st.fmean(d['task'] for _, d in rows)
    if smpl or task:
        b = allm.get('build', 0.0) or 1.0
        print(f'  build split: smpl={smpl:.1f}s ({smpl / tot:.1%} wall, {smpl / b:.0%} of build)'
              f'  task={task:.1f}s ({task / tot:.1%} wall, {task / b:.0%} of build)')
    return section_shares()


if __name__ == '__main__':
    print('Section (Amdahl) baseline from real run log:')
    run()
