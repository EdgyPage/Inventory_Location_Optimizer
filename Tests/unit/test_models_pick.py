"""The pick model (`Optimization/simconfig/models/pick.py`): the task forms, the Palm
probability, the location value, and the drain-order switch -- each against a direct count.
The fill-root verification (S07: the script form sees fulfillment's co-location, the independent
form inverts it) lives in the study."""
from __future__ import annotations

import math
import random

import pytest

from Optimization.simconfig.models import pick


def _days(n=400, seed=2):
    rng = random.Random(seed)
    skus = list(range(30))
    out = []
    for _ in range(n):
        d = set(rng.sample(skus, 4))
        if 0 in d:                      # an affinity pair: 1 is drawn with 0
            d.add(1)
        out.append(d)
    return out


def test_independent_tasks_is_the_poisson_open_count():
    assert pick.tasks_independent([0.0, 0.5, 2.0]) == pytest.approx(
        (1 - math.exp(-0.5)) + (1 - math.exp(-2.0)))
    r = pick.TASK_MODEL.evaluate({'Lambda': [1.0, 1.0]})
    assert r['tasks'] == pytest.approx(2 * (1 - math.exp(-1)))


def test_colocation_delta_is_the_recount_difference():
    days = _days()
    where = {s: s % 10 for s in range(30)}
    base = pick.tasks_script(days, where)
    for new in (1 % 10, 3, 7):                       # moving SKU 0 next to its partner, and away
        moved = dict(where)
        moved[0] = new
        assert pick.tasks_script(days, moved) - base == pytest.approx(
            pick.colocation_delta(0, new, days, where))


def test_co_locating_an_affine_pair_saves_tasks_the_independent_form_cannot_see():
    days = _days()
    where = {s: s % 10 for s in range(30)}
    moved = dict(where)
    moved[0] = where[1]                              # put 0 in its partner's aisle
    assert pick.colocation_delta(0, where[1], days, where) < 0
    lam = lambda w: [sum(sum(1 for s in d if w.get(s) == a) for d in days) / len(days)
                     for a in range(10)]
    # the independent form reads the pair's co-draws as independent visits: it prices the
    # move as a small change of load shape, not as the saved task
    indep = pick.tasks_independent(lam(moved)) - pick.tasks_independent(lam(where))
    script = pick.tasks_script(days, moved) - pick.tasks_script(days, where)
    assert script < indep


def test_palm_open_and_location_value():
    days = [{1, 2}, {1}, {1, 3}, {2, 3}]
    where = {1: 0, 2: 0, 3: 1}
    assert pick.palm_open(1, 0, days, where) == pytest.approx(2 / 3)   # alone on {1}, {1,3}
    assert pick.palm_open(9, 0, days, where) is None
    g = pick.location_value(1, 0, x_b=100.0, h_b=10.0, days=days, where=where,
                            where_x={1: 100.0, 2: 40.0, 3: 0.0}, x_pace=0.5, t_new=50.0,
                            one_way=False)
    # lambda 3/4; P0 2/3; the one shared day overshoots 60 in two ways at 0.5 s/in = 60 s
    assert g == pytest.approx(0.75 * (10.0 + (2 / 3) * 50.0 + (1 / 3) * 60.0))
    assert pick.overshoot_travel(100.0, [40.0], 0.5, one_way=True) == 0.0


def test_drain_orders_differ_only_in_order():
    class _A:
        def __init__(self, key):
            self.key = key

    class _G:
        by_id = {1: _A(('c', 'food', 'small', 'pallet')), 2: _A(('c', 'food', 'small', 'pallet'))}

    bm = {7: [(2, 1, 1, 50), (1, 3, 1, 5)]}
    rec = pick.placement(bm, _G(), 'recorded').sites[7]
    small = pick.placement(bm, _G(), 'smallest').sites[7]
    assert [s.qty for s in small] == [5, 50]
    assert sorted(s.qty for s in rec) == [5, 50]
    with pytest.raises(ValueError):
        pick.placement(bm, _G(), 'largest')
