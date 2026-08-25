"""test_forward_pick_family.py — one spelling for forward-pick vs reserve.

`Task.from_batch` drains `_sku_singleton_bins` before `_sku_pallet_bins` "so that
forward-pick locations are always preferred over reserve locations". Which of those two a
bin is filed under was decided by **three** expressions — `isinstance(unit, Singleton)` on
the unit side, `bin_.unit_type == 'singleton'` twice on the bin side.

They agreed. But only because of an invariant nothing stated and nothing checked:
`_candidates_raw` matches `unit_type` **exactly** in every branch, so a unit never reaches
a bin of another family. Three spellings of one concept, two of them on the opposite object
from the third, is a disagreement with a delay on it — and the put queues route on this
same concept, so a fourth spelling was about to appear.

The unification is **byte-identical**, which is worth stating because the plan for this work
predicted otherwise: it guessed that `FulfillmentBin` not being a `Singleton` meant mixed
catalogues would move. It does not — `unit_category == 'fulfillment'` is not `'singleton'`
either way, so all three expressions already agreed on fulfillment too.

Run:  python -m pytest Tests/unit/test_forward_pick_family.py -q
"""
from __future__ import annotations

import inspect
import types

import pytest

from Warehouse.inventory.inventory_common import (
    FORWARD_PICK_FAMILY, binkey_of, is_forward_pick,
)
from Warehouse.layout.Storage_Primitive import FulfillmentBin, Pallet, Singleton

FAMILIES = (Pallet, Singleton, FulfillmentBin)


def _unit_like(cls, size='small'):
    return types.SimpleNamespace(
        storage_size=size, unit_category=cls.unit_category,
        order=types.SimpleNamespace(
            storage_handle_config=types.SimpleNamespace(
                handling='conveyable', category='food')))


def _bin_like(cls, size='small'):
    return types.SimpleNamespace(
        handling_type='conveyable', storage_type='food',
        storage_size=size, unit_type=cls.unit_category)


# ── the three retired expressions all agreed, and the new one reproduces them ─────

@pytest.mark.parametrize('cls', FAMILIES, ids=lambda c: c.__name__)
def test_the_unit_side_and_bin_side_answers_match(cls):
    """The invariant that made three spellings survivable — asserted, not assumed."""
    assert is_forward_pick(_unit_like(cls)) == is_forward_pick(_bin_like(cls))


@pytest.mark.parametrize('cls', FAMILIES, ids=lambda c: c.__name__)
def test_it_reproduces_isinstance_singleton(cls):
    """The retired unit-side test. `issubclass(FulfillmentBin, Singleton)` is False and so
    is `unit_category == 'singleton'` — they agreed on fulfillment too."""
    assert is_forward_pick(_unit_like(cls)) == issubclass(cls, Singleton)


@pytest.mark.parametrize('cls', FAMILIES, ids=lambda c: c.__name__)
def test_it_reproduces_the_bin_unit_type_test(cls):
    """The retired bin-side test, used at two sites."""
    assert is_forward_pick(_bin_like(cls)) == (cls.unit_category == 'singleton')


def test_only_the_singleton_family_is_forward_pick():
    got = {c.__name__: is_forward_pick(_unit_like(c)) for c in FAMILIES}
    assert got == {'Pallet': False, 'Singleton': True, 'FulfillmentBin': False}


def test_fulfillment_is_reserve_deliberately():
    """Recorded rather than fixed: a fulfillment SKU has ONE bin family, so the two-phase
    drain is a no-op for it and everything lands in reserve. If that ever changes, one
    predicate decides it."""
    assert is_forward_pick(_unit_like(FulfillmentBin)) is False
    doc = inspect.getdoc(is_forward_pick) or ''
    assert 'FULFILLMENT IS NOT FORWARD-PICK' in doc


# ── the family is BinKey slot 4, on either object ─────────────────────────────────

@pytest.mark.parametrize('cls', FAMILIES, ids=lambda c: c.__name__)
def test_the_predicate_is_binkey_slot_four(cls):
    for obj in (_unit_like(cls), _bin_like(cls)):
        assert is_forward_pick(obj) == (binkey_of(obj)[3] == FORWARD_PICK_FAMILY)


def test_the_size_tier_does_not_change_the_answer():
    """Slot 3 spills up; slot 4 never does. A large pallet is no more forward-pick than a
    small one."""
    for size in ('small', 'medium', 'large', 'extra_large'):
        assert is_forward_pick(_unit_like(Pallet, size)) is False


# ── the invariant that makes unit-side and bin-side interchangeable ───────────────

def test_candidate_matching_never_crosses_unit_families():
    """WHY the two sides agree: `_candidates_raw` puts `unit_type` in BinKey slot 4 on
    every branch, so a unit only ever sees bins of its own family. If a branch ever
    matched loosely, the unit-side and bin-side answers could diverge and the single
    predicate would be wrong."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    src = inspect.getsource(Inventory_Manager._candidates_raw)
    assert "key  = binkey_of(unit)" in src            # the singleton branch: exact
    assert "unit_type)" in src                        # the tiered branch: slot 4 is unit_type
    assert 'unit_type = unit.unit_category' in src


# ── and no fourth spelling comes back ─────────────────────────────────────────────

def test_no_module_selects_between_the_two_bin_indexes_by_hand():
    """A ratchet, SCOPED to the concept.

    `unit_type == 'singleton'` is also how bin WIDTH (`Aisle_Dimensions:51`), bin HEIGHT
    (`Aisle_Storage:99`), candidate matching (`_candidates_raw:645`) and planning buckets
    are decided — different questions in the same words, and forbidding the phrase
    outright would be wrong. What must go through one predicate is the choice BETWEEN
    `_sku_singleton_bins` and `_sku_pallet_bins`, so that is what this scans for: a raw
    family test in the neighbourhood of those two dicts.

    Scans code with docstrings stripped, so the prose above is not counted.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {'Warehouse/inventory/inventory_common.py'}
    offenders = []
    for sub in ('Warehouse', 'Optimization'):
        for path in sorted((root / sub).rglob('*.py')):
            rel = path.relative_to(root).as_posix()
            if rel in allowed:
                continue
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = node.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        node.body = body[1:] or [ast.Pass()]
            code = ast.unparse(ast.fix_missing_locations(tree))
            lines = code.splitlines()
            for i, line in enumerate(lines):
                if ('_sku_singleton_bins' not in line
                        and '_sku_pallet_bins' not in line):
                    continue
                window = chr(10).join(lines[max(0, i - 3):i + 4])
                if ('isinstance(unit, Singleton)' in window
                        or "unit_type == 'singleton'" in window):
                    offenders.append(f'{rel}:{i + 1}')
    assert not offenders, (
        f'{offenders} choose between the two bin indexes with a hand-rolled family '
        f'test; use is_forward_pick()')
