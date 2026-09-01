"""yard.binding — did the yard actually bind, and how often did a drain leave work behind.

The prior question of the whole effort. A policy that reorders trailers can only matter
where there is contention: with doors free and nothing standing, every ordering rule picks
the same trailers in the same drain and the arms are byte-identical by construction. So
this family's first figure is not a comparison at all — it is the evidence that the
comparison is licensed.

Two marks, one question:

  `yard_depth`    trailers standing at each drain's ctx-freeze, over the run. Read BEFORE
                  the door fill, because "did the yard bind" is a question about the moment
                  of choice and after the fill there is nothing left to choose.
  `binding_cuts`  how many drains ENDED with a trailer unreached or units still on a staged
                  one. A COUNT OF DRAINS, never a sum of the levels: summing them would
                  re-count the same standing trailer once per batch it waits, which is the
                  mistake `recv_cut` made at 101x on a published headline.

THE PILOT GATE READS THIS FIGURE'S ABSOLUTE VALUE UNDER `fifo`, not its ranking. A config
that shows neither contention nor binding cuts is a declared stop for the campaign — the
arms would be measuring nothing — so the absolute panel is the load-bearing one here and
the comparison views are secondary.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.core import quantities as _q

DEPTH = 'yard_depth'
CUTS = 'binding_cuts'


def _series(ctx, frames):
    """{key: {'batch': [...], 'yard_start': [...]}} — the shape `marks.serial` takes."""
    out = {}
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        d = df.sort_values('batch')
        out[s['key']] = {'batch': d['batch'].values,
                         'yard_start': d['yard_start'].astype(float).values}
    return out


def _cut_entries(ctx, frames):
    """[(strategy, drains that left inbound work)] — a COUNT of drains, not of trailers."""
    out = []
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        out.append((s, float(df['binding_cut'].sum())))
    return out


def _contention_note(frames) -> str:
    """One sentence on whether the yard bound at all — the pilot gate's own reading.

    Deliberately computed over every arm rather than the baseline alone: an arm-specific
    absence would be a policy result, and a universal one is a configuration that cannot
    answer the question at all.
    """
    contended = 0
    total = 0
    for df in frames.values():
        if df.empty:
            continue
        total += len(df)
        contended += int(((df['yard_start'] > 0) & (df['free_doors_start'] <= 0)).sum())
    if not total:
        return 'no drains recorded'
    if not contended:
        return ('NO DRAIN was ever door-bound — every standing trailer found a free door, '
                'so no ordering rule could have changed anything')
    return (f'{contended} of {total} drains had trailers standing with no free door — '
            f'the contention every ordering rule needs to matter')


@evaluation(key='yard.binding', label='Yard depth over drains, and binding cuts per arm',
            scope='config', needs=('yard', 'batch'),
            family='yard', shape=('serial', 'ranked'), quantities=(DEPTH, CUTS))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    frames = {s['key']: ctx.drain_df(s['key']) for s in ctx.strategies}
    series = _series(ctx, frames)
    entries = _cut_entries(ctx, frames)
    if not series and not entries:
        ctx.log.warning('  yard binding: no arm recorded a drain')
        return
    out = io.out_dir(ctx)
    note = _contention_note(frames)
    n = 0
    for view in EVAL_BY_KEY['yard.binding'].views:
        if series:
            ch = chartkit.make(panels=1, panel_w=7.2, panel_h=4.6, legend='gutter')
            drawn = marks.serial(ch, series, quantity=_q.BY_KEY[DEPTH], view=view,
                                 baseline=ctx.base, strategies=ctx.strategies,
                                 x_key='batch', y_key='yard_start')
            if drawn:
                ch.legend()
                ch.title('Trailers standing in the yard', note)
                n += bool(ch.save(os.path.join(out, f'{view}_yard_depth.png'), view=view))
            else:
                ch.abandon()
        if entries and view in _q.derive_views(_q.BY_KEY[CUTS],
                                               _q.SHAPE_BY_NAME['ranked']):
            ch = chartkit.make(
                panels=1, legend='none', panel_w=7.0,
                panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
            if marks.ranked(ch, entries, quantity=_q.BY_KEY[CUTS], view=view,
                            baseline=ctx.base, strategies=ctx.strategies):
                ch.title('Drains that left inbound work standing',
                         'a drain counts ONCE when it ends with an unreached trailer or '
                         'units still on a staged one — never a sum of the levels')
                n += bool(ch.save(os.path.join(out, f'{view}_binding_cuts.png'),
                                  view=view))
            else:
                ch.abandon()
    ctx.log.info(f'  yard binding: {n} figures -> {out} · {note}')
