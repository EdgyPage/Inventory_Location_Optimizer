"""test_funnel_spec_pairs.py — the phase-2 funnel spec is shaped as RULE PAIRS, and every
shape it cannot mean is refused before a run directory exists.

Site-dock 23 builds the spec half of "Re-shape the funnel for arm pairs" (site-dock 06): a
phase-2 cell is an ordered list of `(store_rule, fulfillment_rule)` pairs, `CHANNEL_RESTOCKS`
is DERIVED from that list instead of authored beside it, and the driver's flat-arm refusal
becomes a shape refusal that fires at `get_spec`.

What this file is actually defending:

  * **The derivation reproduces the curated value.** `channel_restocks_for` replaces the
    install `_run_whatif_matrix` has always done, so the reference implementation transcribed
    from that driver is asserted equal for EVERY registered spec.  A derivation that changes
    what today's specs install is not a refactor, it is a silent re-run of the archive.
  * **The rank order survives to the zip.** `strategies_for` used to re-impose the grid's
    order, which throws away the only thing a diagonal reads (site-dock 06 section 0, one
    level down).  `test_the_derived_columns_zip_back_to_the_pairing` runs the whole chain —
    pairs -> columns -> `strategies_for` -> zip — and asserts the arm pairs that come out are
    the ones that went in.
  * **Every refusal fails on the defect**, not merely on a happy path that never reaches it.
    Each one is given a spec that is valid except for the single thing under test.

Run:  python -m pytest Tests/unit/test_funnel_spec_pairs.py -q
"""
from __future__ import annotations

import itertools

import pytest

from Optimization.config import whatif_config as wc
from Optimization.config.whatif_config import (
    COUPLED_CHANNELS, PHASE2_RIDER, PHASE2_RUN_DEFAULTS, ERA_RUN_DEFAULTS, SPECS,
    channel_restocks_for, get_spec, rule_pairs_of, swept_rules_of, validate_spec,
)
from Optimization.config.strategies import (
    CHANNEL_RESTOCKS, RESTOCK_KEYS, STRATEGIES, strategies_for,
)

_BOTH = COUPLED_CHANNELS          # ('store', 'fulfillment') — store first, as a pair is written


def _pair_spec(pairs, **over):
    """A minimal spec that is VALID apart from whatever the caller overrides.

    Every refusal test below starts from this, so a test that stops failing because the spec
    drifted out of shape for an unrelated reason is a test that stopped testing.
    """
    spec = {'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
            'schedulers': ['lpt'], 'rule_pairs': pairs, 'reference': 'k1_off_fifo',
            # A pin is as mandatory as the pairs ("Re-size the funnel in site days"): a
            # ranking is only about the warehouse it was taken on, and the funnel puts a
            # legal build between the phases. `Tests/unit/test_funnel_window.py` owns the
            # refusal; here it is just part of being valid.
            'staffing_pin': {'lbl': 'deadbeef0000'},
            'run_defaults': PHASE2_RUN_DEFAULTS}
    spec.update(over)
    return spec


# ── who the neutrality sweeps are about ─────────────────────────────────────────
# Three sweeps below assert DERIVED == the frozen head expression for every registered spec.
# That is a claim about runs that EXIST — flat `arms` specs, which is every spec the archive
# was built from — and it held over the whole registry only while `inbound_policies` declared
# nothing at all and both sides came back None vacuously.  Since phase 1 ran, its pairs are
# committed (inbound-opt 35), and a rule-pair spec is BY CONSTRUCTION the thing that moves:
# the head expressions predate the shape, so they read it as "no arm set" while the derivation
# reads it as two rank-ordered columns.  That difference is the feature, and it has its own
# tests below; folded into the sweeps it would only make the neutrality claim unstateable.
#
# Split DERIVED from the spec, never listed by name, so a second pair campaign lands on the
# right side of the line the day it is registered rather than the day someone remembers.  Both
# sides are asserted non-empty: an exclusion that silently swallowed the registry would turn
# every sweep it feeds into a test that passes because it visits nothing.
#
# ON THE KEY, NOT ON `rule_pairs_of`.  This runs at COLLECTION, and `rule_pairs_of` normalises
# — so a malformed `PHASE2_PAIRS` (transposed, rider dropped, a rule twice in a column) would
# raise here and error the whole module out, taking with it the dozen refusal tests that exist
# to say WHICH shape broke.  A spec that declares the key is a pair spec; whether what it
# declares is well-shaped is a question for the tests below, which can then answer it.
_FLAT_SPECS = sorted(n for n in SPECS if SPECS[n].get('rule_pairs') is None)
_PAIR_SPECS = sorted(n for n in SPECS if SPECS[n].get('rule_pairs') is not None)


