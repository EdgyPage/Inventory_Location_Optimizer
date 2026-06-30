"""agg.cross_profile — cross-profile roll-up for one pick-config: the same faceted/overlay/
top/breakdown over-time suite as per-config, but on baseline-normalized curves averaged
across profiles.  Reuses the comparison builders.  Params: top_n, top_by."""
import os

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.comparison import overtime_metrics, top_tag
from Performance_Evaluations.comparison.faceted import _facet_metric
from Performance_Evaluations.comparison.overlay import _overlay_metric
from Performance_Evaluations.comparison.top_metric import _top_metric
from Performance_Evaluations.comparison.pick_vs_travel import _pick_travel_bars
from Performance_Evaluations.comparison.delta_bars import _delta_bars


@evaluation(key='agg.cross_profile', label='Cross-profile over-time suite',
            scope='aggregate', needs=('series',), out_subdir='',
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    strategies, S = ctx.agg_series()
    if not strategies:
        ctx.log.warning(f'  aggregate {ctx.pickcfg}: no usable series')
        return
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'

    fac = os.path.join(ctx.out_dir, 'faceted')
    ovl = os.path.join(ctx.out_dir, 'overlay')
    top = os.path.join(ctx.out_dir, 'top')
    brk = os.path.join(ctx.out_dir, 'breakdown')
    for d in (fac, ovl, top, brk):
        os.makedirs(d, exist_ok=True)

    title_prefix = f'AGG {ctx.pickcfg} · {ctx.n_profiles} profiles'
    base = strategies[0]
    tag = top_tag(top_n, top_by)
    for m in overtime_metrics(agg=True):
        ttl = f"{m['t']}  [{title_prefix}]"
        _facet_metric(strategies, S, m, ttl, os.path.join(fac, m['f'] + '.png'))
        _overlay_metric(strategies, S, m, ttl, os.path.join(ovl, m['f'] + '.png'))
        _top_metric(strategies, S, top_n, m, ttl, base,
                    os.path.join(top, f"{tag}_{m['f']}.png"), top_by=top_by)
    _pick_travel_bars(strategies, S, f'Pick vs travel  [{title_prefix}]',
                      os.path.join(brk, 'pick_vs_travel.png'))
    _delta_bars(strategies, S, base, f'Δ vs baseline  [{title_prefix}]',
                os.path.join(brk, 'delta_vs_baseline.png'))
    ctx.log.info(f'  aggregate suite -> {ctx.out_dir} ({ctx.n_profiles} profiles)')
