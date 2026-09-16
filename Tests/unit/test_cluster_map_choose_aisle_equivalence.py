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


# -- the complexity guard: one pass, and it stays one pass -----------------------------

class _CountingId:
    """An aisle id that counts how many times it is hashed.

    WHY NOT A COUNTING DICT, which is what this guard tried first and got wrong: the four-pass
    body rebinds `lifts` to a fresh plain dict after its comprehension, so its second read of
    every aisle goes to that plain dict and an instrumented mapping never sees it. Both bodies
    read the instrumented dict exactly n times, and the guard passed on the code it was built
    to reject.

    A key is hashed on every lookup and on every insert, whichever dict it lands in, so it sees
    all of it:  fused = 1 per live aisle;  four-pass = 3 (comprehension read, dict insert,
    `tied` filter read).
    """

    __slots__ = ('n', 'hits')

    def __init__(self, n):
        self.n = n
        self.hits = [0]

    def __hash__(self):
        self.hits[0] += 1
        return hash(self.n)

    def __eq__(self, other):
        return isinstance(other, _CountingId) and other.n == self.n

    def __repr__(self):
        return f'aisle{self.n}'


def _counted_case(n_aisles, *, distinct_lifts=1, target=2.0, seed=99):
    """A board whose aisle ids count their own hashes. Shares one counter across all ids."""
    rng = random.Random(seed)
    shared = [0]
    by_aisle, prefs, lifts = {}, {}, {}
    ids = [_CountingId(i * 7 + 3) for i in range(n_aisles)]
    rng.shuffle(ids)
    for aid in ids:
        aid.hits = shared
        n_bins = rng.randint(1, 5)
        by_aisle[aid] = [object() for _ in range(n_bins)]
        prefs[aid] = sorted(float(rng.randrange(4)) for _ in range(n_bins))
        lifts[aid] = float(rng.randrange(distinct_lifts))
    shared[0] = 0                      # ignore the hashes spent building the board
    return by_aisle, prefs, lifts, target, shared


#: What a call costs in key hashes, as a function of the board.  DERIVED FROM MEASUREMENT, not
#: guessed: two earlier versions of this guard asserted numbers reasoned from the source and both
#: were wrong — the first missed that the four-pass body rebinds `lifts` (so a counting DICT saw
#: n for both bodies), the second missed that the tie-break's `prefs_by_aisle[a]` lookups are
#: hashes too, and are paid by BOTH bodies.
#:
#:   fused      =     live + tie_break     one lift lookup per live aisle
#:   four-pass  = 3 * live + tie_break     comprehension read, dict insert, `tied` filter read
#:
#: where `tie_break` is `len(tied)` when the tie is wide and 0 when one aisle wins outright, since
#: `len(tied) == 1` returns before the `min`.  Verified exact at n = 8, 40, 200 in both regimes.
def _expected(live, tied, passes):
    return passes * live + (tied if tied > 1 else 0)


def _run_and_count(n_aisles, *, distinct_lifts, body):
    """Run `body` on an instrumented board and return (hashes it cost, live, tied).

    THE SNAPSHOT ON THE NEXT LINE IS LOAD-BEARING.  Describing the board — `max(lifts[a] ...)`
    and the `tied` count — hashes every key twice more, and reading `hits[0]` afterwards charged
    those to the body under test: the fused pass reported 3n and looked identical to the
    four-pass one.  The measurement was measuring itself.
    """
    by_aisle, prefs, lifts, target, hits = _counted_case(
        n_aisles, distinct_lifts=distinct_lifts)
    body(by_aisle, prefs, lifts, target)
    cost = hits[0]                      # snapshot BEFORE describing the board
    live = sum(1 for lst in by_aisle.values() if lst)
    best = max(lifts[a] for a, lst in by_aisle.items() if lst)
    tied = sum(1 for a, lst in by_aisle.items() if lst and lifts[a] == best)
    return cost, live, tied


def _fused(by_aisle, prefs, lifts, target):
    _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, target, lifts=lifts)


@pytest.mark.parametrize('n_aisles', [8, 40, 200, 800])
@pytest.mark.parametrize('distinct_lifts,regime', [(10 ** 9, 'warm'), (1, 'cold')])
def test_the_lift_scan_is_exactly_one_pass(n_aisles, distinct_lifts, regime):
    """THE BOUND, across a 100x span in the axis that was quadratic, in both regimes.

    `warm` gives every aisle a distinct lift, so one wins outright and the tie-break never runs —
    this isolates the lift scan at exactly one hash per live aisle.  `cold` ties every aisle at
    one value, which is the regime whose share was measured rising 3.0% -> 14.4% across the
    ladder and the one that actually grows.

    Not "fewer than before": EXACTLY the derived count.  A `<= 2n` bound would be satisfied by the
    body this replaced in the warm regime, and a wall-clock bound would be satisfied by a faster
    machine.
    """
    hits, live, tied = _run_and_count(n_aisles, distinct_lifts=distinct_lifts, body=_fused)
    assert live == n_aisles, f'fixture built {live} live aisles, expected {n_aisles}'
    want = _expected(live, tied, passes=1)
    assert hits == want, (
        f'{regime}: {hits} key hashes for {live} live aisles ({tied} tied), expected {want}. '
        f'The four-pass body cost {_expected(live, tied, passes=3)}; anything above the '
        f'one-pass figure means a pass came back.')


def test_the_counter_can_actually_fail():
    """NON-VACUITY, and not optional: every assertion above is satisfied by doing LESS work, so a
    counter that stopped counting would pass all of them.  The four-pass body is driven through
    the same instrumented keys and must cost three passes, not one.

    This test is also why the instrument counts KEY HASHES rather than dict reads.  With a
    counting `lifts` mapping this assertion read `n` for BOTH bodies — the four-pass body rebinds
    `lifts` to a fresh plain dict after its comprehension, so its later reads never touch the
    instrument — and the guard passed on exactly the code it exists to reject.
    """
    for n_aisles, distinct in ((40, 1), (40, 10 ** 9)):
        hits, live, tied = _run_and_count(
            n_aisles, distinct_lifts=distinct,
            body=lambda ba, pf, lf, tg: _reference(ba, pf, lf, tg))
        want = _expected(live, tied, passes=3)
        assert hits == want, (
            f'the four-pass reference cost {hits} hashes for {live} aisles ({tied} tied), not '
            f'{want} — the counter is not observing what this guard claims it observes')
        assert hits > _expected(live, tied, passes=1), (
            'the two bodies cost the same, so this guard cannot tell them apart')


def test_the_winner_still_has_the_maximal_lift():
    """THE INVARIANT, separate from the count. A single pass returning the WRONG aisle would
    satisfy every bound above."""
    rng = random.Random(5)
    for n in (3, 17, 120):
        for distinct in (1, 4, 50):
            by_aisle, prefs, lifts, target = _case(
                rng, n, distinct_lifts=distinct, empty_share=0.2, target=1.0)
            aid = _cluster_map_choose_aisle(by_aisle, prefs, None, {}, {}, 1.0, lifts=lifts)
            if aid is None:
                assert not any(by_aisle.values()), 'returned None with live aisles present'
                continue
            assert by_aisle[aid], 'chose an aisle with no bins'
            best = max(lifts[a] for a, lst in by_aisle.items() if lst)
            assert lifts[aid] == best, (
                f'chose aisle {aid} at lift {lifts[aid]}, but the maximum was {best}')
