"""test_funnel_window.py — the campaign's window, its arrival regime and its staffing pin are
each DECLARED ONCE, and the two kinds of "day" cannot be mistaken for one another.

"Re-size the funnel in site days" (`.scratch/inbound-optimization`) landed three declarations
that had been spelled inline, and each one had already cost something:

  * **The window.** `CAMPAIGN_DEPTH_DAYS` / `CAMPAIGN_WINDOW_DAYS` were a `--n-batches 40` on a
    command line and a `--window 20-39` typed at each reading site. A campaign whose two phases
    are launched by hand can silently run to different depths — and the staffing derivation
    reads the sampled SCRIPT, so a depth difference is a different derivation, not just a
    shorter run.
  * **The arrival regime.** `INBOUND_ARRIVAL_REGIME` must ride `run_defaults`, not only the
    inbound axis, because a multi-cell run FREEZES its inventory once per pair before any cell
    starts and the line floor is solved at the lead CONFIG carries then
    (`sim_config.inbound_lead_law`). Declared only on the axis, phase 2 would freeze a
    warehouse stocked for transit 0 and simulate nine of its ten cells against it, logging a
    transit of 0.000 that nothing compares with the cells'.
  * **The pin.** A rule pair is a RANKING, and the funnel deliberately allows a build between
    the phases. `PHASE2_STAFFING_PIN` is what makes a moved derivation a refusal instead of a
    published campaign that ranked a different site.

THE TWO DAYS ARE THE POINT of the first section. A batch is a SITE day (28,800 s) and the yard
fee accrues in CALENDAR days (86,400 s); "Re-run the gate and fix the fee threshold" lost a
session to reading a sweep in one and committing it into the other, which would have left the
fee axis identically zero and reported `0.00` rather than an error. These tests pin the two
apart by their own declarations, so a future change to either is caught here.

Run:  python -m pytest Tests/unit/test_funnel_window.py -q
"""
from __future__ import annotations

import copy

import pytest

from Optimization.config.whatif_config import (
    CAMPAIGN_DEPTH_DAYS, CAMPAIGN_WINDOW_DAYS, INBOUND_ARRIVAL_REGIME, PHASE1_RUN_DEFAULTS,
    PHASE2_RIDER, PHASE2_RUN_DEFAULTS, PHASE2_THRESHOLD_DAYS, PILOT_RUN_DEFAULTS, SPECS,
    phase2_inbound_axis, validate_spec,
)
from Optimization.simconfig import staffing as _staffing
from Warehouse.kernel import timeline

#: The three specs that are the campaign. Everything below asserts about these and only these:
#: `single` and the two canaries must stay flag-off and byte-identical.
_CAMPAIGN = ('inbound_select', 'inbound_policies', 'inbound_pilot')


# ── the window: one declaration, and it is in SITE days ─────────────────────────

def test_the_window_sits_inside_the_depth_and_leaves_a_real_warm_up():
    """A measured window that touched day 0 would band the warm-up, where the replenishment
    wave has not reached the shelf — the exact failure the reference run's first two passes
    hit. A window that ran past the depth would band days that never closed."""
    lo, hi = CAMPAIGN_WINDOW_DAYS
    assert 0 < lo < hi < CAMPAIGN_DEPTH_DAYS
    assert hi == CAMPAIGN_DEPTH_DAYS - 1, 'the window ends on the last day the run closes'
    assert lo >= CAMPAIGN_DEPTH_DAYS // 2, 'at least half the run is warm-up'


@pytest.mark.parametrize('name', _CAMPAIGN)
def test_every_campaign_spec_runs_to_the_declared_depth(name):
    """The depth rides `run_defaults`, so `--spec inbound_select` and `--spec inbound_policies`
    are each the whole launch. Before this it was a `--n-batches 40` on two command lines, and
    the staffing derivation reads the script — so two depths are two derivations."""
    assert SPECS[name]['run_defaults']['n_batches'] == CAMPAIGN_DEPTH_DAYS


def test_the_campaign_day_and_the_fee_day_are_three_fold_apart_and_both_declared():
    """`CAMPAIGN_*_DAYS` are SITE days; `PHASE2_THRESHOLD_DAYS` is a CALENDAR day. Both
    declarations are read here rather than restated, so a change to either shows up as a
    failure instead of as a threshold that silently zeroes the fee axis."""
    from Optimization.Performance_Evaluations.common.units import (
        SECONDS_PER_DAY as CALENDAR_DAY_S)
    site_day_s = timeline.DEFAULT_SHIFT_SECONDS
    assert site_day_s == 8 * 60 * 60 and CALENDAR_DAY_S == 24 * 60 * 60
    assert CALENDAR_DAY_S / site_day_s == 3.0
    # The threshold is BELOW one site day, which reads as nonsense until the units are named:
    # 0.40 calendar days is 1.2 site days. Asserted so the next reader meets the conversion
    # rather than the bare number.
    assert PHASE2_THRESHOLD_DAYS * CALENDAR_DAY_S / site_day_s == pytest.approx(1.2)


