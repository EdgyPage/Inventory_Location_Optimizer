"""test_cluster_map_choose_aisle_equivalence.py — the fused lift scan returns the old answer.

`_cluster_map_choose_aisle` used to make FOUR passes over the live aisles per placement: build
`live`, rebuild `lifts` onto it, take a `max`, then re-filter for `tied`.  The cluster cells
measured the real scan volume at 8,141,090 elements over 76,514 calls (k=1.99, local exponents
pinned at 2.04) — a clean quadratic that the call-tree could not see at all, because a
comprehension is one frame entry however wide it is.  The four passes are now one.

WHY THIS FILE EXISTS RATHER THAN LEANING ON THE EXISTING ORACLES.  The frozen-oracle suite does
reach this function — 1,507 calls across `test_co_demand_pool_equivalence`,
`test_placement_fastpath_equivalence` and `test_assignment_and_labor` — but **1,503 of those take
the `lifts is None` straggler branch**.  The branch that carries the quadratic is the one where a
caller's same-SKU run cache supplies `lifts`, and it was exercised four times.  A green oracle
suite was therefore nearly no evidence about the change, which is the same shape as the
ranked-assign oracle passing 42/42 on a heap it never ran.

The reference below is the ORIGINAL four-pass body, verbatim.  That is the point: equivalence is
asserted against the code that was replaced, not against a re-derivation of what it ought to do.

Run:  python -m pytest Tests/unit/test_cluster_map_choose_aisle_equivalence.py -q
"""
from __future__ import annotations

import math
import random

import pytest

from Warehouse.placement.Assignment_Functions import (
    _closest_abs, _cluster_map_choose_aisle,
)


def _reference(by_aisle, prefs_by_aisle, lifts, target):
    """The four-pass body exactly as it stood before the fusion (`lifts` always supplied)."""
    live = [aid for aid, lst in by_aisle.items() if lst]
    if not live:
        return None
    lifts = {a: lifts[a] for a in live}
    best = max(lifts.values())
    tied = [a for a in live if lifts[a] == best]
    if len(tied) == 1:
        return tied[0]
    if target is None:
        return min(tied, key=lambda a: prefs_by_aisle[a][0])
    return min(tied, key=lambda a: _closest_abs(prefs_by_aisle[a], target))


def _case(rng, n_aisles, *, distinct_lifts, empty_share, target, pref_steps=4):
    """One randomised board.

    `distinct_lifts` controls how wide the LIFT tie is — a small value forces `tied` to hold
    most of the aisles, the cold-start regime whose share was measured rising 3.0% -> 14.4%
    across the ladder.

    `pref_steps` controls how often the GAP then ties as well, and it is the parameter that
    makes `tied`'s ORDER observable at all. With near-continuous prefs the anchor gaps are
    distinct, `min` has a unique winner, and `sorted(tied)` / `reversed(tied)` sabotages
    differed on 0 of 2,400 boards — the test could not see the ordering property this
    function's fusion had to preserve. Production prefs tie constantly: `pref.get(id(b), 0.0)`
    defaults to zero for every bin the caller has no preference for."""
    by_aisle, prefs, lifts = {}, {}, {}
    # THE IDS ARE SHUFFLED AND NON-CONTIGUOUS ON PURPOSE.  A first version numbered aisles
    # `range(n)` and inserted them in order, so `by_aisle` iteration order happened to equal
    # sorted-id order -- and a sabotage that replaced `tied` with `sorted(tied)` differed on
    # 0 of 2,400 boards.  The test asserted an ordering property it could not observe.
    ids = [aid * 7 + 3 for aid in range(n_aisles)]
    rng.shuffle(ids)
    for aid in ids:
        n_bins = 0 if rng.random() < empty_share else rng.randint(1, 5)
        by_aisle[aid] = [object() for _ in range(n_bins)]
        prefs[aid] = sorted(float(rng.randrange(pref_steps))
                            for _ in range(max(n_bins, 1)))
        lifts[aid] = float(rng.randrange(distinct_lifts))
    return by_aisle, prefs, lifts, target


@pytest.mark.parametrize('distinct_lifts', [1, 2, 5, 40])
@pytest.mark.parametrize('empty_share', [0.0, 0.3, 0.9])
def test_the_fused_pass_agrees_with_the_four_pass_body(distinct_lifts, empty_share):
    """The core equivalence, over the regimes that actually differ.

    `distinct_lifts=1` is the cold start — EVERY live aisle ties at one value, so `tied` is the
    whole board and the anchor-gap tie-break decides. That is the case the fusion had to keep
    exactly, because `min` returns the FIRST element achieving the minimum and the fused loop
    had to preserve `by_aisle` iteration order to reproduce it.
    """
    rng = random.Random(4242 + distinct_lifts * 31 + int(empty_share * 10))
    for _ in range(200):
        n = rng.randint(1, 60)
        target = None if rng.random() < 0.25 else float(rng.randrange(4))
        by_aisle, prefs, lifts, target = _case(
            rng, n, distinct_lifts=distinct_lifts, empty_share=empty_share, target=target)

        got = _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, target, lifts=lifts)
        want = _reference(by_aisle, prefs, lifts, target)
        assert got == want, (
            f'fused pass chose {got!r}, four-pass body chose {want!r} '
            f'(n_aisles={n}, distinct_lifts={distinct_lifts}, target={target})')


