"""labor.production_legs — the three legs of production time, per arm.

THE FIGURE THE OBJECTIVE WAS MISSING.  Ticket 10 fixed the inbound objective as expected
future WORK — the put + pick hours a placement generates — and ticket 08 made total
production hours phase 1's selection metric.  Until the `work` frame landed, only the pick
leg was reportable, and the honest consequence was stated in the ticket: a comparison on
pick hours alone systematically favours arms that buy pick time with put-away time, which
is the exact trade the inbound effort exists to measure.

One stacked bar per arm, three segments:

    unload   receiving labour  (`work_events`, role='receive')
    put      put-away labour   (`work_events`, role='put')
    pick     picking labour    (`task_stats.duration`, summed per batch)

Read it for the SPLIT, not the total — the total already has a headline panel.  An arm
whose bar is shorter than its neighbour's while its put segment is longer bought its
picking win with put-away hours, and the put share printed on each bar is what makes that
comparison possible across arms of different sizes.

Deliberately ABSOLUTE-only, which the `composite` mark enforces: the meaning here is the
decomposition, and a decomposition has no single value to compare against a baseline.  The
per-leg comparison against the baseline arm is a real question and it is answered by the
`tables.vs_baseline` evaluation, which now emits `putaway_time` and `unload_time` as their
own rows with paired intervals — numbers, where this is a shape.

Unload is near-absent inbound-off: no trailers, no receiving crew, no unload hours.  That
segment being invisible on an archive run is the model saying it had no inbound, not a
rendering fault.  A run that recorded no work events at all does not reach here — all three
quantities name the `work_events` capability, so the era gate refuses the render with a
reason rather than drawing pick hours under a label promising three legs.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle

#: (frame column, quantity key, SEGMENT LABEL, colour) bottom segment first.
#:
#: The label is carried rather than taken from the quantity, and only for the pick leg does
#: that matter — `production_time`'s label is 'Task makespan', which is its historical name
#: from before the other two legs were measurable and reads as a duration rather than as
#: one share of a stack.  The quantity key stays what it is, because that is what the
#: evaluation declares and draws; what changes is the word in the legend.
#:
#: Colour follows `task_time.breakdown`'s vocabulary rather than inventing a third: blue is
#: stationary work at a bin, orange is the movement-heavy stream.  Put-away takes the
#: middle tone because it is the leg under contention — the one a placement rule moves.
_LEGS = (
    ('unload_seconds', 'unload_time',     'unload (receiving)', '#dd8452'),
    ('put_seconds',    'putaway_time',    'put-away',           '#c44e52'),
    ('pick_seconds',   'production_time', 'pick',               '#4c72b0'),
)


def _totals(ctx, frames):
    """[(strategy, [seconds per leg])] over the arms with a populated work frame.

    Summed over every batch rather than the steady-state window, and that is the one
    choice worth stating: the legs are FLOWS of seconds, the put-away queue drains on its
    own schedule, and a trailing window can cut a put-away burst away from the picking it
    was serving.  The whole run is the only cut where the three legs describe the same
    work.
    """
    out = []
    for s in ctx.strategies:
        df = frames.get(s['key'])
        if df is None or df.empty:
            continue
        out.append((s, [float(df[col].sum()) for col, _q_key, _lbl, _c in _LEGS]))
    return out


@evaluation(key='labor.production_legs',
            label='Production time by leg (unload / put / pick) per arm',
            scope='config', needs=('work',),
            family='labor', shape='composite',
            quantities=('unload_time', 'putaway_time', 'production_time'))
def render(ctx, params):
    frames = {s['key']: ctx.work_df(s['key']) for s in ctx.strategies}
    entries = _totals(ctx, frames)
    if not entries:
        ctx.log.info('  production legs: no arm has a populated work frame')
        return
    out = io.out_dir(ctx)
    keys = [s for s, _v in entries]
    seconds = np.asarray([v for _s, v in entries], dtype=float)   # arms x legs
    # The three legs stack on ONE axis, so they resolve their time unit together from the
    # pooled sample — the rule `units` exists to keep in one place.
    div, unit = chartkit.time_units(seconds.reshape(-1))
    vals = seconds / div
    idx = np.arange(len(keys))

    labels = [label for _col, _q_key, label, _c in _LEGS]
    ch = chartkit.make(panels=1, panel_w=chartkit.width_for_categories(len(keys)),
                       panel_h=4.4, legend='gutter', legend_labels=labels)
    ax = ch.ax
    bottom = np.zeros(len(keys))
    for j, (_col, _q_key, label, colour) in enumerate(_LEGS):
        ax.bar(idx, vals[:, j], bottom=bottom, label=label, color=colour)
        bottom = bottom + vals[:, j]
    put_j = [j for j, (_c, q_key, _lbl, _col) in enumerate(_LEGS)
             if q_key == 'putaway_time'][0]
    for i in range(len(keys)):
        if bottom[i] > 0:
            ax.text(i, bottom[i], f'{vals[i, put_j] / bottom[i] * 100:.0f}% put',
                    ha='center', va='bottom', fontsize=6)
    xlabels = [_stitle(s) for s in keys]
    ax.set_xticks(idx)
    ax.set_xticklabels(xlabels, rotation=90, fontsize=6)
    ax.set_ylabel(f'Σ production time ({unit}, whole run)', fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    # deepen the reserved bottom margin so the rotated category labels are not clipped
    # (chartkit cannot see tick-label extents) — the `task_time.breakdown` idiom.
    need = 0.62 + 0.05 * max((len(t) for t in xlabels), default=0)
    ax.get_subplotspec().get_gridspec().update(
        bottom=min(0.55, min(2.8, need) / ch.fig.get_figheight()))
    ch.legend()
    untimed = int(sum(int(df['untimed_rows'].sum())
                      for df in frames.values() if not df.empty))
    note = ('unload + put + pick, the objective\'s three legs — read the SPLIT, not the '
            'total')
    if untimed:
        # Not decoration, and deliberately silent on a healthy run. The count covers ONLY
        # put and receive rows — a pick row carries no duration by contract and its leg
        # comes from the task frame — so a non-zero here is an interval that went missing
        # and a leg that reads short with nothing else to say so.
        note += (f' · {untimed} put/unload rows carry no duration, so those legs are '
                 f'UNDERSTATED')
    ch.title('Production time by leg', note)
    ch.save(os.path.join(out, 'absolute_production_legs.png'), view='absolute')
    ctx.log.info(f'  production legs: {len(keys)} arms -> {out}')
