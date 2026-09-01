"""test_quantities.py — the quantity table is the ONE declaration, and it is faithful.

`core/quantities.py` replaced four hand-maintained metric tables with one.  Two things
have to hold for that to be an improvement rather than a fifth table:

  1. **Faithfulness.** The tuples it derives must equal, element for element, the literals
     they replaced.  Those literals are copied verbatim into this file — deliberately, and
     they must never be edited to match a change.  `_METRICS`' order is the row order of
     every significance CSV and `_AGG_METRICS`' names are the `metric` column of a
     published one; a silent reorder or rename here re-writes committed evidence.
  2. **Non-vacuity.** The declaration must be able to say something the old tables could
     not — a mandatory suppression reason, a derived view set — and those must be
     enforced, not documented.

The drift this ended, for the record: `Σ task time per batch` in one table against
`total task time per batch` in another for the same quantity, and `throughput` and
`throughput_task` sharing the single axis label `items / hour` while measuring different
things.  Nothing could have caught either, because there was no place the two statements
met.

Run:  python -m pytest Tests/unit/test_quantities.py -q
"""
from __future__ import annotations

import pytest

from Optimization.Performance_Evaluations.common import stats_core, units
from Optimization.Performance_Evaluations.core import quantities as Q


# ── 1. faithfulness: the literals, verbatim, as of the commit that removed them ──

#: `stats_core._METRICS` exactly as it read before the derivation landed.
_LEGACY_METRICS = [
    ('production_time',       'task_sum',  'duration',           True),
    ('objective_task_labor',  'task_mean', 'W',                  True),
    ('objective_total_labor', 'task_sum',  'W',                  True),
    ('task_mean_duration',    'task_mean', 'duration',           True),
    ('makespan',              'batch',     'duration',           True),
    ('throughput',            'batch',     'completion_rate',    False),
    ('throughput_task',       'batch',     'thr_task',           False),
    ('queue_depth',           'batch',     'queue_depth',        True),
    ('sigma_fd',              'batch',     'sigma_fd',           True),
    ('picking_pct',           'batch',     'picking_pct',        False),
    ('reorder_churn',         'batch',     'reorder_placements', True),
]

#: `stats_core._AGG_METRICS` exactly as it read before the derivation landed.  Note the
#: order differs from `_METRICS` and `production_time` goes out as `productivity_hours` —
#: both are historical, both are load-bearing, and both are why `AGGREGATE_ORDER` and
#: `Source.steady_state_name` exist rather than being inferred.
_LEGACY_AGG_METRICS = [
    ('makespan',           'ss_dur',        True),
    ('throughput',         'ss_thr',        False),
    ('throughput_task',    'ss_thr_task',   False),
    ('task_mean_duration', 'ss_task_mean',  True),
    ('productivity_hours', 'ss_prod_hours', True),
]


#: Metrics added AFTER the derivation landed, in the order they were added. The legacy
#: literal above stays frozen; growth is recorded here so the two things the oracle
#: actually protects still hold — no existing row is reordered, and no existing row is
#: renamed. A new metric therefore goes at the END of `QUANTITIES` and every previously
#: published CSV row keeps its index.
_ADDED_METRICS = [
    # 2026-08-25, the working-day clock: throughput against the DAY rather than against
    # the batch makespan. Identical to `throughput` under the continuous default; they
    # separate under a paced release schedule, where a crew that finishes early waits and
    # that idle gap is elapsed time the makespan cannot see.
    ('throughput_elapsed',    'batch',     'thr_elapsed',        False),
    # 2026-08-31, the inbound funnel's selection metric. The objective is unload + put +
    # pick, and the put leg had never been read: `putaway_seconds` is an in-sim property
    # that reaches no table, so the hours live only in `work_events`, which this suite had
    # never opened. `work` is the fourth metric-source kind and the first to arrive since
    # the source dispatch stopped falling through to the task frame.
    ('total_production_time', 'work',      'production_seconds', True),
    ('putaway_time',          'work',      'put_seconds',        True),
    ('unload_time',           'work',      'unload_seconds',     True),
]


