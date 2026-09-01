"""yard.detention — how long the site held each trailer, and how that time is spread.

DETENTION IS arrived -> emptied: the whole time the carrier's trailer is on site,
INCLUDING its hours at a door. That definition is what makes it a fair substrate for a
fee — it is monotone under any ordering, so staging a trailer early and then unloading it
slowly sheds nothing, and no policy can win the fee axis by shuffling where the waiting
happens rather than shortening it.

## Why a distribution, not just a mean

The adversarial control arm (`lifo`) does not RAISE mean detention much — it serves the
same trailers with the same crew — it CONCENTRATES it, holding a few trailers a very long
time while the rest move normally. A mean beside a mean would show that arm as roughly
tied and the fee total as mysteriously worse. The box per arm is where the mechanism
becomes visible, and the threshold is drawn on it as a fixed mark so a reader can see the
tail crossing the line rather than infer it from a number elsewhere.

The mean is still reported, because it is the quantity the ranked comparison is defined
on and the one a percent view can be taken of. The two figures answer different halves.

## Censored rows are IN, and marked

A trailer still standing when the run ends contributes its lower bound. Excluding it would
remove exactly the longest-held trailers — the ones the fee is about — and understate the
arm that produced them. Each arm's censored share is annotated so the bound is legible.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks, present
from Optimization.Performance_Evaluations.common.style import _stitle
from Optimization.Performance_Evaluations.core import quantities as _q

QUANTITY = 'yard_detention_days'


def _entries(ctx, frames):
    """[(strategy, mean detention in days)] over arms with at least one trailer."""
    out = []
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        val = float(df['detention_days'].mean())
        if np.isfinite(val):
            out.append((s, val))
    return out


def _distribution(ctx, frames, threshold, out):
    """The box-per-arm figure: THE view of concentration, and a bespoke mark.

    `marks.ranked` draws one value per arm; this draws a whole sample per arm, which is a
    different mark and is why it is in the bespoke ledger rather than routed through the
    library. Everything else is the house style — the same category axis, the same
    baseline shading, the quantity's own converter for the axis label.
    """
    rows = [(s, np.asarray(frames[s['key']]['detention_days'], dtype=float))
            for s in ctx.strategies
            if frames.get(s['key']) is not None and not frames[s['key']].empty]
    rows = [(s, v[np.isfinite(v)]) for s, v in rows]
    rows = [(s, v) for s, v in rows if v.size]
    if not rows:
        return 0
    # Sorted by MEDIAN rather than by mean: the panel's whole subject is the shape of the
    # tail, and ordering by a statistic the tail dominates would sort the arms by the
    # thing the boxes are there to show.
    rows.sort(key=lambda sv: float(np.median(sv[1])))
    q = _q.BY_KEY[QUANTITY]
    conv, label = present.for_metric(q.key, [v for _s, v in rows])
    vals = [conv(v) if conv is not None else v for _s, v in rows]

    ch = chartkit.make(
        panels=1, legend='none', panel_w=7.0,
        panel_h=chartkit.height_for_categories(len(rows), per=0.3, base=2.2))
    ax = ch.ax
    pos = chartkit.category_axis(ax, [_stitle(s) for s, _v in rows], orient='h')
    # `vert=False`, not `orientation='horizontal'`: the latter arrived in matplotlib 3.10
    # and `requirements.txt` floors at 3.7, so switching would break the floor to silence a
    # PendingDeprecationWarning. Change it when the floor moves, not before.
    ax.boxplot(vals, positions=pos, vert=False, widths=0.6, showfliers=True,
               flierprops=dict(marker='.', markersize=2.5, alpha=0.5),
               medianprops=dict(color='#1a4d7a', lw=1.4))
    base_key = ctx.base['key'] if ctx.base else None
    for i, (s, _v) in enumerate(rows):
        if s['key'] == base_key:
            chartkit.mark_baseline_row(ax, i, orient='h')
    thr = (conv([threshold])[0] if conv is not None else threshold)
    chartkit.reference_line(ax, float(thr), orient='x', label='free threshold',
                            style=chartkit.BOUND_STYLE)
    # The censored share, per arm, on the row it belongs to: a box whose upper whisker is
    # a bound reads exactly like one that is not, and the difference is the whole reason
    # those rows were kept.
    for i, (s, _v) in enumerate(rows):
        n_cens = int(frames[s['key']]['censored'].sum())
        if n_cens:
            ax.text(1.005, pos[i], f'{n_cens} censored', transform=ax.get_yaxis_transform(),
                    fontsize=6, color='#a05000', va='center')
    ax.set_xlabel(label)
    ch.title('Detention per trailer',
             'box = per-trailer detention, arrival to empty · censored rows carry their '
             'lower bound')
    return bool(ch.save(os.path.join(out, 'absolute_detention_distribution.png'),
                        view='absolute'))


@evaluation(key='yard.detention', label='Detention per trailer, mean and distribution',
            scope='config', needs=('yard', 'batch'),
            family='yard', shape='ranked', quantities=(QUANTITY,))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    frames = {s['key']: ctx.yard_df(s['key']) for s in ctx.strategies}
    entries = _entries(ctx, frames)
    if not entries:
        ctx.log.warning('  yard detention: no arm recorded a trailer')
        return
    out = io.out_dir(ctx)
    q = _q.BY_KEY[QUANTITY]
    n = 0
    for view in EVAL_BY_KEY['yard.detention'].views:
        ch = chartkit.make(
            panels=1, legend='none', panel_w=7.0,
            panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
        if not marks.ranked(ch, entries, quantity=q, view=view, baseline=ctx.base,
                            strategies=ctx.strategies):
            ch.abandon()
            continue
        ch.title('Mean detention per trailer',
                 'arrival to empty, including time at a door — monotone under any '
                 'ordering')
        n += bool(ch.save(os.path.join(out, f'{view}_detention_mean.png'), view=view))
    n += _distribution(ctx, frames, ctx.fee_threshold_days(), out)
    ctx.log.info(f'  yard detention: {n} figures over {len(entries)} arms -> {out}')
