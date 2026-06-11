"""diagnose_makespan.py - Phase-1 diagnostic for the assignment-function study.

Read-only.  Over a completed comparison run, decomposes batch makespan into
  total-work / round-robin-imbalance / inter-task-travel
and ranks which PLACEMENT feature actually predicts makespan (and throughput),
so a new assignment function can target the right quantity instead of Sigma f*D
(which this session showed is decoupled from throughput).

The picker dispatcher is round-robin over aisle-id-sorted tasks (fast_pick.py), so
makespan is governed by the per-aisle workload distribution + inter-task travel,
not bin distance.  This script measures exactly that.

Usage:
  python diagnose_makespan.py [<comparison_dir>]
  (bare name resolves under COMPARISON_OUTPUT_DIR; omitted = latest comparison_*)
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(os.path.dirname(_HERE), 'Warehouse'), _HERE]

import json
from Picking_Data import load_batch_stats, load_task_stats
from run_simulation import _OUTPUT_DIR, K_PICKERS

_SS_WINDOW = 50   # steady-state = last _SS_WINDOW batches


# -- per (profile, strategy) feature vector over the steady-state window  ------─

def _strategy_features(db_path: str, run_id: int) -> dict | None:
    bs = {b.batch_id: b for b in load_batch_stats(db_path, run_id)}
    if not bs:
        return None
    by_batch: dict[int, list] = defaultdict(list)
    for t in load_task_stats(db_path, run_id):
        by_batch[t.batch_id].append(t)
    if not by_batch:
        return None
    lo = max(by_batch) - _SS_WINDOW
    rows = []
    for bid, tasks in by_batch.items():
        if bid < lo or bid not in bs:
            continue
        b = bs[bid]
        durs = np.array([t.duration for t in tasks], dtype=float)
        if durs.size == 0 or b.duration <= 0:
            continue
        M          = float(b.duration)
        total_work = float(durs.sum())
        balanced   = total_work / K_PICKERS                       # perfect-balance lower bound
        busy   = defaultdict(float)
        starts = defaultdict(list)
        ends   = defaultdict(list)
        for t in tasks:
            busy[t.picker_id]   += t.duration
            starts[t.picker_id].append(t.task_start_time)
            ends[t.picker_id].append(t.task_end_time)
        max_busy = max(busy.values())                            # makespan with 0 inter-task gap
        nbins = float(sum(t.num_bins_visited for t in tasks)) or 1.0
        rows.append(dict(
            M=M, thr=b.total_items / M,
            # decomposition (fractions of M)
            f_work=balanced / M,                                 # irreducible balanced work
            f_imbalance=max(max_busy - balanced, 0.0) / M,       # round-robin imbalance cost
            f_travel=max(M - max_busy, 0.0) / M,                 # inter-task travel on critical picker
            imbalance_ratio=max_busy / balanced if balanced else 1.0,
            # candidate PLACEMENT features
            total_work=total_work,
            task_cv=float(durs.std() / durs.mean()) if durs.mean() else 0.0,   # aisle-load dispersion
            task_max=float(durs.max()),
            num_tasks=float(len(tasks)),                         # aisles / batch
            items_per_bin=b.total_items / nbins,                 # packing density (coherence)
            num_bins_per_task=float(np.mean([t.num_bins_visited for t in tasks])),
            W_mean=float(np.mean([t.W for t in tasks])),
            sigma_fd=float(b.sigma_fd),                          # NULL CONTROL (layout efficiency)
            picking_pct=float(b.picking_pct),
        ))
    if not rows:
        return None
    return {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}


# -- correlation table across strategies (profile-normalised, pooled)  --------─

_FEATURES = ['task_cv', 'task_max', 'num_tasks', 'items_per_bin', 'num_bins_per_task',
             'W_mean', 'sigma_fd', 'picking_pct', 'total_work']
_RATIO_NORM = {'task_max', 'W_mean', 'sigma_fd', 'total_work', 'items_per_bin'}  # scale-dependent


def main() -> None:
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate box-drawing chars on cp1252
    except Exception:
        pass
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        base = arg if os.path.isabs(arg) else os.path.join(_OUTPUT_DIR, arg)
    else:
        cands = sorted(d for d in os.listdir(_OUTPUT_DIR) if d.startswith('comparison_'))
        if not cands:
            sys.exit(f'No comparison_* runs under {_OUTPUT_DIR}')
        base = os.path.join(_OUTPUT_DIR, cands[-1])
    if not os.path.isdir(base):
        sys.exit(f'Not found: {base}')
    print(f'diagnose_makespan  dir: {base}  (k_pickers={K_PICKERS}, ss_window={_SS_WINDOW})\n')

    # gather per-(profile, strategy) features, normalised to each profile's baseline (strategies[0])
    points: list[dict] = []          # normalised feature ratios + M_ratio + thr_ratio
    decomp: list[dict] = []          # raw decomposition fractions (already unitless)
    n_prof = 0
    for prof in sorted(os.listdir(base)):
        pdir = os.path.join(base, prof)
        if not os.path.isdir(pdir) or prof.startswith('_'):
            continue
        for cfg in sorted(os.listdir(pdir)):
            meta_p = os.path.join(pdir, cfg, 'sim_meta.json')
            if not os.path.exists(meta_p):
                continue
            meta = json.load(open(meta_p))
            feats = {}
            for s in meta['strategies']:
                # sim_meta records an absolute db_path that may be stale if the run
                # was moved; rebuild it from the current config dir + filename.
                db_path = os.path.join(pdir, cfg, os.path.basename(s['db_path']))
                f = _strategy_features(db_path, s['run_id'])
                if f:
                    feats[s['key']] = (s, f)
            if not feats:
                continue
            base_key = meta['strategies'][0]['key']
            if base_key not in feats:
                continue
            bf = feats[base_key][1]
            n_prof += 1
            for key, (s, f) in feats.items():
                row = {'key': key, 'assignment': s.get('assignment', ''),
                       'M_ratio': f['M'] / bf['M'], 'thr_ratio': f['thr'] / bf['thr']}
                for feat in _FEATURES:
                    row[feat] = (f[feat] / bf[feat]) if (feat in _RATIO_NORM and bf[feat]) else f[feat]
                points.append(row)
                decomp.append({k: f[k] for k in ('f_work', 'f_imbalance', 'f_travel', 'imbalance_ratio')})

    print(f'profilesxconfigs scanned: {n_prof}   strategy points: {len(points)}\n')

    # -- makespan decomposition (mean fractions of M)  ------------------------
    print('-- Makespan decomposition (mean fraction of makespan)  ')
    fw = np.mean([d['f_work'] for d in decomp])
    fi = np.mean([d['f_imbalance'] for d in decomp])
    ft = np.mean([d['f_travel'] for d in decomp])
    ir = np.mean([d['imbalance_ratio'] for d in decomp])
    print(f'  balanced work (total_work/k)      : {fw*100:5.1f}%   <- irreducible')
    print(f'  round-robin imbalance cost        : {fi*100:5.1f}%   <- dispatcher (placement can flatten)')
    print(f'  inter-task travel (critical picker): {ft*100:5.1f}%   <- striding (placement: consolidate)')
    print(f'  imbalance ratio max_busy/balanced : {ir:5.2f}x\n')

    # -- feature -> makespan / throughput correlations  ------------------------
    def _pearson(xs, ys):
        x, y = np.array(xs), np.array(ys)
        if x.std() == 0 or y.std() == 0:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])

    Ms  = [p['M_ratio'] for p in points]
    Ts  = [p['thr_ratio'] for p in points]
    print('-- Feature -> makespan / throughput correlation (Pearson r, n='
          f'{len(points)}, profile-normalised)  ')
    print(f'  {"feature":18}{"r(makespan)":>13}{"r(throughput)":>15}')
    table = []
    for feat in _FEATURES:
        xs = [p[feat] for p in points]
        table.append((abs(_pearson(xs, Ms)), feat, _pearson(xs, Ms), _pearson(xs, Ts)))
    for _, feat, rM, rT in sorted(table, reverse=True):
        tag = '  (null control)' if feat == 'sigma_fd' else ''
        print(f'  {feat:18}{rM:>13.3f}{rT:>15.3f}{tag}')
    print('\n  |r(makespan)| ranked desc -> the top feature is what a new assignment fn '
          'should target.\n  Compare it against sigma_fd (the Sigma f*D null control).')


if __name__ == '__main__':
    main()