def test_the_derived_metric_table_is_the_literal_it_replaced():
    """The legacy rows, in the legacy order, with additions only ever appended.

    Two different failures are separated on purpose. A missing or reordered legacy row
    re-writes committed evidence and is the thing this oracle exists to catch; an appended
    row is growth and is recorded in `_ADDED_METRICS` above. Asserting the prefix first
    means a reorder still fails HERE, with the legacy diff, rather than showing up as a
    confusing whole-list mismatch after someone adds a metric.
    """
    got = list(stats_core._METRICS)
    assert got[:len(_LEGACY_METRICS)] == _LEGACY_METRICS, (
        'a legacy metric was reordered, renamed or removed — every significance CSV ever '
        'published has these rows in this order')
    assert got == _LEGACY_METRICS + _ADDED_METRICS, (
        'a metric was added without being recorded in _ADDED_METRICS, or was inserted '
        'somewhere other than the end (which shifts every row below it)')
    assert Q.metric_specs() == got


def test_the_derived_aggregate_table_is_the_literal_it_replaced():
    """Order AND names — this tuple's names are a published CSV's `metric` column."""
    assert list(stats_core._AGG_METRICS) == _LEGACY_AGG_METRICS
    assert Q.aggregate_specs() == _LEGACY_AGG_METRICS


def test_production_time_and_productivity_hours_are_one_quantity():
    """The rename that made them two rows in two tables for eleven months."""
    assert Q.quantity_for('productivity_hours') is Q.quantity_for('production_time')
    assert Q.BY_KEY['production_time'].source.agg_name == 'productivity_hours'


# ── 2. the declaration says things the old tables could not ──────────────────────

def test_a_suppressed_view_must_carry_a_reason():
    """Mandatory, and validated at construction — the `Capability` caveat idiom.

    A view withheld without a recorded reason is indistinguishable from one nobody got
    round to, which is exactly the drift the module exists to end.
    """
    with pytest.raises(ValueError, match='no reason'):
        Q.Quantity(key='x', label='x', axis_stem='x', unit=units.NONE,
                   direction='lower', source=Q.Source(per_batch=('batch', 'x')),
                   views_suppressed=(('percent', '  '),))


def test_every_declared_suppression_reason_is_substantive():
    for q in Q.QUANTITIES:
        for view, reason in q.views_suppressed:
            assert view in ('absolute', 'percent', 'delta', 'effect'), q.key
            assert len(reason.split()) >= 8, \
                f'{q.key} suppresses {view!r} with a reason too short to be a reason'


def test_two_throughput_quantities_no_longer_share_one_axis_label():
    """They did, and the label named neither of them."""
    a = Q.BY_KEY['throughput'].axis_label()
    b = Q.BY_KEY['throughput_task'].axis_label()
    assert a != b
    assert 'items / hour' in a and 'items / hour' in b


def test_a_duration_resolves_its_unit_from_its_own_samples():
    """One picker task is seconds, one batch of labor is hours — a fixed axis prints
    `0.0008` for half the suite."""
    q = Q.BY_KEY['task_mean_duration']
    assert q.axis_label([2.0, 3.0]).endswith('(seconds)')
    assert q.axis_label([2.0e4, 3.0e4]).endswith('(hours)')


def test_a_duration_unit_may_not_declare_a_suffix_it_would_ignore():
    with pytest.raises(ValueError, match='silently'):
        units.Unit('duration_s', 'hours')


# ── 3. the view derivation ───────────────────────────────────────────────────────

def test_views_come_from_the_quantity_and_the_shape_never_from_the_family():
    """The whole point: coverage stops being an editorial per-evaluation decision."""
    thr = Q.BY_KEY['throughput']
    assert Q.derive_views(thr, Q.SERIAL) == {'absolute', 'percent', 'delta'}
    # same quantity, a mark whose categories are arms rather than paired instances
    assert Q.derive_views(thr, Q.RANKED) == {'absolute', 'percent'}
    # a cumulative curve: comparable, but its instances are prefixes
    assert Q.derive_views(thr, Q.CURVE) == {'absolute', 'percent'}
    # ...and a mark with no baseline in it at all
    assert Q.derive_views(thr, Q.INSPECTION) == {'absolute'}


