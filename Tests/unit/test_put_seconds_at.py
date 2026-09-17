"""test_put_seconds_at.py — the billed put and the optimised put, joined at last.

Two callers price the same physical act. `Inventory_Manager._cost_putaway` BILLS a put
through `putaway.put_cost`; `Inbound.gain._Evaluator._cost_at` OPTIMISES where to put it.
Until 2026-09-17 the second restated the first's formula and kept only the travel half:

    billed     travel + M(y) · (intercept + quantity · per_item + quantity · var)
    optimised  travel

No intercept, no per-item charge, and no HEIGHT MULTIPLIER — which is bin-dependent and so
does not cancel out of a difference between two candidate bins.

**That simplification may well be right. The point is that nothing could tell it from
drift.** All 57 `def test_` in `Tests/unit/test_gain_plan.py` validate the evaluator against
a naive rebuild of itself or a closed form of its own formula; not one references
`put_cost`. Meanwhile the put formula moved three times in two months — ADR-0001's per-item
charge, the 2026-08-24 one-clock refactor, ADR-0003 — and each time, whether the gain arms
moved with it was unknowable.

This file is the missing test. It pins the RELATIONSHIP between the two readings, so a
change to either half fails here instead of quietly reweighting every inbound plan.

Run:  python -m pytest Tests/unit/test_put_seconds_at.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Warehouse.kernel.cost_model import (
    DEFAULT_HEIGHT_BRACKETS, SpeedProfile, handle_var, height_multiplier, per_pick,
)
from Warehouse.operations.putaway import PutawayCost, put_cost, put_seconds_at

FOOT = SpeedProfile(2.0, 4.0)

#: A bin high enough to sit in a non-unit height bracket — the whole point of the
#: multiplier, and the term whose absence from the objective is bin-dependent.
HIGH = dict(x_phys=120.0, y_phys=180.0)
LOW = dict(x_phys=120.0, y_phys=6.0)


def _handling(cost, y, weight, volume, quantity):
    """The term `cost=None` drops, written out longhand rather than called."""
    return per_pick(
        height_multiplier(cost.height_brackets, y),
        cost.intercept,
        handle_var(weight, volume, cost.weight_coef, cost.volume_coef,
                   cost.weight_fn, cost.volume_fn),
        quantity,
        cost.per_item,
    )


# ── one body ──────────────────────────────────────────────────────────────────────

def test_put_cost_is_put_seconds_at_with_the_cost_in():
    """`put_cost` is the BILLING name for one expression, not a second implementation."""
    c = PutawayCost()
    billed = put_cost(140.0, 96.0, 12.0, 300.0, 7, FOOT, c)
    shared = put_seconds_at(140.0, 96.0, speed=FOOT, cost=c,
                            weight=12.0, volume=300.0, quantity=7)
    assert billed == shared, 'the billing call stopped routing through the one expression'


def test_put_cost_has_no_arithmetic_of_its_own():
    """Source-checked: the reason the equality above holds must be that there IS one body.

    Two implementations that happen to agree on one input is exactly the state this ticket
    found, and a value test cannot tell that apart from a shared body.
    """
    src = inspect.getsource(put_cost)
    for op in ('+', '*', 'per_pick', 'height_multiplier', 'handle_var'):
        assert op not in src.split('"""')[-1], (
            f'`put_cost` computes {op!r} itself again; it must delegate to put_seconds_at')


# ── the drop, named ───────────────────────────────────────────────────────────────

def test_cost_none_drops_exactly_the_handling_term():
    """The relationship the objective relies on, stated as an equation.

    Not "the optimised reading is smaller" — the exact difference, so that a change to the
    handling expression on either side lands here.
    """
    c = PutawayCost()
    weight, volume, quantity = 12.0, 300.0, 7
    billed = put_seconds_at(140.0, 96.0, speed=FOOT, cost=c,
                            weight=weight, volume=volume, quantity=quantity)
    optimised = put_seconds_at(140.0, 96.0, speed=FOOT, cost=None)
    assert billed - optimised == pytest.approx(
        _handling(c, 96.0, weight, volume, quantity), rel=1e-12)


def test_the_dropped_term_is_not_zero():
    """Non-vacuity: the equation above would hold trivially if handling priced at nothing."""
    c = PutawayCost()
    assert _handling(c, 96.0, 12.0, 300.0, 7) > 1.0, 'the fixture drops nothing'


def test_the_dropped_term_is_bin_dependent_which_is_why_it_matters():
    """A dropped CONSTANT cancels out of a comparison between two bins. This one does not.

    `M(y)` is a step function of height, so two bins in the same aisle at the same x can
    differ in billed handling while the objective scores them identically. That is the
    concrete reason ticket 16 exists, and it is asserted rather than argued.
    """
    c = PutawayCost()
    weight, volume, quantity = 12.0, 300.0, 7
    kw = dict(speed=FOOT, weight=weight, volume=volume, quantity=quantity)

    billed_gap = (put_seconds_at(cost=c, **HIGH, **kw)
                  - put_seconds_at(cost=c, **LOW, **kw))
    optimised_gap = (put_seconds_at(cost=None, x_phys=HIGH['x_phys'], y_phys=HIGH['y_phys'],
                                    speed=FOOT)
                     - put_seconds_at(cost=None, x_phys=LOW['x_phys'], y_phys=LOW['y_phys'],
                                      speed=FOOT))
    assert height_multiplier(DEFAULT_HEIGHT_BRACKETS, HIGH['y_phys']) != \
        height_multiplier(DEFAULT_HEIGHT_BRACKETS, LOW['y_phys']), \
        'the fixture bins share a height bracket, so this proves nothing'
    assert billed_gap != pytest.approx(optimised_gap, rel=1e-9), (
        'the two readings rank these bins identically — either the multiplier stopped '
        'varying with height, or the objective started paying for handling')


# ── the objective really does use it ──────────────────────────────────────────────

def test_the_gain_objective_prices_its_put_through_this_expression():
    """Source-checked, because a VALUE check here would pass on a restated formula.

    The failure this file exists for is not a wrong number; it is a second implementation
    that agrees today. So what is pinned is that `_cost_at` calls the shared expression and
    names its dropped term, not that it returns some particular float.
    """
    from Inbound.gain import _Evaluator

    src = inspect.getsource(_Evaluator._cost_at)
    body = src.split('"""')[-1]
    assert 'put_seconds_at(' in body, (
        'the gain objective stopped calling the shared put expression — it is restating '
        'the cost model again, which is the exact condition ticket 16 closed')
    assert 'cost=None' in body, (
        'the objective must DROP the handling term by name; an unwritten term is how the '
        'two readings parted company for two months without anything noticing')
    assert 'x_pace' not in body, (
        '`_cost_at` is computing put travel itself again beside the shared call')
