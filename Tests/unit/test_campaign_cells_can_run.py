"""test_campaign_cells_can_run.py — a DECLARED cell is a cell the run can actually run.

Inbound-optimization 33, closing the class of failure 31 found.  `phase2_inbound_axis()`
declared ten cells; `Inbound/site_space.py` refused two of them under coupling.  Both files
were honest, both were tested, and **nothing checked them against each other** — so the
campaign carried two dead cells from the day site-dock closed, and the first thing that
would have noticed was a phase-2 launch paying its 975-second freeze and then failing 24
units at their first drain.

The join is one predicate over two declarations: `POLICY_VIEW_NEEDS` (what a yard/dock
entry reads off the frozen view, registered beside the entry) and `COMPOSED_VIEW_FIELDS` /
`UNCOMPOSED_VIEW_FIELDS` (what a two-leaf composition carries, declared beside the
composer).  `uncomposable_policies` is the only place they meet and `validate_spec` is the
only caller that matters — it refuses before the run directory exists.

What is pinned here, and what each catches:

  * **The partition is exhaustive.**  A new `SpaceView` field lands in neither set and
    fails, so classifying it is a decision somebody makes rather than a default somebody
    inherits.  Derive-either-from-the-other and the new field gets the cheap default — the
    exact route the window took in.
  * **Every registered policy declares its reads.**  A missing row would pass the gate by
    answering "reads nothing".
  * **The declaration is TRUE of the composer**, both ways: an uncomposed field is refused,
    and every composed field actually survives a composition.  A field that composed to
    None would be a silent drop, which for `window` is a dead arm rather than a degraded
    one — and a gate checked only against the refusal would not see it.
  * **The gate agrees with the composer cell by cell**, by composing what each declared
    cell's policy would be handed.  This is the assertion that does not need editing when
    "Build the coupled futuresight window zip" (34) lands: it re-derives both sides.
  * **The gate can FAIL** — proved by a synthesised policy, because after 34 there will be
    no failing cell left to point at (memory `real-test-coverage-is-317`).

Run:  python -m pytest Tests/unit/test_campaign_cells_can_run.py -q
"""
from __future__ import annotations

import pytest

from Inbound.gain import FAITHFUL_GAIN_FAMILIES, GAIN_POLICIES
from Inbound.priorities import DOCK_POLICIES, POLICY_VIEW_NEEDS, YARD_POLICIES, view_needs
from Inbound.site_space import (
    COMPOSED_VIEW_FIELDS, UNCOMPOSED_VIEW_FIELDS, compose_site_view, uncomposable_policies)
from Inbound.space import SpaceView

from Optimization.config import whatif_config as wc
from Optimization.config.whatif_config import (
    SPECS, inbound_policies_of, phase2_inbound_axis, validate_spec)

_STORE, _FUL = 'store', 'fulfillment'

#: The campaign specs. A spec with no inbound axis and no inbound run defaults names no
#: policy and this whole gate is inert for it, which is why the list is not "every spec".
_CAMPAIGN = ('inbound_select', 'inbound_policies', 'inbound_pilot')

#: THE CELLS THE COUPLED MODEL DECLINES TODAY, pinned so that emptying the set is the
#: visible half of 34 landing. `fsight_w5` and `fsight_wall` are 31's finding; 32 decided
#: BUILD, so when the window is composed this becomes `set()` and this test is what says
#: so. It is NOT an allowlist for new dead cells — a third name appearing here is the
#: defect this module exists to catch, not a line to add.
_KNOWN_DEAD = {'fsight_w5', 'fsight_wall'}

#: BinKey is a PLAIN TUPLE — `(handling, category, storage_size, unit_category)`, shaped
#: like `test_site_space_view.py`'s so the composer's regime filter behaves as in
#: production.
_K_STORE = ('conveyable', 'food', 'medium', 'pallet')
_K_FUL = ('conveyable', 'fulfillment', 'singleton', 'fulfillment')


class _Bin:
    """A bin-shaped stand-in: `regime_of` duck-types over exactly these attributes."""

    def __init__(self, key, tag=''):
        self.handling_type, self.storage_type, self.storage_size, self.unit_type = key
        self.tag = tag