def test_a_share_never_gets_a_percent_view():
    """A percent improvement of a percentage is a percent of a percent."""
    pick = Q.BY_KEY['picking_pct']
    assert pick.unit.kind == 'share'
    assert 'percent' not in Q.derive_views(pick, Q.SERIAL)
    assert Q.derive_views(pick, Q.SERIAL) == {'absolute', 'delta'}
    assert Q.suppression_reason(pick, 'percent')


def test_a_contrast_quantity_carries_the_delta_view_and_nothing_else():
    """`cost.scoring_ms_per_unit` shipped for one day under an `absolute_` prefix; the
    stance field is what makes that unrepresentable.

    A contrast IS a difference: there is no level of it, and a percent of a difference
    restates the baseline twice. Note that `contrast` is NOT the effect VIEW — an effect
    size with its interval is a property of the significance MARK, which is why the
    self-describing shapes answer for themselves here.
    """
    q = Q.BY_KEY['scoring_ms_per_unit']
    assert q.stance == 'contrast'
    for shape in Q.SHAPES:
        got = Q.derive_views(q, shape)
        assert got == (shape.fixed if shape.self_describing else {'delta'}), shape.name


def test_the_effect_view_comes_from_the_mark_not_from_any_quantity():
    for q in Q.QUANTITIES:
        assert 'effect' not in Q.derive_views(q, Q.RANKED)
        assert 'effect' not in Q.derive_views(q, Q.SERIAL)
        assert Q.derive_views(q, Q.EFFECT) == {'effect'}


def test_every_quantity_derives_at_least_one_view_for_some_shape():
    """A quantity suppressed into silence is a declaration with no consumer."""
    for q in Q.QUANTITIES:
        assert any(Q.derive_views(q, s) for s in Q.SHAPES), q.key


# ── 4. internal consistency of the table itself ──────────────────────────────────

def test_every_quantity_is_readable_somewhere():
    """A declaration nothing can read is a declaration nothing can draw."""
    for q in Q.QUANTITIES:
        assert q.source.readable, \
            f'{q.key} declares no source at any scope, so nothing can ever read it'


def test_a_sourceless_quantity_is_refused_at_construction():
    with pytest.raises(ValueError, match='no source'):
        Q.Quantity(key='x', label='x', axis_stem='x', unit=units.NONE,
                   direction='lower', source=Q.Source())


def test_keys_and_stems_are_unique():
    keys = [q.key for q in Q.QUANTITIES]
    assert len(keys) == len(set(keys))
    stems = [q.stem for q in Q.QUANTITIES if q.stem]
    assert len(stems) == len(set(stems)), 'two quantities would write the same figure'


def test_a_series_quantity_declares_a_stem_and_a_title():
    """Both feed the over-time painter; a series source without them renders nameless."""
    for q in Q.QUANTITIES:
        if q.source.series is not None:
            assert q.stem and q.series_title, q.key


def test_the_stdlib_promise_holds():
    """`quantities` and `units` must be importable without the plotting stack.

    This is what lets a test, `ingest.py` or a schema tool read the table — and it is what
    makes "the emitted figure set equals the derived one" checkable rather than a
    convention. Checked by source inspection: importing the module in a process that has
    already imported numpy would prove nothing.
    """
    import ast
    import inspect
    banned = {'numpy', 'pandas', 'matplotlib', 'scipy'}
    for mod in (Q, units):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                got = {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                got = {(node.module or '').split('.')[0]}
            elif isinstance(node, ast.Lambda):
                raise AssertionError(
                    f'{mod.__name__} uses a lambda; a converter that cannot be a lambda '
                    f'has to be a declared Unit, which is the point')
            else:
                continue
            assert not (got & banned), f'{mod.__name__} imports {got & banned}'
