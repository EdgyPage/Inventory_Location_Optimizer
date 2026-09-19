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

Phase 2 runs the COUPLED site dock, which adds four more of the same kind — each one a way of
handing phase 2 a plausible answer instead of an error:

  * **The pairing is the rank DIAGONAL, built from `chosen`.** `arms` is `sorted(set(...))`,
    so a pairing built from it is alphabetical: still a list of pairs, still the right length,
    and wrong in a way no downstream code can detect.
  * **Ragged rankings zip to the common length and SAY so.** A silent truncation hands phase 2
    a shorter campaign than the ranking supports.
  * **The extension cap counts the UNION across channels.** Extending `_gain_bundle_for` is
    work per FAMILY, site-wide, so a per-channel cap of N can commit 2N — a bound that can be
    silently doubled is not bounding the thing it exists to bound.
  * **A coupled run root is refused.** Phase 1 is uncoupled by definition; pointed at a coupled
    root the selector would rank coupled leaves and emit a hand-off indistinguishable from a
    real one. It is the only place in the hand-off where a wrong-phase number enters silently.

Run:  python -m pytest Tests/unit/test_restock_selection.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from Optimization.config.strategies import FAITHFUL_GAIN_FAMILIES
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


def _layout(root, cells, pairs=('prof_a',), coupled=None):
    """The run descriptor. Written because it is what a real run has — `runlayout.cells`
    infers cells from `sim_*.db` files only when there is none, and a scratch tree of JSON has
    no DBs to infer from.

    `coupled` is left ABSENT unless a test asks for it: that is the shape of every run that
    exists today, and the selector's guard has to be inert on it."""
    from Optimization.runschema import contract
    doc = {'version': 2, 'schema_id': contract.head(), 'kind': 'single',
           'spec': 'inbound_select', 'base': os.path.basename(root),
           'reference': cells[0], 'cells': [{'name': c} for c in cells],
           'pairs': list(pairs), 'channels': ['store'], 'arms': None}
    if coupled is not None:
        doc['coupled'] = coupled
    with open(os.path.join(root, 'run_layout.json'), 'w') as f:
        json.dump(doc, f)


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


def test_the_mandatory_rider_needs_no_extension(run_root):
    """A gain cell builds a bundle for EVERY arm in the set, not only for the arms a gain
    policy was chosen for — so the `fifo` rider, which is not optional, would refuse at worker
    startup unless the evaluator serves it. It does since ticket 21 (the uniform adapter), so
    this field reports empty; it stays because a future BASELINE_RULE change must surface here
    and not 480 work units into phase 2. The rider is outside the extension cap either way."""
    store = sel.select(run_root, k=2, log=lambda *_a: None)['channels']['store']
    assert 'fifo' in FAITHFUL_GAIN_FAMILIES, 'the rider must have a faithful bundle'
    assert store['rider_needs_bundle_extension'] == []
    assert 'fifo' not in store['needs_bundle_extension'], 'the rider must not eat the cap'
    assert sel.BASELINE_RULE == 'fifo', (
        'the two assertions above only cover the rider while it IS fifo')


def test_a_gain_bundle_really_is_built_for_the_rider():
    """The thing itself, not a restatement of it: a gain cell over an arm set containing
    `fifo` gets a bundle for BOTH fifo arms, and the refusal still fires for a family the
    evaluator genuinely cannot price."""
    from types import SimpleNamespace

    from Optimization.config.strategies import STRATEGY_BY_KEY
    from Optimization.simdriver.strategy_runner import _gain_bundle_for
    mgr = SimpleNamespace(_zoning_enabled=False, _aisle_sku_sets={}, _aisle_idx_sets={},
                          _aisle_demand_sum={})
    spec = {'fee_threshold_days': 2.0, 'urgency_horizon_days': 0.0}
    for key in ('uni_fifo_norsl', 'opt_fifo_norsl'):
        bundle = _gain_bundle_for(STRATEGY_BY_KEY[key], mgr, None, {}, 1.0, spec)
        assert bundle.uniform, f'{key} must take the uniform adapter, not the merge default'
        assert bundle.pool_factory is None
    # ...and an unfaithful family still refuses, so the branch above is about fifo and not a
    # gate that stopped gating.  `rank_maxlabor` is the worst-case control and was never in
    # phase 1's top five, so it is outside the set on purpose -- its MIRROR `rank_minlabor`
    # is inside it (ticket 20), which is what makes this a gate and not a family prefix.
    with pytest.raises(ValueError, match='no faithful gain bundle'):
        _gain_bundle_for(STRATEGY_BY_KEY['uni_rank_maxlabor_norsl'], mgr, None, {}, 1.0, spec)


