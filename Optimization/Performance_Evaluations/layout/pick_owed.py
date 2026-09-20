"""layout.pick_owed — what the PLANNED demand owes the placement, per arm, against the
optimal-assignment floor.

## The question this exists to answer, and why nothing else could

The inbound campaign's second phase ranks UNLOADING policies.  Every other metric in this
suite is a FLOW — production seconds, unload seconds, throughput, items picked — and over
a window long enough to unload everything a flow total is INVARIANT to the order the
unloading happened in.  Same trailers, same packs, same seconds, different sequence.  The
first phase-2 ranking came back as an exact tie across ten cells, and that was the honest
answer to a question the metric could not be asked.

An ordering policy changes WHEN stock reaches a shelf and WHICH shelf is free when it gets
there.  That is a property of the STATE, not of the flow, so the measurement has to be a
state read: at each batch, what would the run's own planned demand cost to serve from
where the stock currently stands?

    pick_owed(t) = Σ over the planned lines, of the MEAN at-location cost over the bins
                   that SKU actually occupies at batch t

Same arithmetic as `optimal_work` (`inventory_optimal._optimal_work_assign`, through the
shared `cost_model.per_pick`), so the score and its floor are one measurement and the
ratio between them means something: 1.0 is the best placement the geometry admits.

## The honesty term, and why it is not folded in

A planned line whose SKU has nothing on any shelf costs this score NOTHING — there is no
bin to walk to.  Alone, that would rank "leave it all in the yard" first, which is the
exact inversion the metric exists to prevent.  `unservable_weight` is the census of that
demand, recorded per batch beside the score and carried onto this figure's subtitle: a run
whose census is materially non-zero was decided by AVAILABILITY, not by placement, and
says so on the figure rather than being quietly published as a placement result.

No fabricated penalty is charged for it.  A made-up number would make the score a single
rankable scalar again at the cost of making it partly fiction, and which of the two forces
moved it would no longer be recoverable from the figure.

## Views

Derived (`ranked` mark × `pick_owed_s`), exactly as `layout.travel`'s are:

  absolute  each arm's steady-state score beside the optimal-assignment floor, which turns
            a bare seconds number into "how much of the achievable improvement did this
            rule capture"
  percent   per-arm improvement against the baseline, with the bootstrap interval of the
            PAIRED per-batch improvements, so a gap that crosses zero is reported as not
            shown rather than as small
"""
import os

import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.core import quantities as _q

#: the one quantity this evaluation RANKS
QUANTITY = 'pick_owed_s'

#: Read beside it and never ranked — the census that says whether the ranking is about
#: placement at all.  Drawn as a trajectory by the over-time family, which is why it is
#: NOT in this evaluation's `quantities=`: declaring a quantity it does not draw would
#: make the view derivation promise a figure that does not exist.
CENSUS = 'unservable_weight'

#: Above this share of the mean score, the census stops being a footnote.  Not a tolerance
#: on a float — it is the point where the reader has to be told that availability, and not
#: placement, is what separated the arms.
CENSUS_MATERIAL = 0.01

_TITLES = {
    'absolute': ('Pick work owed by the placement',
                 'steady-state seconds the planned demand owes the current layout; '
                 'optimal-assignment floor shaded'),
    'percent':  ('Pick work owed vs baseline',
                 'median of the per-batch improvements in owed seconds, with the '
                 'bootstrap interval of that same median'),
}


def _per_batch(ctx, key, column):
    """One arm's per-batch column off the batch frame, indexed by batch.

    Straight off the frame rather than through the generic metric-series helper, for
    `layout.travel`'s reason: that helper takes a task frame it never consults for a
    batch-scoped column, and asking the broker for it would cost a real read per arm and
    make this evaluation's `needs=` a lie.

    `dropna` is load-bearing here and is not in `layout.travel`: both columns read NULL
    rather than 0.0 on a vintage that predates them (`BATCH_UNKNOWN_ON_OLDER_VINTAGES`),
    and a 0.0 would be the best score the column can take.
    """
    d = ctx.batch_df(key)
    if d is None or d.empty or column not in d:
        return pd.Series(dtype=float)
    return d.set_index('batch_id')[column].astype(float).dropna()