def test_the_registry_splits_into_flat_and_pair_specs_and_neither_side_is_empty():
    """Non-vacuity for the three sweeps `_FLAT_SPECS` parametrizes, and for the pair-shaped
    tests that are the other half of the same story.  Stated once, here, because a sweep whose
    parameter list went empty reports as PASSED for every name it no longer visits."""
    assert _FLAT_SPECS, ('every registered spec declares rule pairs — the neutrality sweeps '
                         'below now visit nothing and cannot fail')
    assert _PAIR_SPECS, ('no registered spec declares rule pairs — the campaign spec lost its '
                         'pairing, or PHASE2_PAIRS went back to None')
    assert set(_FLAT_SPECS) | set(_PAIR_SPECS) == set(SPECS)
    assert 'inbound_policies' in _PAIR_SPECS         # the campaign, carrying phase 1's draw


# ── the derivation reproduces what the driver installs today ────────────────────
# Transcribed from `_run_whatif_matrix` (Optimization/simdriver/scenario.py) as it stood
# BEFORE the pair-shaped derivation was wired in: 'all' -> None (full suite), a subset -> that
# tuple on every channel, absent/None -> CHANNEL_RESTOCKS left exactly as committed.  The
# driver now calls `channel_restocks_for` itself, so this is a FROZEN COPY of the behaviour
# being preserved rather than a description of live code -- which is the only thing that makes
# "derived == declared today" a claim and not a tautology.

def _head_install(spec, channels):
    if spec.get('arms') is None:
        return None
    arms = None if str(spec['arms']).lower() == 'all' else tuple(spec['arms'])
    return {ch: arms for ch in channels}


@pytest.mark.parametrize('name', _FLAT_SPECS)
@pytest.mark.parametrize('channels', [_BOTH, ('store',)])
def test_the_derived_channel_restocks_reproduce_the_committed_install(name, channels):
    """DERIVED == DECLARED TODAY, asserted, for every FLAT-armed registered spec and both
    catalogue shapes — the precondition for the derivation replacing the driver's install at
    all.  The pair specs are the ones it deliberately does not reproduce; their columns are
    asserted directly below, against the pairing rather than against the head."""
    spec = SPECS[name]
    assert channel_restocks_for(spec, channels) == _head_install(spec, channels), name


def test_the_committed_channel_restocks_survive_a_spec_that_declares_nothing():
    """`--spec single` is the old flat run: `arms: None` must leave the committed per-channel
    subsets untouched, which is what None (rather than an empty dict) means here."""
    assert channel_restocks_for(SPECS['single'], _BOTH) is None
    assert CHANNEL_RESTOCKS == {'store': None, 'fulfillment': None}


def test_a_rule_pair_list_derives_the_arm_set_it_came_from():
    """A diagonal of like-with-like pairs derives exactly the flat arm set the same campaign
    would have declared — the curated value, reproduced rather than eyeballed."""
    pairs = (('fifo', 'fifo'), ('tmin', 'tmin'), ('rank_labor', 'rank_labor'))
    flat = {'arms': ('fifo', 'tmin', 'rank_labor')}
    assert channel_restocks_for(_pair_spec(pairs), _BOTH) == _head_install(flat, _BOTH)


