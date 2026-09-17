"""test_cold_start_tie_break.py — the merged pref index answers what the O(A) scan answered.

`_cluster_map_choose_aisle`'s cold start is the growing case, not a corner: a SKU with no
partner placed anywhere gives EVERY live aisle a delta lift of exactly 0.0, so `tied` is every
live aisle and the tie-break degenerates to "the live aisle whose pref is closest to `target`".
Measured on a 16x ladder, that fraction rises **3.0% -> 14.4%**
(`docs/design/COMPLEXITY_ROUND_FINDINGS.md` section 3.5.1), on top of a scan already fitting
k=1.99.

`_AislePrefIndex` answers it in O(log N + k). This file is what says it answers the SAME thing.

## THE TIE ORDER IS THE DIFFICULTY, and it is not the obvious one

The scan being replaced is `min(tied, key=lambda a: _closest_abs(prefs_by_aisle[a], target))`,
and `min` returns the FIRST element achieving the minimum. So among aisles whose closest pref is
equally distant from `target`, the winner is the EARLIEST IN `by_aisle` ITERATION ORDER -- not
the lowest pref, not the lowest aisle id. Ticket 05's own record says this is "precisely the
property the equivalence test needed two attempts to be able to observe", so it is tested here
by CONSTRUCTING the collision rather than hoping a random fixture produces one.

## And it must survive consumption

The index is a third structure in multiset-sync with `by_aisle` and `prefs_by_aisle`: a bin the
wave hands out has to leave all three, or the next cold query answers from a bin that is gone.
The randomised leg interleaves removals with queries for exactly that reason.

Run:  python -m pytest Tests/unit/test_cold_start_tie_break.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.placement.Assignment_Functions import _AislePrefIndex, _closest_abs


def _scan(by_aisle, prefs, target):
    """The scan `_AislePrefIndex` replaces, verbatim in behaviour."""
    live = [a for a, lst in by_aisle.items() if lst]
    if not live:
        return None
    if target is None:
        return min(live, key=lambda a: prefs[a][0])
    return min(live, key=lambda a: _closest_abs(prefs[a], target))


def _build(spec):
    """`{aid: [pref, ...]}` -> (by_aisle, prefs, index). Insertion order IS `by_aisle` order."""
    by_aisle = {a: [object()] * len(ps) for a, ps in spec.items()}
    prefs = {a: sorted(ps) for a, ps in spec.items()}
    return by_aisle, prefs, _AislePrefIndex(by_aisle, prefs)


# ── the tie rule ──────────────────────────────────────────────────────────────────

def test_equal_gaps_go_to_the_EARLIEST_aisle_in_by_aisle_order():
    """Two aisles equidistant from `target`, one on each side. `min` keeps the first it saw."""
    spec = {70: [12.0], 30: [8.0]}                 # 70 is FIRST in by_aisle order
    by_aisle, prefs, idx = _build(spec)
    assert idx.closest(10.0) == 70 == _scan(by_aisle, prefs, 10.0)

    # The same two aisles, declared the other way round: the answer must FOLLOW the order.
    by_aisle, prefs, idx = _build({30: [8.0], 70: [12.0]})
    assert idx.closest(10.0) == 30 == _scan(by_aisle, prefs, 10.0)


def test_the_tie_is_not_broken_on_aisle_id_or_on_pref():
    """NON-VACUITY for the test above: it must fail for an index that broke ties the two
    plausible wrong ways. Here the earliest aisle has BOTH the higher id and the higher pref,
    so `min(id)` and `min(pref)` would each give the other answer."""
    by_aisle, prefs, idx = _build({70: [12.0], 30: [8.0]})
    got = idx.closest(10.0)
    assert got == 70, got
    assert got != min(by_aisle), 'this fixture cannot tell an id tie-break apart'
    assert prefs[got][0] > prefs[30][0], 'this fixture cannot tell a pref tie-break apart'


def test_an_exact_hit_still_respects_the_order():
    """`target` sits exactly on a pref in two aisles: gap 0.0 both ways."""
    by_aisle, prefs, idx = _build({55: [5.0, 9.0], 11: [5.0]})
    assert idx.closest(5.0) == 55 == _scan(by_aisle, prefs, 5.0)


def test_a_tie_can_be_won_from_further_out_in_pref_order():
    """THE WALK'S REAL REQUIREMENT. The nearer-in-pref-order entry is NOT always the answer:
    an equally distant entry further out can win on rank, so the walk cannot stop at the first
    minimal gap it meets."""
    # aisle 9 is first in by_aisle order and sits BELOW target; aisle 4 sits above, equidistant.
    by_aisle, prefs, idx = _build({9: [4.0], 4: [6.0]})
    assert idx.closest(5.0) == 9 == _scan(by_aisle, prefs, 5.0)
    by_aisle, prefs, idx = _build({4: [6.0], 9: [4.0]})
    assert idx.closest(5.0) == 4 == _scan(by_aisle, prefs, 5.0)


def test_target_None_is_the_globally_smallest_pref():
    """`target is None` replaces `min(tied, key=lambda a: prefs[a][0])`."""
    by_aisle, prefs, idx = _build({8: [9.0, 2.0], 3: [7.0]})
    assert idx.closest(None) == 8 == _scan(by_aisle, prefs, None)


# ── consumption ───────────────────────────────────────────────────────────────────

def test_a_consumed_bin_stops_answering():
    by_aisle, prefs, idx = _build({1: [5.0], 2: [6.0]})
    assert idx.closest(5.0) == 1
    idx.remove(1, 5.0)
    by_aisle[1].pop(); prefs[1].remove(5.0)
    assert idx.closest(5.0) == 2 == _scan(by_aisle, prefs, 5.0)


def test_an_emptied_aisle_disappears_exactly_as_the_scan_skips_it():
    """`if not lst: continue` is how the scan drops an exhausted aisle; the index drops it by
    having no alive entry left."""
    by_aisle, prefs, idx = _build({1: [5.0], 2: [50.0]})
    idx.remove(1, 5.0)
    by_aisle[1].pop(); prefs[1].remove(5.0)
    assert idx.closest(5.0) == 2 == _scan(by_aisle, prefs, 5.0)


def test_removing_one_of_several_equal_prefs_leaves_the_rest():
    """The multiset case: two bins in one aisle share a pref, and one is handed out."""
    by_aisle, prefs, idx = _build({1: [5.0, 5.0], 2: [99.0]})
    idx.remove(1, 5.0)
    by_aisle[1].pop(); prefs[1].remove(5.0)
    assert idx.closest(5.0) == 1, 'the aisle still holds a bin at that pref'
    idx.remove(1, 5.0)
    by_aisle[1].pop(); prefs[1].remove(5.0)
    assert idx.closest(5.0) == 2


def test_an_empty_index_answers_None():
    by_aisle, prefs, idx = _build({1: [5.0]})
    idx.remove(1, 5.0)
    by_aisle[1].pop(); prefs[1].clear()
    assert idx.closest(5.0) is None and _scan(by_aisle, prefs, 5.0) is None


# ── the randomised leg ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(8))
def test_the_index_matches_the_scan_under_interleaved_consumption(seed):
    """400 trials of the same shape found no divergence; this is the pinned subset.

    Small integer prefs on purpose: ties are the case under test, so the fixture has to
    MANUFACTURE them rather than wait for floats to collide.
    """
    rng = random.Random(1000 + seed)
    for _trial in range(60):
        spec = {a: [float(rng.randint(0, 20)) for _ in range(rng.randint(1, 5))]
                for a in rng.sample(range(100, 140), rng.randint(1, 8))}
        by_aisle, prefs, idx = _build(spec)
        for _step in range(6):
            live = [a for a, lst in by_aisle.items() if lst]
            if not live:
                break
            for target in [None] + [float(rng.randint(-2, 22)) for _ in range(3)]:
                assert idx.closest(target) == _scan(by_aisle, prefs, target), (
                    f'target={target} spec={spec} live={live}')
            a = rng.choice(live)
            p = prefs[a].pop(rng.randrange(len(prefs[a])))
            by_aisle[a].pop()
            idx.remove(a, p)


def test_the_randomised_leg_actually_produces_ties():
    """NON-VACUITY: with no collisions the tie rule is never exercised and the leg above is a
    test of `bisect`."""
    rng = random.Random(1000)
    ties = 0
    for _trial in range(60):
        spec = {a: [float(rng.randint(0, 20)) for _ in range(rng.randint(1, 5))]
                for a in rng.sample(range(100, 140), rng.randint(1, 8))}
        by_aisle, prefs, _idx = _build(spec)
        for target in (5.0, 10.0, 15.0):
            live = [a for a, lst in by_aisle.items() if lst]
            gaps = [_closest_abs(prefs[a], target) for a in live]
            if len(gaps) != len(set(gaps)):
                ties += 1
    assert ties >= 20, f'only {ties} tied configurations; the tie rule is barely exercised'
