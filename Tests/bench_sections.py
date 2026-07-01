"""bench_sections.py - Amdahl baseline: where wall-time actually goes per batch, parsed from
the newest comparison run.log (real full-scale data on THIS machine).  Each GPU candidate's
whole-run impact is bounded by its section's share, so the runner weights speedups by these.

The strategy_runner checkpoint line carries per-section wall:
    | reord=..s build=..s pre=..s sim=..s extr=..s inv=..s     (+ db=..s wall=..s)
We average those across all checkpoints (and split early vs late, since reord grows as queues
fill).  Run: python Tests/bench_sections.py
"""
from __future__ import annotations

import glob
import os
import re
import statistics as st

# build now logs an optional sub-split `(smpl=..s task=..s)` between build= and pre=; the
# group is optional so older logs (without it) still parse.
_SEC_RE = re.compile(r'reord=(?P<reord>[\d.]+)s build=(?P<build>[\d.]+)s'
                     r'(?:\s*\(smpl=(?P<smpl>[\d.]+)s task=(?P<task>[\d.]+)s\))?'
                     r' pre=(?P<pre>[\d.]+)s sim=(?P<sim>[\d.]+)s '
                     r'extr=(?P<extr>[\d.]+)s inv=(?P<inv>[\d.]+)s')
_DB_RE = re.compile(r'\bdb=([\d.]+)s')
_BATCH_RE = re.compile(r'Batch\s+(\d+)/')
_ROOTS = [r'F:\Data\Inventory_Optimizer_Data\Optimization_Outputs',
          r'H:\Data\Inventory_Optimizer_Data\Optimization_Outputs', os.getcwd()]
_SECTIONS = ['build', 'reord', 'pre', 'sim', 'extr', 'inv', 'db']


def _latest_log():
    logs = []
    for r in _ROOTS:
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
            b = _BATCH_RE.search(line)
            rows.append((int(b[1]) if b else -1, d))
    return rows


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
        print('  no comparison run.log found under F:/H:/cwd; skipping section baseline')
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
