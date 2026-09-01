"""yard.fee — what the inbound ordering cost the carrier, per arm.

Two quantities, one ranked mark, and they are deliberately reported side by side:

  `yard_overage_days`            Σ max(0, detention − threshold) over every trailer. A
                                 carrier charges per trailer per day held, so this is the
                                 quantity that is actually billed, and it SUMS.
  `yard_over_threshold_trailers` how many trailers accrued any overage at all.

One trailer held a fortnight and a fortnight of trailers each held a day are the same
number of trailer-days and are not the same operational problem — the first is one carrier
relationship in trouble, the second is a systemic dwell. The total alone cannot tell them
apart, so the count rides beside it rather than being derivable-in-principle from a chart
nobody will do the arithmetic on.

CENSORED TRAILERS ARE IN BOTH. A trailer still standing when the run stops has been held
at least until the run ended, and under an adversarial ordering (`lifo`) that is exactly
where the concentrated overage sits. Dropping those rows would report `lifo`'s fee as
clipped rather than concentrated, which inverts the signal the arm exists to produce; the
subtitle says how many rows are bounded rather than observed.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.core import quantities as _q

#: (quantity key, per-arm fold) — the two panels, in reading order.
_PANELS = (
    ('yard_overage_days', lambda df: float(df['overage_days'].sum())),
    ('yard_over_threshold_trailers', lambda df: float(df['over_threshold'].sum())),
)

_TITLES = {
    'absolute': ('Yard overage per arm',
                 'trailer-days held past the free threshold, censored trailers included'),
    'percent':  ('Yard overage vs baseline',
                 'improvement against the baseline arm; an arm is absent where the '
                 'baseline accrued no overage at all and the comparison is undefined'),
}


def _entries(ctx, frames, fold):
    """[(strategy, per-arm scalar)] over the arms that have any trailer at all.

    No `paired` companion, and that is a decision rather than an omission: the fee's
    instances are TRAILERS, and trailer `seq` n of one arm is the same physical trailer as
    `seq` n of another (leads are seq-keyed draws, common random numbers across arms) — but
    only when both arms ran the same catalogue to the same depth. Pairing on that
    assumption without checking it would put a bootstrap interval on a comparison that may
    be lining up different shipments. A ranked mark without `paired` draws points with no
    interval, which is honest about what it does not know.
    """
    out = []
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        out.append((s, fold(df)))
    return out


def _censored_note(frames) -> str:
    n = sum(int(df['censored'].sum()) for df in frames.values() if not df.empty)
    total = sum(len(df) for df in frames.values() if not df.empty)
    if not n:
        return 'every trailer emptied before the run ended'
    return (f'{n} of {total} trailer rows are CENSORED — still on site at run end, so '
            f'their detention is a lower bound')


@evaluation(key='yard.fee', label='Yard overage and over-threshold trailers per arm',
            scope='config', needs=('yard', 'batch'),
            family='yard', shape='ranked',
            quantities=tuple(k for k, _f in _PANELS))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    frames = {s['key']: ctx.yard_df(s['key']) for s in ctx.strategies}
    baseline = ctx.base
    out = io.out_dir(ctx)
    n = 0
    for view in EVAL_BY_KEY['yard.fee'].views:
        for key, fold in _PANELS:
            entries = _entries(ctx, frames, fold)
            if not entries:
                continue
            q = _q.BY_KEY[key]
            ch = chartkit.make(
                panels=1, legend='none', panel_w=7.0,
                panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
            if not marks.ranked(ch, entries, quantity=q, view=view, baseline=baseline,
                                strategies=ctx.strategies):
                ch.abandon()
                continue
            title, sub = _TITLES[view]
            if key != 'yard_overage_days':
                title = f'{q.label} per arm'
            ch.title(title, f'{sub} · {_censored_note(frames)}')
            n += bool(ch.save(os.path.join(out, f'{view}_{key}.png'), view=view))
    ctx.log.info(f'  yard fee: {n} figures -> {out} '
                 f'(threshold {ctx.fee_threshold_days():g} d)')
