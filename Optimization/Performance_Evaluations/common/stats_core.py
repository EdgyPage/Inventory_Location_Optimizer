"""Statistical machinery for the significance suite (verbatim from the retired
Stats_Analysis): metric specs, multiple-comparison correction, paired effect sizes,
descriptives, the omnibus+pairwise test runner, and the by-initial grouping helpers.

The four plot primitives that consume these results live in stats/plots.py.
"""
from __future__ import annotations

import math

import numpy as np
import scipy.stats as st

from Optimization.Performance_Evaluations.core import quantities as _quantities


# ── metric specs ────────────────────────────────────────────────────────────────
# DERIVED, not declared.  Both tables come from `core/quantities.py`, which is the one
# place a measurable thing is described — unit, label, direction, and where to read it at
# each scope.  They were literals here for as long as this module existed, and the cost of
# that was four competing tables downstream that had already drifted apart on labels.
#
# The shapes are unchanged and are what every consumer in this package iterates:
#   _METRICS      (name, source, column, lower_is_better)
#                 source: 'batch'     -> per-batch column of df_b
#                         'task_mean' -> per-batch mean of a df_t column
#                         'task_sum'  -> per-batch sum  of a df_t column
#   _AGG_METRICS  (name, ss_field, lower_is_better) — the cross-profile steady-state
#                 scalars read out of a profile's series document
#
# ORDER IS THE ROW ORDER OF PUBLISHED CSVs and is pinned in the quantity table (see
# `QUANTITIES` for the per-batch order, `AGGREGATE_ORDER` for the cross-profile one).
# `Tests/unit/test_quantities.py` asserts both derived tuples equal the literals they
# replaced, element for element.
_METRICS = _quantities.metric_specs()
_AGG_METRICS = _quantities.aggregate_specs()


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


# ── resampled intervals ──────────────────────────────────────────────────────────
#
# A published point estimate and the interval printed beside it MUST be the same
# estimator, or the interval can exclude its own point — which reads as an arithmetic
# error and invites a reader to quote whichever number is larger.  The t-interval in
# `_descriptives` only serves a mean; this suite reports MEDIANS wherever a per-batch
# ratio is involved (one near-empty batch makes the mean of a ratio unstable), so the
# median needs an interval of its own.  A percentile bootstrap gives one for any
# statistic, and a fixed seed keeps it as reproducible as the rest of the pipeline.

_BOOT = 2000


