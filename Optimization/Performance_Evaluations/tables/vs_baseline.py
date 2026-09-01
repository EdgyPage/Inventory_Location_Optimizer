"""tables.vs_baseline — every arm against the baseline, as auditable numbers.

The headline table figure prints the claim the whole study turns on: this arm saves this
much labor against FIFO, with this effect size, this interval and this p.  Those numbers
existed only as pixels.  The by-initial documents beside them answer a DIFFERENT question
(optimal vs uniform placement WITHIN one assignment function), so a reader who tried to
check a headline figure against the tables would find no row for it — and the docs site's
rule that every published number cites a committed file could not be satisfied for the
most important numbers in the run.

One row per (strategy, metric), each carrying the same quantities the headline figure
shows and computed by the same rules:

    pct_median   the median of the per-batch improvements, improvement-oriented
                 (positive = better), which is the figure's point estimate
    ci_lo/ci_hi  the bootstrap interval OF THAT MEDIAN — same estimator, so the
                 interval always contains the point
    hedges_g     the paired effect size on the raw per-batch values
    rank_biserial  its distribution-free companion
    p_wilcoxon / p_holm   raw and family-corrected across arms within a metric
    n_batches    the paired batches the two arms actually share

Every arm appears, not just the podium: the figure shows the top N, and a reader
checking why an arm did NOT make the podium needs its row too.
"""
import csv
import os

import numpy as np
import scipy.stats as st

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.frames import _metric_series
from Optimization.Performance_Evaluations.common.stats_core import (
    _METRICS, _boot_ci, _hedges_g_paired, _holm, _rank_biserial)

_FIELDS = ('strategy', 'initial', 'assignment', 'reslot', 'metric', 'n_batches',
           'baseline_median', 'arm_median', 'pct_median', 'ci_lo', 'ci_hi',
           'hedges_g', 'rank_biserial', 'p_wilcoxon', 'p_holm', 'better')


def _paired_values(ctx, key, source, col):
    return _metric_series(ctx.metric_frames_for(key), source, col, 0)


def compute_vs_baseline(ctx):
    """[row dicts] — every arm against ctx.base, over every declared metric."""
    base = ctx.base
    rows = []
    for name, source, col, lower in _METRICS:
        pb = _paired_values(ctx, base['key'], source, col)
        per_metric = []
        for s in ctx.strategies:
            if s['key'] == base['key']:
                continue
            ps = _paired_values(ctx, s['key'], source, col)
            common = sorted(set(pb.index) & set(ps.index))
            if len(common) < 3:
                continue
            b = pb.loc[common].values.astype(float)
            v = ps.loc[common].values.astype(float)
            diffs = chartkit.improvement_pct_series(v, b, lower_is_better=lower)
            if not diffs.size:
                continue
            lo, hi = _boot_ci(diffs)
            try:
                p = float(st.wilcoxon(v, b).pvalue) if np.any(v != b) else 1.0
            except ValueError:
                p = float('nan')
            # BOTH effect sizes are oriented like the percentage — positive = the arm is
            # better, whichever direction "better" runs for this metric.  Leaving either
            # in its raw sign prints an improvement of +0.05% beside an effect of -0.70
            # in the same row, and a reader cannot tell which of them means good.
            rb = _rank_biserial(b, v) if lower else _rank_biserial(v, b)
            g = _hedges_g_paired(b, v) if lower else _hedges_g_paired(v, b)
            med = float(np.median(diffs))
            per_metric.append(dict(
                strategy=s['key'], initial=s.get('initial', ''),
                assignment=s.get('assignment', ''), reslot=s.get('reslot', ''),
                metric=name, n_batches=len(common),
                baseline_median=float(np.median(b)), arm_median=float(np.median(v)),
                pct_median=med, ci_lo=lo, ci_hi=hi,
                hedges_g=g, rank_biserial=rb,
                p_wilcoxon=p, p_holm=float('nan'),
                better=('arm' if med > 0 else 'baseline' if med < 0 else 'tie')))
        # Holm across the ARMS within one metric: that is the family of comparisons a
        # reader makes when they scan a metric's column looking for a winner.
        if per_metric:
            adj = _holm(np.asarray([r['p_wilcoxon'] for r in per_metric], dtype=float))
            for r, pa in zip(per_metric, adj):
                r['p_holm'] = float(pa)
        rows.extend(per_metric)
    return rows


@evaluation(key='tables.vs_baseline', label='Every arm vs the baseline (auditable numbers)',
            scope='config', needs=('batch', 'task'), out_subdir='tables')
def render(ctx, params):
    rows = compute_vs_baseline(ctx)
    if not rows:
        ctx.log.warning('  vs-baseline table: no arm shares enough batches with the baseline')
        return
    path = os.path.join(io.out_dir(ctx), 'vs_baseline.csv')
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(_FIELDS))
        w.writeheader()
        w.writerows(rows)
    ctx.log.info(f'  vs-baseline table: {len(rows)} rows -> {path}')