def test_the_reader_takes_the_window_from_the_declaration():
    """`--window campaign` resolves to the constant. A campaign leaf judged against a
    hand-typed window reports utilization against a warm-up the constants never saw, and
    nothing in the report says so."""
    from Diagnostics.equilibrium_report import _window_arg
    assert _window_arg('campaign') == CAMPAIGN_WINDOW_DAYS
    assert _window_arg('20-39') == (20, 39)     # the LO-HI form is untouched
    assert _window_arg(None) is None


# ── the arrival regime: on the RUN, because the freeze reads it ─────────────────

@pytest.mark.parametrize('name', _CAMPAIGN)
def test_every_campaign_spec_declares_the_arrival_regime_at_run_level(name):
    """Including phase 2, whose cells state it again on the axis. That is not a duplication:
    the axis decides what a CELL simulates, this decides what the FREEZE plans."""
    assert INBOUND_ARRIVAL_REGIME.items() <= SPECS[name]['run_defaults'].items()


def test_the_axis_and_the_run_level_regime_cannot_drift_apart():
    """Every inbound-ON cell carries the same trailer, doors, team and lead law the freeze
    plans against. Derived from one set of constants today; asserted so it stays that way."""
    on = [ov for suffix, ov in phase2_inbound_axis() if suffix != 'inb_off']
    assert on, 'the axis has no inbound-on cell'
    for ov in on:
        for key, val in INBOUND_ARRIVAL_REGIME.items():
            assert ov[key[len('inbound_'):]] == val, key


def test_the_inbound_off_anchor_still_turns_the_pipeline_off_over_the_frozen_stock():
    """The run-level regime is what the freeze reads; `_apply_cell` overwrites all of it per
    cell, so `inb_off` is still the structural zero — same warehouse, same stock, no yard."""
    anchor = dict(phase2_inbound_axis())['inb_off']
    assert anchor['trailer_type'] is None and anchor['standing_yard'] is False
    assert anchor['lead_minutes'] == 0.0 and anchor['lead_spread'] == 0.0
    assert anchor['door_team'] is None
    # …and it states every key the on-cells do, so nothing leaks in from the previous cell.
    assert set(anchor) == set(dict(phase2_inbound_axis())['fifo'])


def test_the_declared_regime_gives_the_coverage_a_nonzero_transit():
    """The defect this moved the regime to fix: with no trailer type in CONFIG the lead law is
    None and the line floor is solved at transit 0. Driven through the real accessor and the
    real CONFIG (restored in a finally), so it is the freeze's own read path."""
    from Optimization.config.sim_config import CONFIG, inbound_lead_law
    g = CONFIG['global']
    saved = {k: g.get(k) for k in INBOUND_ARRIVAL_REGIME}
    try:
        for k in INBOUND_ARRIVAL_REGIME:
            g[k] = None
        assert inbound_lead_law() is None, 'no trailer type must read as no pipeline'
        g.update(INBOUND_ARRIVAL_REGIME)
        law = inbound_lead_law()
        assert law is not None and law['lead_s'] > 0 and law['lead_sigma'] > 0
        assert law['trailer_type'] == INBOUND_ARRIVAL_REGIME['inbound_trailer_type']
    finally:
        g.update(saved)


def test_phase_one_stays_uncoupled_and_the_other_two_couple():
    """Phase 1 is the ranking run and `run_restock_selection.select` refuses a coupled root;
    the pilot and phase 2 both measure the SITE's dock, which is the whole point of coupling."""
    assert 'couple_channels' not in PHASE1_RUN_DEFAULTS
    assert PHASE2_RUN_DEFAULTS['couple_channels'] is True
    assert PILOT_RUN_DEFAULTS['couple_channels'] is True


# ── the staffing pin ────────────────────────────────────────────────────────────

def _derived(**over) -> dict:
    """A derived block of the shape `staffing.derive` returns, with only the keys the pin
    reads filled in plus two reporting-only ones the pin must IGNORE."""
    d = {
        'provenance': 'derived', 'day_seconds': 28800.0,
        'channels': {
            'store': {'pickers': 31, 'daily_demand_units': 6125.270697870213,
                      's_pick': {'value': 105.12956892288425, 'provenance': 'derived'},
                      'batch': {'mean_fraction': 0.00245335},
                      'script': {'batches': 40, 'units': 259422.0},
                      'analytic': {'seconds_per_unit': 73.6175082071726},
                      'expected_utilization': {'pick': 0.7636907210212946}},
            'fulfillment': {'pickers': 23, 'daily_demand_units': 30583.057325862326,
                            's_pick': {'value': 17.65746281158561, 'provenance': 'derived'},
                            'batch': {'mean_fraction': 0.018138},
                            'script': {'batches': 40, 'units': 1259124.0},
                            'analytic': {'seconds_per_unit': 2.9868447724910587},
                            'expected_utilization': {'pick': 0.8391053443982079}},
        },
        'put': {'crew': 64, 's_put': {'store': {'value': 100.08021417054391},
                                      'fulfillment': {'value': 28.54272766573454}},
                'expected_utilization': {'store': 0.35214585127399056}},
        'receiving': {'crew': 23, 'load_packs_per_day': 17105.098765036786},
    }
    d.update(over)
    return d


