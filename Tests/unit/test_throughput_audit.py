"""test_throughput_audit.py — the audit's per-day frame and its reading vocabulary.

`frames._sdf` joins the drain-or-cap ledger to the batch and work frames through
`work_day` and prices each day against the staffing expectations; `throughput.audit`
reads it and reports (.scratch/department-calibration, "Declare the equilibrium bands",
decision 7).  Pinned here: the empty-frame rule (no ledger -> no rows, never zeros),
`capped` and the START-gate overtime, the per-day legs, utilization NaN without a crew,
and the audit's one-word readings -- a below-band picking read on a campaign arm is the
arm's travel saving, on the baseline it is just below band.

Run:  python -m pytest Tests/unit/test_throughput_audit.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from Optimization.Performance_Evaluations.common.frames import _sdf, _SHIFT_COLS
from Optimization.Performance_Evaluations.throughput import audit

S = 28800.0


def _ledger():
    return [{'day': 0, 'cap_end': S, 'end_s': S - 400, 'drained': 1, 'standing': 0,
             'standing_put': 0, 'standing_dock': 0, 'standing_carry': 0,
             'standing_carry_labour': 0, 'standing_carry_supply': 0,
             'last_finish': S - 400},
            {'day': 1, 'cap_end': 2 * S, 'end_s': 2 * S, 'drained': 0, 'standing': 7,
             'standing_put': 5, 'standing_dock': 2, 'standing_carry': 0,
             'standing_carry_labour': 0, 'standing_carry_supply': 0,
             'last_finish': 2 * S + 90.0}]


def _batches():
    return pd.DataFrame({'batch_id': [0, 1], 'work_day': [0, 1],
                         'released_late': [0.0, 33.0], 'items_demanded': [1000, 1200],
                         'total_items': [980, 900], 'task_makespan': [14400.0, 20000.0]})


def _work():
    return pd.DataFrame({'batch_id': [0, 1], 'put_seconds': [3000.0, 5000.0],
                         'unload_seconds': [1000.0, 1500.0], 'pick_seconds': [0.0, 0.0],
                         'production_seconds': [0.0, 0.0], 'untimed_rows': [0, 0]})


def _expectations():
    return {'day_seconds': S, 'band_tol': 0.10,
            'departments': {'pick': {'crew': 2, 'expected': 0.30},
                            'put': {'crew': 1, 'expected': 0.12}},
            'absent': {'recv': 'no site crew derived'}, 'flags': {}}


def test_no_ledger_means_an_empty_frame_with_its_columns_never_a_frame_of_zeros():
    df = _sdf([], _batches(), _work(), _expectations())
    assert df.empty and list(df.columns) == _SHIFT_COLS


def test_the_day_frame_joins_the_ledger_to_the_batches_and_the_legs():
    df = _sdf(_ledger(), _batches(), _work(), _expectations())
    assert list(df['day']) == [0, 1]
    assert list(df['capped']) == [0, 1]
    assert list(df['overtime_s']) == [0.0, 90.0]
    assert list(df['released_late_s']) == [0.0, 33.0]
    assert list(df['pick_seconds']) == [14400.0, 20000.0]
    assert list(df['put_seconds']) == [3000.0, 5000.0]
    assert list(df['unload_seconds']) == [1000.0, 1500.0]
    assert df['pick_utilization'].tolist() == pytest.approx([0.25, 20000.0 / (2 * S)])
    assert df['put_utilization'].tolist() == pytest.approx([3000.0 / S, 5000.0 / S])
    assert df['recv_utilization'].isna().all(), 'no receiving crew expected: NaN, not zero'


def test_the_carry_halves_ride_the_frame_and_a_pre_split_row_reads_nan_never_zero():
    ledger = _ledger()
    ledger[1].update({'standing': 16, 'standing_carry': 9,
                      'standing_carry_labour': 2, 'standing_carry_supply': 7})
    df = _sdf(ledger, _batches(), _work(), _expectations())
    assert list(df['standing_carry_labour']) == [0, 2]
    assert list(df['standing_carry_supply']) == [0, 7]
    # the pre-split vintage: the loader hands the halves back as None -> NaN, never 0
    old = [{k: (None if k.startswith('standing_carry_') else v) for k, v in r.items()}
           for r in _ledger()]
    df = _sdf(old, _batches(), _work(), _expectations())
    assert df['standing_carry_labour'].isna().all()
    assert df['standing_carry_supply'].isna().all()
    assert list(df['standing_carry']) == [0, 0] and list(df['capped']) == [0, 1]


def test_without_expectations_the_verdicts_render_and_every_utilization_is_nan():
    df = _sdf(_ledger(), _batches(), _work(), None)
    assert list(df['capped']) == [0, 1]
    for col in ('pick_utilization', 'put_utilization', 'recv_utilization'):
        assert df[col].isna().all()


def test_a_day_with_no_batch_carries_zero_batches_and_nan_legs():
    ledger = _ledger() + [{'day': 2, 'cap_end': 3 * S, 'end_s': 3 * S - 1, 'drained': 1,
                           'standing': 0, 'standing_put': 0, 'standing_dock': 0,
                           'standing_carry': 0, 'last_finish': 3 * S - 1}]
    df = _sdf(ledger, _batches(), _work(), _expectations())
    row = df[df['day'] == 2].iloc[0]
    assert row['n_batches'] == 0 and np.isnan(row['pick_seconds'])
    assert row['released_late_s'] == 0.0


def test_the_reading_vocabulary_tells_a_saving_from_a_failure():
    tol = 0.10
    below = {'realized': 0.15, 'expected': 0.30, 'delta': -0.15}
    assert audit._read('pick', below, is_base=False, tol=tol) == 'below: travel saving'
    assert audit._read('pick', below, is_base=True, tol=tol) == 'below band'
    assert audit._read('put', below, is_base=False, tol=tol) == 'below band'
    assert audit._read('put', {'realized': 0.5, 'expected': 0.3, 'delta': 0.2},
                       is_base=False, tol=tol) == 'ABOVE band'
    assert audit._read('recv', {'realized': 0.31, 'expected': 0.3, 'delta': 0.01},
                       is_base=False, tol=tol) == 'in band'
    assert audit._read('recv', {'absent': 'no crew'}, is_base=False, tol=tol) == 'n/a'


def test_the_share_reading_vocabulary_names_the_band_the_trend_and_the_carry():
    """The two flow clauses' one-word readings ("Split the missed-share clause into supply
    and labour"): a level with no stamped expectation says so; a trend past its tolerance
    or a carry past a day's capacity is named beside the band word, because each fails the
    clause on its own."""
    assert audit._read_share({}) == 'n/a'
    assert audit._read_share({'level': None}) == 'n/a'
    assert audit._read_share({'level': 0.07, 'in_band': None}) == 'n/a (no expectation)'
    assert audit._read_share({'level': 0.07, 'in_band': True, 'delta': -0.008,
                              'trend': 0.001, 'trend_tol': 0.02}) == 'in band'
    assert audit._read_share({'level': 0.15, 'in_band': False, 'delta': 0.13,
                              'trend': 0.0}) == 'ABOVE band'
    assert audit._read_share({'level': 0.01, 'in_band': False, 'delta': -0.06}) == 'below band'
    assert audit._read_share({'level': 0.10, 'in_band': True, 'delta': 0.0, 'trend': 0.12,
                              'trend_tol': 0.02}) == 'in band · trending'
    assert audit._read_share({'level': 0.10, 'in_band': True, 'delta': 0.0, 'trend': 0.12,
                              'trend_tol': 0.02, 'carry_bounded': False}) == \
        'in band · trending, carry past a day'


def test_the_inspection_table_carries_a_supply_and_a_cut_share_row_per_arm():
    from Optimization.simconfig import equilibrium as _eq
    head = ['arm', '2', '1', '1 · not delivered', '1']
    sup = _eq.Clause('supply', True, {'level': 0.0702, 'expected': 0.078, 'delta': -0.0078,
                                      'tol': 0.02, 'in_band': True, 'trend': 0.001,
                                      'trend_tol': 0.02})
    lab = _eq.Clause('labour', False, {'level': 0.0983, 'expected': None, 'delta': None,
                                       'tol': None, 'in_band': None, 'trend': 0.122,
                                       'trend_tol': 0.02, 'carry_bounded': True})
    v = _eq.Verdict(False, 0, 1, {'supply': sup, 'labour': lab})
    rows = audit._share_rows(head, v)
    assert [r[5:] for r in rows] == [
        ['supply share', '-', '0.078', '0.070', '±0.020', 'in band'],
        ['cut share', '-', '-', '0.0983', '-', 'n/a (no expectation) · trending']]
    assert all(r[:5] == [''] * 5 for r in rows), 'the head cells stay blank below the arm'
    # no verdict at all (no expectations for this leaf): the rows still render as n/a
    assert [r[5:] for r in audit._share_rows(head, None)] == [
        ['supply share', '-', '-', '-', '-', 'n/a'], ['cut share', '-', '-', '-', '-', 'n/a']]


def test_the_flags_line_names_an_override_and_the_closed_form():
    # No calibration record and no reference run ("Derive the expected-travel closed
    # form"): the expectations come from the closed form unless a flag overrode them.
    assert 'override' in audit._flags({'flags': {'overridden': True}})
    assert 'saturated' in audit._flags({'flags': {'saturated': True}})
    assert 'closed form' in audit._flags({'flags': {'overridden': False, 'saturated': False}})
    assert 'no staffing record' in audit._flags(None)


def test_the_evaluation_is_registered_gated_and_grouped():
    from Optimization import Performance_Evaluations  # noqa: F401 - populate the registry
    from Optimization.Performance_Evaluations import presets
    from Optimization.Performance_Evaluations.core import quantities as q
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    ev = EVAL_BY_KEY['throughput.audit']
    assert ev.family == 'throughput' and set(ev.shape) == {'ranked', 'inspection'}
    assert ev.quantities == ('days_capped',)
    assert q.BY_KEY['days_capped'].capability == 'shift_days'
    assert 'shift' in ev.needs
    assert 'throughput.audit' in presets._TRENDS