#: A legal non-None value per UNCOMPOSED field, so the refusal can be driven generically.
#: Guarded below: a new uncomposed field with no sample here fails rather than silently
#: dropping out of the sweep.
_SAMPLE = {'window': ({101: 3}, {102: 1})}


def _leaves(**over):
    """Two leaves shaped as production builds them — every COMPOSED field carrying
    something, so "did it survive the composition" is answerable for each.  `frozen_at`
    matches: one drain is one freeze, and a mismatch is its own refusal."""
    s_bins = (_Bin(_K_STORE, 's0'),)
    f_bins = (_Bin(_K_FUL, 'f0'),)
    store = SpaceView(empties={_K_STORE: s_bins}, emptied_at={id(s_bins[0]): 5.0},
                      predicted={_K_STORE: s_bins}, released_at=10.0, versions=(1, 2, 3),
                      frozen_at=100.0, **over)
    ful = SpaceView(empties={_K_FUL: f_bins}, emptied_at={id(f_bins[0]): 6.0},
                    predicted={_K_FUL: f_bins}, released_at=20.0, versions=(4, 5, 6),
                    frozen_at=100.0, **over)
    return store, ful


def _composed_carrying(fields):
    """Compose two leaves that carry `fields` beyond the base, or the ValueError it raises."""
    over = {f: _SAMPLE[f] for f in fields if f in _SAMPLE}
    store, ful = _leaves(**over)
    try:
        return compose_site_view([(_STORE, store), (_FUL, ful)])
    except ValueError as exc:
        return exc


# ── 1. the two declarations are exhaustive and honest ────────────────────────────

def test_every_space_view_field_is_classified_exactly_once():
    """The exhaustiveness IS the gate: a field in neither set is a field the composer's
    behaviour is undeclared for, and the declaration is what every caller reads."""
    assert COMPOSED_VIEW_FIELDS | UNCOMPOSED_VIEW_FIELDS == set(SpaceView.__slots__), (
        'a SpaceView field is in neither COMPOSED_VIEW_FIELDS nor UNCOMPOSED_VIEW_FIELDS '
        '(or names one that no longer exists) — classify it: does compose_site_view merge '
        'it, or refuse a leaf carrying it?')
    assert not (COMPOSED_VIEW_FIELDS & UNCOMPOSED_VIEW_FIELDS)


def test_every_registered_policy_declares_what_it_reads():
    """A policy with no row would answer "reads nothing" and pass every composability gate
    by default — the silent direction."""
    registered = set(YARD_POLICIES) | set(DOCK_POLICIES)
    assert registered == set(POLICY_VIEW_NEEDS), (
        'a yard/dock policy declares no SpaceView needs (or a row names a policy that is '
        'not registered): register its reads beside its entry')
    for policy, needs in POLICY_VIEW_NEEDS.items():
        assert needs <= set(SpaceView.__slots__), f'{policy} declares a field that is not on the view'


def test_every_uncomposed_field_has_a_sample_so_the_sweep_below_is_total():
    """Non-vacuity guard for this module itself: a new uncomposed field with no sample
    would drop out of `test_an_uncomposed_field_is_refused` and prove nothing."""
    assert UNCOMPOSED_VIEW_FIELDS <= set(_SAMPLE)


# ── 2. the declaration is true of the composer, BOTH ways ────────────────────────

@pytest.mark.parametrize('field', sorted(UNCOMPOSED_VIEW_FIELDS))
def test_an_uncomposed_field_is_refused_rather_than_dropped(field):
    """Refused, loudly, and naming the field. Dropped silently, `futuresight` is not a
    degraded arm but a dead one — it raises on a None window by design."""
    out = _composed_carrying({field})
    assert isinstance(out, ValueError), f'{field} is declared uncomposed but composed anyway'
    assert f'`{field}`' in str(out)