def test_the_tie_is_actually_wide_in_the_cold_case():
    """NON-VACUITY for the parametrisation above. If `distinct_lifts=1` did not in fact produce
    a wide tie, every case would return on `len(tied) == 1` and the tie-break — the half the
    fusion could most easily have broken — would never run at all."""
    rng = random.Random(7)
    by_aisle, prefs, lifts, target = _case(
        rng, 40, distinct_lifts=1, empty_share=0.0, target=2.0)
    live = [a for a, lst in by_aisle.items() if lst]
    best = max(lifts[a] for a in live)
    tied = [a for a in live if lifts[a] == best]
    assert len(tied) == len(live) == 40, f'cold case tied only {len(tied)} of {len(live)}'


def test_an_all_empty_board_returns_none_from_both():
    """`live` was the empty-check; the fused loop has no `live`, so the guard moved to `tied`."""
    by_aisle = {0: [], 1: [], 2: []}
    prefs = {0: [1.0], 1: [2.0], 2: [3.0]}
    lifts = {0: 0.0, 1: 0.0, 2: 0.0}
    assert _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, 1.0, lifts=lifts) is None
    assert _reference(by_aisle, prefs, lifts, 1.0) is None


def test_a_nan_lift_is_skipped_exactly_as_max_skipped_it():
    """`max` never selects a NaN it meets after a real value, because `nan > x` is False. The
    fused loop uses the same `>` and `==`, so it must agree — including on which aisle wins.
    Pinned because a reader could 'fix' this into `if v >= best` and silently change it."""
    by_aisle = {0: [object()], 1: [object()], 2: [object()]}
    prefs = {0: [1.0], 1: [2.0], 2: [3.0]}
    lifts = {0: 1.0, 1: math.nan, 2: 2.0}
    assert _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, 0.0, lifts=lifts) \
        == _reference(by_aisle, prefs, lifts, 0.0) == 2


def test_negative_lifts_do_not_lose_to_an_absent_zero():
    """`_delta_lift_from_row` sums `(lift - 1) * f`, so a value can be negative. The old body
    seeded its max from the live values themselves; the fused loop seeds from `-inf`, which must
    not change which aisle wins when every lift is below zero."""
    by_aisle = {0: [object()], 1: [object()]}
    prefs = {0: [1.0], 1: [2.0]}
    lifts = {0: -5.0, 1: -2.0}
    assert _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, 0.0, lifts=lifts) \
        == _reference(by_aisle, prefs, lifts, 0.0) == 1


def test_the_boards_really_do_produce_GAP_ties_not_just_lift_ties():
    """THE NON-VACUITY THAT MATTERS, and the one this file got wrong twice.

    `tied`'s ORDER is only observable when two aisles tie on lift AND then tie again on anchor
    gap — otherwise `min` has a unique winner and any permutation of `tied` gives the same
    answer. The first fixture used `range(n)` ids inserted in order (so `sorted(tied)` was a
    no-op) and near-continuous prefs (so gaps never tied): sabotages replacing `tied` with
    `sorted(tied)` or `reversed(tied)` differed on **0 of 2,400 boards**. The test asserted an
    ordering property it could not see, while the source comment called that order load-bearing.

    With shuffled non-contiguous ids and coarse prefs, those same two sabotages differ on 939
    and 1,239 boards. This test pins the fixture property that makes them visible, so nobody
    'improves' the generator back into vacuity.
    """
    rng = random.Random(11)
    boards_with_a_gap_tie = 0
    for _ in range(300):
        n = rng.randint(2, 60)
        target = float(rng.randrange(4))
        by_aisle, prefs, lifts, target = _case(
            rng, n, distinct_lifts=1, empty_share=0.0, target=target)
        live = [a for a, lst in by_aisle.items() if lst]
        gaps = [_closest_abs(prefs[a], target) for a in live]
        if len(gaps) != len(set(gaps)):
            boards_with_a_gap_tie += 1
    assert boards_with_a_gap_tie > 250, (
        f'only {boards_with_a_gap_tie}/300 boards have two aisles tied on BOTH lift and gap — '
        f'the ordering of `tied` is unobservable on the rest, so the equivalence test above '
        f'cannot catch a permutation bug')

    # ...and the ids must not arrive in sorted order, or `sorted(tied)` is a no-op regardless.
    rng2 = random.Random(12)
    by_aisle, _p, _l, _t = _case(rng2, 40, distinct_lifts=1, empty_share=0.0, target=1.0)
    keys = list(by_aisle)
    assert keys != sorted(keys), 'aisle ids arrive sorted — `sorted(tied)` would change nothing'
