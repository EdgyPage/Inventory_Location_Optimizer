"""test_restock_selection.py — phase 1's hand-off ranks RULES, on hours, and never on nothing.

`run_restock_selection` is the seam between the inbound funnel's two phases: it reads a
phase-1 run's series documents, ranks the seventeen restock RULES on total production hours,
and writes `restock_selection.json` naming the arm set phase 2 will carry.

Four things about it are decisions rather than mechanics, and each has a way of failing that
produces a plausible answer instead of an error:

  * **The unit is a RULE.** `CHANNEL_RESTOCKS` filters on `Strategy.restock`, so choosing
    `tmin` necessarily takes `uni_tmin_norsl` AND `opt_tmin_norsl`. A selector that ranked
    arms would hand phase 2 an arm set it cannot express.
  * **Absence is loud.** The metric is capability-gated, so a run with no `work_events` writes
    NaN. A selector reading that as a tie would rank all seventeen rules on nothing.
  * **`fifo` rides along** whether or not it ranks: without it `run_channel_rollup` baselines
    every saving against an arbitrary arm, and it is also the order-blind negative control.
  * **The extension cap is real.** Only the faithful gain families have a bundle; every other
    family refuses at the first drain. A top-five holding four unfaithful families would be
    discovered when an arm died mid-sweep.

Run:  python -m pytest Tests/unit/test_restock_selection.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from Inbound.gain import FAITHFUL_GAIN_FAMILIES
from Optimization import run_restock_selection as sel


def _leaf(root, cell, pair, config, arms, channel=None):
    """Write one channel run's sim_meta.json + series.json. `arms` is {arm_key: seconds}."""
    parts = [root, cell, pair, config] + ([channel] if channel else [])
    d = os.path.join(*parts)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'sim_meta.json'), 'w') as f:
        json.dump({'name': config, 'run_dir': d, 'inventory': pair,
                   'channel': channel or 'store',
                   'strategies': [{'key': k} for k in arms]}, f)
    with open(os.path.join(d, 'series.json'), 'w') as f:
        json.dump({'strategies': [{'key': k, sel.METRIC_FIELD: v} for k, v in arms.items()]}, f)


def _layout(root, cells, pairs=('prof_a',)):
    """The run descriptor. Written because it is what a real run has — `runlayout.cells`
    infers cells from `sim_*.db` files only when there is none, and a scratch tree of JSON has
    no DBs to infer from."""
    from Optimization.runschema import contract
    with open(os.path.join(root, 'run_layout.json'), 'w') as f:
        json.dump({'version': 2, 'schema_id': contract.head(), 'kind': 'single',
                   'spec': 'inbound_select', 'base': os.path.basename(root),
                   'reference': cells[0], 'cells': [{'name': c} for c in cells],
                   'pairs': list(pairs), 'channels': ['store'], 'arms': None}, f)


def _arms(**by_rule):
    """{arm_key: seconds} for both arms of each named rule; `opt_` gets the value, `uni_` +10%."""
    out = {}
    for rule, secs in by_rule.items():
        out[f'opt_{rule}_norsl'] = secs
        out[f'uni_{rule}_norsl'] = secs * 1.1
    return out


#: Five faithful-bundle rules plus fifo, cheapest first by construction.  Values are seconds;
#: the artifact reports hours, so 3600 s reads as 1.00 h.
_CHEAP = dict(tmin=3600.0, tmax=7200.0, rank_popularity=10800.0, rank_random=14400.0,
              fifo=18000.0, comp=21600.0, expn=25200.0)


@pytest.fixture()
def run_root(tmp_path):
    root = str(tmp_path / 'comparison_20260831_000000')
    _leaf(root, 'k1_off', 'prof_a', 'store', _arms(**_CHEAP))
    _layout(root, ['k1_off'])
    return root


# ── the unit is a rule, and the score is hours ───────────────────────────────────

def test_the_ranking_is_by_rule_not_by_arm(run_root):
    doc = sel.select(run_root, k=3, log=lambda *_a: None)
    ranking = doc['channels']['store']['ranking']
    assert [r['rule'] for r in ranking] == [
        'tmin', 'tmax', 'rank_popularity', 'rank_random', 'fifo', 'comp', 'expn']
    assert all(len(r['arm_hours']) == 2 for r in ranking), 'a rule is two arms, always'


def test_a_rules_score_is_its_best_arm_and_both_are_recorded(run_root):
    """Selecting a rule takes both its arms, and what makes it worth carrying is that ONE of
    them performs — but a reader has to be able to see when the two disagree."""
    top = sel.select(run_root, log=lambda *_a: None)['channels']['store']['ranking'][0]
    assert top['rule'] == 'tmin'
    assert top['best_arm'] == 'opt_tmin_norsl'
    assert top['score_hours'] == pytest.approx(1.0)
    assert top['arm_hours']['uni_tmin_norsl'] == pytest.approx(1.1)