def _block_len(n: int) -> int:
    """Moving-block length for a series of n observations — the standard n^(1/3) rule."""
    return int(max(1, min(n // 2, round(n ** (1.0 / 3.0)))))


def _moving_block_index(rng, n: int, n_boot: int, block: int) -> np.ndarray:
    """(n_boot, n) resampling index built from overlapping blocks of `block` batches."""
    if block <= 1:
        return rng.integers(0, n, size=(n_boot, n))
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)
    return idx[:, :n]


def _boot_ci(sample, stat=np.median, *, n_boot: int = _BOOT, seed: int = 0,
             alpha: float = 0.05, block: int | None = None) -> tuple:
    """(lo, hi) MOVING-BLOCK bootstrap interval for `stat` over a per-batch series.

    Blocks, not independent draws.  These samples are per-batch differences from one
    continuous run: each batch inherits the previous batch's layout, so the differences
    carry serial correlation — measured on this design, lag-1 through lag-3 sit outside
    the +/-2/sqrt(n) white-noise band for some arms.  Resampling batches independently
    would treat 75 correlated observations as 75 independent ones and report an interval
    narrower than the data earns.  Resampling contiguous blocks keeps the local
    dependence intact; with no correlation present the block length collapses toward the
    i.i.d. case and costs nothing.

    Deterministic by construction: a seeded Generator, never the global RNG.  NaN pair
    when fewer than 3 finite observations survive — the same floor the paired tests use.
    """
    a = np.asarray(sample, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 3:
        return float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    idx = _moving_block_index(rng, a.size, n_boot,
                              _block_len(a.size) if block is None else block)
    draws = stat(a[idx], axis=1)
    lo, hi = np.percentile(draws, [alpha / 2 * 100, (1 - alpha / 2) * 100])
    return float(lo), float(hi)


def _lag1_autocorr(sample) -> float:
    """Lag-1 autocorrelation of a series; NaN when it cannot be computed.

    Reported beside an interval so a reader can see WHY the interval is block-based:
    compare it against the white-noise band 2/sqrt(n).
    """
    x = np.asarray(sample, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 4 or x.var() == 0:
        return float('nan')
    x = x - x.mean()
    return float(np.dot(x[:-1], x[1:]) / np.dot(x, x))


def _rank_biserial_ci(a, b, *, n_boot: int = _BOOT, seed: int = 0,
                      alpha: float = 0.05) -> tuple:
    """(lo, hi) for the matched-pairs rank-biserial, resampling the PAIRS.

    The obvious closed form — the signed-rank null variance — depends only on n, so it
    hands every row of a panel an identical whisker and is widest exactly where the true
    uncertainty is smallest (an arm that wins every batch has r = 1 with almost no
    sampling error).  Resampling the pairs lets the data set the width.
    """
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 3:
        return float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    idx = _moving_block_index(rng, x.size, n_boot, _block_len(x.size))
    draws = np.array([_rank_biserial(x[i], y[i]) for i in idx], dtype=float)
    lo, hi = np.percentile(draws, [alpha / 2 * 100, (1 - alpha / 2) * 100])
    return float(max(-1.0, lo)), float(min(1.0, hi))


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


# ── the comparison census ────────────────────────────────────────────────────────
#
# WHY THIS EXISTS.  The published pages keep making one shape of claim — "positive in
# all 68 same-rule comparisons", "median +44.8%, range 28.5-39.8%" — and until now every
# instance was hand-counted at the call site and restated in prose, so a reader could
# only check it by recomputing.  This is that count, generalised: ONE reduction over any
# flat comparison row set, grouped by any key, emitted as an artifact the page renders.
# The claim then IS the artifact instead of a transcription of it.
#
# Public name, unlike its underscore-prefixed neighbours, because the what-if writers at
# Optimization/ root consume it from outside this package.

def _sign_test(n_pos: int, n_neg: int, alpha: float = 0.05) -> tuple:
    """(p, p_floor, ci_lo, ci_hi) for "more comparisons land in the claimed direction".

    Exact binomial against p=0.5, one-sided (the claim is directional), with a
    Clopper-Pearson interval on the win rate.  Ties are excluded from the denominator
    here — the standard sign-test convention — and reported separately by the caller so
    the exclusion is visible rather than assumed.

    `p_floor` is 0.5**n: the SMALLEST p this many comparisons can produce, reached only by
    a clean sweep.  It is returned because without it a small group is silently
    unfalsifiable — at n=4 the floor is 0.0625, so a rule that went the claimed way in
    every single comparison still prints "not significant" against a conventional
    threshold, and a reader scanning the column would draw the opposite conclusion from
    the one the data supports.  Publish the floor beside the p and the reader can see when
    the test, rather than the effect, ran out of room.

    (nan, …) when nothing is left to test: an all-ties group has no direction to have been
    right about, and reporting p=1 there would read as evidence of no effect.
    """
    n = n_pos + n_neg
    if n == 0:
        return float('nan'), float('nan'), float('nan'), float('nan')
    res = st.binomtest(n_pos, n, 0.5, alternative='greater')
    ci = res.proportion_ci(confidence_level=1.0 - alpha, method='exact')
    return float(res.pvalue), float(0.5 ** n), float(ci.low), float(ci.high)


def _pluck(row, spec):
    """Read `spec` off `row` — a mapping key, an attribute, or a callable applied to it."""
    if callable(spec):
        return spec(row)
    if isinstance(row, dict):
        return row.get(spec)
    return getattr(row, spec, None)


def census(rows, *, value, group_by=(), better='higher', alpha: float = 0.05) -> list:
    """Count/span evidence for a directional claim, over any flat comparison row set.

    One dict per group plus a trailing overall row (``group={}``), each carrying:

        n n_pos n_neg n_zero n_nan     the population and how it splits
        win_rate                       n_pos / (n_pos + n_neg), ties excluded
        p_sign ci_lo ci_hi             exact one-sided sign test + Clopper-Pearson
        p_floor                        the smallest p this n could ever reach (0.5**n) —
                                       read `p_sign` against it, not against 0.05
        median q1 q3 iqr min max       the span, over the FINITE values

    `value` may be a mapping key, an attribute name, or a callable taking the row.  Each
    member of `group_by` is a NAME (used as the ``group_<name>`` column) or a
    ``(name, callable)`` pair when the group is derived rather than stored — a bare
    callable is refused, because a group column with no name cannot be written to a CSV
    or looked up from a page.  `better` fixes the sign convention — with ``'lower'`` a
    negative value counts as positive evidence — so `n_pos` always means "in the claimed
    direction" no matter which way the underlying metric points.  That is the whole
    reason this takes a direction rather than leaving it to the caller: a census whose
    sign convention lives at the call site is exactly the hand-rolled count it replaces.

    NaNs are dropped from every statistic and counted in `n_nan`; zeros are counted in
    `n_zero`, kept in the span, and excluded from the sign test.  `n` is the finite
    count, so ``n == n_pos + n_neg + n_zero``.
    """
    if better not in ('higher', 'lower'):
        raise ValueError(f"better must be 'higher' or 'lower', got {better!r}")
    sign = 1.0 if better == 'higher' else -1.0
    specs = []
    for g in group_by:
        name, getter = g if isinstance(g, tuple) else (g, g)
        if not isinstance(name, str):
            raise ValueError(f'group_by needs a name: pass (name, callable), got {g!r}')
        specs.append((name, getter))
    keys = tuple(name for name, _ in specs)

    buckets: dict = {}
    order: list = []
    for row in rows:
        g = tuple(_pluck(row, getter) for _, getter in specs)
        if g not in buckets:
            buckets[g] = []
            order.append(g)
        buckets[g].append(_pluck(row, value))

    out = []
    for g in order + ([()] if keys else []):
        vals = buckets[g] if g in buckets else [v for b in buckets.values() for v in b]
        # Every row carries every group column — None marks the overall row — so the
        # result drops straight into a DictWriter without a heterogeneous-keys dance.
        group = {k: None for k in keys}
        group.update(zip(keys, g))
        out.append(_census_one(group, vals, sign, alpha))
    return out


def _census_one(group: dict, values, sign: float, alpha: float) -> dict:
    raw = np.asarray([np.nan if v is None else v for v in values], dtype=float)
    n_nan = int((~np.isfinite(raw)).sum())
    a = raw[np.isfinite(raw)] * sign
    n_pos = int((a > 0).sum())
    n_neg = int((a < 0).sum())
    n_zero = int((a == 0).sum())
    p, p_floor, lo, hi = _sign_test(n_pos, n_neg, alpha)
    n = int(a.size)
    # The span is reported in the ORIGINAL orientation — a reader comparing it against a
    # number on the page must see the same sign the page shows.
    o = a * sign
    return dict(
        **{f'group_{k}': v for k, v in group.items()},
        n=n, n_pos=n_pos, n_neg=n_neg, n_zero=n_zero, n_nan=n_nan,
        win_rate=(n_pos / (n_pos + n_neg)) if (n_pos + n_neg) else float('nan'),
        p_sign=p, p_floor=p_floor, ci_lo=lo, ci_hi=hi,
        median=float(np.median(o)) if n else float('nan'),
        q1=float(np.percentile(o, 25)) if n else float('nan'),
        q3=float(np.percentile(o, 75)) if n else float('nan'),
        iqr=float(np.percentile(o, 75) - np.percentile(o, 25)) if n else float('nan'),
        min=float(o.min()) if n else float('nan'),
        max=float(o.max()) if n else float('nan'),
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
