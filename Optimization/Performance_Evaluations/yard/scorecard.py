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

_COLS = ('arm', 'trailers', 'censored', 'doors', 'door util', 'receiver busy',
         'dock ceiling', 'yard depth (mean/max)', 'free doors (mean)', 'drains bound')

#: The SITE table's extra column, inserted after `receiver busy` — the per-channel SHARES of
#: that same number.  Present ONLY at site scope, and that is not cosmetic: an uncoupled
#: leaf's yard IS that channel's, so a "share" column there would print 100% and assert
#: something nobody asked — and every archived run's `absolute_yard_scorecard.png` would
#: gain an all-`'-'` column on its next re-analysis, which is a rendered regression the
#: row-level byte-identity measurement could not have seen.
_SITE_COL = 'per channel'
_SITE_AT = _COLS.index('receiver busy') + 1


def _cols(site: bool) -> tuple:
    return (_COLS[:_SITE_AT] + (_SITE_COL,) + _COLS[_SITE_AT:]) if site else _COLS


def _arm_span_days(ctx, key) -> float:
    """The arm's own wall of sim time, in days — utilization's denominator."""
    df = ctx.batch_df(key)
    if df.empty:
        return 0.0
    start = float(df['batch_start_time'].min())
    end = float((df['batch_start_time'] + df['duration']).max())
    return max(0.0, end - start) / float(SECONDS_PER_DAY)


def _receiver_busy(ctx, key) -> str:
    """The crew's BUSY SHARE: receiver-seconds charged ÷ (crew × day_seconds × WORK DAYS).

    The door-team cap's own read-out ("Decide the contention regime under the derived crew",
    4), and deliberately beside door utilization rather than in place of it: doors can be
    saturated while the crew idles (a capped dock cannot seat everyone) and the crew can be
    saturated with doors to spare. Neither direction is better, which is why this lives in
    the inspection table and is not a Quantity.

    THE DENOMINATOR IS WORK DAYS, not the arm's calendar span, and the distinction is not
    pedantic: the sim clock only advances through working hours, so an arm of 40 work days on
    an 8-hour day spans 13.3 CALENDAR days. Dividing a crew's seconds by the calendar span
    over-reads the share by exactly the ratio of the two — it reported 184% busy on the first
    real run this was rendered against, which is how it was caught. `_arm_span_days` is the
    right denominator for DOOR utilization (door spans are calendar time) and the wrong one
    here, which is why the two read-outs beside each other do not share one.

    Counted as DISTINCT `work_day` values rather than batches, so it stays right if a day
    ever releases more than one batch, and it is the same count
    `equilibrium._utilization_clause` grants against — the two reports cannot mean different
    things by "busy". '-' when the run derived no crew (flag-off) or recorded no receiving
    seconds; never 0%, which is a different and stronger claim.
    """
    exp = ctx.staffing_expectations()
    if not exp:
        return '-'
    crew = int(((exp.get('departments') or {}).get('recv') or {}).get('crew') or 0)
    S = float(exp.get('day_seconds') or 0.0)
    if crew <= 0 or S <= 0.0:
        return '-'
    bdf = ctx.batch_df(key)
    if bdf.empty or 'recv_seconds' not in bdf.columns or 'work_day' not in bdf.columns:
        return '-'
    n_days = int(bdf['work_day'].nunique())
    if n_days <= 0:
        return '-'
    return _busy_share(bdf, crew, S, n_days)


def _busy_share(bdf, crew: int, S: float, n_days: int) -> str:
    """One frame's receiver-busy share against the SITE crew and the SITE's work days.

    Factored out of `_receiver_busy` so the site number and the per-channel shares are
    computed by ONE function against ONE denominator.  That is the whole point of printing
    them together: a share is of a whole the channel does not own (ADR-0005 says so in as
    many words), so it is only readable beside the total it is a share OF.
    """
    if bdf.empty or 'recv_seconds' not in bdf.columns:
        return '-'
    worked = float(np.nansum(bdf['recv_seconds'].values))
    return f'{worked / (crew * S * n_days) * 100.0:.0f}%'


def _receiver_split(ctx, key) -> str:
    """The per-channel receiver shares, beside the site number and never instead of it.

    `a-right-site-total-hides-two-wrong-shares` is exact about why this column exists:
    put-away's site load was 0.9% off while both per-channel bands failed in OPPOSITE
    directions.  A site total is not evidence for the split, and the split is the only
    place the crews' fairness is visible.

    Called only from the SITE registration — an uncoupled leaf's yard IS that channel's, so
    the column does not exist there at all rather than printing 100%.  Both shares use the
    SITE crew and the SITE's work-day count: they are shares of one whole, so they sum to the
    site number and are not utilizations in their own right.
    """
    leaf_df = ctx.leaf_batch_df
    exp = ctx.staffing_expectations()
    crew = int(((exp or {}).get('departments', {}).get('recv') or {}).get('crew') or 0)
    S = float((exp or {}).get('day_seconds') or 0.0)
    bdf = ctx.batch_df(key)
    if crew <= 0 or S <= 0.0 or bdf.empty or 'work_day' not in bdf.columns:
        return '-'
    n_days = int(bdf['work_day'].nunique())
    if n_days <= 0:
        return '-'
    return ' / '.join(f'{ch[:1]} {_busy_share(leaf_df(key, ch), crew, S, n_days)}'
                      for ch in ctx.channels)