def test_hours_are_summed_across_profiles(tmp_path):
    """08's rule: hours are additive and physical, ranks are not. Two profiles of one hour
    each is a two-hour rule — and every rule is measured on the identical leaf set, so the
    sum stays like-for-like."""
    root = str(tmp_path / 'run')
    for pair in ('prof_a', 'prof_b'):
        _leaf(root, 'k1_off', pair, 'store', _arms(**_CHEAP))
    _layout(root, ['k1_off'], pairs=('prof_a', 'prof_b'))
    doc = sel.select(root, log=lambda *_a: None)
    store = doc['channels']['store']
    assert len(store['leaves']) == 2
    assert store['ranking'][0]['score_hours'] == pytest.approx(2.0)


def test_the_two_channels_are_ranked_separately(tmp_path):
    """Store and fulfillment are independent warehouses; one global list would rank two
    warehouses on one scale, and the two may legitimately carry different top-ks."""
    root = str(tmp_path / 'run')
    _leaf(root, 'k1_off', 'prof_a', 'store', _arms(**_CHEAP), channel='store')
    _leaf(root, 'k1_off', 'prof_a', 'ful_calibrated',
          _arms(**{**_CHEAP, 'tmin': 99999.0, 'expn': 60.0}), channel='fulfillment')
    _layout(root, ['k1_off'])
    doc = sel.select(root, k=1, log=lambda *_a: None)
    assert doc['channels']['store']['chosen'] == ['tmin']
    assert doc['channels']['fulfillment']['chosen'] == ['expn']


# ── absence must be loud ─────────────────────────────────────────────────────────

def test_a_rule_with_a_missing_reading_is_excluded_and_named(tmp_path):
    """A partial sum would rank a rule measured on FEWER profiles as cheaper than all its
    rivals — the most plausible wrong answer this module can produce."""
    root = str(tmp_path / 'run')
    _leaf(root, 'k1_off', 'prof_a', 'store', _arms(**_CHEAP))
    partial = _arms(**_CHEAP)
    partial['opt_tmin_norsl'] = None            # the capability-gated NaN, as JSON writes it
    _leaf(root, 'k1_off', 'prof_b', 'store', partial)
    _layout(root, ['k1_off'], pairs=('prof_a', 'prof_b'))
    said = []
    doc = sel.select(root, log=said.append)
    rows = {r['rule']: r for r in doc['channels']['store']['ranking']}
    assert rows['tmin']['rank'] is None and rows['tmin']['score_hours'] is None
    assert rows['tmin']['missing'][0]['leaves'] == ['prof_b/store']
    assert rows['tmax']['rank'] == 1, 'the rest still rank, cheapest first'
    assert any('tmin' in m and 'excluded' in m for m in said)
    assert 'tmin' not in doc['channels']['store']['chosen']


def test_an_arm_key_outside_the_strategy_grid_is_dropped_and_named(tmp_path):
    """`uni_rank_labor_norsl` splits into three fields whose middle one contains underscores,
    so every hand-rolled parse of an arm key is a guess. Unknown keys must not be bucketed
    into a rule that exists."""
    root = str(tmp_path / 'run')
    arms = _arms(**_CHEAP)
    arms['uni_not_a_rule_norsl'] = 1.0
    _leaf(root, 'k1_off', 'prof_a', 'store', arms)
    _layout(root, ['k1_off'])
    said = []
    doc = sel.select(root, log=said.append)
    assert doc['channels']['store']['unknown_arms'] == ['uni_not_a_rule_norsl']
    assert any('not in the strategy grid' in m for m in said)


# ── the fifo rider and the extension cap ─────────────────────────────────────────

def test_fifo_rides_along_even_when_it_does_not_rank(run_root):
    """It is the analysis baseline AND the order-blind negative control. Without it every
    phase-2 saving is measured against an arbitrary arm and looks fine."""
    store = sel.select(run_root, k=2, log=lambda *_a: None)['channels']['store']
    assert store['chosen'] == ['tmin', 'tmax'], 'fifo is not one of the k'
    assert 'fifo' in store['arms']
    assert store['arms'] == sorted(set(store['chosen'] + ['fifo']))


def test_the_extension_cap_backfills_from_the_faithful_set(tmp_path):
    """Only the faithful families have a gain bundle; every other refuses at the first drain.
    A top-five holding four unfaithful families would be discovered mid-sweep, not here."""
    root = str(tmp_path / 'run')
    # Four unfaithful rules ranked ahead of two faithful ones.
    _leaf(root, 'k1_off', 'prof_a', 'store',
          _arms(comp=1.0, expn=2.0, cmin=3.0, cmax=4.0, tmin=5.0, tmax=6.0, fifo=7.0))
    _layout(root, ['k1_off'])
    said = []
    store = sel.select(root, k=4, extension_cap=2, log=said.append)['channels']['store']
    assert store['needs_bundle_extension'] == ['comp', 'expn']
    assert store['backfilled_past'] == ['cmin', 'cmax']
    assert store['chosen'] == ['comp', 'expn', 'tmin', 'tmax']
    assert any('extension cap' in m for m in said)