def test_an_asymmetric_pairing_derives_two_different_columns_in_rank_order():
    """The columns are the pairing's slices, IN RANK ORDER — not sets, and not sorted."""
    pairs = (('tmin', 'rank_labor'), (*PHASE2_RIDER,), ('rank_labor', 'map'))
    got = channel_restocks_for(_pair_spec(pairs), _BOTH)
    assert got == {'store': ('tmin', 'fifo', 'rank_labor'),
                   'fulfillment': ('rank_labor', 'fifo', 'map')}
    # and the store column is NOT the sorted/grid order of its own members
    assert got['store'] != tuple(k for k in RESTOCK_KEYS if k in got['store'])


@pytest.mark.parametrize('name', _PAIR_SPECS)
def test_a_committed_pair_specs_columns_are_its_own_pairing_sliced(name):
    """The claim the flat sweeps hand over: a registered pair spec's per-channel columns are
    its declared pairing, sliced by slot and IN THE DECLARED ORDER.

    Asserted against the spec's own `rule_pairs` rather than against a literal list, because
    the literal is phase 1's artifact (`restock_selection.json`, on the results drive) and a
    checkout cannot read it — `Tests` must not grow a dependency on a run tree.  What is
    checkable here is that nothing between the declaration and the install re-orders, de-dupes
    or transposes it, which is every failure the derivation exists to prevent.
    """
    pairs = rule_pairs_of(SPECS[name])
    cols = channel_restocks_for(SPECS[name], _BOTH)
    for slot, ch in enumerate(COUPLED_CHANNELS):
        assert cols[ch] == tuple(p[slot] for p in pairs), (name, ch)
        assert len(cols[ch]) == len(pairs), (name, ch, 'a column collapsed — a repeated rule')
        assert all(r in RESTOCK_KEYS for r in cols[ch]), (name, ch)
    # …and the pairing survives the round trip back out of the columns: zip the two columns
    # and the pairs come back. A transposed pair would satisfy both slot assertions above
    # only if the columns were equal, which the rider alone does not make them.
    assert tuple(zip(*(cols[ch] for ch in COUPLED_CHANNELS))) == pairs, name
    assert PHASE2_RIDER in pairs, (name, 'the analysis baseline rode off the campaign')


def test_the_derived_columns_zip_back_to_the_pairing():
    """THE PAYOFF. pairs -> columns -> `strategies_for` -> the zip `_prepare_site_run` does:
    the arm pairs that come out are the rule pairs that went in, like stock_mode with like.

    This is the whole reason the columns are ordered.  With a grid-order filter the zip pairs
    rank 1 against whichever rule sits earliest in the strategy grid, which is a real campaign
    that runs to completion and is not the one phase 1 ranked.
    """
    pairs = (('tmin', 'rank_labor'), (*PHASE2_RIDER,), ('rank_labor', 'map'))
    cols = channel_restocks_for(_pair_spec(pairs), _BOTH)
    store_arms = strategies_for(cols['store'])
    ful_arms = strategies_for(cols['fulfillment'])
    assert len(store_arms) == len(ful_arms) == 2 * len(pairs)   # two stock modes per rule pair
    seen = []
    for a_s, a_f in zip(store_arms, ful_arms):
        assert a_s.stock_mode == a_f.stock_mode      # the diagonal extends to stock_mode …
        seen.append((a_s.restock, a_f.restock))
    # … so each rule pair appears exactly twice, once per stock mode, and nothing else appears
    assert sorted(seen) == sorted(list(pairs) * 2)


def test_a_rule_pair_spec_refuses_a_single_channel_run():
    """Said HERE first: `_prepare_site_run` refuses the same shape, after the catalogue has
    been read and both leaves prepared."""
    with pytest.raises(ValueError, match='nothing to pair'):
        channel_restocks_for(_pair_spec((PHASE2_RIDER,)), ('store',))


# ── run_layout.json's flat `arms` field ─────────────────────────────────────────

