"""agg.tables — the cross-profile significance DATA products (strategies paired by
profile over the steady-state scalars).

The aggregate mirror of the per-config tables evaluations: the numbers land flat in the
declared tables dir under the aggregate root, the figures live in the aggregate
significance eval, and BOTH are built from the shared computation in this module
(`compute_aggregate_stats` / `compute_aggregate_by_initial`) so they cannot disagree.
The retired per-assignment-function directory tree is gone — the by-initial fork writes
one combined CSV (exact retired columns) plus ONE consolidated test document keyed by
assignment function.

Values are the RAW per-profile steady-state scalars from each profile's series
document — not the ×-baseline-normalized curves the trajectory painter draws — so the
descriptives keep honest units, exactly as the retired suite computed them.  The
`by_initial` param picks the fork (default True, matching the by-initial preset).
"""
import json
import os

import numpy as np
import pandas as pd
import scipy.stats as st

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.stats_core import (
    _AGG_METRICS, _clean, _descriptives, _group_by_assignment, _opt_better, _run_tests)


def _finite(x):
    return x is not None and np.isfinite(x)


# ── shared computation (also imported by the aggregate significance eval) ───────────

def compute_aggregate_stats(profile_series_list):
    """The all-strategies suite over the steady-state scalars, paired by profile.

    Returns (keys, colors, summary_rows, per_metric):
      keys / colors  the strategies common to EVERY profile, first-profile order;
      summary_rows   {'strategy', 'metric', **descriptives} rows — the CSV body;
      per_metric     {metric_name: {'box_values', 'tests', 'lower'}} for the panels.
    """
    if not profile_series_list:
        return [], [], [], {}
    keys = [d['key'] for d in profile_series_list[0].get('strategies', [])]
    per_profile = [{d['key']: d for d in ps.get('strategies', [])}
                   for ps in profile_series_list]
    keys = [k for k in keys if all(k in pp for pp in per_profile)]
    colors = [d.get('color', '#888888')
              for d in profile_series_list[0].get('strategies', [])
              if d['key'] in keys]

    summary_rows, per_metric = [], {}
    for name, field, lower in _AGG_METRICS:
        rows = []
        for pp in per_profile:
            vals = [pp[k].get(field) for k in keys]
            if all(_finite(v) for v in vals):
                rows.append(vals)
        M = np.array(rows, float) if rows else None
        box_values = ([M[:, j] for j in range(len(keys))]
                      if M is not None and M.shape[0] else [np.array([])] * len(keys))
        for k, vals in zip(keys, box_values):
            summary_rows.append({'strategy': k, 'metric': name, **_descriptives(vals)})
        per_metric[name] = dict(
            box_values=box_values,
            tests=_run_tests(M if (M is not None and M.shape[0] >= 3) else None,
                             keys, lower),
            lower=lower)
    return keys, colors, summary_rows, per_metric


#: Profiles a uni-vs-opt contrast needs before it is worth a paired test.  A sweep with
#: two inventory variants has TWO profiles per group, which is below this floor — so the
#: cross-profile by-initial suite legitimately produces nothing there.  Named, because the
#: caller must be able to say WHICH reason applies: "no pairs" and "too few profiles" look
#: identical downstream and mean completely different things.
MIN_PAIRED_PROFILES = 3


def paired_profile_diagnosis(profile_series_list) -> str:
    """Why a by-initial contrast produced nothing — in the reader's terms, not a shrug.

    Returns '' when it should have produced something.
    """
    n = len(profile_series_list)
    if not n:
        return 'no profiles in this group'
    keys = [d['key'] for d in profile_series_list[0].get('strategies', [])]
    pairs = [fn for fn, p in _group_by_assignment(keys).items()
             if 'uni' in p and 'opt' in p]
    if not pairs:
        return f'none of the {len(keys)} arms form a uni/opt pair'
    if n < MIN_PAIRED_PROFILES:
        return (f'{len(pairs)} uni/opt pair(s) across only {n} profile(s); a paired test '
                f'needs {MIN_PAIRED_PROFILES}. That is a property of this sweep shape '
                f'(one profile per inventory variant), not a failure — add inventory '
                f'variants, or read the per-leaf significance suite instead.')
    return f'{len(pairs)} uni/opt pair(s) over {n} profiles, but no metric had finite values'


