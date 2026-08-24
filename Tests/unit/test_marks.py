"""test_marks.py — a stance cannot be mislabelled, and an interval estimates the dot.

`common/marks.py` exists for two properties that no amount of care at a call site was
going to deliver, because both failure modes are invisible at the call site:

  1. **No caller can hand in a percentage.** Every entry point takes RAW values in the
     quantity's own units plus a `view`, and derives the stance itself. Two figures shipped
     under the wrong prefix before this existed — `absolute_compute_cost.png` plotting a
     delta, and `delta_travel_vs_baseline.png` plotting a percent — and both passed every
     save-time check, because each of those checks compared a declaration against another
     declaration and none ever saw a number.
  2. **A comparison view's interval is the interval OF THE COMPARISON.** The dot and its
     whiskers must estimate the same quantity, and that quantity must be the contrast, not
     the arm's own spread wearing a percentage. Published pages carry the sentence "where
     an interval crosses zero the arm has not been shown to move travel at all"; it is only
     true of the bootstrap of the paired improvements.

Run:  python -m pytest Tests/unit/test_marks.py -q
"""
from __future__ import annotations

import inspect

import matplotlib
matplotlib.use('Agg')

import numpy as np
import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations.common import chartkit, marks
from Optimization.Performance_Evaluations.core import quantities as Q
from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY


def _arms(n=3):
    return [{'key': f'uni_a{i}', 'assignment': f'A{i}', 'initial': 'uni', 'label': f'A{i}'}
            for i in range(n)] + [{'key': 'uni_fifo', 'assignment': 'FIFO',
                                   'initial': 'uni', 'label': 'FIFO'}]


# ── 1. the stance is derived, never supplied ─────────────────────────────────────

def test_no_entry_point_accepts_a_precomputed_stance():
    """The property, checked on the signatures rather than trusted.

    A parameter named for an answer — `pct`, `improvement`, `delta`, `values_pct` — is the
    door this module exists to close.
    """
    banned = {'pct', 'percent', 'improvement', 'improvements', 'delta', 'deltas',
              'ratio', 'values_pct', 'pct_values'}
    for fn in (marks.ranked, marks.serial):
        names = set(inspect.signature(fn).parameters)
        assert not (names & banned), f'{fn.__name__} accepts {sorted(names & banned)}'


def test_the_percent_view_is_the_one_sign_convention():
    """Positive = better, for a lower-is-better quantity as much as a higher-is-better one."""
    lower = Q.BY_KEY['production_time']        # lower is better
    higher = Q.BY_KEY['throughput']            # higher is better
    # arm beats a baseline of 100 by being SMALLER
    v, label = marks._view_values(lower, [90.0], [100.0], 'percent', [90.0, 100.0])
    assert v[0] == pytest.approx(10.0), 'a lower-is-better win must read positive'
    assert 'higher = better' in label
    # ...and by being LARGER for the other direction
    v2, _ = marks._view_values(higher, [110.0], [100.0], 'percent', [110.0, 100.0])
    assert v2[0] == pytest.approx(10.0)


def test_the_delta_view_is_oriented_too():
    """An unoriented difference makes the reader remember the quantity's direction to know
    which end of the axis is the win, and half of them do not."""
    lower = Q.BY_KEY['sigma_fd']
    v, label = marks._view_values(lower, [90.0], [100.0], 'delta', [90.0, 100.0])
    assert v[0] > 0, 'a lower-is-better improvement must read positive in the delta view'
    assert 'higher = better' in label


def test_an_unknown_view_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match='cannot draw view'):
        marks._view_values(Q.BY_KEY['sigma_fd'], [1.0], [1.0], 'effect', [1.0])


# ── 2. the interval estimates the same thing as the dot ──────────────────────────

def test_the_comparison_point_and_its_interval_come_from_one_sample():
    """Four retired sites reported a median with a bootstrap of that median and a fifth a
    mean with a t-interval — a split `stats_core` writes a comment forbidding."""
    q = Q.BY_KEY['sigma_fd']
    rng = np.random.default_rng(7)
    base = rng.normal(100.0, 5.0, 60)
    arm = base * 0.95
    series = marks._paired_series(q, (arm, base), 'percent')
    assert series.size == 60
    assert float(np.median(series)) == pytest.approx(5.0, abs=0.5)