def _head_layout_arms(spec):
    """`run_simulation`'s expression for the descriptor's `arms`, before site-dock 23."""
    return None if spec.get('arms') in (None, 'all') else list(spec['arms'])


@pytest.mark.parametrize('name', _FLAT_SPECS)
def test_the_descriptors_arm_field_is_unchanged_for_every_committed_spec(name):
    """…for the flat-armed specs. A pair spec's descriptor MOVES by design — `None` there
    would claim the committed 34-arm suite for a run that swept a diagonal — and the next
    test is that move."""
    assert swept_rules_of(SPECS[name]) == _head_layout_arms(SPECS[name]), name


def test_a_rule_pair_campaign_records_the_rules_it_actually_swept():
    """`None` there means "the committed suite" — a descriptor claiming 34 arms for a run that
    swept a twelve-rule diagonal. The union, in first-seen order, is the honest flat answer."""
    pairs = (('tmin', 'rank_labor'), (*PHASE2_RIDER,), ('rank_labor', 'map'))
    assert _head_layout_arms(_pair_spec(pairs)) is None          # what it used to record …
    assert swept_rules_of(_pair_spec(pairs)) == ['tmin', 'rank_labor', 'fifo', 'map']


def test_the_driver_writes_the_descriptor_through_that_accessor():
    """The accessor only helps if the descriptor is written THROUGH it, and nothing else here
    can see that: `write_run_layout` is called once, deep in the driver's setup, on a run this
    suite does not launch.  Asserted structurally on the call itself rather than on a literal
    line, so it survives a re-wrap (site-dock 18's finding about source-inspecting guards)."""
    import inspect
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation)
    call = src[src.index('write_run_layout(') + len('write_run_layout('):]
    call = call[:call.index('\n            coupled=')]        # the call's argument list
    assert 'swept_rules_of(spec_dict)' in call
    assert "spec_dict['arms']" not in call                    # …and not the old expression


# ── the shape refusals ──────────────────────────────────────────────────────────

def test_a_flat_arm_tuple_under_the_new_name_is_refused():
    """The stale-`PHASE2_ARMS` case: renamed, not re-shaped.  Read as a pair list it is a
    campaign of ONE pair whose store rule is the string 'fifo'."""
    with pytest.raises(ValueError, match='FLAT arm tuple'):
        validate_spec(_pair_spec(('fifo', 'tmin')), 'flat')


@pytest.mark.parametrize('pairs, match', [
    ((PHASE2_RIDER, ('tmin', 'map', 'fifo')), r"rule_pairs`\[1\]"),        # a triple
    ((PHASE2_RIDER, 'tmin'), r"rule_pairs`\[1\]"),                         # a bare rule
    ((PHASE2_RIDER, ('tmin',)), r"rule_pairs`\[1\]"),                      # a single
    ((PHASE2_RIDER, ('tmin', 'rank_labour')), 'not a restock rule'),      # a typo'd rule
    ((PHASE2_RIDER, ('no_such_rule', 'tmin')), 'not a restock rule'),
    ('fifo', 'must be an ordered list'),                                  # a bare string
    ((), 'is empty'),
])
def test_a_malformed_pair_list_is_refused(pairs, match):
    with pytest.raises(ValueError, match=match):
        validate_spec(_pair_spec(pairs), 'bad')


@pytest.mark.parametrize('pairs', [
    (('tmin', 'rank_labor'), ('map', 'fifo')),        # fifo on the FULFILLMENT side only
    (('fifo', 'rank_labor'), ('map', 'tmin')),        # fifo on the STORE side only
    (('tmin', 'rank_labor'),),                        # no fifo at all
])
def test_a_pair_list_without_the_fifo_rider_is_refused(pairs):
    """Strictly stronger than the flat `'fifo' not in arms` it replaces: the first two cases
    satisfy that check and still leave one leaf with no baseline and no negative control."""
    with pytest.raises(ValueError, match='rider'):
        validate_spec(_pair_spec(pairs), 'no-rider')


