"""Per-strategy time-series construction, the series.json dump, top-N selection, and the
cross-profile aggregation (verbatim from the retired Comparison_Plots).

`_build_series` is wrapped by EvalContext.series(); `_aggregate_series` by
AggregateContext.agg_series().  `_dump_series` and `_select_top` are used by the
config.series graph and the top_metric graph respectively.
"""
import json
import math
from collections import defaultdict

import numpy as np

from Performance_Evaluations.common.style import _WIN, _SMOOTH, _TOP_DIMS


def _build_series(strategies, df_b, df_t):
    """Per-strategy time series + steady-state scalars for the comparison suite.

    Returns {key: dict | None}.  Task metrics carry their own x ('task_batch')
    because some batches may have no surviving (non-outlier) tasks.
    """
    S = {}
    for s in strategies:
        b = df_b[s['key']]
        t = df_t[s['key']]
        if b.empty:
            S[s['key']] = None
            continue
        bd    = b.sort_values('batch_id')
        batch = bd['batch_id'].values
        # Over-time trajectories use the small _SMOOTH window so early dynamics stay visible.
        thr   = bd['completion_rate'].rolling(_SMOOTH, min_periods=1).mean().values
        # Layout/travel cost over time (Sigma f*D) — the dimension where an optimal initial
        # start shows its advantage (and where uniform converges as reorders re-place items).
        sigma = bd['sigma_fd'].rolling(_SMOOTH, min_periods=1).mean().values

        if not t.empty:
            g   = t.groupby('batch_id')['duration']
            agg = g.agg(['mean', 'median']).sort_index()
            q25 = g.quantile(0.25).reindex(agg.index)
            q75 = g.quantile(0.75).reindex(agg.index)
            tsum = g.sum().reindex(agg.index)        # production time = Σ task time / batch
            tb   = agg.index.values
            roll = lambda ser: ser.rolling(_SMOOTH, min_periods=1).mean().values
            tmean, tmed = roll(agg['mean']), roll(agg['median'])
            tp25,  tp75 = roll(q25),         roll(q75)
            tprod = roll(tsum)
        else:
            tb = batch
            tmean = tmed = tp25 = tp75 = tprod = np.full(len(batch), np.nan)

        maxb = int(batch.max())
        lo   = maxb - _WIN + 1                        # steady-state window (unchanged)
        ssb  = bd[bd['batch_id'] >= lo]
        sst  = t[t['batch_id'] >= lo] if not t.empty else t
        S[s['key']] = dict(
            batch=batch, thr=thr, sigma_fd=sigma,
            task_batch=tb, task_mean=tmean, task_median=tmed,
            task_p25=tp25, task_p75=tp75, prod_hours=tprod,
            ss_thr=float(ssb['completion_rate'].mean()),
            ss_dur=float(ssb['duration'].mean()),
            ss_sigma=float(ssb['sigma_fd'].mean()),
            ss_task_mean=float(sst['duration'].mean()) if len(sst) else float('nan'),
            ss_prod_hours=(float(sst.groupby('batch_id')['duration'].sum().mean())
                           if len(sst) else float('nan')),
            picking_pct=float(ssb['picking_pct'].mean()),
            traveling_pct=float(ssb['traveling_pct'].mean()),
            initial=s.get('initial', ''), assignment=s.get('assignment', ''),
            reslot=s.get('reslot', ''), color=s['color'],
            label=s.get('label', s['key']), key=s['key'],
        )
    return S


def _dump_series(strategies, S, path, extra=None):
    out = []
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        out.append({k: (v.tolist() if isinstance(v, np.ndarray) else v)
                    for k, v in d.items()})
    payload = {'strategies': out}
    if extra:
        payload.update(extra)              # e.g. optimal_work / optimal_sigma_fd floors
    with open(path, 'w') as f:
        json.dump(payload, f)