def compute_aggregate_by_initial(profile_series_list):
    """The uni-vs-opt contrast per assignment function, paired by profile.

    Returns (combined, per_fn) shaped like the per-config counterpart:
      combined  rows with EXACTLY the retired summary columns (assignment, metric,
                n_profiles, uni_median, opt_median, pct_change_opt_vs_uni, p_wilcoxon,
                better) — medians/p computed with the retired semantics (each family's
                values collected and finite-filtered independently; p only when the two
                samples pair up 1:1);
      per_fn    {fn: {'keys', 'metrics': {name: detail}}} where the detail's u/o arrays
                ARE profile-paired (profiles holding both arms, both finite) so the
                test document and the significance panels rest on honest pairs.
    """
    if not profile_series_list:
        return [], {}
    keys = [d['key'] for d in profile_series_list[0].get('strategies', [])]
    groups = _group_by_assignment(keys)
    per_profile = [{d['key']: d for d in ps.get('strategies', [])}
                   for ps in profile_series_list]
    combined, per_fn = [], {}
    for fn, pair in groups.items():
        if 'uni' not in pair or 'opt' not in pair:
            continue
        uk, ok = pair['uni'], pair['opt']
        fn_metrics = {}
        for name, field, lower in _AGG_METRICS:
            # PROFILE-PAIRED from the start.  Filtering each arm's profiles independently
            # and then handing the two arrays to a paired test is the classic way to
            # publish a p from a broken pairing: a profile missing on one side only
            # shortens that array, and if two different profiles drop out — one per arm —
            # the lengths still match and Wilcoxon silently compares profile i of one arm
            # against a DIFFERENT profile i of the other.
            up, op_ = [], []
            for pp in per_profile:
                if uk in pp and ok in pp:
                    a, b = pp[uk].get(field), pp[ok].get(field)
                    if _finite(a) and _finite(b):
                        up.append(float(a))
                        op_.append(float(b))
            u, o = np.array(up, float), np.array(op_, float)
            if u.size < MIN_PAIRED_PROFILES:
                continue
            um, om = float(np.median(u)), float(np.median(o))
            p = float('nan')
            try:
                p = float(st.wilcoxon(u, o).pvalue) if np.any(u != o) else 1.0
            except ValueError:
                p = float('nan')
            combined.append({
                'assignment': fn, 'metric': name, 'n_profiles': int(u.size),
                'uni_median': um, 'opt_median': om,
                'pct_change_opt_vs_uni': ((om - um) / um * 100.0) if um else float('nan'),
                'p_wilcoxon': p, 'better': _opt_better(um, om, lower),
            })
            tests = _run_tests(np.column_stack([u, o]), [uk, ok], lower)
            fn_metrics[name] = dict(u=u, o=o, lower=lower, n=int(u.size),
                                    uni_median=um, opt_median=om, p_wilcoxon=p,
                                    tests=tests)
        if fn_metrics:
            per_fn[fn] = {'keys': dict(pair), 'metrics': fn_metrics}
    return combined, per_fn


# ── the evaluation ──────────────────────────────────────────────────────────────────

@evaluation(key='agg.tables', label='Cross-profile significance tables',
            scope='aggregate', needs=('series',), out_subdir='tables',
            defaults={'by_initial': True})
def render(ctx, params):
    out = io.out_dir(ctx)
    if params.get('by_initial'):
        combined, per_fn = compute_aggregate_by_initial(ctx.profile_series_list)
        pd.DataFrame(combined).to_csv(os.path.join(out, 'by_initial_summary.csv'),
                                      index=False)
        doc = _clean({
            'pickcfg': ctx.pickcfg, 'n_profiles': ctx.n_profiles,
            'assignments': {
                fn: {'keys': d['keys'],
                     'metrics': {name: det['tests']
                                 for name, det in d['metrics'].items()}}
                for fn, d in per_fn.items()},
        })
        with open(os.path.join(out, 'by_initial_tests.json'), 'w') as f:
            json.dump(doc, f, indent=2)
        ctx.log.info(f'  aggregate by-initial tables: {len(per_fn)} '
                     f'assignment fns -> {out}')
        return

    keys, _colors, summary_rows, per_metric = compute_aggregate_stats(
        ctx.profile_series_list)
    if len(keys) < 2:
        ctx.log.warning(f'  aggregate tables {ctx.pickcfg}: <2 common strategies')
        return
    pd.DataFrame(summary_rows).to_csv(os.path.join(out, 'aggregate_summary.csv'),
                                      index=False)
    all_tests = {name: d['tests'] for name, d in per_metric.items()}
    with open(os.path.join(out, 'aggregate_tests.json'), 'w') as f:
        json.dump(_clean({'pickcfg': ctx.pickcfg, 'n_profiles': ctx.n_profiles,
                          'metrics': all_tests}), f, indent=2)
    ctx.log.info(f'  aggregate tables: {len(_AGG_METRICS)} metrics -> {out}')
