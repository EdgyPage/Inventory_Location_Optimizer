"""tables.stats / tables.by_initial — the per-config significance DATA products.

The retired stats suite computed the paired tests once and wrote CSVs, JSON and four PNGs
per metric into per-assignment subdirectories.  Post-redesign the split is clean: the
NUMBERS live here (flat, in the declared tables dir), the FIGURES live in the
significance family — and both are built from the same shared computation in this module
(`compute_config_stats` / `compute_by_initial`), so the panels can never disagree with
the tables they sit beside.

  * `tables.stats`      — per-strategy descriptives (the exact retired columns,
                          including sem and the 95% CI bounds) plus the omnibus+pairwise
                          test document over all strategies.
  * `tables.by_initial` — the uni-vs-opt contrast per assignment function: the combined
                          summary CSV (exact retired columns) plus ONE consolidated test
                          document keyed by assignment function, replacing the retired
                          17 per-function copies.

ss_lo=0 throughout: every batch is a fair paired observation, so the tests use the full
n (~100), not just the steady-state tail window (the retired suite's deliberate choice).
"""
import json
import os

import numpy as np
import pandas as pd
import scipy.stats as st

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.frames import _metric_series, _aligned
from Optimization.Performance_Evaluations.common.stats_core import (
    _METRICS, _descriptives, _run_tests, _clean,
    _group_by_assignment, _opt_better,
)


# ── shared computation (also imported by the significance panels) ───────────────────

def compute_config_stats(strategies, df_b, df_t, ss_lo, travel_handling=None):
    """The full per-config suite over all strategies, computed once.

    Returns (summary_rows, per_metric):
      summary_rows  list of {'strategy', 'metric', **descriptives} — the CSV body,
                    including the travel/handling decomposition rows when provided;
      per_metric    {metric_name: {'box_values', 'tests', 'lower'}} — everything the
                    merged effect panels need, in _METRICS order.
    """
    keys = [s['key'] for s in strategies]
    summary_rows, per_metric = [], {}
    for name, source, col, lower in _METRICS:
        series_by_key = {k: _metric_series(df_b[k], df_t[k], source, col, ss_lo)
                         for k in keys}
        box_values = [series_by_key[k].values for k in keys]
        for k, vals in zip(keys, box_values):
            summary_rows.append({'strategy': k, 'metric': name, **_descriptives(vals)})
        M = _aligned(series_by_key, keys)
        per_metric[name] = dict(box_values=box_values,
                                tests=_run_tests(M, keys, lower), lower=lower)

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
    return summary_rows, per_metric


def compute_by_initial(strategies, df_b, df_t, ss_lo):
    """The uni-vs-opt contrast per assignment function, computed once.

    Returns (combined, per_fn):
      combined  list of rows with EXACTLY the retired summary columns
                (assignment, metric, n, uni_median, opt_median,
                 pct_change_opt_vs_uni, p_wilcoxon, better) — pct_change is the raw
                median shift, sign NOT improvement-oriented, as the retired CSV had it;
      per_fn    {fn: {'keys': {'uni','opt'}, 'metrics': {metric_name: detail}}} where
                detail carries the paired arrays (u, o), the direction flag, the full
                pairwise test document, and the summary scalars — the raw material for
                the consolidated test JSON and the significance heatmap/forest panels.
    """
    groups = _group_by_assignment([s['key'] for s in strategies])
    combined, per_fn = [], {}
    for fn, pair in groups.items():
        if 'uni' not in pair or 'opt' not in pair:
            continue
        fn_metrics = {}
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
            tests = _run_tests(np.column_stack([u, o]), [pair['uni'], pair['opt']], lower)
            fn_metrics[name] = dict(u=u, o=o, lower=lower, n=len(common),
                                    uni_median=um, opt_median=om, p_wilcoxon=p,
                                    tests=tests)
        if fn_metrics:
            per_fn[fn] = {'keys': dict(pair), 'metrics': fn_metrics}
    return combined, per_fn


def by_initial_tests_doc(per_fn, ss_lo):
    """The consolidated by-initial test document: one JSON-ready dict keyed by
    assignment function (replaces the retired one-file-per-function layout)."""
    return _clean({
        'ss_lo': int(ss_lo),
        'assignments': {
            fn: {'keys': d['keys'],
                 'metrics': {name: det['tests'] for name, det in d['metrics'].items()}}
            for fn, d in per_fn.items()
        },
    })


# ── the two CSV/JSON evaluations ────────────────────────────────────────────────────

@evaluation(key='tables.stats', label='Significance descriptives + tests (all strategies)',
            scope='config', needs=('batch', 'task', 'breakdown'), out_subdir='tables')
def render_stats(ctx, params):
    summary_rows, per_metric = compute_config_stats(
        ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0,
        travel_handling=ctx.breakdown())
    out = io.out_dir(ctx)
    pd.DataFrame(summary_rows).to_csv(os.path.join(out, 'stats_summary.csv'),
                                      index=False)
    all_tests = {name: d['tests'] for name, d in per_metric.items()}
    with open(os.path.join(out, 'tests.json'), 'w') as f:
        json.dump(_clean({'metrics': all_tests, 'ss_lo': 0,
                          'note': 'nonparametric primary; statsmodels absent → '
                                  'ANOVA is one-way (not repeated-measures)'}), f, indent=2)
    ctx.log.info(f'  stats tables: {len(_METRICS)} metrics -> {out}')


@evaluation(key='tables.by_initial', label='Uni-vs-opt tables per assignment fn',
            scope='config', needs=('batch', 'task', 'breakdown'), out_subdir='tables',
            by_initial=True)
def render_by_initial(ctx, params):
    combined, per_fn = compute_by_initial(
        ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0)
    out = io.out_dir(ctx)
    pd.DataFrame(combined).to_csv(os.path.join(out, 'by_initial_summary.csv'),
                                  index=False)
    with open(os.path.join(out, 'by_initial_tests.json'), 'w') as f:
        json.dump(by_initial_tests_doc(per_fn, 0), f, indent=2)
    ctx.log.info(f'  by-initial tables: {len(per_fn)} assignment fns -> {out}')
