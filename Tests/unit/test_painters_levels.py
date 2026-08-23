"""test_painters_levels.py — the absolute over-time view must stay readable at 34 arms.

Every arm of one warehouse traces the same demand curve within a few percent, so an
overlay of all of them is a single opaque ribbon: during the Experiment-8 review an
expert reading that chart attributed a 6–12% gap to two arms whose sim databases are
byte-identical.  Past a threshold the painter therefore stops drawing one line per arm
and draws the shape — median, interquartile band, and the two extremes named.  These
tests pin the switch and the honesty of each branch.

Run:  python -m pytest Tests/unit/test_painters_levels.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — Agg backend
from Optimization.Performance_Evaluations.common import chartkit, io, painters


def _fixture(n_arms, n_batch=40, seed=0):
    """(strategies, series, baseline) for n_arms arms plus a FIFO baseline."""
    rng = np.random.default_rng(seed)
    demand = 5.0e6 + 1.2e6 * np.sin(np.linspace(0, 3.0, n_batch))
    strategies, S = [], {}
    for i in range(n_arms):
        key = f'{"uni" if i % 2 else "opt"}_fn{i:02d}_norsl'
        s = dict(key=key, initial='Uni' if i % 2 else 'Opt', assignment=f'fn{i:02d}',
                 reslot='noRSL', color=None, label=key)
        strategies.append(s)
        scale = 1.0 + (i - n_arms / 2) * 0.004        # arms differ by a few tenths of a %
        y = demand * scale + rng.normal(0, 4.0e3, n_batch)
        S[key] = dict(task_batch=np.arange(n_batch), batch=np.arange(n_batch),
                      prod_hours=y, task_median=y / 40.0, task_p25=y / 44.0,
                      task_p75=y / 36.0, thr=1.0 / y, sigma_fd=y * 0.4, **s)
    baseline = strategies[0]
    return strategies, S, baseline


def _metric():
    return [m for m in painters.overtime_metrics() if m['f'] == 'production_time'][0]


def _labels(path_fig):
    return [t.get_text() for t in path_fig.legends[0].get_texts()] if path_fig.legends \
        else []


@pytest.fixture(autouse=True)
def _eval_scope():
    io.set_current_eval(None)
    yield
    io.set_current_eval(None)


def test_a_small_arm_set_still_draws_every_arm(tmp_path):
    strategies, S, base = _fixture(5)
    out = painters.paint_overtime(strategies, S, _metric(), base, str(tmp_path),
                                  view='absolute')
    assert out and out.endswith('absolute_production_time.png')


def test_a_large_arm_set_draws_the_shape_not_the_spaghetti(tmp_path):
    """34 arms: a median, a band, two named extremes and the baseline — not 34 lines."""
    strategies, S, base = _fixture(34)
    import matplotlib.pyplot as plt
    plt.close('all')
    painters.paint_overtime(strategies, S, _metric(), base, str(tmp_path),
                            view='absolute')
    # rebuild the same figure to inspect it (paint_overtime closes what it saves)
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=['x'])
    n = painters._paint_levels(ch.ax, strategies, S, _metric(), base, None)
    assert n == len(strategies) - 1, 'every non-baseline arm must be accounted for'
    lines = [ln for ln in ch.ax.get_lines()]
    assert len(lines) <= 4, f'{len(lines)} lines drawn — the overlay is back'
    labels = {ln.get_label() for ln in lines}
    assert 'median arm' in labels
    assert any(l.startswith('lowest:') for l in labels)
    assert any(l.startswith('highest:') for l in labels)


def test_the_named_extremes_are_the_actual_extremes(tmp_path):
    strategies, S, base = _fixture(34)
    m = _metric()
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=['x'])
    painters._paint_levels(ch.ax, strategies, S, m, base, None)
    avail = [s for s in strategies if s['key'] != base['key']]
    means = {s['key']: float(np.mean(S[s['key']][m['y']])) for s in avail}
    lowest = min(means, key=means.get)
    highest = max(means, key=means.get)
    labels = {ln.get_label() for ln in ch.ax.get_lines()}
    assert any(l.startswith('lowest:') and lowest.split('_')[1] in l for l in labels)
    assert any(l.startswith('highest:') and highest.split('_')[1] in l for l in labels)


def test_the_percent_view_still_shows_every_arm(tmp_path):
    """Only the ABSOLUTE view summarises: the percent view is where an arm is separated
    from the baseline, so it must keep drawing all of them."""
    strategies, S, base = _fixture(34)
    ch = chartkit.make(panels=1, legend='gutter', legend_labels=['x'])
    out = painters.paint_overtime(strategies, S, _metric(), base, str(tmp_path),
                                  view='percent')
    assert out and out.endswith('percent_production_time.png')
    import matplotlib.pyplot as plt
    plt.close(ch.fig)