def test_a_rule_ranked_twice_in_one_column_is_refused():
    """The repeat that makes the two columns ragged — which `_prepare_site_run` refuses only
    after the parent has planned the inventory and prepared both leaves."""
    pairs = (PHASE2_RIDER, ('tmin', 'map'), ('tmin', 'rank_labor'))
    with pytest.raises(ValueError, match='store channel .* at BOTH rank'):
        validate_spec(_pair_spec(pairs), 'dup')
    # and the fulfillment column is checked too, not just the first one
    pairs = (PHASE2_RIDER, ('tmin', 'map'), ('rank_labor', 'map'))
    with pytest.raises(ValueError, match='fulfillment channel .* at BOTH rank'):
        validate_spec(_pair_spec(pairs), 'dup')


def test_declaring_both_arms_and_rule_pairs_is_refused():
    """Two answers to one question; whichever the driver reads, the other is silently lost."""
    with pytest.raises(ValueError, match='BOTH `arms` and `rule_pairs`'):
        validate_spec(_pair_spec((PHASE2_RIDER,), arms=('fifo',)), 'both')


@pytest.mark.parametrize('defaults', [ERA_RUN_DEFAULTS, {}, None,
                                      {**ERA_RUN_DEFAULTS, 'couple_channels': False}])
def test_rule_pairs_without_declared_coupling_are_refused(defaults):
    """Coupling is DECLARED, never inferred from the inbound flag (site-dock 18): every cell
    couples, its inbound-off pole included, so no value of that flag implies it."""
    with pytest.raises(ValueError, match='couple_channels'):
        validate_spec(_pair_spec((PHASE2_RIDER,), run_defaults=defaults), 'uncoupled')


def test_an_inbound_matrix_with_no_arm_set_is_refused_at_get_spec():
    """A matrix that never received an arm set has SKIPPED the selection rather than chosen
    it: it refuses at `get_spec` — before the run directory exists — and the message names the
    constant to set and the artifact field to copy it from.

    PHASE 1 HAS RUN (inbound-opt 35), so this can no longer be posed as "the committed spec,
    today".  It used to be, and that made it a test of the constant's value as much as of the
    gate.  The gate is what matters and it is still live: the reachable defect is now an edit
    that DROPS the pairing rather than one that never supplied it, so the spec under test is
    the committed campaign with its `rule_pairs` taken back off — valid in every other
    respect, which is what makes the refusal attributable to the one thing removed.
    """
    stripped = {k: v for k, v in SPECS['inbound_policies'].items() if k != 'rule_pairs'}
    with pytest.raises(ValueError, match='PHASE2_PAIRS'):
        validate_spec(stripped, 'inbound_policies')
    with pytest.raises(ValueError, match='restock_selection'):
        validate_spec(stripped, 'inbound_policies')


def test_every_registered_spec_passes_its_own_shape_check():
    """A gate that refused the whole registry would be found by a person, not by a test.

    No spec is excused any more.  `inbound_policies` was, for as long as it was legitimately
    unbuildable — its pairs were None until phase 1 ranked them — and that exemption is the
    kind that outlives its reason silently, so it goes out with the constant it was about.
    """
    for name in sorted(SPECS):
        assert get_spec(name) is SPECS[name], name


