"""agg.stats / agg.stats_by_initial — cross-profile significance suite (strategies paired
by profile).  `agg.stats` compares all common strategies into _aggregate/<pickcfg>/stats/;
`agg.stats_by_initial` pairs uni_<fn> vs opt_<fn> into stats_by_initial/<fn>/ + summary."""
import json
import os

import numpy as np
import pandas as pd
import scipy.stats as st

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _fresh_dir
from Performance_Evaluations.common.stats_core import (
    _AGG_METRICS, _descriptives, _run_tests, _clean,
    _group_by_assignment, _opt_better,
)
from Performance_Evaluations.stats.plots import _plot_dist, _plot_pmatrix, _plot_effect, _plot_rank


def _run_aggregate_stats(profile_series_list, out_dir, log, pickcfg) -> None:
    """Cross-profile significance suite (strategies paired by profile)."""
    _fresh_dir(out_dir)
    if not profile_series_list:
        return
    # key order from the first profile; keep only keys present in every profile
    keys = [d['key'] for d in profile_series_list[0].get('strategies', [])]
    per_profile = [{d['key']: d for d in ps.get('strategies', [])}
                   for ps in profile_series_list]
    keys = [k for k in keys if all(k in pp for pp in per_profile)]
    colors = [profile_series_list[0]['strategies'][i].get('color', '#888888')
              for i, d in enumerate(profile_series_list[0]['strategies'])
              if d['key'] in keys]
    if len(keys) < 2:
        log.warning(f'  aggregate stats {pickcfg}: <2 common strategies')
        return

    summary_rows, all_tests = [], {}
    for name, field, lower in _AGG_METRICS:
        rows = []
        for pp in per_profile:
            vals = [pp[k].get(field) for k in keys]
            if all(v is not None and np.isfinite(v) for v in vals):
                rows.append(vals)
        M = np.array(rows, float) if rows else None
        box_values = ([M[:, j] for j in range(len(keys))]
                      if M is not None and M.shape[0] else [np.array([])] * len(keys))
        for k, vals in zip(keys, box_values):
            summary_rows.append({'strategy': k, 'metric': name, **_descriptives(vals)})

        tests = _run_tests(M if (M is not None and M.shape[0] >= 3) else None, keys, lower)
        all_tests[name] = tests
        try:
            if M is not None and M.shape[0]:
                _plot_dist(box_values, keys, colors, name, tests, out_dir)
            _plot_pmatrix(tests, keys, name, out_dir)
            _plot_effect(tests, keys, name, out_dir)
            _plot_rank(tests, keys, colors, name, out_dir)
        except Exception as exc:                                  # noqa: BLE001
            log.error(f'  aggregate stats plot failed for {name}: {exc!r}')

    pd.DataFrame(summary_rows).to_csv(os.path.join(out_dir, 'aggregate_summary.csv'),
                                      index=False)
    with open(os.path.join(out_dir, 'aggregate_tests.json'), 'w') as f:
        json.dump(_clean({'pickcfg': pickcfg, 'n_profiles': len(profile_series_list),
                          'metrics': all_tests}), f, indent=2)
    log.info(f'  aggregate stats: {len(_AGG_METRICS)} metrics → {out_dir}')


def _run_aggregate_stats_by_initial(profile_series_list, out_dir, log, pickcfg) -> None:
    """Cross-profile: per assignment function, run the aggregate paired suite uni_<fn> vs
    opt_<fn> into out_dir/<fn>/, plus a combined out_dir/by_initial_summary.csv."""
    _fresh_dir(out_dir)
    if not profile_series_list:
        return
    keys = [d['key'] for d in profile_series_list[0].get('strategies', [])]
    groups = _group_by_assignment(keys)
    combined, n_done = [], 0
    for fn, pair in groups.items():
        if 'uni' not in pair or 'opt' not in pair:
            continue
        want = {pair['uni'], pair['opt']}
        sub_list = [{**ps, 'strategies': [d for d in ps.get('strategies', [])
                                          if d.get('key') in want]}
                    for ps in profile_series_list]
        _run_aggregate_stats(sub_list, os.path.join(out_dir, fn), log, pickcfg)
        # combined contrast across profiles (paired by profile)
        per_profile = [{d['key']: d for d in ps.get('strategies', [])} for ps in profile_series_list]
        for name, field, lower in _AGG_METRICS:
            u = [pp[pair['uni']].get(field) for pp in per_profile if pair['uni'] in pp]
            o = [pp[pair['opt']].get(field) for pp in per_profile if pair['opt'] in pp]
            u = np.array([v for v in u if v is not None and np.isfinite(v)], float)
            o = np.array([v for v in o if v is not None and np.isfinite(v)], float)
            if u.size < 3 or o.size < 3:
                continue
            um, om = float(np.median(u)), float(np.median(o))
            p = float('nan')
            if u.size == o.size:
                try:
                    p = float(st.wilcoxon(u, o).pvalue) if np.any(u != o) else 1.0
                except ValueError:
                    p = float('nan')
            combined.append({
                'assignment': fn, 'metric': name, 'n_profiles': int(min(u.size, o.size)),
                'uni_median': um, 'opt_median': om,
                'pct_change_opt_vs_uni': ((om - um) / um * 100.0) if um else float('nan'),
                'p_wilcoxon': p, 'better': _opt_better(um, om, lower),
            })
        n_done += 1
    pd.DataFrame(combined).to_csv(os.path.join(out_dir, 'by_initial_summary.csv'), index=False)
    log.info(f'  aggregate by-initial stats: {n_done} assignment fns -> {out_dir}')


@evaluation(key='agg.stats', label='Cross-profile significance suite',
            scope='aggregate', needs=('series',), out_subdir='stats')
def render_stats(ctx, params):
    _run_aggregate_stats(ctx.profile_series_list, os.path.join(ctx.out_dir, 'stats'),
                         ctx.log, ctx.pickcfg)


@evaluation(key='agg.stats_by_initial', label='Cross-profile significance, uni-vs-opt per fn',
            scope='aggregate', needs=('series',), out_subdir='stats_by_initial', by_initial=True)
def render_stats_by_initial(ctx, params):
    _run_aggregate_stats_by_initial(ctx.profile_series_list,
                                    os.path.join(ctx.out_dir, 'stats_by_initial'),
                                    ctx.log, ctx.pickcfg)
