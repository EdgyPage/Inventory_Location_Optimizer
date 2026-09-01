"""yard.scorecard — the read-outs that have no honest direction, in one table.

Three numbers the yard produces that a reader needs and that must NOT be ranked:

  DOOR UTILIZATION       Σ door spans ÷ (doors × the arm's span). Neither high nor low is
                         good. Low says the doors were never the constraint; high says they
                         were saturated and adding one would change the answer. Both are
                         findings about the CONFIGURATION, not scores for the policy.
  THE CONTENTION PAIR    trailers standing and doors free at each drain's freeze. The
                         licence for the whole comparison — with doors free and nothing
                         standing, every ordering rule picks the same trailers and the arms
                         are byte-identical by construction.
  THE CENSORED SHARE     what fraction of trailer rows are bounded rather than observed. A
                         provenance fact about the other figures, not a result.

`DIRECTIONS` admits only 'lower' and 'higher', so declaring any of these as a Quantity
would force a direction none of them has — and a false direction is worse than no
declaration, because every derived view downstream would then assert it. They live here
instead, in an INSPECTION mark, which is the family grammar's own slot for a read-out with
no baseline in it. The column semantics they are built from are declared where the reads
actually happen, in `common/frames.py`.

## The door count is DERIVED, and exactly

Nothing records `INBOUND_DOCK_DOORS` where the analysis can see it (that is the run-shape
layer's job). But `free_doors_start = doors − staged`, and the first drain of a run always
freezes with nothing staged, so `max(free_doors_start)` IS the door count whenever the
run's first drain is in the frame. On a resumed arm whose early drains are missing it
becomes a lower bound, which makes utilization an UPPER bound — stated in the table rather
than left for a reader to assume away.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle
from Optimization.Performance_Evaluations.common.units import SECONDS_PER_DAY

_COLS = ('arm', 'trailers', 'censored', 'doors', 'door util', 'yard depth (mean/max)',
         'free doors (mean)', 'drains bound')


def _arm_span_days(ctx, key) -> float:
    """The arm's own wall of sim time, in days — utilization's denominator."""
    df = ctx.batch_df(key)
    if df.empty:
        return 0.0
    start = float(df['batch_start_time'].min())
    end = float((df['batch_start_time'] + df['duration']).max())
    return max(0.0, end - start) / float(SECONDS_PER_DAY)


def _row(ctx, s, tdf, ddf):
    n = len(tdf)
    cens = int(tdf['censored'].sum()) if n else 0
    doors = int(ddf['free_doors_start'].max()) if not ddf.empty else 0
    span = _arm_span_days(ctx, s['key'])
    door_days = float(np.nansum(tdf['door_span_days'].values)) if n else 0.0
    # '-' rather than 0% when the denominator is missing: a utilization of zero is a real
    # and different claim from one that could not be computed, and printing the first for
    # the second is how a configuration finding becomes a policy finding.
    util = ('-' if doors <= 0 or span <= 0.0
            else f'{door_days / (doors * span) * 100.0:.0f}%')
    depth = ('-' if ddf.empty
             else f"{ddf['yard_start'].mean():.1f} / {int(ddf['yard_start'].max())}")
    free = '-' if ddf.empty else f"{ddf['free_doors_start'].mean():.1f}"
    bound = ('-' if ddf.empty
             else f"{int(ddf['binding_cut'].sum())} of {len(ddf)}")
    return [_stitle(s), str(n), f'{cens}' if cens else '0', str(doors) if doors else '-',
            util, depth, free, bound]


@evaluation(key='yard.scorecard', label='Yard read-outs with no direction',
            scope='config', needs=('yard', 'batch'),
            family='yard', shape='inspection')
def render(ctx, params):
    rows = []
    for s in ctx.strategies:
        tdf, ddf = ctx.yard_df(s['key']), ctx.drain_df(s['key'])
        if tdf.empty and ddf.empty:
            continue
        rows.append(_row(ctx, s, tdf, ddf))
    if not rows:
        ctx.log.warning('  yard scorecard: no arm recorded a yard')
        return

    ch = chartkit.make(panels=1, panel_w=12.0, legend='none',
                       panel_h=chartkit.height_for_categories(len(rows), per=0.42,
                                                              base=1.2))
    ax = ch.ax
    ax.axis('off')
    ax.grid(False)
    tbl = ax.table(cellText=rows, colLabels=list(_COLS), cellLoc='center',
                   bbox=[0.0, 0.0, 1.0, 1.0])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    cells = tbl.get_celld()
    for c in range(len(_COLS)):
        hc = cells[(0, c)]
        hc.set_facecolor('#34495e')
        hc.set_text_props(color='white', fontweight='bold', fontsize=7)
    ch.title('Yard read-outs',
             'inspection only — none of these has a better direction · door count derived '
             'as max(free doors at freeze), so utilization is an upper bound on a resumed '
             f'arm · free threshold {ctx.fee_threshold_days():g} d')
    out = io.out_dir(ctx)
    ch.save(os.path.join(out, 'absolute_yard_scorecard.png'), view='absolute')
    ctx.log.info(f'  yard scorecard: {len(rows)} arms -> {out}')