def test_the_faithful_set_is_the_one_the_driver_actually_accepts():
    """Two copies of this list is a bug with a delay on it: the selector would keep proposing
    rules the evaluator refuses, or refuse rules it serves.

    Since ticket 03 there is ONE copy -- `FAITHFUL_GAIN_FAMILIES` is derived from which
    `PlacementPolicy` declares a `gain` adapter, so "declared faithful" and "has a branch in
    the driver" are the same statement rather than two that can drift. What is left to check
    is that every declared adapter is one the driver can actually serve: a record naming
    `pool` with no factory, or a factory with no record, would be the same class of bug one
    level down.
    """
    import inspect

    from Optimization.config.strategies import POLICY_BY_KEY
    from Optimization.simdriver import strategy_runner

    declared = {k for k, pol in POLICY_BY_KEY.items() if pol.gain}
    assert set(FAITHFUL_GAIN_FAMILIES) == declared, (
        f'the derived set and the records disagree: '
        f'{sorted(set(FAITHFUL_GAIN_FAMILIES) ^ declared)}')
    assert declared, 'no family declares a gain adapter; this would pass vacuously'

    for fam in FAITHFUL_GAIN_FAMILIES:
        pol = POLICY_BY_KEY[fam]
        if pol.gain == 'pool':
            assert fam in strategy_runner._POOL_FACTORIES, (
                f'{fam} declares a pool adapter with no factory; the driver refuses at '
                f'import, but only if that check is still there')
            assert pol.ledger_terms, (
                f'{fam} opens a pool over copies of nothing -- a virtual placement would '
                f'advance the real warehouse')

    # And a family that declares nothing must NOT be in the set.
    unfaithful = [k for k, pol in POLICY_BY_KEY.items() if not pol.gain]
    assert unfaithful, 'every family is faithful, so the exclusion proves nothing'
    assert not (set(unfaithful) & set(FAITHFUL_GAIN_FAMILIES))

    src = inspect.getsource(strategy_runner._gain_bundle_for)
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
    every = {pol.key: 3600.0 * (i + 1) for i, pol in enumerate(_RESTOCKS)}
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


# ── the rank diagonal: rule pairs for the coupled phase 2 ────────────────────────

def _two_channel_root(tmp_path, store_arms, ful_arms, coupled=None):
    """A phase-1 root with both channels ranked, each on its own arm-key -> seconds map."""
    root = str(tmp_path / 'run')
    _leaf(root, 'k1_off', 'prof_a', 'store', store_arms, channel='store')
    _leaf(root, 'k1_off', 'prof_a', 'ful_calibrated', ful_arms, channel='fulfillment')
    _layout(root, ['k1_off'], coupled=coupled)
    return root


def test_the_rule_pairs_are_the_rank_diagonal_not_the_sorted_arm_set(tmp_path):
    """The two channels rank on scales that are not comparable, so the pairing is applied
    AFTER ranking, by zipping the ordered `chosen` lists. `arms` is `sorted(set(...))` — a
    pairing built from it is alphabetical, which is still a list of pairs and still the right
    length, and wrong in a way nothing downstream can detect.

    The two channels are given OPPOSITE rankings so the diagonal and the alphabetical zip
    cannot agree by accident, and so element 0 of a pair provably comes from the store."""
    root = _two_channel_root(
        tmp_path,
        _arms(tmin=3600.0, tmax=7200.0, fifo=36000.0),
        _arms(tmax=3600.0, tmin=7200.0, fifo=36000.0))
    doc = sel.select(root, k=2, log=lambda *_a: None)
    rp = doc['rule_pairs']
    assert rp['order'] == ['store', 'fulfillment'], 'the tuple order is stated, not implied'
    assert rp['chosen'][:2] == [['tmin', 'tmax'], ['tmax', 'tmin']]
    assert rp['complete'] is True and rp['incomplete_reason'] is None
    # ...and the alphabetical zip it must not be:
    assert doc['channels']['store']['arms'] == ['fifo', 'tmax', 'tmin']
    assert rp['chosen'][0] != ['fifo', 'fifo'], 'built from `chosen`, never from `arms`'


def test_the_fifo_rule_pair_rides_along_outside_k(tmp_path):
    """Same two reasons as the scalar rider — the rollup's baseline and the order-blind
    negative control — and `('fifo', 'fifo')` is the only spelling of it that cannot be
    satisfied by fifo appearing on one side of a pair."""
    root = _two_channel_root(tmp_path, _arms(**_CHEAP), _arms(**_CHEAP))
    rp = sel.select(root, k=2, log=lambda *_a: None)['rule_pairs']
    assert rp['chosen'] == [['tmin', 'tmin'], ['tmax', 'tmax'], ['fifo', 'fifo']]
    assert rp['rider'] == ['fifo', 'fifo']
    assert rp['chosen'][-1] == rp['rider'], 'appended, so it is not one of the k'