def test_the_campaign_spec_declares_coupling_and_the_era():
    """PHASE2_RUN_DEFAULTS is the era PLUS coupling, and the campaign carries it.  The era
    keys are asserted as a SUBSET rather than by identity: the pilot spec is read the same
    way, and a campaign that silently dropped `shift_drain_or_cap` would derive no crews."""
    assert ERA_RUN_DEFAULTS.items() <= PHASE2_RUN_DEFAULTS.items()
    assert PHASE2_RUN_DEFAULTS['couple_channels'] is True
    assert SPECS['inbound_policies']['run_defaults'] is PHASE2_RUN_DEFAULTS
    # …and phase 1 stays UNCOUPLED: it is the ranking run, and a coupled root is exactly what
    # `run_restock_selection.select` refuses.
    assert 'couple_channels' not in SPECS['inbound_select']['run_defaults']
    # The campaign states its pairing and nothing else: an `arms` key beside it would be the
    # one the driver reads, and would make both columns the same set.
    assert 'arms' not in SPECS['inbound_policies']
    # …and it DOES state one. Phase 1's ranking is copied in (inbound-opt 35), so the campaign
    # is a real diagonal rather than the `None` placeholder it carried while phase 1 was
    # pending — and the pin rides with it, because a ranking is only about the warehouse it
    # was taken on and `validate_spec` refuses the pairs without it.
    assert rule_pairs_of(SPECS['inbound_policies']) == tuple(
        tuple(p) for p in wc.PHASE2_PAIRS)
    assert SPECS['inbound_policies']['staffing_pin'] is wc.PHASE2_STAFFING_PIN
    assert wc.PHASE2_STAFFING_PIN, 'the campaign carries an empty pin — validate_spec refuses'


# ── strategies_for: the order the zip reads ─────────────────────────────────────

def _head_strategies_for(restocks):
    """`strategies_for` as it stood before site-dock 23 — a grid-order filter."""
    return list(STRATEGIES) if restocks is None else [s for s in STRATEGIES
                                                      if s.restock in restocks]


@pytest.mark.parametrize('size', [1, 2, 3])
def test_strategies_for_is_unchanged_for_every_grid_ordered_arm_set(size):
    """NEUTRALITY, exhaustively: for any rule subset written in GRID order — which every
    committed `CHANNEL_RESTOCKS` value and every registered spec's arm set is — the new
    ordering is byte-identical to the old filter, so no run that exists today moves."""
    for combo in itertools.combinations(RESTOCK_KEYS, size):
        assert strategies_for(combo) == _head_strategies_for(combo), combo
    assert strategies_for(None) == _head_strategies_for(None)
    assert strategies_for(RESTOCK_KEYS) == list(STRATEGIES)


@pytest.mark.parametrize('name', _FLAT_SPECS)
def test_no_committed_spec_moves_an_arm(name):
    """The same claim, aimed at the registry rather than at the grid — and at the flat-armed
    specs, which are the ones the archive was built from.  A pair spec's columns are in RANK
    order, which is the whole point of the shape and is exactly what the old grid-order filter
    cannot reproduce; `test_strategies_for_follows_the_declared_rank_order` is that half."""
    for ch, rules in (channel_restocks_for(SPECS[name], _BOTH) or {}).items():
        assert strategies_for(rules) == _head_strategies_for(rules), (name, ch)


def test_strategies_for_follows_the_declared_rank_order():
    """…and the re-ordering is LIVE, not vacuous: a rule list that is not in grid order comes
    back in the order it was declared, initial (uni/opt) outer."""
    declared = ('tmin', 'fifo')
    got = [s.key for s in strategies_for(declared)]
    assert got == ['uni_tmin_norsl', 'uni_fifo_norsl', 'opt_tmin_norsl', 'opt_fifo_norsl']
    assert got != [s.key for s in _head_strategies_for(declared)]


def test_strategies_for_refuses_an_unordered_arm_set():
    """A set has no order, so under coupling it pairs the two channels by hash order — a
    different campaign on a different build, with nothing to show for it."""
    with pytest.raises(TypeError, match='ORDERED'):
        strategies_for({'fifo', 'tmin'})


def test_a_repeated_rule_cannot_open_a_second_rank_slot():
    """`strategies_for` dedupes by first occurrence, which is why a repeat SHORTENS a column
    instead of raising there — and why `validate_spec` refuses the repeat itself."""
    assert strategies_for(('fifo', 'tmin', 'fifo')) == strategies_for(('fifo', 'tmin'))


# ── the driver installs what the derivation says ────────────────────────────────
# `_run_whatif_matrix` is where an arm set stops being a spec and starts being run state.
# The derivation above is only worth having if the driver actually READS it, and these run
# the real function with its one heavy call stubbed out -- a single-cell spec skips the
# freeze, so the install is the only thing that happens before the cell loop.