def test_the_pin_ignores_what_the_record_only_reports():
    """`derived_differs` rightly fails on anything, because it answers "is this the SAME run".
    The pin answers "is this the same WAREHOUSE", so a change to what the record reports must
    not refuse a campaign — otherwise the next reporting change stops the funnel."""
    base = _derived()
    noisy = copy.deepcopy(base)
    noisy['channels']['store']['expected_utilization']['pick'] = 0.99
    noisy['channels']['store']['analytic']['seconds_per_unit'] = 1.0
    noisy['put']['expected_utilization']['store'] = 0.01
    noisy['receiving']['load_packs_per_day'] = 1.0
    noisy['provenance'] = 'declared'
    assert _staffing.derived_differs(base, noisy), 'the fixture must actually differ'
    assert _staffing.pin_digest(base) == _staffing.pin_digest(noisy)


@pytest.mark.parametrize('mutate', [
    lambda d: d['put'].__setitem__('crew', 65),
    lambda d: d['receiving'].__setitem__('crew', 22),
    lambda d: d['channels']['store'].__setitem__('pickers', 30),
    lambda d: d['channels']['store'].__setitem__('daily_demand_units', 6200.0),
    lambda d: d['channels']['store']['s_pick'].__setitem__('value', 106.0),
    lambda d: d['put']['s_put']['fulfillment'].__setitem__('value', 29.0),
    lambda d: d['channels']['fulfillment']['batch'].__setitem__('mean_fraction', 0.019),
    lambda d: d['channels']['fulfillment']['script'].__setitem__('batches', 75),
], ids=['put_crew', 'recv_crew', 'pickers', 'demand', 's_pick', 's_put', 'batch', 'depth'])
def test_the_pin_catches_every_move_that_changes_what_a_run_fields(mutate):
    """One assertion per thing the campaign is actually pinning. `depth` is in the list
    because the derivation reads the sampled script: two phases at different depths derive
    different per-day loads from one catalogue."""
    moved = _derived()
    mutate(moved)
    assert _staffing.pin_digest(moved) != _staffing.pin_digest(_derived())


def test_the_pin_tolerates_a_float_wobble_below_its_declared_precision():
    """The digest would otherwise be an `==` on floats by the back door — the one comparison
    this repo forbids. `PIN_SIGFIGS` is where the tolerance is declared, once."""
    wobbled = _derived()
    v = wobbled['channels']['store']['s_pick']['value']
    wobbled['channels']['store']['s_pick']['value'] = v * (1 + 1e-9)
    assert _staffing.pin_digest(wobbled) == _staffing.pin_digest(_derived())
    assert _staffing.PIN_SIGFIGS >= 6


def test_a_phase_two_spec_without_a_pin_is_refused_at_spec_build():
    """Valid in every other respect, so the refusal fails on the defect and not on a happy
    path it never reaches.

    "Valid in every other respect" got two new requirements with inbound-optimization 33,
    and the happy path below states both rather than inheriting them: the axis drops the
    `fsight_*` cells (the coupled composer declines a futuresight window until 34 lands the
    zip) and the rules stay inside `FAITHFUL_GAIN_FAMILIES` (`rank_labor` has no faithful
    gain bundle, so every gain cell would die at its first drain). Neither is about the
    pin; both are refusals this spec now trips first. See
    `test_campaign_cells_can_run.py`."""
    spec = {**SPECS['inbound_policies'],
            'inbound': [e for e in phase2_inbound_axis() if not e[0].startswith('fsight')],
            'rule_pairs': [('tmin', 'tmax'), PHASE2_RIDER]}
    spec.pop('staffing_pin', None)
    with pytest.raises(ValueError, match='staffing_pin'):
        validate_spec(spec, 'inbound_policies')
    validate_spec({**spec, 'staffing_pin': {'some_pair': 'deadbeef0000'}}, 'inbound_policies')


def test_the_run_refuses_the_pair_whose_derivation_moved():
    """The half `validate_spec` cannot do: the derivation does not exist until the catalogue is
    read and the script precomputed. A run carrying no pin is every non-campaign run and must
    be a strict no-op."""
    from Optimization.simdriver.workunits import _check_campaign_pin
    d = _derived()
    label = 'mixed_20260816_131535__mixed_realistic_bell_lt0'
    _check_campaign_pin(None, label, d)                       # no pin: the no-op path
    _check_campaign_pin({label: _staffing.pin_digest(d)}, label, d)
    moved = _derived()
    moved['put']['crew'] = 65
    with pytest.raises(RuntimeError, match='campaign pin'):
        _check_campaign_pin({label: _staffing.pin_digest(d)}, label, moved)
    # A pair the pin never names was never ranked by phase 1 — also a refusal, not a pass.
    with pytest.raises(RuntimeError, match='does not name the pair'):
        _check_campaign_pin({'some_other_pair': _staffing.pin_digest(d)}, label, d)