def test_a_paired_series_drops_the_undefined_pairs_only():
    q = Q.BY_KEY['sigma_fd']
    arm = np.array([90.0, 95.0, np.nan, 80.0])
    base = np.array([100.0, 0.0, 100.0, 100.0])       # a zero baseline is undefined
    series = marks._paired_series(q, (arm, base), 'percent')
    assert series.size == 2, 'exactly the zero-baseline pair and the NaN pair should drop'


def test_a_paired_series_is_empty_when_the_arms_do_not_line_up():
    """Handing a paired test two arrays of different lengths is how a comparison ends up
    pairing batch i of one arm against a different batch i of the other."""
    q = Q.BY_KEY['sigma_fd']
    assert marks._paired_series(q, (np.arange(5.0), np.arange(4.0)), 'percent').size == 0


def test_the_paired_delta_is_oriented_like_the_paired_percent():
    q = Q.BY_KEY['sigma_fd']                     # lower is better
    arm, base = np.full(10, 90.0), np.full(10, 100.0)
    assert np.all(marks._paired_series(q, (arm, base), 'delta') > 0)
    assert np.all(marks._paired_series(q, (arm, base), 'percent') > 0)


def test_a_comparison_view_without_a_pairing_draws_no_interval():
    """Honest about what it does not know, rather than substituting a different statistic."""
    q = Q.BY_KEY['sigma_fd']
    arms = _arms()
    ch = chartkit.make(panels=1, legend='none')
    drawn = marks.ranked(ch, [(s, 100.0 + i) for i, s in enumerate(arms)],
                         quantity=q, view='percent', baseline=arms[-1], strategies=arms)
    assert drawn == 3
    assert not [c for c in ch.ax.containers if getattr(c, 'has_xerr', False)]
    ch.abandon()


def test_a_pairing_produces_an_interval_per_arm():
    q = Q.BY_KEY['sigma_fd']
    arms = _arms()
    rng = np.random.default_rng(3)
    base = rng.normal(100.0, 4.0, 40)
    paired = {s['key']: (base * (0.9 + 0.02 * i), base) for i, s in enumerate(arms)}
    ch = chartkit.make(panels=1, legend='none')
    drawn = marks.ranked(ch, [(s, 100.0) for s in arms], quantity=q, view='percent',
                         baseline=arms[-1], strategies=arms, paired=paired)
    assert drawn == 3
    assert [c for c in ch.ax.containers if getattr(c, 'has_xerr', False)], \
        'a pairing was supplied and no interval was drawn'
    ch.abandon()


# ── 3. the ranked mark's reading conventions ─────────────────────────────────────

def test_the_baseline_is_a_datum_in_the_absolute_view_and_absent_from_a_comparison():
    """It cannot be its own competitor, and at zero it is the axis, not a bar."""
    q = Q.BY_KEY['sigma_fd']
    arms = _arms()
    entries = [(s, 100.0 + i) for i, s in enumerate(arms)]
    ch = chartkit.make(panels=1, legend='none')
    assert marks.ranked(ch, entries, quantity=q, view='absolute', baseline=arms[-1],
                        strategies=arms) == 4
    ch.abandon()
    ch2 = chartkit.make(panels=1, legend='none')
    assert marks.ranked(ch2, entries, quantity=q, view='percent', baseline=arms[-1],
                        strategies=arms) == 3
    ch2.abandon()


def test_nothing_drawable_returns_zero_rather_than_an_empty_chart():
    q = Q.BY_KEY['sigma_fd']
    arms = _arms()
    ch = chartkit.make(panels=1, legend='none')
    assert marks.ranked(ch, [(s, float('nan')) for s in arms], quantity=q,
                        view='absolute', baseline=arms[-1], strategies=arms) == 0
    ch.abandon()


# ── 4. the bespoke ledger ────────────────────────────────────────────────────────

def test_every_bespoke_entry_names_a_real_evaluation_view_with_a_real_reason():
    for key, reason in marks.BESPOKE.items():
        eval_key, _, view = key.partition(':')
        assert eval_key in EVAL_BY_KEY, key
        assert view in EVAL_BY_KEY[eval_key].views, f'{key} excuses a view it does not have'
        assert len(reason.split()) >= 8, key


def test_the_bespoke_ledger_is_capped():
    assert len(marks.BESPOKE) <= marks.BESPOKE_CEILING