def _row(ctx, s, tdf, ddf, site: bool = False):
    n = len(tdf)
    cens = int(tdf['censored'].sum()) if n else 0
    # RECORDED first, derived second. The derivation below is exact only when the run's
    # first drain is in the frame, so on a resumed arm it is a lower bound — and printing a
    # lower-bound door count beside a ceiling computed from the recorded one would put two
    # different door counts in one table. The run spec records the count now (the run-shape
    # layer), so the fallback is for runs that predate the recording, exactly as the fee
    # threshold's is.
    doors = int(ctx.sim_result.get('inbound_dock_doors') or 0)
    derived_doors = int(ddf['free_doors_start'].max()) if not ddf.empty else 0
    if doors <= 0:
        doors = derived_doors
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
    # The ceiling is a RUN fact (cap × doors ÷ crew), the same on every row; it is printed
    # per row anyway so a reader comparing two arms never has to carry it in their head.
    ceil_v = ctx.dock_ceiling()
    ceiling = 'uncapped' if ceil_v is None else f'{ceil_v * 100.0:.0f}%'
    row = [_stitle(s), str(n), f'{cens}' if cens else '0', str(doors) if doors else '-',
           util, _receiver_busy(ctx, s['key']), ceiling, depth, free, bound]
    if site:
        row.insert(_SITE_AT, _receiver_split(ctx, s['key']))
    return row


@evaluation(key='yard.scorecard', label='Yard read-outs with no direction',
            scope='config', needs=('yard', 'batch'),
            family='yard', shape='inspection')
def render(ctx, params, site: bool = False):
    cols = _cols(site)
    rows = []
    for s in ctx.strategies:
        tdf, ddf = ctx.yard_df(s['key']), ctx.drain_df(s['key'])
        if tdf.empty and ddf.empty:
            continue
        rows.append(_row(ctx, s, tdf, ddf, site=site))
    if not rows:
        ctx.log.warning('  yard scorecard: no arm recorded a yard')
        return

    ch = chartkit.make(panels=1, panel_w=12.0, legend='none',
                       panel_h=chartkit.height_for_categories(len(rows), per=0.42,
                                                              base=1.2))
    ax = ch.ax
    ax.axis('off')
    ax.grid(False)
    tbl = ax.table(cellText=rows, colLabels=list(cols), cellLoc='center',
                   bbox=[0.0, 0.0, 1.0, 1.0])
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    cells = tbl.get_celld()
    for c in range(len(cols)):
        hc = cells[(0, c)]
        hc.set_facecolor('#34495e')
        hc.set_text_props(color='white', fontweight='bold', fontsize=7)
    ch.title('Yard read-outs',
             'inspection only — none of these has a better direction · door count recorded '
             'by the run (derived as max free doors at freeze only on a run that predates '
             'the recording, where utilization is then an upper bound) · receiver busy = '
             'receiver-seconds ÷ crew × WORK days (door util is over CALENDAR span — the '
             'clock only runs in working hours) · dock ceiling = cap × doors ÷ crew, the share '
             'of the crew the dock can seat at once'
             + (' · per channel = each leaf SHARE of that same site number, printed beside '
                'it and never instead of it (a right site total hides two wrong shares)'
                if site else '')
             + ' · free threshold '
             + f'{ctx.fee_threshold_days():g} d')
    out = io.out_dir(ctx)
    ch.save(os.path.join(out, 'absolute_yard_scorecard.png'), view='absolute')
    ctx.log.info(f'  yard scorecard: {len(rows)} arms -> {out}')


#: THE SAME FIGURE AT SITE SCOPE.  A yard is a LEAF's on an uncoupled run — each channel
#: fields its own transit, dock and crew — and the SITE's on a coupled one, where one dock
#: and one door set serve both channels and the trailer rows live in
#: the contract's `site_inbound_db` (ADR-0005).  Both run shapes are real, so the
#: evaluation is registered at both scopes over ONE render body; each registration's `yard`
#: request is DENIED on the other's runs, which is how a reader learns which model produced
#: the figure rather than having to infer it from a directory.
@evaluation(key='yard.site_scorecard', label='Site yard read-outs (coupled)',
            scope='site', needs=('yard', 'batch'),
            family='yard', shape='inspection')
def render_site(ctx, params):
    """Yard read-outs with no direction — the SITE's yard, for a coupled run.

    The render body is `render`, unchanged: a `SiteContext` populates the same
    `_by_key` shape an `EvalContext` does, so every frame broker works over it
    untouched and site-ness lives in what the keys point AT ("Re-scope the analysis
    surfaces to the site", section 1).

    The ONE thing the site table adds is the per-channel SHARE column, which exists only
    here: a right site total hides two wrong shares, and an uncoupled leaf has no share to
    report because its yard is its own.
    """
    return render(ctx, params, site=True)