def _select_top(strategies, S, top_n, top_by):
    """Choose which strategies the top/ plot shows.

    'global'  → the top_n strategies overall (by steady-state throughput).
    a dim     → the top_n WITHIN each value of that dim (initial/assignment/reslot),
                so the comparison isn't dominated by one family (e.g. optimal-start).
    Returns (selected_strategies, group_of_key) where group_of_key maps key→group
    label ('' for global)."""
    avail = [s for s in strategies if S.get(s['key'])]
    # Rank by productivity-hours (Σ task length = total labor); lower is better.
    # This is the optimization target, not batch wall-time.  NaN sorts last.
    def rank(s):
        v = S[s['key']].get('ss_prod_hours')
        return float('inf') if v is None or (isinstance(v, float) and v != v) else v
    if top_by in _TOP_DIMS:
        groups = defaultdict(list)
        for s in avail:
            groups[s[top_by]].append(s)
        out, gof = [], {}
        for g in sorted(groups):
            for s in sorted(groups[g], key=rank)[:top_n]:
                out.append(s)
                gof[s['key']] = g
        return out, gof
    return sorted(avail, key=rank)[:top_n], {}


def _aggregate_series(profile_series_list):
    """Average per-strategy curves across profiles, normalized per-profile to that
    profile's baseline-strategy steady-state mean (scale-free).  Returns
    (agg_strategies, agg_S) ready for the same builders."""
    per_key = defaultdict(list)               # key -> [(strat_dict, baseline_dict)]
    order   = {}
    for ps in profile_series_list:
        strs = ps.get('strategies', [])
        if not strs:
            continue
        base = strs[0]
        for i, d in enumerate(strs):
            per_key[d['key']].append((d, base))
            order.setdefault(d['key'], i)

    def _avg_norm(items, field, norm_field):
        arrs = []
        for d, base in items:
            a  = np.asarray(d.get(field, []), dtype=float)
            nb = base.get(norm_field)
            if a.size and nb and not math.isnan(nb):
                arrs.append(a / nb)
        if not arrs:
            return np.array([])
        mlen = min(len(a) for a in arrs)
        return np.mean([a[:mlen] for a in arrs], axis=0)

    agg_S, agg_strats = {}, []
    for key, items in per_key.items():
        rep   = items[0][0]
        thr   = _avg_norm(items, 'thr',         'ss_thr')
        tmean = _avg_norm(items, 'task_mean',   'ss_task_mean')
        tmed  = _avg_norm(items, 'task_median', 'ss_task_mean')
        tp25  = _avg_norm(items, 'task_p25',    'ss_task_mean')
        tp75  = _avg_norm(items, 'task_p75',    'ss_task_mean')
        tprod = _avg_norm(items, 'prod_hours',  'ss_prod_hours')
        sigma = _avg_norm(items, 'sigma_fd',    'ss_sigma')
        thr_ratio = [d['ss_thr'] / b['ss_thr'] for d, b in items if b.get('ss_thr')]
        dur_ratio = [d['ss_dur'] / b['ss_dur'] for d, b in items if b.get('ss_dur')]
        agg_S[key] = dict(
            batch=np.arange(len(thr)), thr=thr, sigma_fd=sigma,
            task_batch=np.arange(len(tmed)), task_mean=tmean, task_median=tmed,
            task_p25=tp25, task_p75=tp75, prod_hours=tprod,
            ss_thr=float(np.mean(thr_ratio)) if thr_ratio else float('nan'),
            ss_dur=float(np.mean(dur_ratio)) if dur_ratio else float('nan'),
            picking_pct=float(np.mean([d['picking_pct']   for d, _ in items])),
            traveling_pct=float(np.mean([d['traveling_pct'] for d, _ in items])),
            initial=rep.get('initial', ''), assignment=rep.get('assignment', ''),
            reslot=rep.get('reslot', ''), color=rep['color'],
            label=rep.get('label', key), key=key,
        )
        agg_strats.append({k: rep.get(k, '') for k in ('key', 'initial', 'assignment',
                                                       'reslot', 'label')} | {'color': rep['color']})
    agg_strats.sort(key=lambda s: order.get(s['key'], 1 << 30))
    return agg_strats, agg_S
