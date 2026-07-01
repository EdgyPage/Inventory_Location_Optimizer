"""Statistical machinery for the significance suite (verbatim from the retired
Stats_Analysis): metric specs, multiple-comparison correction, paired effect sizes,
descriptives, the omnibus+pairwise test runner, and the by-initial grouping helpers.

The four plot primitives that consume these results live in stats/plots.py.
"""
from __future__ import annotations

import math

import numpy as np
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
    ('production_time',    'task_sum',  'duration',           True),   # PRIMARY: Σ task time/batch (sim units)
    # Analytical objective: E[labor of a random task] = mean task W (D+P+C), and its sum
    # (total analytical labor).  Scored from task structure, so robust to sim wall-timing
    # noise / reorder starvation — the direct yardstick for the slotting objective.
    ('objective_task_labor', 'task_mean', 'W',                True),   # E[task labor] (objective)
    ('objective_total_labor', 'task_sum', 'W',                True),   # Σ analytical task labor
    ('task_mean_duration', 'task_mean', 'duration',           True),
    ('makespan',           'batch',     'duration',           True),   # wall-time (parallel)
    ('throughput',         'batch',     'completion_rate',    False),
    ('queue_depth',        'batch',     'queue_depth',        True),   # put-away backlog (honesty)
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


def _run_tests(M: np.ndarray, keys: list, lower_better: bool) -> dict:
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


def _initial_of(key: str):
    if key.startswith('opt_'):
        return 'opt'
    if key.startswith('uni_'):
        return 'uni'
    return None


def _group_by_assignment(keys: list) -> dict:
    """{assignment_fn: {'uni': key, 'opt': key}} for keys that carry an initial token,
    preserving first-seen function order."""
    groups: dict = {}
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