def test_ragged_rankings_zip_to_the_common_length_and_say_so(tmp_path):
    """The lists genuinely can differ: a rule disqualified for a missing reading, a cap that
    backfills on one side only, or fewer rankable rules than k. A silent truncation would hand
    phase 2 a shorter campaign than the ranking supports — so the zip takes the common length,
    logs loudly and stamps `complete: false`, and `select` still WRITES, because the ranking is
    what a human reads to choose between re-running phase 1 and accepting the short campaign."""
    root = _two_channel_root(
        tmp_path,
        _arms(tmin=3600.0, tmax=7200.0),                                     # 2 rules
        _arms(tmin=3600.0, tmax=7200.0, rank_random=10800.0, comp=14400.0))  # 4 rules
    said = []
    doc = sel.select(root, k=3, log=said.append)
    rp = doc['rule_pairs']
    assert len(doc['channels']['store']['chosen']) == 2
    assert len(doc['channels']['fulfillment']['chosen']) == 3
    assert rp['chosen'] == [['tmin', 'tmin'], ['tmax', 'tmax'], ['fifo', 'fifo']]
    assert rp['complete'] is False
    assert 'ragged' in rp['incomplete_reason']
    assert 'store chose 2' in rp['incomplete_reason']
    assert any('ragged' in m for m in said), 'loudly, not only in the artifact'
    with open(os.path.join(root, 'restock_selection.json')) as f:
        assert json.load(f)['rule_pairs']['complete'] is False, 'written anyway'


def test_a_one_channel_run_produces_no_rule_pairs_and_fabricates_no_rider(run_root):
    """A diagonal needs two rankings. A pair list holding nothing but a manufactured
    `('fifo', 'fifo')` would read as a very short campaign rather than as the store-only run
    it came from."""
    said = []
    rp = sel.select(run_root, k=2, log=said.append)['rule_pairs']
    assert rp['chosen'] == []
    assert rp['complete'] is False
    assert 'fulfillment' in rp['incomplete_reason']
    assert any('no rule pairs' in m for m in said)


def test_a_rule_pair_is_runnable_only_if_both_members_are_faithful(tmp_path):
    """A gain cell builds a bundle for EVERY arm in the set, so one unfaithful member refuses
    the whole coupled unit at worker startup. "Both channels are individually fine" is not the
    property that makes a unit run, so the report is per pair as well as per rule."""
    root = _two_channel_root(tmp_path,
                             _arms(comp=3600.0, tmin=7200.0),
                             _arms(tmin=3600.0, comp=7200.0))
    said = []
    doc = sel.select(root, k=1, log=said.append)
    assert doc['rule_pairs']['chosen'] == [['comp', 'tmin'], ['fifo', 'fifo']]
    assert doc['rule_pairs']['needs_bundle_extension'] == [
        {'rule_pair': ['comp', 'tmin'], 'unfaithful': ['comp']}]
    assert any('cannot run until' in m for m in said)


# ── the extension cap counts the union across channels ───────────────────────────

def test_the_extension_cap_is_a_union_across_channels(tmp_path):
    """Extending `_gain_bundle_for` is work per FAMILY, site-wide: both channels choosing
    `comp` costs ONE extension, not two. Under a per-channel cap of 2 the store would go on to
    commit `cmin` as well and the project would owe three extensions for a cap of two — a bound
    that can be silently doubled is not bounding the thing it exists to bound.

    Fulfillment is consumed first (alphabetical), spends the whole cap on comp + expn; the
    store then gets comp FREE — already paid for — and is backfilled past cmin."""
    root = _two_channel_root(
        tmp_path,
        _arms(comp=3600.0, cmin=7200.0, tmin=10800.0, tmax=14400.0),
        _arms(comp=3600.0, expn=7200.0, tmin=10800.0, tmax=14400.0))
    said = []
    doc = sel.select(root, k=2, extension_cap=2, log=said.append)
    ful, store = doc['channels']['fulfillment'], doc['channels']['store']
    assert ful['chosen'] == ['comp', 'expn']
    assert store['chosen'] == ['comp', 'tmin'], (
        'comp is free (the union already holds it) but cmin is a THIRD family and is capped')
    assert store['backfilled_past'] == ['cmin']
    assert doc['extension_union'] == ['comp', 'expn'], 'two distinct families, not three'
    assert len(doc['extension_union']) <= doc['extension_cap']
    assert any('union across channels' in m for m in said)


