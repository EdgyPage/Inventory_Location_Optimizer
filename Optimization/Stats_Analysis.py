"""Stats_Analysis.py — significance testing for the comparison/graphing suite.

Slots into run_analysis.py → Comparison_Plots.py.  Adds rigorous statistical
comparison of strategies on top of the descriptive plots:

  * per-config  (run_config_stats)  — strategies are PAIRED by batch_id (every
    strategy in a config uses the same SEED_BATCHES, so batch i is the same
    generated batch).  Steady-state batches only (the last _WIN, outliers already
    flagged out upstream).  n ≈ 50 paired observations.
  * cross-profile (run_aggregate_stats) — each profile's steady-state scalar is one
    paired observation; strategies are PAIRED by profile.  n = #profiles.

Methods (per metric): nonparametric is primary (Friedman omnibus + pairwise
Wilcoxon signed-rank + matched-pairs rank-biserial effect), parametric reported
alongside (one-way ANOVA + paired t + Hedges' g).  All-pairwise N×N matrices,
Holm + Benjamini-Hochberg corrected.  Graphs: notched box plots (no violins),
pairwise p-value + effect-size heatmaps, mean-rank bars.

Self-contained (own _save_close/_fresh_dir, uses each strategy's own color) so it
imports nothing from Comparison_Plots — avoids an import cycle, since
Comparison_Plots imports this module lazily at its call sites.
"""
from __future__ import annotations

import json
import math
import os
import shutil

import matplotlib
matplotlib.use('Agg')   # safe if already set; needed when imported standalone
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.stats as st


# ── metric specs ────────────────────────────────────────────────────────────────
# (name, source, column, lower_is_better)
#   source: 'batch'      -> per-batch column of df_b
#           'task_mean'  -> per-batch mean of a df_t column
#           'task_sum'   -> per-batch sum  of a df_t column (productivity hours)
# Ordered with the optimization target FIRST: productivity-hours = Σ task length
# (total labor), then mean task time — both parallelism-independent.  Batch-duration
# "makespan" is wall-time (parallelism-dependent) and is reported as secondary.
_METRICS = [
    ('productivity_hours', 'task_sum',  'duration',           True),   # PRIMARY: Σ task length
    # Analytical objective: E[labor of a random task] = mean task W (D+P+C), and its sum
    # (total analytical labor).  Scored from task structure, so robust to sim wall-timing
    # noise / reorder starvation — the direct yardstick for the slotting objective.
    ('objective_task_labor', 'task_mean', 'W',                True),   # E[task labor] (objective)
    ('objective_total_labor', 'task_sum', 'W',                True),   # Σ analytical task labor
    ('task_mean_duration', 'task_mean', 'duration',           True),
    ('makespan',           'batch',     'duration',           True),   # wall-time (parallel)
    ('throughput',         'batch',     'completion_rate',    False),
    ('sigma_fd',           'batch',     'sigma_fd',           True),
    ('picking_pct',        'batch',     'picking_pct',        False),
    ('reorder_churn',      'batch',     'reorder_placements', True),
]

# cross-profile steady-state scalars (from series.json): (name, ss_field, lower_is_better)
_AGG_METRICS = [
    ('makespan',           'ss_dur',        True),
    ('throughput',         'ss_thr',        False),
    ('task_mean_duration', 'ss_task_mean',  True),
    ('productivity_hours', 'ss_prod_hours', True),
]


# ── small local helpers (no Comparison_Plots import → no cycle) ──────────────────

