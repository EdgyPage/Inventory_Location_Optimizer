"""layout.travel — travel cost per arm against the baseline, the layout family's
comparison work.

The layout family owned only the churn overlay, and churn is flat (often exactly zero
when reslotting is off), so the family that should answer "did this placement rule
actually shorten the walking?" answered nothing.  The quantity that does differentiate
arms — Σ f·D, demand-weighted distance, the objective the assignment functions optimise —
was reachable only through a diagnostics small-multiples grid, and diagnostics is the
family explicitly exempted from comparison work.

Two views of one quantity, and the set is DERIVED (`ranked` mark × `sigma_fd`), not
chosen here.  A ranked mark's categories are ARMS, which do not pair with themselves, so
the derivation gives no delta — the per-batch difference lives with the serial marks:

  absolute  each arm's steady-state Σ f·D beside the layout floor when the run publishes
            one, which turns a unitless objective into "how much of the achievable
            improvement did this rule capture".
  percent   per-arm improvement against the baseline, sorted, with the bootstrap interval
            of the arm's own per-batch spread, so the ranking says which gaps are
            separable and which are not.

**The percent view used to be called `delta`.**  It computed `improvement_pct_series`,
labelled its axis through `pct_axis`, and saved as `delta_travel_vs_baseline.png` under
`view='delta'` — a percent under a delta's name, which five save-time checks passed
because every one of them compared a declaration against another declaration and none of
them ever saw a number.  It was not carelessness: the `layout` family's allow-list had no
`percent` in it, so there was nowhere honest to put the figure.  Both halves are fixed —
the view set comes from the quantity and the mark, and `common/marks.py` computes the
stance from raw values, so the filename and the arithmetic come from one decision.
"""
import os

import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.common.stats_core import _boot_ci
from Optimization.Performance_Evaluations.core import quantities as _q

#: the one quantity this evaluation draws
QUANTITY = 'sigma_fd'

_TITLES = {
    'absolute': ('Travel cost per arm',
                 'steady-state demand-weighted distance; baseline shaded'),
    'percent':  ('Travel cost vs baseline',
                 'per-arm improvement in Σ f·D, with the bootstrap interval of the '
                 "arm's own per-batch spread"),
}


def _per_batch(ctx, key):
    """Σ f·D per batch for one arm, indexed by batch.

    Read straight off the batch frame rather than through the generic metric-series
    helper: that helper takes a task frame it does not consult for a batch-scoped
    column, and asking the broker for the task frame would both cost a real read per arm
    and make this evaluation's `needs=` declaration a lie.
    """
    d = ctx.batch_df(key)
    if d is None or d.empty or 'sigma_fd' not in d:
        return pd.Series(dtype=float)
    return d.set_index('batch_id')['sigma_fd'].astype(float)


def _entries(ctx, S):
    """[(strategy, steady-state Σ f·D)] and the matching raw intervals, in Σ f·D.

    The interval is the bootstrap of the arm's own per-batch values rather than of the
    improvement, because `marks` derives the stance itself and therefore takes every input
    in the quantity's own units — which is also what stops the retired failure of
    converting the point and leaving the whiskers behind.  An arm with fewer than three
    batches carries a value and no interval.
    """
    entries, intervals = [], []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        val = d.get('ss_sigma')
        if val is None or not np.isfinite(float(val)):
            continue
        entries.append((s, float(val)))
        pb = _per_batch(ctx, s['key'])
        intervals.append(_boot_ci(pb.values) if pb.size >= 3 else None)
    return entries, intervals


def _figure(ctx, entries, intervals, baseline, view, out):
    q = _q.BY_KEY[QUANTITY]
    ch = chartkit.make(
        panels=1, legend='none', panel_w=7.0,
        panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
    if not marks.ranked(ch, entries, quantity=q, view=view, baseline=baseline,
                        strategies=ctx.strategies, intervals=intervals):
        return ch.abandon()
    if view == 'absolute':
        # `ctx.optimal` — NOT `optimal_sigma_fd`, which is the key inside `sim_result` and
        # not an attribute of the context.  The misspelling made this None for the whole
        # life of the module, so the layout floor was never once drawn.  The context
        # coerces the value to 0.0 when the run publishes none, which is why `> 0` is the
        # right absence test rather than `is not None`.
        floor = getattr(ctx, 'optimal', None)
        if floor is not None and np.isfinite(float(floor)) and float(floor) > 0:
            chartkit.reference_line(ch.ax, float(floor), orient='x',
                                    label='layout floor', style=chartkit.BOUND_STYLE,
                                    legend_label='layout floor (best achievable)')
    title, sub = _TITLES[view]
    ch.title(title, sub)
    return ch.save(os.path.join(out, f'{view}_travel_per_arm.png'), view=view)


@evaluation(key='layout.travel', label='Travel cost per arm vs the baseline',
            scope='config', needs=('batch', 'series'),
            family='layout', shape='ranked', quantities=(QUANTITY,))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    S = ctx.series()
    baseline = ctx.base
    if S.get(baseline['key']) is None:
        ctx.log.warning('  layout travel: no baseline series; skipped')
        return
    entries, intervals = _entries(ctx, S)
    if not entries:
        ctx.log.warning('  layout travel: no arm published a steady-state Σ f·D')
        return
    out = io.out_dir(ctx)
    n = 0
    for view in EVAL_BY_KEY['layout.travel'].views:
        n += bool(_figure(ctx, entries, intervals, baseline, view, out))
    ctx.log.info(f'  layout travel: {n} views over {len(entries)} arms -> {out}')
