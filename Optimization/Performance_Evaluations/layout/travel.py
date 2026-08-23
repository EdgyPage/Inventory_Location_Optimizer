"""layout.travel — travel cost per arm against the baseline, the layout family's
comparison view.

The layout family owned only the churn overlay, and churn is flat (often exactly zero
when reslotting is off), so the family that should answer "did this placement rule
actually shorten the walking?" answered nothing.  The quantity that does differentiate
arms — Σ f·D, demand-weighted distance, the objective the assignment functions optimise —
was reachable only through a diagnostics small-multiples grid, and diagnostics is the
family explicitly exempted from comparison work.

Two views of one quantity, both against the FIFO baseline:

  * delta   — per-arm % improvement in steady-state Σ f·D, sorted, with the bootstrap
              interval of the per-batch improvements, so the ranking says which gaps are
              separable and which are not.
  * absolute — the same arms' Σ f·D beside the layout floor when the run publishes one
              (`optimal_sigma_fd`), which turns a unitless objective into "how much of
              the achievable improvement did this rule capture".
"""
import os

import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.stats_core import _boot_ci
from Optimization.Performance_Evaluations.common.style import _stitle


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


def _rows(ctx, S, baseline):
    """[(strategy, pct improvement, lo, hi, ss_sigma)] against the baseline arm."""
    pb = _per_batch(ctx, baseline['key'])
    out = []
    for s in ctx.strategies:
        d = S.get(s['key'])
        if d is None or s['key'] == baseline['key']:
            continue
        ps = _per_batch(ctx, s['key'])
        common = sorted(set(pb.index) & set(ps.index))
        if len(common) < 3:
            continue
        diffs = chartkit.improvement_pct_series(
            ps.loc[common].values, pb.loc[common].values, lower_is_better=True)
        if not diffs.size:
            continue
        lo, hi = _boot_ci(diffs)
        out.append((s, float(np.median(diffs)), lo, hi, d.get('ss_sigma')))
    out.sort(key=lambda r: (-r[1] if np.isfinite(r[1]) else np.inf))
    return out


def _delta_figure(ctx, rows, out):
    labels = [_stitle(s) for s, *_r in rows]
    ch = chartkit.make(
        panels=1, legend='none', panel_w=7.0,
        panel_h=chartkit.height_for_categories(len(rows), per=0.3, base=2.0))
    ax = ch.ax
    ys = np.arange(len(rows))
    for y, (s, pct, lo, hi, _ss) in zip(ys, rows):
        # the interval runs along X here (one row per arm), so it is drawn directly
        # rather than through draw_ci, whose whiskers are vertical
        if np.isfinite(lo) and np.isfinite(hi):
            ax.plot([lo, hi], [y, y], color='#666666', lw=1.2, zorder=2)
            for end in (lo, hi):
                ax.plot([end, end], [y - 0.16, y + 0.16], color='#666666', lw=1.0,
                        zorder=2)
        ax.scatter([pct], [y], s=38, color=chartkit.strategy_color(s, ctx.strategies),
                   edgecolors='black', linewidths=0.5, zorder=3)
    ax.axvline(0, **{**chartkit.BASELINE_STYLE, 'lw': 1.2})
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_ylim(-0.8, len(rows) - 0.2)
    ax.invert_yaxis()
    tag = chartkit.pct_axis(ax, better='up', axis='x')
    ax.set_xlabel(f'travel cost vs FIFO {tag}')
    vals = [v for _s, v, _lo, _hi, _ss in rows] + \
           [v for _s, _p, v, _hi, _ss in rows if np.isfinite(v)] + \
           [v for _s, _p, _lo, v, _ss in rows if np.isfinite(v)]
    if vals:
        lo_x, hi_x = min(vals + [0.0]), max(vals + [0.0])
        pad = (hi_x - lo_x) * 0.12 or 1.0
        ax.set_xlim(lo_x - pad, hi_x + pad)
    ch.title('Travel cost vs FIFO — demand-weighted distance',
             'median of the per-batch improvements, with its bootstrap interval')
    return ch.save(os.path.join(out, 'delta_travel_vs_baseline.png'), view='delta')


def _absolute_figure(ctx, rows, baseline, S, out):
    # `ctx.optimal` — NOT `optimal_sigma_fd`, which is the key inside `sim_result` and not
    # an attribute of the context.  The misspelling made `floor` unconditionally None, so
    # the layout-floor line this module's docstring promises has never once been drawn.
    # The context coerces the value to 0.0 when the run publishes none, which is why the
    # `> 0` guard below is the right absence test rather than an `is not None`.
    floor = getattr(ctx, 'optimal', None)
    entries = [(baseline, S[baseline['key']].get('ss_sigma'))] + \
              [(s, ss) for s, _p, _lo, _hi, ss in rows]
    entries = [(s, float(v)) for s, v in entries
               if v is not None and np.isfinite(float(v))]
    if not entries:
        return None
    labels = [_stitle(s) for s, _v in entries]
    ch = chartkit.make(
        panels=1, legend='none', panel_w=7.0,
        panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
    ax = ch.ax
    ys = np.arange(len(entries))
    for y, (s, v) in zip(ys, entries):
        is_base = s['key'] == baseline['key']
        ax.barh([y], [v], height=0.72, zorder=2,
                color=(chartkit.BASELINE_STYLE['color'] if is_base
                       else chartkit.strategy_color(s, ctx.strategies)))
    if floor is not None and np.isfinite(float(floor)) and float(floor) > 0:
        ax.axvline(float(floor), color='#1a7a4d', lw=1.6, ls='--', zorder=3,
                   label='layout floor (best achievable)')
        ax.text(float(floor), -0.75, ' layout floor', color='#1a7a4d', fontsize=7,
                ha='left', va='bottom')
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_ylim(-0.9, len(entries) - 0.3)
    ax.invert_yaxis()
    # Bars are anchored at zero on purpose: a truncated value axis on a BAR chart
    # misstates the ratio between bars, which is the one thing bars are read for.  The
    # comparison view beside this one is where the small differences are resolved.
    ax.set_xlabel('steady-state Σ f·D (lower = better)')
    ch.title('Travel cost per arm', 'FIFO baseline in black')
    return ch.save(os.path.join(out, 'absolute_travel_per_arm.png'), view='absolute')


@evaluation(key='layout.travel', label='Travel cost vs baseline (delta + absolute)',
            scope='config', needs=('batch', 'series'),
            family='layout', views=('delta', 'absolute'))
def render(ctx, params):
    S = ctx.series()
    baseline = ctx.base
    if S.get(baseline['key']) is None:
        ctx.log.warning('  layout travel: no baseline series; skipped')
        return
    rows = _rows(ctx, S, baseline)
    if not rows:
        ctx.log.warning('  layout travel: no arm shares enough batches with the baseline')
        return
    out = io.out_dir(ctx)
    _delta_figure(ctx, rows, out)
    _absolute_figure(ctx, rows, baseline, S, out)
    ctx.log.info(f'  layout travel: 2 figures over {len(rows)} arms -> {out}')
