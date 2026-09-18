"""test_placement_policy.py — the one declaration per placement family, EXERCISED.

Adding a placement policy cost 5-8 declarations across four packages. `PlacementPolicy`
collapses four of them: the positional `_RESTOCKS` tuple, `FAITHFUL_GAIN_FAMILIES`, the gain
evaluator's per-family `aisle_state=` dicts, and its `if restock == ...` chain.

**The one field that cannot be derived is the one worth testing.** `ledger_terms` names the
`AisleLedger` books a family's placement COMMITS to, and the inbound gain evaluator copies
exactly those before every virtual placement. It has to be DECLARED, because the evaluator
needs the list before it builds a pool to read it off. So the declaration carries the hazard
`_gain_bundle_for` states out loud:

> A dict left off that list is not a refusal, it is a virtual placement advancing the REAL
> warehouse.

This file closes that by BUILDING each family and comparing the declaration against
`AisleLedger.bound` — the books the pool (or the per-unit fn) was actually handed. That is
the fix shape memory `symbol-table-relationship-not-verified-by-symbols` records: a gate that
resolves NAMES cannot catch a wrong RELATIONSHIP, so exercise it and assert it produced
something.

Run:  python -m pytest Tests/unit/test_placement_policy.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402

from Optimization.config.strategies import (                       # noqa: E402
    FAITHFUL_GAIN_FAMILIES, POLICY_BY_KEY, RESTOCK_KEYS, _RESTOCKS)
from Warehouse.inventory.aisle_ledger import AisleLedger           # noqa: E402
from Warehouse.layout.Storage_Primitive import viable_storage_units  # noqa: E402
from Warehouse.placement.policy import GAIN_ADAPTERS, PlacementPolicy  # noqa: E402


# ── the record's own refusals ─────────────────────────────────────────────────────

def test_a_record_refuses_an_unknown_gain_adapter():
    with pytest.raises(ValueError) as ei:
        PlacementPolicy('x', 'X', lambda m, c: None, gain='telepathy')
    assert 'telepathy' in str(ei.value)


def test_a_record_refuses_a_ledger_term_that_is_not_a_book():
    """A term with no book is a copy the gain evaluator would never make."""
    with pytest.raises(ValueError) as ei:
        PlacementPolicy('x', 'X', lambda m, c: None, ledger_terms=('aisle_sku_sets',))
    assert 'aisle_sku_sets' in str(ei.value), 'the manager spelling is not the ledger spelling'
    assert 'sku_sets' in str(ei.value)


def test_a_pool_family_with_no_ledger_terms_is_refused():
    """The exact failure the declaration exists to prevent: the evaluator would copy nothing
    and the virtual placement would land in the live warehouse."""
    with pytest.raises(ValueError) as ei:
        PlacementPolicy('x', 'X', lambda m, c: None, gain='pool')
    assert 'advance' in str(ei.value) or 'advancing' in str(ei.value)


def test_state_names_is_the_manager_spelling_of_the_same_books():
    pol = POLICY_BY_KEY['rank_cartlabor']
    assert pol.state_names == tuple('aisle_' + t for t in pol.ledger_terms)
    assert all(t in AisleLedger.POLICY_BOOKS for t in pol.ledger_terms)


# ── the registry ──────────────────────────────────────────────────────────────────

def test_every_restock_key_has_exactly_one_record():
    assert len(_RESTOCKS) == len(POLICY_BY_KEY) == len(RESTOCK_KEYS) == 17
    assert tuple(POLICY_BY_KEY) == RESTOCK_KEYS, 'the derived key order moved'


def test_the_faithful_families_are_derived_not_listed():
    assert set(FAITHFUL_GAIN_FAMILIES) == {k for k, p in POLICY_BY_KEY.items() if p.gain}
    assert all(p.gain in GAIN_ADAPTERS for p in _RESTOCKS if p.gain)


def test_the_strategy_grid_carries_every_record_through():
    """`STRATEGIES` is built from these records; a field that stopped being copied across
    would arm a worker wrongly with nothing raising."""
    from Optimization.config.strategies import STRATEGIES
    for s in STRATEGIES:
        pol = POLICY_BY_KEY[s.restock]
        assert s.build is pol.build
        assert s.needs_affinity == pol.needs_affinity
        assert s.needs_demand == pol.needs_demand
        assert s.uses_aisle_index == pol.uses_aisle_index


# ── the declaration against the wiring ────────────────────────────────────────────

def _ledgers_of(assets) -> list[AisleLedger]:
    """Every `AisleLedger` the built placement actually binds, from both halves.

    A pooled family exposes its ledger as `pool._led`; the per-unit families expose theirs
    as `place_one.ledger`. Both are the object, not a list, so what comes back is what the
    family can write rather than what it says it writes.
    """
    out = []
    p = assets.mgr.placement
    led = getattr(p.place_one, 'ledger', None)
    if led is not None:
        out.append(led)
    if p.is_pooled:
        unit = next(u for o in assets.inventory.orders
                    for u in viable_storage_units(o, 1))
        cands = list(assets.mgr._candidates(unit))[:12]
        if len(cands) >= 3:
            pool = p.open_pool(list(cands), unit)
            inner = getattr(pool, '_led', None)
            if inner is not None:
                out.append(inner)
    return out


@pytest.mark.parametrize('key', [p.key for p in _RESTOCKS])
def test_the_declared_ledger_terms_are_the_books_the_family_binds(key):
    """Built, not read. The declaration and the wiring are the same fact twice, and this is
    the only place they are made to agree."""
    arm = f'uni_{key}_norsl'
    assets = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy=arm, seed=1,
                             coverage=2.0, safety=0.4)
    pol = POLICY_BY_KEY[key]
    bound: set = set()
    for led in _ledgers_of(assets):
        bound |= set(led.bound)

    declared = set(pol.ledger_terms)
    # A RIDER binds itself off its carrier and is unbound wherever the carrier is a copy, so
    # it is bound-but-undeclared by design (`AisleLedger.RIDERS`); the danger this test names
    # -- bound, undeclared, therefore uncopied by the evaluator -- cannot arise for it, and
    # `test_partner_aisles.py` proves the unbinding under a copy-on-write view directly.
    from Warehouse.inventory.aisle_ledger import AisleLedger
    bound -= set(AisleLedger.RIDERS)
    assert bound == declared, (
        f'{key}: declares {sorted(declared)} but its placement binds {sorted(bound)}. '
        f'A book bound and NOT declared is the dangerous direction -- the gain evaluator '
        f'would not copy it, and a virtual placement would advance the real warehouse.')


def test_the_comparison_above_is_not_vacuous():
    """At least one family must bind something, or every assertion above compares two empty
    sets. And the families must not all bind the SAME thing, or the declaration carries no
    information."""
    seen = {}
    for key in ('rank_labor', 'rank_cartlabor', 'rank_minlabor', 'map'):
        assets = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy=f'uni_{key}_norsl',
                                 seed=1, coverage=2.0, safety=0.4)
        seen[key] = frozenset().union(*(set(l.bound) for l in _ledgers_of(assets))) \
            if _ledgers_of(assets) else frozenset()
    assert seen['rank_labor'], 'no family binds anything; every comparison is empty vs empty'
    assert len(set(seen.values())) > 1, 'every family binds the same books'
    assert 'vol_sum' in seen['rank_cartlabor'] and 'vol_sum' not in seen['rank_labor'], (
        'the cart term is what separates these two families; if both or neither bind it '
        'the declaration is not distinguishing them')
    assert 'member_pos' in seen['rank_minlabor']
    assert not seen['map'], 'the map family binds no aisle books and should declare none'
