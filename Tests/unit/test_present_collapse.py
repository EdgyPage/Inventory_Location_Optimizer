"""test_present_collapse.py — four presentation tables became one, and the diff is named.

`significance/suite._PRESENT`, `aggregate/sig._PRESENT`, `painters.overtime_metrics()`
and `headline._METRIC_GROUPS` each carried a label and a direction for the same
quantities.  Collapsing them into `core/quantities.py` means picking one string wherever
the four disagreed — and they did.

This file is the record of that pick.  Both retired tables are copied in verbatim; the
derivation must reproduce them EXCEPT at the entries listed in `_INTENDED_MOVES`, each
with the reason it moved.  A label that changes without an entry here is a regression, and
an entry here that no longer differs is a stale exception to delete.  That is a stronger
acceptance test than "byte-identical" would have been: it says what moved rather than
forbidding movement.

`_METRIC_GROUPS` moved not at all and is asserted byte-identical.

Run:  python -m pytest Tests/unit/test_present_collapse.py -q
"""
from __future__ import annotations

from Optimization.Performance_Evaluations.common import painters, present
from Optimization.Performance_Evaluations.core import quantities as Q
from Optimization.Performance_Evaluations.headline.top_vs_baseline import _METRIC_GROUPS

#: Samples whose median is comfortably above an hour, so every duration resolves to
#: 'hours' and the retired tables' `_TIME` marker can be compared against a concrete
#: string rather than a sentinel.
_HOURS = [2.0e7, 3.0e7, 4.0e7]

#: `significance/suite._PRESENT`, expanded: a `_TIME` entry became `'<stem> (hours)'`
#: under `_HOURS`, a fixed entry was its label verbatim.
_LEGACY_SUITE_LABELS = {
    'production_time':       'Σ task time per batch (hours)',
    'objective_task_labor':  'E[task labor] (W)',
    'objective_total_labor': 'Σ task labor (W)',
    'task_mean_duration':    'mean task duration (hours)',
    'makespan':              'batch makespan (hours)',
    'throughput':            'items / hour',
    'throughput_task':       'items / hour',
    'queue_depth':           'put-away queue depth (units)',
    'sigma_fd':              'total f·D',
    'picking_pct':           '% of time picking',
    'reorder_churn':         'reorder placements / batch',
}

#: `aggregate/sig._PRESENT`, expanded the same way.
_LEGACY_AGG_LABELS = {
    'makespan':           'batch makespan (hours)',
    'throughput':         'items / hour',
    'throughput_task':    'items / hour',
    'task_mean_duration': 'mean task duration (hours)',
    'productivity_hours': 'Σ task time per batch (hours)',
}

#: `painters.overtime_metrics()`'s `yl`, by figure stem.  A time metric's `yl` is the
#: stem WITHOUT the unit — the painter appends the unit it resolved across every arm.
_LEGACY_OVERTIME_YL = {
    'task_duration':     'task duration',
    'avg_task_duration': 'mean task duration',
    'throughput':        'throughput (items / hour)',
    'production_time':   'total task time per batch',
    'layout_travel':     'total f·D (lower = better)',
}

#: (table, metric-or-stem) -> (new string, why it moved).  Every difference must be here.
#: Keyed by TABLE as well as name because the two namespaces overlap and disagree:
#: `production_time` is unchanged as a significance metric and changed as an over-time
#: figure stem, and one key for both would let a real regression hide behind the other's
#: exception.
_INTENDED_MOVES = {
    ('significance', 'throughput'): (
        'throughput / batch makespan (items / hour)',
        'the two throughput metrics measure different things and shared one axis label '
        'that named neither of them, so a reader could not tell the two panels apart'),
    ('significance', 'throughput_task'): (
        'throughput / task makespan (items / hour)',
        'the other half of the same collision — this panel and the batch-makespan one '
        'were labelled identically while plotting different measurements'),
    ('significance', 'sigma_fd'): (
        'total f·D (lower = better)',
        'a score has no physical unit, so nothing on the axis said which way was good. '
        'The over-time chart of this exact quantity already carried the hint and the '
        'significance panel did not, which is the drift; the hint is now a rule for '
        'every unit-less score rather than one hand-written string'),
    ('aggregate', 'throughput'): (
        'throughput / batch makespan (items / hour)',
        'the same collision in the cross-profile mirror of the same panels, which is '
        'why fixing it in one table was never going to be enough'),
    ('aggregate', 'throughput_task'): (
        'throughput / task makespan (items / hour)',
        'the other half of the collision in the cross-profile mirror of these panels'),
    ('overtime', 'throughput'): (
        'throughput / batch makespan (items / hour)',
        'this axis named the quantity but not WHICH throughput; it now matches the '
        'significance panel of the same measurement word for word'),
    ('overtime', 'production_time'): (
        'Σ task time per batch',
        'the same quantity was "Σ task time per batch" in two tables and "total task '
        'time per batch" in this one, so the majority spelling wins'),
}


def _check(table: str, legacy: dict, actual: dict) -> set:
    moved = {k for k in legacy if legacy[k] != actual[k]}
    for key in sorted(moved):
        was, now = legacy[key], actual[key]
        assert (table, key) in _INTENDED_MOVES, (
            f'{table}: {key!r} moved from {was!r} to {now!r} with no recorded reason. '
            f'Either the derivation regressed, or add it to _INTENDED_MOVES with why.')
        want, _why = _INTENDED_MOVES[(table, key)]
        assert now == want, f'{table}: {key!r} is {now!r}, expected {want!r}'
    return moved


