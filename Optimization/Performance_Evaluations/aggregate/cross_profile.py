"""agg.cross_profile — cross-profile roll-up for one pick-config: the same faceted/overlay/
top/breakdown over-time suite as per-config, but on baseline-normalized curves averaged
across profiles.  Reuses the comparison builders.  Params: top_n, top_by."""
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.painters import (
    _delta_bars, _facet_metric, _overlay_metric, _pick_travel_bars, _top_metric,
    overtime_metrics, top_tag)


@evaluation(key='agg.cross_profile', label='Cross-profile over-time suite',
            scope='aggregate', needs=('series',),
            # The declared multi-dir owner: this suite mirrors the per-config compare tree
            # under the aggregate root, so it owns FOUR subdirs — previously '' here, which
            # exempted all 17 of its figures from the artifact-map bound check.
            out_subdir=('breakdown', 'faceted', 'overlay', 'top'),
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    strategies, S = ctx.agg_series()
    if not strategies:
        ctx.log.warning(f'  aggregate {ctx.pickcfg}: no usable series')
        return
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'

    fac = io.out_dir(ctx, pick='faceted')
    ovl = io.out_dir(ctx, pick='overlay')
    top = io.out_dir(ctx, pick='top')
    brk = io.out_dir(ctx, pick='breakdown')

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
