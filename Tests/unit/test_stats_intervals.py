"""test_stats_intervals.py — a published interval must belong to the number beside it.

Four defects found by review of the rebuilt significance layer, each pinned here so it
cannot come back:

  1. a paired sample built with `improvement_pct` over a zero baseline gained fabricated
     "0.0% — no change" entries that dragged the centre and tightened the interval;
  2. the rank-biserial whisker came from the signed-rank NULL variance, so it depended
     only on n — identical on every row of a panel, and widest where the effect was most
     decisive;
  3. a median point estimate was printed beside a t-interval of the MEAN, which can
     exclude its own point;
  4. an aggregate paired test was fed arrays filtered independently per arm, so a
     dropout on one side silently mispaired the rest.

Run:  python -m pytest Tests/unit/test_stats_intervals.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from Optimization.Performance_Evaluations.common import chartkit
from Optimization.Performance_Evaluations.common.stats_core import (
    _boot_ci, _rank_biserial, _rank_biserial_ci)


# ── 1. undefined pairs never become ties ────────────────────────────────────────

def test_zero_baselines_do_not_dilute_the_paired_sample():
    """The regression: 40 zero-baseline batches among 35 real 20% improvements.  With
    the pairs kept as fabricated zeros the centre collapses toward zero; dropped, the
    sample reports what actually happened on the batches where it could be measured."""
    base = [0.0] * 40 + [100.0] * 35
    strat = [3.0] * 40 + [80.0] * 35
    kept = chartkit.improvement_pct_series(strat, base, lower_is_better=True)
    assert kept.size == 35
    assert float(np.median(kept)) == pytest.approx(20.0)
    fabricated = np.array([0.0] * 40 + [20.0] * 35)     # what the old code produced
    assert float(np.median(fabricated)) != pytest.approx(20.0)


# ── 2. the effect interval responds to the data, not just to n ──────────────────

def test_rank_biserial_interval_narrows_when_the_effect_is_decisive():
    n = 60
    rng = np.random.default_rng(7)
    base = rng.normal(100.0, 5.0, n)
    decisive = base - 20.0                       # wins every single pair
    marginal = base - rng.normal(0.4, 5.0, n)    # wins about half
    lo_d, hi_d = _rank_biserial_ci(base, decisive)
    lo_m, hi_m = _rank_biserial_ci(base, marginal)
    assert (hi_d - lo_d) < (hi_m - lo_m), (
        'the interval is not responding to the data — this is the null-variance bug, '
        'whose width depends only on n')
    assert _rank_biserial(base, decisive) == pytest.approx(1.0)
    assert hi_d <= 1.0 and lo_m >= -1.0


def test_rank_biserial_interval_is_deterministic_and_brackets_the_estimate():
    rng = np.random.default_rng(3)
    a = rng.normal(50.0, 8.0, 40)
    b = a - rng.normal(2.0, 6.0, 40)
    first = _rank_biserial_ci(a, b)
    assert first == _rank_biserial_ci(a, b), 'seeded resampling must be reproducible'
    r = _rank_biserial(a, b)
    assert first[0] <= r <= first[1]


def test_interval_helpers_refuse_a_sample_too_small_to_resample():
    assert all(np.isnan(v) for v in _boot_ci([1.0, 2.0]))
    assert all(np.isnan(v) for v in _rank_biserial_ci([1.0], [2.0]))


# ── 3. the interval belongs to the estimator it is printed with ─────────────────

def test_the_median_interval_contains_the_median_on_skewed_data():
    """Per-batch labor ratios are right-skewed: a few batches where the better layout
    avoids a long trip.  A t-interval of the MEAN can sit entirely above the median —
    the arrangement that printed an interval excluding its own point estimate."""
    skewed = np.concatenate([np.full(70, 1.8), np.array([28.0, 31.0, 35.0, 40.0, 44.0])])
    med = float(np.median(skewed))
    lo, hi = _boot_ci(skewed)
    assert lo <= med <= hi

    mean = float(np.mean(skewed))
    sem = float(np.std(skewed, ddof=1) / np.sqrt(skewed.size))
    t_lo, t_hi = mean - 1.99 * sem, mean + 1.99 * sem
    assert not (t_lo <= med <= t_hi), (
        'the fixture no longer reproduces the mean/median split it exists to pin')


def test_boot_ci_is_deterministic():
    rng = np.random.default_rng(11)
    sample = rng.normal(3.0, 1.0, 50)
    assert _boot_ci(sample) == _boot_ci(sample)


# ── 4. the aggregate by-initial test pairs by profile ───────────────────────────

def test_vs_baseline_rows_are_auditable_and_self_consistent():
    """The headline figure's claim must exist as numbers a reader can check: the point
    estimate inside its own interval, the effect oriented the same way as the percentage,
    and every arm present — including the ones that did not make the podium."""
    import pandas as pd
    from Optimization.Performance_Evaluations.tables.vs_baseline import (
        compute_vs_baseline)

    rng = np.random.default_rng(5)
    n = 60
    arms = {'uni_fifo_norsl': 1.00, 'opt_rank_labor_norsl': 0.93,
            'opt_rank_maxlabor_norsl': 1.06}

    class _Ctx:
        strategies = [dict(key=k, initial=k[:3], assignment=k.split('_', 1)[1],
                           reslot='noRSL', color=None, label=k) for k in arms]
        base = strategies[0]
        log = type('L', (), {'info': staticmethod(lambda *a: None),
                             'warning': staticmethod(lambda *a: None)})()

        def _frame(self, key):
            base = 100.0 + rng.normal(0, 4.0, n)
            return pd.DataFrame({
                'batch_id': np.arange(n), 'duration': base * arms[key],
                'completion_rate': 1.0 / (base * arms[key]),
                'thr_task': 1.0 / (base * arms[key]),
                'task_makespan': base * arms[key], 'sigma_fd': base * arms[key],
                'picking_pct': 90.0, 'queue_depth': 0.0, 'reload_moves': 0.0,
                'reorder_placements': 0.0, 'total_items': 1000.0, 'W': base,
                'is_outlier': 0})

        def batch_df(self, key):
            return self._frame(key)

        def task_df(self, key):
            f = self._frame(key)
            return pd.DataFrame({'batch_id': f['batch_id'], 'duration': f['duration'],
                                 'W': f['W']})

    rows = compute_vs_baseline(_Ctx())
    assert rows, 'no rows produced'
    keys = {r['strategy'] for r in rows}
    assert 'opt_rank_maxlabor_norsl' in keys, 'a losing arm must still get a row'
    assert 'uni_fifo_norsl' not in keys, 'the baseline is not compared with itself'
    for r in rows:
        if np.isfinite(r['ci_lo']) and np.isfinite(r['ci_hi']):
            assert r['ci_lo'] <= r['pct_median'] <= r['ci_hi'], r
        assert r['n_batches'] >= 3
        assert r['better'] in ('arm', 'baseline', 'tie')
        if r['better'] == 'arm':
            assert r['pct_median'] > 0
    # the 7%-faster arm must read as an improvement on a duration metric
    fast = [r for r in rows if r['strategy'] == 'opt_rank_labor_norsl'
            and r['metric'] == 'makespan']
    assert fast and fast[0]['pct_median'] > 0 and fast[0]['rank_biserial'] > 0


def test_vs_baseline_effect_sizes_point_the_same_way_as_the_percentage():
    """Every quantity in a row must agree on which direction is good.  Left raw, Hedges
    g comes out negative for an arm whose duration fell — an improvement printed beside
    a negative effect, in the same row, under one heading."""
    from Optimization.Performance_Evaluations.tables.vs_baseline import (
        compute_vs_baseline)
    import pandas as pd

    n = 40
    rng = np.random.default_rng(9)
    base = 100.0 + rng.normal(0, 3.0, n)
    arms = {'uni_fifo_norsl': 1.00, 'opt_fast_norsl': 0.90, 'opt_slow_norsl': 1.10}

    class _Ctx:
        strategies = [dict(key=k, initial=k[:3], assignment=k.split('_', 1)[1],
                           reslot='noRSL', color=None, label=k) for k in arms]
        base = strategies[0]
        log = type('L', (), {'info': staticmethod(lambda *a: None),
                             'warning': staticmethod(lambda *a: None)})()

        def _frame(self, key):
            v = base * arms[key]
            return pd.DataFrame({
                'batch_id': np.arange(n), 'duration': v, 'completion_rate': 1.0 / v,
                'thr_task': 1.0 / v, 'task_makespan': v, 'sigma_fd': v,
                'picking_pct': 90.0, 'queue_depth': 0.0, 'reload_moves': 0.0,
                'reorder_placements': 0.0, 'total_items': 1000.0, 'W': v,
                'is_outlier': 0})

        def batch_df(self, key):
            return self._frame(key)

        def task_df(self, key):
            f = self._frame(key)
            return pd.DataFrame({'batch_id': f['batch_id'], 'duration': f['duration'],
                                 'W': f['W']})

    for r in compute_vs_baseline(_Ctx()):
        if abs(r['pct_median']) < 0.5:
            continue                       # a tie says nothing about direction
        assert np.sign(r['hedges_g']) == np.sign(r['pct_median']), r
        assert np.sign(r['rank_biserial']) == np.sign(r['pct_median']), r


def test_aggregate_by_initial_pairs_profiles_and_drops_half_pairs():
    """A profile present for one arm only must shrink BOTH arms' samples, never just
    one: equal lengths achieved by independent filtering are what let a mispaired
    Wilcoxon publish a p from comparing different profiles to each other."""
    from Optimization.Performance_Evaluations.aggregate.tables import (
        compute_aggregate_by_initial)

    def prof(uni, opt):
        strat = []
        if uni is not None:
            strat.append({'key': 'uni_rank_labor_norsl', 'ss_thr': uni, 'ss_dur': uni,
                          'ss_thr_task': uni, 'ss_prod_hours': uni, 'ss_sigma': uni})
        if opt is not None:
            strat.append({'key': 'opt_rank_labor_norsl', 'ss_thr': opt, 'ss_dur': opt,
                          'ss_thr_task': opt, 'ss_prod_hours': opt, 'ss_sigma': opt})
        return {'strategies': strat}

    # five profiles; #2 is missing the opt arm and #4 is missing the uni arm, so an
    # independent filter would hand the test two arrays of four and mispair them.
    profiles = [prof(10.0, 9.0), prof(11.0, None), prof(12.0, 10.0),
                prof(None, 11.0), prof(14.0, 12.0)]
    combined, per_fn = compute_aggregate_by_initial(profiles)
    assert per_fn, 'no uni/opt pair was formed'
    det = per_fn['rank_labor_norsl']['metrics']
    for name, d in det.items():
        assert d['u'].size == d['o'].size == 3, (name, d['u'].size, d['o'].size)
    for row in combined:
        assert row['n_profiles'] == 3
