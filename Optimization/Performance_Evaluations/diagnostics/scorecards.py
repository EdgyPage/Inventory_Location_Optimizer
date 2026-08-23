"""diagnostics.scorecards — one three-panel read-out per arm.

Batch duration (hours, rolling mean) · layout efficiency (optimal ÷ realised total
f·D when the optimal floor is known, raw f·D otherwise) · inventory churn (% of bins
moved per batch).  Ported from the retired per-strategy scorecards; the shared panel
helpers that module imported are inlined here (their home module is gone).  A raw
operational read-out for inspecting one arm in isolation — the diagnostics exemption
in the family grammar.
"""
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.frames import _roll
from Optimization.Performance_Evaluations.common.style import _stitle, _WIN


# ── panel series (the retired shared helpers, inlined) ──────────────────────────

def _eff_series(df, optimal):
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    if optimal > 0:
        y = (optimal / d['sigma_fd'].clip(lower=1e-9) * 100.0).rolling(
            _WIN, min_periods=1).mean()
    else:
        y = d['sigma_fd'].rolling(_WIN, min_periods=1).mean()
    return d['batch_id'].values, y.values


def _churn_series(df, total_bins):
    if df.empty:
        return None
    d = df.sort_values('batch_id')
    moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
    if total_bins > 0:
        moved = moved / total_bins * 100.0
    return d['batch_id'].values, moved.rolling(_WIN, min_periods=1).mean().values


@evaluation(key='diagnostics.scorecards', label='Per-arm scorecards',
            scope='per_strategy', needs=('batch',),
            family='diagnostics', views=('absolute',))
def render(ctx, params):
    out = io.out_dir(ctx)
    optimal = ctx.optimal
    for s in ctx.strategies:
        df = ctx.batch_df(s['key'])
        if df.empty:
            continue
        color = chartkit.strategy_color(s, ctx.strategies)
        ch = chartkit.make(panels=3, ncols=3, panel_w=4.2, panel_h=3.0,
                           legend='none')
        a1, a2, a3 = ch.axes

        d = df.sort_values('batch_id')
        a1.plot(d['batch_id'].values, chartkit.to_hours(_roll(df, 'duration', _WIN)),
                color=color, lw=1.2)
        a1.set_title('Batch duration', fontsize=9)
        a1.set_ylabel(f'{chartkit.HOURS} (rolling mean)', fontsize=7)

        eff = _eff_series(df, optimal)
        if eff is not None:
            a2.plot(eff[0], eff[1], color=color, lw=1.2)
            if optimal > 0:
                a2.axhline(100.0, color='grey', lw=0.7, ls='--')
        a2.set_title('Total f·D ' + ('(% of optimal)' if optimal > 0 else '(raw)'),
                     fontsize=9)
        a2.set_ylabel('% of optimal' if optimal > 0 else 'total f·D', fontsize=7)

        churn = _churn_series(df, ctx.total_bins)
        if churn is not None:
            a3.plot(churn[0], churn[1], color=color, lw=1.2)
        a3.set_title('Churn', fontsize=9)
        a3.set_ylabel('% of bins moved / batch' if ctx.total_bins > 0
                      else 'bins moved / batch', fontsize=7)

        for ax in ch.axes:
            ax.set_xlabel('batch', fontsize=7)
            ax.tick_params(labelsize=6)
        ch.legend()
        ch.title(f'Scorecard — {_stitle(s)}')
        ch.save(os.path.join(out, f"absolute_scorecard_{s['key']}.png"),
                view='absolute')