@pytest.mark.parametrize('field', sorted(COMPOSED_VIEW_FIELDS))
def test_a_composed_field_actually_survives_the_composition(field):
    """The half a refusal-only check cannot see: a field declared composed that comes back
    None is the same defect as a refusal, arriving silently instead."""
    composed = _composed_carrying(set())
    assert not isinstance(composed, ValueError), composed
    value = getattr(composed, field)
    assert value is not None, f'{field} is declared composed but the composed view drops it'
    assert value or value == 0, f'{field} is declared composed but came back empty'


# ── 3. the campaign's declared cells, against what a coupled run can do ──────────

@pytest.mark.parametrize('name', _CAMPAIGN)
def test_the_gate_agrees_with_the_composer_on_every_declared_cell(name):
    """The assertion that survives 34: it re-derives BOTH sides rather than remembering
    either. For each policy a campaign spec names, hand a two-leaf composition exactly what
    that policy reads and check the gate's verdict against what actually happens."""
    for policy in inbound_policies_of(SPECS[name]):
        blocked = policy in uncomposable_policies([policy])
        out = _composed_carrying(view_needs(policy))
        assert blocked == isinstance(out, ValueError), (
            f'{name}: the gate and the composer disagree about {policy!r} — gate says '
            f'{"unrunnable" if blocked else "runnable"}, composing what it reads '
            f'{"raised" if isinstance(out, ValueError) else "succeeded"}')


def test_the_campaign_axis_names_no_cell_the_coupled_model_declines():
    """Phase 2 couples EVERY cell (PHASE2_RUN_DEFAULTS), so an uncomposable policy on the
    axis is a dead cell. Pinned rather than asserted empty because 34 has not landed:
    emptying `_KNOWN_DEAD` is the visible half of that ticket."""
    blocked = uncomposable_policies(inbound_policies_of(SPECS['inbound_policies']))
    dead = {suffix for suffix, ov in phase2_inbound_axis()
            if {ov.get('yard_policy'), ov.get('dock_policy')} & set(blocked)}
    assert dead == _KNOWN_DEAD, (
        'the set of phase-2 cells the coupled model declines has changed. Emptied: 34 has '
        'landed the window zip — clear _KNOWN_DEAD. Grown: a new cell names a policy that '
        'reads a structure a composed view does not carry, which is the defect this module '
        'exists to catch, not a line to add here.')


def test_the_pilot_and_the_selection_run_name_nothing_uncomposable():
    """The other two campaign specs, asserted EMPTY rather than pinned: neither names a
    gain policy today, and a change that gave one an uncomposable cell should fail here."""
    for name in ('inbound_select', 'inbound_pilot'):
        assert not uncomposable_policies(inbound_policies_of(SPECS[name])), name


# ── 4. the gate can fail — the sabotage direction ────────────────────────────────

def test_a_policy_reading_an_uncomposed_field_is_caught(monkeypatch):
    """After 34 there is no failing cell left to point at, so the check's ability to fail
    is proved by synthesising one.

    The synthesised need is a structure that is NOT on the view at all, deliberately: a
    need drawn from `UNCOMPOSED_VIEW_FIELDS` would stop catching anything the moment that
    set empties, which is precisely the era this test has to keep working in."""
    monkeypatch.setitem(POLICY_VIEW_NEEDS, '_saboteur', frozenset({'empties', '_unbuilt'}))
    blocked = uncomposable_policies(['fifo', '_saboteur'])
    assert set(blocked) == {'_saboteur'}, 'the gate did not catch a policy it cannot serve'
    assert blocked['_saboteur'] == frozenset({'_unbuilt'}), (
        'the gate must report the structures that are missing, not the whole need')


def test_a_spec_declaring_such_a_cell_is_refused_at_spec_build(monkeypatch):
    """End to end through the door every run comes through, so the refusal is proved where
    it fires and not only in the predicate."""
    monkeypatch.setitem(POLICY_VIEW_NEEDS, '_saboteur', frozenset({'_unbuilt'}))
    monkeypatch.setitem(YARD_POLICIES, '_saboteur', lambda t, ctx: 0.0)
    monkeypatch.setitem(DOCK_POLICIES, '_saboteur', lambda t, ctx: 0.0)
    spec = {'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
            'schedulers': ['lpt'], 'arms': ['fifo', 'tmin'], 'reference': 'k1_off_a',
            'run_defaults': {'couple_channels': True},
            'inbound': [('a', {'yard_policy': 'fifo', 'dock_policy': 'fifo'}),
                        ('b', {'yard_policy': '_saboteur', 'dock_policy': '_saboteur'})]}
    with pytest.raises(ValueError, match=r"cell\(s\) \['b'\]"):
        validate_spec(spec, 'sabotage')