def _save_close(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _fresh_dir(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


def _short(key: str) -> str:
    """Compact axis label: drop the trailing reslot token (_norsl)."""
    return key[:-6] if key.endswith('_norsl') else key


def _clean(obj):
    """Recursively replace NaN/Inf with None so the JSON is valid + portable."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


# ── multiple-comparison correction ──────────────────────────────────────────────

def _holm(pvals: np.ndarray) -> np.ndarray:
    """Holm-Bonferroni step-down on a 1D array of p-values (NaNs passed through)."""
    p = np.asarray(pvals, dtype=float)
    out = np.full_like(p, np.nan)
    idx = np.where(np.isfinite(p))[0]
    if idx.size == 0:
        return out
    order = idx[np.argsort(p[idx])]
    m = order.size
    prev = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * p[i])
        prev = max(prev, adj)          # enforce monotonicity
        out[i] = prev
    return out


def _bh(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR on a 1D array of p-values (NaNs passed through)."""
    p = np.asarray(pvals, dtype=float)
    out = np.full_like(p, np.nan)
    idx = np.where(np.isfinite(p))[0]
    if idx.size == 0:
        return out
    order = idx[np.argsort(p[idx])]
    m = order.size
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        adj = min(prev, p[i] * m / (rank + 1))
        prev = adj
        out[i] = adj
    return out


# ── paired effect sizes ──────────────────────────────────────────────────────────

def _rank_biserial(a: np.ndarray, b: np.ndarray) -> float:
    """Matched-pairs rank-biserial correlation in [-1, 1].
    >0 ⇒ a tends to exceed b.  Derived from the signed-rank sums of (a-b)."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[d != 0]
    if d.size == 0:
        return 0.0
    r = st.rankdata(np.abs(d))
    t_plus = r[d > 0].sum()
    t_minus = r[d < 0].sum()
    tot = t_plus + t_minus
    return float((t_plus - t_minus) / tot) if tot else 0.0


def _hedges_g_paired(a: np.ndarray, b: np.ndarray) -> float:
    """Hedges' g on the paired differences (small-sample corrected Cohen's d_z)."""
    d = np.asarray(a, float) - np.asarray(b, float)
    n = d.size
    sd = d.std(ddof=1) if n > 1 else 0.0
    if not sd:
        return 0.0
    g = d.mean() / sd
    return float(g * (1 - 3 / (4 * n - 1)))   # bias correction


# ── descriptive statistics ───────────────────────────────────────────────────────

def _descriptives(a: np.ndarray) -> dict:
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    n = a.size
    if n == 0:
        return dict(n=0, mean=np.nan, median=np.nan, std=np.nan, sem=np.nan,
                    ci_lo=np.nan, ci_hi=np.nan, iqr=np.nan, cv=np.nan,
                    min=np.nan, max=np.nan, skew=np.nan, kurtosis=np.nan)
    mean = float(a.mean())
    std = float(a.std(ddof=1)) if n > 1 else 0.0
    sem = float(st.sem(a)) if n > 1 else 0.0
    if n > 1 and sem > 0:
        ci_lo, ci_hi = st.t.interval(0.95, n - 1, loc=mean, scale=sem)
    else:
        ci_lo = ci_hi = mean
    return dict(
        n=n, mean=mean, median=float(np.median(a)), std=std, sem=sem,
        ci_lo=float(ci_lo), ci_hi=float(ci_hi),
        iqr=float(np.percentile(a, 75) - np.percentile(a, 25)),
        cv=float(std / mean) if mean else np.nan,
        min=float(a.min()), max=float(a.max()),
        skew=float(st.skew(a)) if n > 2 else np.nan,
        kurtosis=float(st.kurtosis(a)) if n > 3 else np.nan,
    )


# ── core: tests over an aligned (n_blocks × k) paired matrix ─────────────────────

def _mean_ranks(M: np.ndarray, lower_better: bool) -> np.ndarray:
    """Average within-block rank per column; rank 1 = best (direction-aware)."""
    vals = M if lower_better else -M
    ranks = np.vstack([st.rankdata(vals[r, :]) for r in range(vals.shape[0])])
    return ranks.mean(axis=0)


def _run_tests(M: np.ndarray, keys: list[str], lower_better: bool) -> dict:
    """Omnibus + all-pairwise tests on a paired matrix M (rows = blocks, cols = keys)."""
    k = len(keys)
    res: dict = {
        'keys': keys, 'n_blocks': int(M.shape[0]) if M is not None else 0,
        'friedman': {'stat': np.nan, 'p': np.nan},
        'anova':    {'stat': np.nan, 'p': np.nan},
        'mean_rank': {key: np.nan for key in keys},
    }
    nan_mat = lambda: np.full((k, k), np.nan)
    for name in ('p_wilcoxon', 'p_wilcoxon_holm', 'p_wilcoxon_bh',
                 'p_ttest', 'p_ttest_holm', 'rank_biserial', 'hedges_g', 'median_pct'):
        res[name] = nan_mat()

    if M is None or M.shape[0] < 3 or k < 2:
        return res

    cols = [M[:, j] for j in range(k)]
    # omnibus
    try:
        if k >= 3:
            s, p = st.friedmanchisquare(*cols)
            res['friedman'] = {'stat': float(s), 'p': float(p)}
    except Exception:
        pass
    try:
        s, p = st.f_oneway(*cols)
        res['anova'] = {'stat': float(s), 'p': float(p)}
    except Exception:
        pass

    mr = _mean_ranks(M, lower_better)
    res['mean_rank'] = {key: float(mr[j]) for j, key in enumerate(keys)}

    pw = res['p_wilcoxon']; pt = res['p_ttest']
    rb = res['rank_biserial']; hg = res['hedges_g']; mp = res['median_pct']
    for i in range(k):
        for j in range(i + 1, k):
            a, b = cols[i], cols[j]
            d = a - b
            # Wilcoxon (paired); all-zero diff ⇒ no difference
            if np.any(d != 0):
                try:
                    wp = float(st.wilcoxon(a, b).pvalue)
                except Exception:
                    wp = np.nan
            else:
                wp = 1.0
            try:
                tp = float(st.ttest_rel(a, b).pvalue)
            except Exception:
                tp = np.nan
            r = _rank_biserial(a, b)
            g = _hedges_g_paired(a, b)
            mb = float(np.median(b))
            mpct = float((np.median(a) - mb) / mb * 100) if mb else np.nan
            pw[i, j] = pw[j, i] = wp
            pt[i, j] = pt[j, i] = tp
            rb[i, j] = r;  rb[j, i] = -r        # signed: row vs col
            hg[i, j] = g;  hg[j, i] = -g
            mp[i, j] = mpct
            mp[j, i] = float((mb - np.median(a)) / np.median(a) * 100) if np.median(a) else np.nan

    # corrections over the unique upper-triangle pairs, then mirror back
    iu = np.triu_indices(k, 1)
    res['p_wilcoxon_holm'] = _mirror(pw, iu, _holm(pw[iu]))
    res['p_wilcoxon_bh']   = _mirror(pw, iu, _bh(pw[iu]))
    res['p_ttest_holm']    = _mirror(pt, iu, _holm(pt[iu]))
    return res


def _mirror(template: np.ndarray, iu, vals: np.ndarray) -> np.ndarray:
    out = np.full_like(template, np.nan)
    out[iu] = vals
    out.T[iu] = vals
    return out


# ── plots ────────────────────────────────────────────────────────────────────────

def _stars(p: float) -> str:
    if p is None or not np.isfinite(p):
        return ''
    return '***' if p < 1e-3 else '**' if p < 1e-2 else '*' if p < 5e-2 else 'ns'


def _plot_dist(box_values, keys, colors, name, tests, out_dir):
    """Notched box plots of the per-block values across strategies (no violins)."""
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(keys)), 5))
    bp = ax.boxplot(box_values, notch=True, showmeans=True, patch_artist=True,
                    meanprops=dict(marker='D', markerfacecolor='black',
                                   markeredgecolor='black', markersize=4),
                    medianprops=dict(color='black'))
    for patch, c in zip(bp['boxes'], colors):
        patch.set_facecolor(c); patch.set_alpha(0.55)
    rng = np.random.default_rng(0)
    for i, v in enumerate(box_values, start=1):
        v = np.asarray(v, float); v = v[np.isfinite(v)]
        if v.size:
            ax.scatter(rng.normal(i, 0.05, v.size), v, s=6, color=colors[i - 1],
                       alpha=0.35, edgecolors='none', zorder=3)
    ax.set_xticks(range(1, len(keys) + 1))
    ax.set_xticklabels([_short(k) for k in keys], rotation=40, ha='right', fontsize=8)
    ax.set_ylabel(name)
    ax.grid(axis='y', alpha=0.3)
    fr = tests.get('friedman', {})
    p = fr.get('p')
    sub = (f"Friedman χ²={fr.get('stat'):.1f}, p={p:.2e}"
           if p is not None and np.isfinite(p) else 'Friedman n/a')
    ax.set_title(f'{name} — steady-state distribution by strategy\n{sub}',
                 fontsize=11, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_dist.png'))


def _plot_pmatrix(tests, keys, name, out_dir):
    """N×N Holm-corrected Wilcoxon p heatmap (−log10 scale, starred)."""
    P = np.asarray(tests['p_wilcoxon_holm'], float)
    k = len(keys)
    L = -np.log10(np.clip(P, 1e-12, 1.0))
    np.fill_diagonal(L, np.nan)
    fig, ax = plt.subplots(figsize=(0.8 * k + 3, 0.8 * k + 2))
    im = ax.imshow(L, cmap='viridis', vmin=0, vmax=4)
    for i in range(k):
        for j in range(k):
            if i != j and np.isfinite(P[i, j]):
                ax.text(j, i, _stars(P[i, j]), ha='center', va='center',
                        color='white', fontsize=8)
    ax.set_xticks(range(k)); ax.set_yticks(range(k))
    ax.set_xticklabels([_short(x) for x in keys], rotation=40, ha='right', fontsize=7)
    ax.set_yticklabels([_short(x) for x in keys], fontsize=7)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label('−log10(Holm p)')
    ax.set_title(f'{name} — pairwise Wilcoxon (Holm)\n* p<.05  ** p<.01  *** p<.001',
                 fontsize=10, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_pmatrix.png'))


def _plot_effect(tests, keys, name, out_dir):
    """N×N signed rank-biserial effect-size heatmap (row vs col)."""
    R = np.asarray(tests['rank_biserial'], float)
    k = len(keys)
    fig, ax = plt.subplots(figsize=(0.8 * k + 3, 0.8 * k + 2))
    im = ax.imshow(R, cmap='RdBu_r', vmin=-1, vmax=1)
    for i in range(k):
        for j in range(k):
            if i != j and np.isfinite(R[i, j]):
                ax.text(j, i, f'{R[i, j]:.2f}', ha='center', va='center',
                        color='black', fontsize=7)
    ax.set_xticks(range(k)); ax.set_yticks(range(k))
    ax.set_xticklabels([_short(x) for x in keys], rotation=40, ha='right', fontsize=7)
    ax.set_yticklabels([_short(x) for x in keys], fontsize=7)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label('rank-biserial (row − col)')
    ax.set_title(f'{name} — pairwise effect size\n(+ ⇒ row larger than col)',
                 fontsize=10, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_effect.png'))


def _plot_rank(tests, keys, colors, name, out_dir):
    """Mean Friedman rank per strategy (lower = better)."""
    mr = tests.get('mean_rank', {})
    items = [(k, mr.get(k)) for k in keys if mr.get(k) is not None and np.isfinite(mr.get(k))]
    if not items:
        return
    items.sort(key=lambda kv: kv[1])
    cmap = {k: c for k, c in zip(keys, colors)}
    labels = [_short(k) for k, _ in items]
    vals = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(items)), 4.5))
    ax.bar(range(len(labels)), vals, color=[cmap[k] for k, _ in items], alpha=0.8)
    ax.set_ylabel('mean rank (1 = best)')
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=40, ha='right', fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    ax.set_title(f'{name} — mean Friedman rank (lower is better)',
                 fontsize=11, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_rank.png'))


# ── metric extraction (per-config) ───────────────────────────────────────────────

def _metric_series(df_b_k, df_t_k, source, col, ss_lo):
    """Per-batch steady-state Series (indexed by batch_id) for one strategy/metric."""
    if source == 'batch':
        d = df_b_k[df_b_k['batch_id'] >= ss_lo]
        if d.empty or col not in d:
            return pd.Series(dtype=float)
        return d.set_index('batch_id')[col]
    d = df_t_k[df_t_k['batch_id'] >= ss_lo]
    if d.empty or col not in d:
        return pd.Series(dtype=float)
    g = d.groupby('batch_id')[col]
    return g.mean() if source == 'task_mean' else g.sum()


def _aligned(series_by_key, keys):
    """Intersect batch indices common to all strategies → paired matrix (n × k)."""
    idxs = [set(series_by_key[k].index) for k in keys if len(series_by_key[k])]
    if len(idxs) != len(keys) or not idxs:
        return None
    common = sorted(set.intersection(*idxs))
    if len(common) < 3:
        return None
    return np.column_stack([series_by_key[k].loc[common].values for k in keys])


# ── public API ────────────────────────────────────────────────────────────────────

def run_config_stats(strategies, df_b, df_t, ss_lo, out_dir, log,
                     travel_handling=None) -> None:
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


def run_aggregate_stats(profile_series_list, out_dir, log, pickcfg) -> None:
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


# ── compare BY INITIAL assignment (hold the assignment function constant) ─────────
# Instead of comparing assignment functions within one initial family, these pair
# uni_<fn> vs opt_<fn> for each assignment function <fn> — answering "does the optimal
# initial layout beat a uniform start for the SAME reorder policy?".

def _assignment_fn(key: str) -> str:
    """Strip the leading initial-family token ('uni_'/'opt_') → the assignment+reslot key
    that identifies the function held constant (e.g. uni_rank_labor_norsl → rank_labor_norsl)."""
    for p in ('uni_', 'opt_'):
        if key.startswith(p):
            return key[len(p):]
    return key


def _initial_of(key: str) -> str | None:
    if key.startswith('opt_'):
        return 'opt'
    if key.startswith('uni_'):
        return 'uni'
    return None


def _group_by_assignment(keys: list[str]) -> dict:
    """{assignment_fn: {'uni': key, 'opt': key}} for keys that carry an initial token,
    preserving first-seen function order."""
    groups: dict[str, dict] = {}
    for k in keys:
        ini = _initial_of(k)
        if ini is None:
            continue
        groups.setdefault(_assignment_fn(k), {})[ini] = k
    return groups


def _opt_better(uni_med: float, opt_med: float, lower: bool) -> str:
    """'opt' if the optimal-initial median is the better one for this metric, else 'uni'."""
    if not (np.isfinite(uni_med) and np.isfinite(opt_med)) or uni_med == opt_med:
        return 'tie'
    opt_wins = (opt_med < uni_med) if lower else (opt_med > uni_med)
    return 'opt' if opt_wins else 'uni'


def run_config_stats_by_initial(strategies, df_b, df_t, ss_lo, out_dir, log,
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
        run_config_stats(pair_strats, df_b, df_t, ss_lo,
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


def run_aggregate_stats_by_initial(profile_series_list, out_dir, log, pickcfg) -> None:
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
        run_aggregate_stats(sub_list, os.path.join(out_dir, fn), log, pickcfg)
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
