"""test_placement_selection_is_not_a_scan.py — the heap's win, fenced as a call count.

`bd29d2eb` replaced `_TravelBalancedPool.take`'s per-placement argmin over every aisle with a
`(score, rank)` heap. The comment it left records what that scan cost at campaign scale:

    4,852,858 takes x 224 aisles = 1.09 BILLION iterations, 71.2% of the receive drain

**Nothing guards it.** `test_travel_balanced_equivalence.py` proves the pool's RESULT is
byte-identical to the frozen oracle — and a "simplification" back to a linear scan would keep
every one of those results identical and pass the whole file, while restoring the billion
iterations. That is the same shape as `_admit_held`, which survived every release because the
only thing anyone checked was that it produced the right answer.

So this file counts calls, in the style `Tests/unit/test_admit_held_is_linear.py` established:

  * a wall-clock assertion would be flaky; a call count is exact and deterministic;
  * the bound is stated against the ONE variable that must not enter it — the aisle count.

WHAT IS PINNED, and the distinction is the finding this round measured:

  * **the winner refresh is O(1) per take** — exactly one `_aisle_best` per successful
    placement, whatever the warehouse looks like;
  * **the selection is NOT O(aisles)** — within a SKU run, growing the aisle count 8x must not
    grow the per-take work at all;
  * **the run-boundary rebuild IS O(aisles)**, deliberately, and is pinned at exactly
    `boundaries x live_aisles` so that a change to it is a decision rather than a drift. It
    fits k = 1.912 against catalogue size and is this round's top refactor candidate
    (`.scratch/complexity-round/issues/07-...`); pinning it here is what will make that
    refactor's effect legible rather than invisible.

Run:  python -m pytest Tests/unit/test_placement_selection_is_not_a_scan.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.placement import Assignment_Functions as AF

# Co-located helpers — the repo's documented sibling-import convention (Tests/README.md:
# "Helper-sharing tests must be co-located ... bare name, same directory only").
from test_travel_balanced_equivalence import _Affinity, _mk_state, _rand_wave, _wp

#: Captured ONCE, at import. Two `_drive` calls in one test both monkeypatch the class, and
#: a wrapper that captured `Pool.take` at install time would wrap the previous WRAPPER --
#: so the first counter would also count the second run's takes. The first version of this
#: file did exactly that and reported 20 takes against 10.
_ORIG_TAKE = AF._TravelBalancedPool.take
_ORIG_AISLE_BEST = AF._TravelBalancedPool._aisle_best


class _Counter:
    """Counts `_aisle_best` calls, split by whether a run boundary is in progress.

    The split cannot be read from the total: both call sites sit inside `take`, so the tracer's
    (child, parent) flow counting cannot separate them either. `take` is wrapped to observe
    `self._run_sku` against the unit's SKU — the same condition the rebuild branch tests — and
    the live aisle count at that moment.
    """

    def __init__(self):
        self.takes = self.boundaries = self.rebuild = self.refresh = 0
        self.aisles_at_boundary: list[int] = []
        self._pending = 0

    def install(self, monkeypatch, base_take=None):
        Pool = AF._TravelBalancedPool
        real_take = base_take or _ORIG_TAKE
        real_ab = _ORIG_AISLE_BEST
        outer = self

        def take(self, unit):
            outer.takes += 1
            if unit.order.sku != getattr(self, '_run_sku', object()):
                outer.boundaries += 1
                a = len(self._by_aisle)
                outer.aisles_at_boundary.append(a)
                outer._pending = a
            else:
                outer._pending = 0
            return real_take(self, unit)

        def _aisle_best(self, aid, var):
            if outer._pending > 0:
                outer.rebuild += 1
                outer._pending -= 1
            else:
                outer.refresh += 1
            return real_ab(self, aid, var)

        monkeypatch.setattr(Pool, 'take', take)
        monkeypatch.setattr(Pool, '_aisle_best', _aisle_best)
        return self


def _drive(monkeypatch, *, n_aisles, n_skus=6, units_per_sku=4, bins_per_aisle=6, seed=7,
           base_take=None):
    """One wave through a real `_TravelBalancedPool`, counted.

    `base_take` swaps the implementation under the counter, which is how the non-vacuity
    test drives a deliberate scan without the counter wrapping itself.
    """
    c = _Counter().install(monkeypatch, base_take=base_take)
    rng = random.Random(seed)
    units, bins, freq, qty, plp, _vol = _rand_wave(
        rng, n_skus, units_per_sku, n_aisles, bins_per_aisle)
    aids = sorted({b.location[0] for b in bins})
    st = _mk_state(aids)
    pool = AF._TravelBalancedPool(
        bins, _Affinity([u.order.sku for u in units]), _wp(),
        st['aisle_sku_sets'], st['aisle_idx_sets'], st['aisle_demand_sum'],
        st['aisle_pick_load_sum'], plp, freq, qty, cart=None)
    placed = 0
    for u in pool.order(units):
        if pool.take(u)[0] is not None:
            placed += 1
    return c, placed


# ── the invariants ────────────────────────────────────────────────────────────────

def test_the_refresh_is_one_call_per_take_whatever_the_warehouse_looks_like(monkeypatch):
    """The O(1) half. Each successful placement refreshes exactly the aisle it drew from."""
    for n_aisles in (3, 24):
        c, placed = _drive(monkeypatch, n_aisles=n_aisles)
        assert placed > 0, 'nothing was placed, so the counts below are about an empty run'
        assert c.refresh == c.takes, (
            f'{c.refresh} refreshes for {c.takes} takes at {n_aisles} aisles — the winner '
            f'refresh is supposed to be exactly one per take')


def test_the_selection_does_not_walk_the_aisles(monkeypatch):
    """THE regression `bd29d2eb` removed, stated as a count rather than a stopwatch.

    Per-take work is measured OUTSIDE run boundaries, because a boundary legitimately costs
    O(aisles) and would swamp the signal. Growing the warehouse 8x must not move it at all; if
    the heap becomes a scan again, this number tracks the aisle count.
    """
    small, _ = _drive(monkeypatch, n_aisles=3)
    large, _ = _drive(monkeypatch, n_aisles=24)

    assert small.takes == large.takes, (
        'the two runs placed different numbers of units, so their per-take costs are not '
        'comparable — the wave must be identical apart from the aisle count')

    per_take_small = small.refresh / small.takes
    per_take_large = large.refresh / large.takes
    assert per_take_large <= per_take_small + 1e-9, (
        f'per-take selection work grew from {per_take_small:.2f} to {per_take_large:.2f} when '
        f'the warehouse went 3 -> 24 aisles. The selection is walking the aisles again: at '
        f'campaign scale that scan was 4,852,858 takes x 224 aisles = 1.09 BILLION iterations.')


def test_the_run_boundary_rebuild_is_exactly_one_pass_per_aisle(monkeypatch):
    """The O(aisles) half, pinned so that changing it is a DECISION.

    This is the term that fits k = 1.912 against catalogue size and is the round's top
    candidate. Pinning the exact relationship now is what will make a lazy-bound refactor's
    effect legible — a change here should show up as this assertion failing with a smaller
    number, not as silence.
    """
    c, _ = _drive(monkeypatch, n_aisles=12)
    assert c.boundaries > 1, 'only one SKU run — the rebuild path is barely exercised'
    expected = sum(c.aisles_at_boundary)
    assert c.rebuild == expected, (
        f'{c.rebuild} rebuild calls against {expected} = sum of live aisles at each of '
        f'{c.boundaries} boundaries. The rebuild is meant to touch each live aisle exactly '
        f'once per SKU run.')


def test_the_counter_can_tell_a_scan_from_a_heap(monkeypatch):
    """NON-VACUITY, and the most important test here.

    Every assertion above is a bound satisfied by doing LESS work, so a counter that undercounts
    — or a driver that places nothing — passes them all. This drives a deliberate O(aisles)
    selection and asserts the counter reports it, so the fence is known to have teeth before it
    is trusted.
    """
    def scanning_take(self, unit):
        # the pre-bd29d2eb shape: touch every aisle on every placement.  `self._aisle_best`
        # resolves to the COUNTER's wrapper, so the scan is what gets counted.
        if unit.order.sku == getattr(self, '_run_sku', None):
            for aid in list(self._by_aisle):
                self._aisle_best(aid, self._var)
        return _ORIG_TAKE(self, unit)

    small, _ = _drive(monkeypatch, n_aisles=3, base_take=scanning_take)
    large, _ = _drive(monkeypatch, n_aisles=24, base_take=scanning_take)
    per_take_small = small.refresh / small.takes
    per_take_large = large.refresh / large.takes
    assert per_take_large > per_take_small + 1e-9, (
        f'a deliberately scanning take reported {per_take_small:.2f} -> {per_take_large:.2f} '
        f'per-take work across an 8x aisle growth — the counter cannot see a scan, so the '
        f'checks above fence nothing')