def test_an_unknown_policy_name_raises_rather_than_passing(monkeypatch):
    """A gate that skipped what it could not resolve would pass the one spelling mistake
    the registries also miss — a cell naming `gain_myopik` resolves nowhere and would
    otherwise sail through as "reads nothing"."""
    with pytest.raises(KeyError, match='declares no SpaceView needs'):
        uncomposable_policies(['gain_myopik'])


# ── 5. the same gap from the ARM axis ────────────────────────────────────────────

def _phase2_like(**over):
    base = {'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
            'schedulers': ['lpt'], 'staffing_pin': {'stub': 1}, 'reference': 'k1_off_fifo',
            'run_defaults': {'couple_channels': True},
            # The futuresight cells removed: this section is about the ARM axis, and a
            # spec that fails the composability gate first would prove nothing about it.
            'inbound': [e for e in phase2_inbound_axis() if not e[0].startswith('fsight')]}
    return {**base, **over}


def test_a_gain_cell_over_an_unfaithful_rule_is_refused():
    """`rule_pairs` is a HAND-COPIED artifact field, and the artifact legitimately reports
    families that still need `_gain_bundle_for` extended (`needs_bundle_extension`, ticket
    20) — so copying a ranking before the extension lands is a reachable edit, and the
    failure is the same shape: a declared campaign that dies at its first drain."""
    unfaithful = next(r for r in wc.RESTOCK_KEYS if r not in FAITHFUL_GAIN_FAMILIES)
    spec = _phase2_like(rule_pairs=[('fifo', 'fifo'), (unfaithful, 'tmin')])
    with pytest.raises(ValueError, match='faithful bundle only for'):
        validate_spec(spec, 'unfaithful')


def test_the_same_rules_pass_when_no_cell_names_a_gain_policy():
    """The condition is real, not decoration: `_gain_bundle_for` is only called when the
    inbound spec names a gain policy, so a fifo/lifo-only matrix may sweep any rule."""
    unfaithful = next(r for r in wc.RESTOCK_KEYS if r not in FAITHFUL_GAIN_FAMILIES)
    axis = [e for e in phase2_inbound_axis()
            if e[1].get('yard_policy') not in GAIN_POLICIES]
    assert axis, 'the axis has no non-gain cell left to make this test mean anything'
    validate_spec(_phase2_like(inbound=axis,
                               rule_pairs=[('fifo', 'fifo'), (unfaithful, 'tmin')]),
                  'nongain')


def test_a_faithful_ranking_over_the_runnable_cells_is_accepted():
    """The green case, and the shape phase 2 launches in once 34 lands: no refusal fires on
    a spec whose cells and arms the run can both actually serve."""
    validate_spec(_phase2_like(rule_pairs=[('fifo', 'fifo'), ('tmin', 'tmax')]), 'clean')


# ── 6. the gate is conditional on COUPLING, and correctly so ─────────────────────

def test_an_uncoupled_spec_naming_the_same_cells_is_not_refused():
    """A composition of ONE is the view by identity, so `futuresight` is perfectly
    runnable uncoupled — refusing it there would be a false gate, and phase 1 is
    uncoupled by design."""
    spec = {'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
            'schedulers': ['lpt'], 'arms': 'all', 'reference': 'k1_off_fifo',
            'run_defaults': {'couple_channels': False},
            'inbound': phase2_inbound_axis()}
    validate_spec(spec, 'uncoupled')
    store, _ful = _leaves(window=_SAMPLE['window'])
    assert compose_site_view([(_STORE, store)]) is store, (
        'a one-leaf composition must be the view itself, window and all')