def test_the_mandatory_rider_is_reported_as_needing_a_bundle_of_its_own(run_root):
    """A gain cell builds a bundle for EVERY arm in the set, not only for the arms a gain
    policy was chosen for — so the `fifo` rider, which is not optional, refuses at worker
    startup unless the evaluator serves it. It sits OUTSIDE the extension cap because the cap
    governs which optional families are worth extending, and this one has no opt-out."""
    store = sel.select(run_root, k=2, log=lambda *_a: None)['channels']['store']
    assert 'fifo' not in FAITHFUL_GAIN_FAMILIES, 'the premise of this test'
    assert store['rider_needs_bundle_extension'] == ['fifo']
    assert 'fifo' not in store['needs_bundle_extension'], 'the rider must not eat the cap'


def test_a_gain_bundle_really_is_refused_for_the_rider():
    """The finding itself, not a restatement of it: phase 2 cannot run a gain cell over an arm
    set containing `fifo` until `_gain_bundle_for` serves it."""
    from types import SimpleNamespace

    from Optimization.config.strategies import STRATEGY_BY_KEY
    from Optimization.simdriver.strategy_runner import _gain_bundle_for
    mgr = SimpleNamespace(_zoning_enabled=False, _aisle_sku_sets={}, _aisle_idx_sets={},
                          _aisle_demand_sum={})
    spec = {'fee_threshold_days': 2.0, 'urgency_horizon_days': 0.0}
    with pytest.raises(ValueError, match='no faithful gain bundle'):
        _gain_bundle_for(STRATEGY_BY_KEY['uni_fifo_norsl'], mgr, None, {}, 1.0, spec)
    # ...and a faithful family still builds, so the refusal is about the family, not the stub.
    assert _gain_bundle_for(STRATEGY_BY_KEY['uni_tmin_norsl'], mgr, None, {}, 1.0, spec)


def test_the_faithful_set_is_the_one_the_driver_actually_accepts():
    """Two copies of this list is a bug with a delay on it: the selector would keep proposing
    rules the evaluator refuses, or refuse rules it serves."""
    import inspect

    from Optimization.simdriver import strategy_runner
    src = inspect.getsource(strategy_runner._gain_bundle_for)
    for fam in FAITHFUL_GAIN_FAMILIES:
        assert f"'{fam}'" in src, f'{fam} is declared faithful but has no branch in the driver'
    assert 'FAITHFUL_GAIN_FAMILIES' in src, 'the refusal message no longer names the one list'


# ── the artifact, and the one shape it refuses ───────────────────────────────────

def test_the_artifact_lands_at_the_run_root_and_states_its_metric(run_root):
    doc = sel.select(run_root, log=lambda *_a: None)
    out = os.path.join(run_root, 'restock_selection.json')
    assert os.path.exists(out)
    with open(out) as f:
        on_disk = json.load(f)
    assert on_disk == doc
    assert on_disk['metric']['quantity'] == 'total_production_time'
    assert on_disk['metric']['series_field'] == 'ss_prod_total'
    assert 'ratios' in on_disk['metric']['not_read'], (
        'the artifact must say WHY the cross-profile CSV is not the source: it normalizes '
        'each profile to its own baseline, so it holds ratios rather than hours')
    assert on_disk['baseline_rule'] == 'fifo'
    assert on_disk['run']['cell'] == 'k1_off'


def test_all_seventeen_rules_are_recorded_not_just_the_cut(tmp_path):
    """The full ranking, so a later reader can see how close the decision was."""
    from Optimization.config.strategies import _RESTOCKS
    root = str(tmp_path / 'run')
    every = {k: 3600.0 * (i + 1) for i, (k, *_rest) in enumerate(_RESTOCKS)}
    _leaf(root, 'k1_off', 'prof_a', 'store', _arms(**every))
    _layout(root, ['k1_off'])
    store = sel.select(root, log=lambda *_a: None)['channels']['store']
    assert len(store['ranking']) == len(_RESTOCKS) == 17
    assert len(store['chosen']) == 5


def test_a_multi_cell_run_is_refused(tmp_path):
    """Phase 1 is ONE cell by design: two layouts cannot be ranked on one hours scale, and
    silently picking a cell would hide which one the decision came from."""
    root = str(tmp_path / 'run')
    for cell in ('k1_off', 'k2_l10_off'):
        _leaf(root, cell, 'prof_a', 'store', _arms(**_CHEAP))
    _layout(root, ['k1_off', 'k2_l10_off'])
    with pytest.raises(SystemExit, match='ONE cell'):
        sel.select(root, log=lambda *_a: None)


def test_an_unanalyzed_run_says_what_to_run(tmp_path):
    root = str(tmp_path / 'run')
    os.makedirs(os.path.join(root, 'k1_off', 'prof_a', 'store'))
    with open(os.path.join(root, 'k1_off', 'prof_a', 'store', 'sim_meta.json'), 'w') as f:
        json.dump({'name': 'store', 'run_dir': 'x', 'strategies': []}, f)
    _layout(root, ['k1_off'])
    with pytest.raises(SystemExit, match='analyze_run'):
        sel.select(root, log=lambda *_a: None)