def _actual(table: str) -> dict:
    if table == 'significance':
        return {n: present.for_metric(n, _HOURS)[1] for n in _LEGACY_SUITE_LABELS}
    if table == 'aggregate':
        return {n: present.for_metric(n, _HOURS)[1] for n in _LEGACY_AGG_LABELS}
    return {m['f']: m['yl'] for m in painters.overtime_metrics()}


_LEGACY = {'significance': _LEGACY_SUITE_LABELS, 'aggregate': _LEGACY_AGG_LABELS,
           'overtime': _LEGACY_OVERTIME_YL}


def test_the_significance_axis_labels_moved_only_where_recorded():
    moved = _check('significance', _LEGACY_SUITE_LABELS, _actual('significance'))
    assert moved == {'throughput', 'throughput_task', 'sigma_fd'}


def test_the_aggregate_axis_labels_moved_only_where_recorded():
    moved = _check('aggregate', _LEGACY_AGG_LABELS, _actual('aggregate'))
    assert moved == {'throughput', 'throughput_task'}


def test_the_overtime_axis_labels_moved_only_where_recorded():
    actual = _actual('overtime')
    assert set(actual) == set(_LEGACY_OVERTIME_YL), 'the over-time figure SET changed'
    moved = _check('overtime', _LEGACY_OVERTIME_YL, actual)
    assert moved == {'throughput', 'production_time'}


def test_every_recorded_move_is_still_a_move():
    """A stale exception is worse than none: it licenses a future regression."""
    for table, key in _INTENDED_MOVES:
        assert _LEGACY[table][key] != _actual(table)[key], \
            f'_INTENDED_MOVES[{(table, key)!r}] no longer describes a change'


def test_every_recorded_move_carries_a_real_reason():
    for key, (_new, why) in _INTENDED_MOVES.items():
        assert len(why.split()) >= 10, f'{key}: the reason is too short to be one'


#: The five panels as published, plus the flag `_METRIC_GROUPS` gained when a group that
#: cannot be PAIRED joined it.  All five original sources are per-batch, so all five are
#: pairable and the flag is True throughout — which is exactly why it had to be added for
#: the sixth and seventh rather than inferred.
_PUBLISHED_GROUPS = [
    ('Task makespan',        'ss_prod_hours', True,  'task_sum', 'duration', True),
    ('Batch makespan',       'ss_dur',        True,  'batch',    'duration', True),
    ('Thr / batch makespan', 'ss_thr',        False, 'batch',    'completion_rate', True),
    ('Thr / task makespan',  'ss_thr_task',   False, 'batch',    'thr_task', True),
    ('Layout total f·D',     'ss_sigma',      True,  'batch',    'sigma_fd', True),
]

#: Appended by the inbound funnel (ticket 08), which granted both a headline slot: the
#: campaign asks "does space-aware inbound beat FIFO, AND AT WHAT FEE COST", and a headline
#: carrying only hours answers half of it.  Recorded here for the same reason
#: `test_quantities._ADDED_METRICS` exists — growth is legitimate, a reorder of what has
#: already been published is not, and the two failures must be told apart.
_APPENDED_GROUPS = [
    ('Total production time', 'ss_prod_total', True, 'work', 'production_seconds', True),
    # NOT pairable: the fee's instances are TRAILERS, so there is no batch i of one arm to
    # difference against batch i of another, and the group renders without the paired
    # effect annotation its five neighbours carry.
    ('Yard overage', 'yard_overage_total', True, 'trailer', 'overage_days', False),
]


def test_the_headline_metric_groups_did_not_move_at_all():
    """These are the panel labels of a published headline figure.

    The prefix is asserted first and separately: a rename or a reorder of the five
    published panels renumbers a figure that is already committed evidence, while an
    append leaves every one of them where it was.  Collapsing the two into one equality
    would report both as the same diff.
    """
    got = [tuple(g) for g in _METRIC_GROUPS]
    assert got[:len(_PUBLISHED_GROUPS)] == _PUBLISHED_GROUPS, (
        'a published headline panel was renamed, reordered or dropped')
    assert got == _PUBLISHED_GROUPS + _APPENDED_GROUPS, (
        'a headline group was added without being recorded in _APPENDED_GROUPS, or was '
        'inserted somewhere other than the end')


# ── the structural properties, not just the strings ──────────────────────────────

def test_the_overtime_specs_still_carry_every_key_the_painter_reads():
    needed = {'x', 'y', 'blo', 'bhi', 'f', 't', 'time', 'conv', 'yl', 'lower_is_better'}
    for m in painters.overtime_metrics():
        assert needed <= set(m), f"{m.get('f')} is missing {needed - set(m)}"
        assert m['q'] is Q.BY_KEY[m['q'].key]


def test_a_time_metric_hands_the_painter_no_converter():
    """Its divisor is pooled across every arm, which only `_time_axis` can see. A
    spec-level converter resolved against no samples would silently claim seconds."""
    for m in painters.overtime_metrics():
        if m['time']:
            assert m['conv'] is None, m['f']
        else:
            assert m['yl'] == m['q'].axis_label(), m['f']


def test_the_converter_is_picklable_and_names_itself():
    """It crosses into a spawned worker, and `<lambda>` in a traceback names nothing."""
    import pickle
    conv, _label = present.for_metric('throughput')
    assert pickle.loads(pickle.dumps(conv)) == conv
    assert 'items / hour' in repr(conv)


def test_a_metric_with_no_scaling_gets_no_converter():
    """`merged_effect_panel` decides whether values were converted from `conv is None`;
    handing it an identity function would change that decision."""
    assert present.for_metric('sigma_fd')[0] is None
    assert present.for_metric('picking_pct')[0] is None