class _Stop(Exception):
    """Raised by the stubbed cell applier so the matrix stops right after the install."""


def _drive(monkeypatch, spec, channels=_BOTH):
    """Run `_run_whatif_matrix` as far as the install, then stop. Returns what it installed
    into `strategies.CHANNEL_RESTOCKS` and into `CONFIG['channels'][ch]['restocks']`."""
    import logging
    from Optimization.config import strategies as st
    from Optimization.config.sim_config import CONFIG
    from Optimization.simdriver import scenario as sc

    for ch in channels:
        monkeypatch.setitem(st.CHANNEL_RESTOCKS, ch, ('sentinel-untouched',))
        monkeypatch.setitem(CONFIG['channels'][ch], 'restocks', ('sentinel-untouched',))
    # STOPPED AT `_apply_cell`, NOT AT `_run_scenario`.  `_apply_cell` mutates a process-wide
    # CONFIG that nothing resets between cells, so letting it run leaks the cell's zoning and
    # scheduler into every later test in the session — measured: it broke three tests in
    # `test_settings_module` and `test_simconfig_registry` that assert CONFIG holds COPIES of
    # the registry's dicts. The install is complete before this line, which is the only thing
    # these tests are about.
    monkeypatch.setattr(sc, '_apply_cell',
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    log = logging.getLogger('spec-drive'); log.setLevel(logging.CRITICAL)
    try:
        sc._run_whatif_matrix('/tmp/nowhere', [('lbl', 'inv.db', 'aff.db')], log, spec)
    except _Stop:
        pass
    return ({ch: st.CHANNEL_RESTOCKS.get(ch) for ch in channels},
            {ch: CONFIG['channels'][ch].get('restocks') for ch in channels})


def test_the_driver_installs_each_channels_own_column_from_a_pair_spec(monkeypatch):
    """The whole point of the spec shape: store sweeps the store column and fulfillment the
    fulfillment column, IN RANK ORDER — not the same flat set twice."""
    pairs = [PHASE2_RIDER, ('tmin', 'map'), ('map', 'tmin')]
    installed, refreshed = _drive(monkeypatch, _pair_spec(pairs), _BOTH)
    assert installed['store'] == ('fifo', 'tmin', 'map')
    assert installed['fulfillment'] == ('fifo', 'map', 'tmin')
    assert installed['store'] != installed['fulfillment'], (
        'the two channels installed the same set, so the pairing bought nothing')
    for ch in _BOTH:
        assert refreshed[ch] not in (None, ('sentinel-untouched',)), (
            f'{ch}: CONFIG restocks was not refreshed, so the snapshot taken at import '
            f'still stands and the install is half-applied')


def test_a_spec_that_declares_no_arm_set_leaves_the_committed_value_alone(monkeypatch):
    """`--spec single` is the old flat run. None must mean "do not touch", which is the only
    reason a plain run is still bit-identical to what it always was."""
    installed, refreshed = _drive(monkeypatch, SPECS['single'], _BOTH)
    assert installed == {ch: ('sentinel-untouched',) for ch in _BOTH}
    assert refreshed == {ch: ('sentinel-untouched',) for ch in _BOTH}


def test_a_flat_arm_spec_still_installs_the_same_subset_everywhere(monkeypatch):
    """Unchanged behaviour for every spec on the books: one tuple, both channels."""
    spec = dict(SPECS['single'])
    spec['arms'] = ('fifo', 'tmin')
    installed, _ = _drive(monkeypatch, spec, _BOTH)
    assert installed == {ch: ('fifo', 'tmin') for ch in _BOTH}


def test_the_flat_fifo_rider_refusal_still_fires_at_the_driver(monkeypatch):
    """`run_channel_rollup` falls back to an ARBITRARY arm without it, so a fifo-less flat
    subset renders plausible, meaningless savings. Refused at minute zero."""
    spec = dict(SPECS['single'])
    spec['arms'] = ('tmin', 'map')
    with pytest.raises(ValueError, match='no `fifo` rule'):
        _drive(monkeypatch, spec, _BOTH)


def test_a_pair_spec_satisfies_the_flat_rider_check_by_construction(monkeypatch):
    """The pair-shaped rider check is STRONGER — it wants the rider as a PAIR — and that puts
    `fifo` in BOTH derived columns, so the flat check downstream is vacuous for any pair spec
    that got this far. Asserted, because the driver used to skip the flat check for pair specs
    behind a branch no input could reach."""
    pairs = [PHASE2_RIDER, ('tmin', 'map')]
    installed, _ = _drive(monkeypatch, _pair_spec(pairs), _BOTH)
    assert installed['store'] == (PHASE2_RIDER[0], 'tmin')
    for ch, rules in installed.items():
        assert 'fifo' in rules, f'{ch} column has no fifo, so the flat check is NOT vacuous'


def test_the_driver_validates_a_spec_that_never_came_through_get_spec(monkeypatch):
    """`get_spec` is the door every RUN comes through, but `_run_whatif_matrix` is also
    reachable with a hand-built spec — a test, a bench harness — and a malformed one would
    otherwise reach the channel install and half-apply it."""
    bad = _pair_spec([PHASE2_RIDER, ('tmin', 'map')], arms=('fifo',))
    with pytest.raises(ValueError, match='BOTH `arms` and `rule_pairs`'):
        _drive(monkeypatch, bad, _BOTH)


def test_an_inbound_spec_with_no_arm_set_is_refused_at_the_driver_too(monkeypatch):
    """The refusal that used to be written over the built CELLS. It lives in `validate_spec`
    now, and the DRIVER calls that — so a campaign spec that lost its pairing stops at minute
    zero rather than launching the full 34-arm suite over ten cells.

    The spec is the committed campaign with `rule_pairs` removed: since phase 1 ran the
    campaign itself builds, and what this test is about is the seam, not the constant."""
    stripped = {k: v for k, v in SPECS['inbound_policies'].items() if k != 'rule_pairs'}
    with pytest.raises(ValueError, match='states no arm set'):
        _drive(monkeypatch, stripped, _BOTH)

# -- phase 2 as reframed: unloading policies under ONE rule pair ---------------------------

def test_the_unload_spec_carries_the_winner_and_the_rider_and_nothing_else():
    """10 cells x 2 pairs x 2 stock modes = 40 units, not the 120 of the factorial.  The
    winner is PHASE2_PAIRS[0] read off the constant, never typed twice."""
    spec = SPECS['inbound_unload']
    assert rule_pairs_of(spec) == (tuple(wc.PHASE2_WINNER), wc.PHASE2_RIDER)
    assert tuple(wc.PHASE2_WINNER) == tuple(wc.PHASE2_PAIRS[0])
    assert spec['run_defaults'] is PHASE2_RUN_DEFAULTS
    assert spec['staffing_pin'] is wc.PHASE2_STAFFING_PIN
    assert 'arms' not in spec
    from Optimization.simdriver import cells as c
    built = c._build_cells(get_spec('inbound_unload'))
    names = [x.name for x in built]
    assert len(names) == 10 and 'k1_off_fifo' in names and 'k1_off_inb_off' in names
    assert c.reference_cell(built) == 'k1_off_fifo'
    assert 'inbound_unload' in _PAIR_SPECS


def test_the_axis_keep_filter_subsets_and_refuses_a_name_it_does_not_build():
    kept = [n for n, _ov in wc.phase2_inbound_axis(keep=('fifo', 'gmyopic', 'inb_off'))]
    assert kept == ['fifo', 'gmyopic', 'inb_off']          # the axis order, not keep's
    everything = [n for n, _ov in wc.phase2_inbound_axis()]
    assert [n for n, _ov in wc.phase2_inbound_axis(keep=None)] == everything
    with pytest.raises(ValueError, match='does not build'):
        wc.phase2_inbound_axis(keep=('fifo', 'gmyopc'))
