"""stats.suite / stats.by_initial — per-config significance suite (strategies paired by
batch_id).  `stats.suite` compares all strategies into stats/; `stats.by_initial` pairs
uni_<fn> vs opt_<fn> per assignment function into stats_by_initial/<fn>/ plus a combined
by_initial_summary.csv.  Both consume ctx.breakdown() for the travel/handling rows."""
import json
import os

import numpy as np
import pandas as pd
import scipy.stats as st

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _fresh_dir
from Performance_Evaluations.common.frames import _metric_series, _aligned
from Performance_Evaluations.common.stats_core import (
    _METRICS, _descriptives, _run_tests, _clean,
    _group_by_assignment, _opt_better,
)
from Performance_Evaluations.stats.plots import _plot_dist, _plot_pmatrix, _plot_effect, _plot_rank


def _run_config_stats(strategies, df_b, df_t, ss_lo, out_dir, log, travel_handling=None) -> None:
    """Per-config significance suite (strategies paired by batch_id).

    travel_handling: optional {key: (travel, handling)} from the picker-event
    decomposition; adds travel-fraction / travel-time / handling-time rows to the
    summary so 'where task time goes' sits next to the productivity stats."""
    _fresh_dir(out_dir)
    keys = [s['key'] for s in strategies]
    colors = [s.get('color', '#888888') for s in strategies]
    summary_rows, all_tests = [], {}

    for name, source, col, lower in _METRICS:
        series_by_key = {k: _metric_series(df_b[k], df_t[k], source, col, ss_lo)
                         for k in keys}
        box_values = [series_by_key[k].values for k in keys]
        for k, vals in zip(keys, box_values):
            summary_rows.append({'strategy': k, 'metric': name, **_descriptives(vals)})

        M = _aligned(series_by_key, keys)
        tests = _run_tests(M, keys, lower)
        all_tests[name] = tests

        try:
            _plot_dist(box_values, keys, colors, name, tests, out_dir)
            _plot_pmatrix(tests, keys, name, out_dir)
            _plot_effect(tests, keys, name, out_dir)
            _plot_rank(tests, keys, colors, name, out_dir)
        except Exception as exc:                                  # noqa: BLE001
            log.error(f'  stats plot failed for {name}: {exc!r}')

    # travel vs handling decomposition (parallelism-independent; from picker_events)
    if travel_handling:
        for k in keys:
            if k in travel_handling:
                tr, hd = travel_handling[k]
                tot = tr + hd
                summary_rows.append({'strategy': k, 'metric': 'travel_fraction',
                                     **_descriptives(np.array([tr / tot if tot else np.nan]))})
                summary_rows.append({'strategy': k, 'metric': 'travel_time',
                                     **_descriptives(np.array([tr]))})
                summary_rows.append({'strategy': k, 'metric': 'handling_time',
                                     **_descriptives(np.array([hd]))})

    pd.DataFrame(summary_rows).to_csv(os.path.join(out_dir, 'stats_summary.csv'),
                                      index=False)
    with open(os.path.join(out_dir, 'tests.json'), 'w') as f:
        json.dump(_clean({'metrics': all_tests, 'ss_lo': int(ss_lo),
                          'note': 'nonparametric primary; statsmodels absent → '
                                  'ANOVA is one-way (not repeated-measures)'}), f, indent=2)
    log.info(f'  stats: {len(_METRICS)} metrics → {out_dir}')


def _run_config_stats_by_initial(strategies, df_b, df_t, ss_lo, out_dir, log,
                                 travel_handling=None) -> None:
    """Per-config: for each assignment function, run the full paired suite uni_<fn> vs
    opt_<fn> into out_dir/<fn>/, plus a combined out_dir/by_initial_summary.csv giving the
    opt-vs-uni contrast (medians, % change, Wilcoxon p, winner) per function and metric."""
    _fresh_dir(out_dir)
    groups = _group_by_assignment([s['key'] for s in strategies])
    by_key = {s['key']: s for s in strategies}
    combined, n_done = [], 0
    for fn, pair in groups.items():
        if 'uni' not in pair or 'opt' not in pair:
            continue
        pair_strats = [by_key[pair['uni']], by_key[pair['opt']]]   # uni = baseline first
        _run_config_stats(pair_strats, df_b, df_t, ss_lo,
                          os.path.join(out_dir, fn), log, travel_handling=travel_handling)
        for name, source, col, lower in _METRICS:
            su = _metric_series(df_b[pair['uni']], df_t[pair['uni']], source, col, ss_lo)
            so = _metric_series(df_b[pair['opt']], df_t[pair['opt']], source, col, ss_lo)
            common = sorted(set(su.index) & set(so.index))
            if len(common) < 3:
                continue
            u, o = su.loc[common].values, so.loc[common].values
            um, om = float(np.median(u)), float(np.median(o))
            try:
                p = float(st.wilcoxon(u, o).pvalue) if np.any(u != o) else 1.0
            except ValueError:
                p = float('nan')
            combined.append({
                'assignment': fn, 'metric': name, 'n': len(common),
                'uni_median': um, 'opt_median': om,
                'pct_change_opt_vs_uni': ((om - um) / um * 100.0) if um else float('nan'),
                'p_wilcoxon': p, 'better': _opt_better(um, om, lower),
            })
        n_done += 1
    pd.DataFrame(combined).to_csv(os.path.join(out_dir, 'by_initial_summary.csv'), index=False)
    log.info(f'  by-initial stats: {n_done} assignment fns -> {out_dir}')


@evaluation(key='stats.suite', label='Significance suite (paired by batch)',
            scope='config', needs=('batch', 'task', 'breakdown'), out_subdir='stats')
def render_suite(ctx, params):
    # ss_lo=0: every batch is a fair paired observation, so the omnibus/pairwise tests
    # use the full n (~100), not just the tail window.
    _run_config_stats(ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0,
                      os.path.join(ctx.run_dir, 'stats'), ctx.log,
                      travel_handling=ctx.breakdown())


@evaluation(key='stats.by_initial', label='Significance suite, uni-vs-opt per fn',
            scope='config', needs=('batch', 'task', 'breakdown'),
            out_subdir='stats_by_initial', by_initial=True)
def render_by_initial(ctx, params):
    _run_config_stats_by_initial(ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0,
                                 os.path.join(ctx.run_dir, 'stats_by_initial'), ctx.log,
                                 travel_handling=ctx.breakdown())
