"""test_census.py — the count/span claim a page makes must be the one the data supports.

`stats_core.census` is the general form of a count the site kept making by hand ("positive
in all 68 same-rule comparisons, median +44.8%, range 28.5-39.8%").  Because a page now
renders it verbatim instead of restating it, every convention baked into it is published:

  1. direction — `better='lower'` must count a NEGATIVE value as positive evidence, or an
     improvement on a lower-is-better metric reads as a loss;
  2. ties — excluded from the sign test (the standard convention) but still counted and
     still inside the span, so the exclusion is visible;
  3. undefined values — dropped and counted, never silently folded in as zeros, which is
     the same defect Tests/unit/test_stats_intervals.py pins for the paired sample;
  4. orientation of the span — reported in the ORIGINAL sign, so a reader comparing it to
     a number on the page sees the sign the page shows;
  5. grouping — one row per group PLUS an overall row, every row carrying every group
     column, so the result writes straight to a CSV;
  6. an all-tie group reports NaN, not p=1 — "no direction to be right about" is not
     evidence of no effect.

Run:  python -m pytest Tests/unit/test_census.py -q
"""
from __future__ import annotations

import math

import pytest

from Optimization.Performance_Evaluations.common.stats_core import census


def _rows(vals, **fixed):
    return [dict(v=v, **fixed) for v in vals]


# ── 1. direction ────────────────────────────────────────────────────────────────

def test_lower_is_better_counts_negatives_as_wins():
    """A labor saving is a negative delta; it must not be counted as a loss."""
    rows = _rows([-2.0, -1.0, -3.0, 0.5])
    hi = census(rows, value='v', better='higher')[0]
    lo = census(rows, value='v', better='lower')[0]
    assert hi['n_pos'] == 1 and hi['n_neg'] == 3
    assert lo['n_pos'] == 3 and lo['n_neg'] == 1
    # ...and the span keeps the reader's orientation in BOTH cases.
    assert lo['min'] == pytest.approx(-3.0)
    assert lo['max'] == pytest.approx(0.5)
    assert lo['median'] == pytest.approx(-1.5)


def test_an_unknown_direction_is_refused():
    with pytest.raises(ValueError):
        census(_rows([1.0]), value='v', better='up')


# ── 2. ties ─────────────────────────────────────────────────────────────────────

def test_ties_leave_the_sign_test_but_stay_in_the_count_and_the_span():
    c = census(_rows([1.0, 1.0, 0.0, 0.0, 0.0, -1.0]), value='v')[0]
    assert (c['n'], c['n_pos'], c['n_neg'], c['n_zero']) == (6, 2, 1, 3)
    assert c['win_rate'] == pytest.approx(2 / 3)          # ties out of the denominator
    assert c['min'] == pytest.approx(-1.0)                # ...but inside the span
    assert c['n'] == c['n_pos'] + c['n_neg'] + c['n_zero']


def test_an_all_tie_group_reports_no_p_value_rather_than_one():
    c = census(_rows([0.0, 0.0, 0.0]), value='v')[0]
    assert c['n_zero'] == 3
    assert math.isnan(c['p_sign']) and math.isnan(c['win_rate'])


# ── 3. undefined values ─────────────────────────────────────────────────────────

def test_nans_and_nones_are_dropped_and_counted_not_treated_as_ties():
    c = census(_rows([2.0, float('nan'), None, 4.0]), value='v')[0]
    assert c['n'] == 2 and c['n_nan'] == 2 and c['n_zero'] == 0
    assert c['median'] == pytest.approx(3.0)


# ── 4. the claim itself ─────────────────────────────────────────────────────────

def test_a_clean_sweep_is_significant_and_its_interval_reaches_one():
    """The published shape: every comparison positive, so the CI's upper edge is 1.0."""
    c = census(_rows([0.5] * 20), value='v')[0]
    assert c['n_pos'] == 20 and c['n_neg'] == 0
    assert c['win_rate'] == pytest.approx(1.0)
    assert c['p_sign'] < 1e-5
    assert c['ci_hi'] == pytest.approx(1.0)
    assert 0.0 < c['ci_lo'] < 1.0            # exact interval, so never a degenerate point


def test_a_coin_flip_is_not_significant():
    c = census(_rows([1.0] * 10 + [-1.0] * 10), value='v')[0]
    assert c['win_rate'] == pytest.approx(0.5)
    assert c['p_sign'] > 0.4


# ── 5. grouping ─────────────────────────────────────────────────────────────────

def test_groups_plus_an_overall_row_all_carrying_every_column():
    rows = _rows([1.0, 2.0], channel='store') + _rows([-1.0], channel='fulfillment')
    out = census(rows, value='v', group_by=('channel',))
    assert len(out) == 3                                   # two groups + overall
    assert [r['group_channel'] for r in out] == ['store', 'fulfillment', None]
    assert out[-1]['n'] == 3 and out[-1]['n_pos'] == 2     # overall spans both groups
    keys = {frozenset(r) for r in out}
    assert len(keys) == 1, 'every row must carry every column, for csv.DictWriter'


def test_a_derived_group_needs_a_name():
    rows = _rows([1.0], channel='store')
    named = census(rows, value='v',
                   group_by=(('regime', lambda r: r['channel'].upper()),))
    assert named[0]['group_regime'] == 'STORE'
    with pytest.raises(ValueError):
        census(rows, value='v', group_by=(lambda r: r['channel'],))


def test_ungrouped_returns_exactly_one_row():
    assert len(census(_rows([1.0, 2.0]), value='v')) == 1


def test_value_may_be_a_callable_and_rows_may_be_objects():
    class Row:
        def __init__(self, a, b):
            self.a, self.b = a, b
    rows = [Row(3.0, 1.0), Row(1.0, 2.0)]
    c = census(rows, value=lambda r: r.a - r.b)[0]
    assert (c['n_pos'], c['n_neg']) == (1, 1)
    assert census(rows, value='a')[0]['median'] == pytest.approx(2.0)
