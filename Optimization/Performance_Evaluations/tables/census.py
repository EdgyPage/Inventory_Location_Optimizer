"""tables.census — the count/span claims the pages keep making, computed instead of typed.

"Positive in all 68 same-rule comparisons per channel", "median +44.8 %, range 28.5–39.8 %",
"positive in all 136 comparisons".  Every one of those was hand-counted at authoring time
and then RESTATED as prose, so the only way to check one was to re-derive it from the
staged what-if rows.  That is the shape of claim `stats_core.census` generalises, and this
is where it gets emitted so a page can render the sentence from the artifact rather than
assert it beside one.

WHAT IS COUNTED.  Each row of the cross-cell what-if table is one same-rule comparison: an
arm under the treatment scheduler against the identical arm under the reference, on the
same catalogue and the same demand.  A census over those rows, grouped four ways, answers
"how broadly does this hold" with an n, a win rate, an exact sign test and a span — the
four things a reader needs before quoting a median.

WHY A SIGN TEST AND NOT THE PAIRED TEST THE LEAF TABLES USE.  Different question.  The leaf
tables ask whether ONE arm beats the baseline, over 75 paired batches.  This asks how many
ARMS moved in the claimed direction, over 68 of them — the unit of observation is the arm,
the comparison is already a difference, and the only distributional assumption worth making
about a set of 68 arm-level deltas is which side of zero each one fell.
"""
import csv
import json
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.stats_core import census

#: (column, better-direction, what a page would call it).  Each is one claim class the
#: published pages make; adding a metric here adds a checkable claim, not a new chart.
_CLAIMS = (
    ('thr_gain_vs_ref_pct',    'higher', 'throughput uplift vs the reference scheduler'),
    ('auc_gain_vs_ref_pct',    'higher', 'area between the cumulative volume curves'),
    ('labor_delta_vs_ref_pct', 'higher', 'labor change (expected ~0: the invariance claim)'),
)

#: How the rows are sliced.  `overall` falls out of `census` itself as the trailing row.
_GROUPINGS = (
    ('channel',            ('channel',)),
    ('channel_x_rule',     ('channel', 'assignment')),
    ('scheduler',          ('scheduler',)),
)


def _num(row, key):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return float('nan')


def comparisons(rows, reference):
    """The rows that ARE comparisons — the reference cell's own are not.

    The what-if table carries every arm in every cell, reference included, and a reference
    arm's "gain vs reference" is exactly 0 because it is being compared with itself.  Left
    in, those rows double the count, halve the win rate, and drag the median toward zero:
    the first render of this census reported 136 comparisons per channel with 68 ties and a
    store median of +19.9 %, against a true 68 comparisons, no ties, and +44.8 %.  A tie in
    a sign test means "the two were equal"; a row compared with itself means nothing at all.
    """
    if not reference:
        return list(rows)
    return [r for r in rows if r.get('cell') != reference]


def census_blocks(rows) -> list:
    """[{metric, better, grouping, rows:[census...]}] over the what-if comparison rows."""
    out = []
    for metric, better, label in _CLAIMS:
        for gname, keys in _GROUPINGS:
            got = census(rows, value=lambda r, m=metric: _num(r, m),
                         group_by=keys, better=better)
            out.append({'metric': metric, 'label': label, 'better': better,
                        'grouping': gname, 'rows': got})
    return out


@evaluation(key='tables.census', label='How broadly each comparison holds (counts + sign test)',
            scope='run', needs=('whatif',), out_subdir='tables')
def render(ctx, params):
    doc_in = ctx.whatif_doc('whatif_volume_json')
    reference = doc_in.get('reference')
    rows = comparisons(ctx.whatif_rows('whatif_volume_csv'), reference)
    if not rows:
        return
    blocks = census_blocks(rows)
    out = io.out_dir(ctx)

    flat = []
    for b in blocks:
        for r in b['rows']:
            flat.append({'metric': b['metric'], 'grouping': b['grouping'],
                         'better': b['better'], **r})
    # One table, several groupings, so the group columns differ block to block: a row
    # grouped by channel alone has no `group_assignment`.  Union the headers and let the
    # blanks be blank — the alternative is one CSV per grouping, which pushes the join
    # onto whoever opens them.
    cols, seen = [], set()
    for r in flat:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    path = os.path.join(out, 'comparison_census.csv')
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval='')
        w.writeheader()
        w.writerows(flat)
    ctx.log.info(f'  wrote {os.path.basename(path)} ({len(flat)} rows)')

    # The JSON is what the site renders from: a macro looks up (metric, grouping, group)
    # and composes the sentence, so the page carries the call and never the number.
    doc = {
        'reference_cell': reference,
        'treatment_cells': sorted({r.get('cell') for r in rows if r.get('cell')}),
        'n_comparisons': len(rows),
        'note': ('One row per same-rule comparison: an arm under a treatment scheduler '
                 'against the identical arm under the reference cell, on the same '
                 'catalogue and the same demand. The reference rows are '
                 'excluded — an arm compared with itself is not a comparison. The sign '
                 'test excludes exact ties from its denominator and reports them as '
                 'n_zero.'),
        'blocks': blocks,
    }
    with open(os.path.join(os.path.dirname(out), 'comparison_census.json'),
              'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)