def test_the_channel_consumption_order_is_recorded_because_it_is_a_decision(tmp_path):
    """One channel's choice now depends on the other's through an alphabetical order that
    means nothing physically. There is no order-free alternative — the two rankings share
    units but not scales, so no global rank exists to allocate against — so the order is
    declared on the artifact rather than left to be re-derived from a `sorted()` call."""
    root = _two_channel_root(tmp_path, _arms(**_CHEAP), _arms(**_CHEAP))
    doc = sel.select(root, k=2, log=lambda *_a: None)
    assert doc['extension_channel_order'] == ['fulfillment', 'store']
    assert 'UNION' in doc['extension_cap_scope']


# ── the one guard, and the stamp that has nothing to gate ────────────────────────

def test_a_coupled_run_root_is_refused(tmp_path):
    """Phase 1 is uncoupled by definition. Pointed at a coupled root the selector would rank
    coupled leaves perfectly happily and emit a hand-off artifact indistinguishable from a real
    one — the only place in the whole hand-off where a wrong-phase number enters silently."""
    root = _two_channel_root(tmp_path, _arms(**_CHEAP), _arms(**_CHEAP), coupled=True)
    with pytest.raises(SystemExit, match='COUPLED'):
        sel.select(root, log=lambda *_a: None)
    assert not os.path.exists(os.path.join(root, 'restock_selection.json')), (
        'refused before it ranked anything, so no artifact exists to be mistaken for one')


def test_the_guard_is_inert_on_every_run_that_exists_today(tmp_path):
    """`coupled` is ABSENT from every run written so far, so the guard must read a missing key
    as uncoupled rather than refusing the entire existing archive."""
    root = _two_channel_root(tmp_path, _arms(**_CHEAP), _arms(**_CHEAP))
    from Optimization.runschema.sim_manifest import read_run_layout
    assert 'coupled' not in read_run_layout(root)
    assert sel.select(root, k=1, log=lambda *_a: None)['rule_pairs']['chosen']


def test_the_put_regime_is_stamped_because_there_is_no_join_to_gate(tmp_path):
    """Phase 1 fields the site's put and receiving crews TWICE (one whole derived crew per
    leaf) and phase 2 fields them once. What doubles is capacity, not labour seconds, so the
    hours move by an amount that differs per arm and RANKS are no more invariant than hours.
    Nothing in the codebase reads two run roots, so this stamp and the published caveat are the
    whole of how that asymmetry is carried."""
    root = _two_channel_root(tmp_path, _arms(**_CHEAP), _arms(**_CHEAP))
    doc = sel.select(root, k=1, log=lambda *_a: None)
    assert doc['put_regime'] == 'per-leaf' == sel.PUT_REGIME
    note = doc['put_regime_note']
    assert 'twice' in note and 'RANKS' in note, (
        'a reader who takes the stamp as "hours only" would still compare ranks across it')

# -- the margins are FIELDS, so "best 3" is a stated set with stated separations ----------

def test_the_ranking_carries_its_margins_as_fields(run_root):
    """`margin_pct` is the gap UP to the next rank as a percent of this rule's hours (None
    for the last rankable rule); `gap_to_baseline_pct` is the signed distance from the fifo
    control.  Both computed from `score_hours`, so a reader never has to."""
    doc = sel.select(run_root, k=3, log=lambda *_a: None)
    rows = {r['rule']: r for r in doc['channels']['store']['ranking']}
    assert rows['tmin']['margin_pct'] == pytest.approx(100.0)          # 1 h -> 2 h
    assert rows['tmax']['margin_pct'] == pytest.approx(50.0)           # 2 h -> 3 h
    assert rows['expn']['margin_pct'] is None                          # last rankable
    assert rows['fifo']['gap_to_baseline_pct'] == pytest.approx(0.0)
    assert rows['tmin']['gap_to_baseline_pct'] == pytest.approx(-80.0)  # 1 h vs 5 h
    assert rows['comp']['gap_to_baseline_pct'] == pytest.approx(20.0)


def test_a_disqualified_rule_carries_no_margin(tmp_path):
    root = str(tmp_path / 'run')
    _leaf(root, 'k1_off', 'prof_a', 'store', {**_arms(tmin=3600.0, fifo=7200.0),
                                               'opt_tmax_norsl': float('nan'),
                                               'uni_tmax_norsl': 9000.0})
    _layout(root, ['k1_off'])
    doc = sel.select(root, k=2, log=lambda *_a: None)
    rows = {r['rule']: r for r in doc['channels']['store']['ranking']}
    assert rows['tmax']['rank'] is None and rows['tmax']['margin_pct'] is None
    assert rows['tmax']['gap_to_baseline_pct'] is None
