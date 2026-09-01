"""throughput.missed — demand the floor was asked for and could not pick.

The availability axis. It was floated as the inbound OBJECTIVE and lost to expected future
work, and it is here on the terms of that decision: a REPORTED axis, never a target and
never a selection metric. Nothing ranks arms by it and nothing optimises toward it. It
exists because a policy that wins hours by starving the shelf has to show that somewhere,
and this is the somewhere.

## Two supply reasons, and one that is not

`unpicked_unstocked` (nothing on the shelf) and `unpicked_unavailable` (stock exists, the
bin could not be reached) are what inbound can move. `unpicked_daycut` is NOT included and
is not an oversight: the whistle stopping a picker mid-shift is a staffing fact, so folding
it in would let a longer working day read as better inbound. `unpicked_notasks` is out for
the same kind of reason — no task was built for it, which is scheduling.

## Ungated by the yard, gated by carryover

Demand service is meaningful with no inbound model at all, so these draw on every run that
CAN answer them. What they cannot outrun is the `carryover` table, which postdates most of
the archive — hence the `carryover` capability on both quantities and the broker's denial
when the table is absent. An arm with no missed rows served all its demand, which is a
result and renders normally.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.core import quantities as _q

#: (quantity, per-arm fold over the per-batch frame).  `missed_pieces` SUMS — it is a FLOW
#: of items, additive across batches.  `missed_share` is a share and is averaged over the
#: batches that had demand, never summed, and never recomputed as total/total: the two
#: differ whenever batch sizes vary, and the per-batch mean is the one the caption claims.
_PANELS = (
    ('missed_pieces', lambda df: float(df['missed_pieces'].sum())),
    ('missed_share', lambda df: float(np.nanmean(df['missed_share'].values))),
)


def _entries(ctx, frames, fold):
    out = []
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        val = fold(df)
        if np.isfinite(val):
            out.append((s, val))
    return out


@evaluation(key='throughput.missed', label='Demand missed, per arm',
            scope='config', needs=('missed',),
            family='throughput', shape='ranked',
            quantities=tuple(k for k, _f in _PANELS))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    frames = {s['key']: ctx.missed_df(s['key']) for s in ctx.strategies}
    out = io.out_dir(ctx)
    n = 0
    for view in EVAL_BY_KEY['throughput.missed'].views:
        for key, fold in _PANELS:
            q = _q.BY_KEY[key]
            if view not in _q.derive_views(q, _q.SHAPE_BY_NAME['ranked']):
                continue                      # a share carries no percent — see the unit
            entries = _entries(ctx, frames, fold)
            if not entries:
                continue
            ch = chartkit.make(
                panels=1, legend='none', panel_w=7.0,
                panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
            if not marks.ranked(ch, entries, quantity=q, view=view, baseline=ctx.base,
                                strategies=ctx.strategies):
                ch.abandon()
                continue
            ch.title(f'{q.label} per arm',
                     'supply reasons only — a day cut is a staffing fact, not an '
                     'availability one')
            n += bool(ch.save(os.path.join(out, f'{view}_{key}.png'), view=view))
    ctx.log.info(f'  throughput missed: {n} figures -> {out}')