def _census(ctx):
    """(worst unservable weight over every arm and batch, the arm that carried it).

    The MAXIMUM, not the mean: the question is whether this run was ever decided by
    availability, and a run that starved for five batches and recovered still had its
    score decided by something other than placement across those five.
    """
    worst, who = 0.0, ''
    for s in ctx.strategies:
        u = _per_batch(ctx, s['key'], CENSUS)
        if u.empty:
            continue
        m = float(u.max())
        if m > worst:
            worst, who = m, s['key']
    return worst, who


def _entries(ctx, S, baseline):
    """(entries, paired) — the absolute view's scalars, and the pairing for the rest.

    Same shape as `layout.travel._entries`, and deliberately so: an arm sharing fewer than
    three batches with the baseline keeps its bar and loses its dot, which is the honest
    pair of answers rather than dropping the arm from both.
    """
    pb = _per_batch(ctx, baseline['key'], QUANTITY) if baseline else pd.Series(dtype=float)
    entries, paired = [], {}
    for s in ctx.strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        val = d.get('ss_pick_owed')
        if val is None or not np.isfinite(float(val)):
            continue
        entries.append((s, float(val)))
        ps = _per_batch(ctx, s['key'], QUANTITY)
        common = sorted(set(pb.index) & set(ps.index))
        if len(common) >= 3:
            paired[s['key']] = (ps.loc[common].values, pb.loc[common].values)
    return entries, paired


def _subtitle(base_sub, worst, who):
    """The declared subtitle, plus the census when the census has something to say."""
    if worst <= 0:
        return f'{base_sub}; every planned line had shelf stock'
    return (f'{base_sub}. CENSUS: up to {worst:,.0f} planned line(s) with no shelf stock '
            f'({who}) — that demand costs this score nothing, so read the gap as partly '
            f'availability')


def _figure(ctx, entries, paired, baseline, view, out, census):
    q = _q.BY_KEY[QUANTITY]
    ch = chartkit.make(
        panels=1, legend='none', panel_w=7.0,
        panel_h=chartkit.height_for_categories(len(entries), per=0.3, base=2.0))
    if not marks.ranked(ch, entries, quantity=q, view=view, baseline=baseline,
                        strategies=ctx.strategies, paired=paired):
        return ch.abandon()
    if view == 'absolute':
        # `optimal_work`, NOT `optimal` — the latter is the Σ f·D floor `layout.travel`
        # draws, a different objective in different units.  The context coerces both to
        # 0.0 when the run publishes none, which is why `> 0` is the absence test rather
        # than `is not None`.
        floor = getattr(ctx, 'optimal_work', None)
        if floor is not None and np.isfinite(float(floor)) and float(floor) > 0:
            chartkit.reference_line(ch.ax, float(floor), orient='x',
                                    label='optimal assignment', style=chartkit.BOUND_STYLE,
                                    legend_label='optimal assignment (best achievable)')
    title, sub = _TITLES[view]
    ch.title(title, _subtitle(sub, *census))
    return ch.save(os.path.join(out, f'{view}_pick_owed_per_arm.png'), view=view)


@evaluation(key='layout.pick_owed', label='Pick work owed by the placement, per arm',
            scope='config', needs=('batch', 'series'),
            family='layout', shape='ranked', quantities=(QUANTITY,))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    S = ctx.series()
    baseline = ctx.base
    if S.get(baseline['key']) is None:
        ctx.log.warning('  pick owed: no baseline series; skipped')
        return
    entries, paired = _entries(ctx, S, baseline)
    if not entries:
        ctx.log.warning('  pick owed: no arm published a steady-state placement score')
        return
    census = _census(ctx)
    worst = census[0]
    if worst > 0:
        mean_score = float(np.mean([v for _s, v in entries])) or 1.0
        material = '' if worst / mean_score <= CENSUS_MATERIAL else ' — MATERIALLY so'
        ctx.log.warning(
            f'  pick owed: CENSUS non-zero — up to {worst:,.0f} planned line(s) with no '
            f'shelf stock ({census[1]}). The score prices no walk for those, so this '
            f'ranking is partly about availability{material}')
    out = io.out_dir(ctx)
    n = 0
    for view in EVAL_BY_KEY['layout.pick_owed'].views:
        n += bool(_figure(ctx, entries, paired, baseline, view, out, census))
    ctx.log.info(f'  pick owed: {n} views over {len(entries)} arms -> {out}')
