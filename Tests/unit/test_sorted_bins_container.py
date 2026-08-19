"""
test_sorted_bins_container.py — `_SortedBins` behaves exactly like the `set[Aisle.Bin]` it
replaces, with iteration permanently in `location` order.

The container exists so `Task.from_batch` can stop paying two `sorted()` calls per batch SKU
(the t_task k=2.31 deep-ladder offender). Its contract: iteration == `sorted(mirror_set,
key=location)` after ANY interleaving of adds and discards (property-tested, seeded);
identity-semantics membership (a raw `set` of Bins hashes by id — equal-located distinct
objects must not alias); defaultdict-compatible truthiness/len; duplicate adds rejected
loudly (structurally impossible upstream — an assert, not a silent dedupe).

Run: python -m pytest Tests/unit/test_sorted_bins_container.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.inventory.inventory_common import _SortedBins


class _Bin:
    """Location-keyed stand-in; identity semantics like Aisle.Bin (no __eq__/__hash__)."""
    __slots__ = ('location',)

    def __init__(self, aid, x, y):
        self.location = (aid, x, y)


def _mk_bins(rng, n):
    # globally unique locations, like production (monotone aisle ids, unique bays)
    locs = rng.sample([(a, x, y) for a in range(1, 8)
                       for x in range(1, 12) for y in range(1, 12)], n)
    return [_Bin(*t) for t in locs]


@pytest.mark.parametrize('seed', range(6))
def test_iteration_matches_sorted_mirror_under_churn(seed):
    rng = random.Random(300 + seed)
    bins = _mk_bins(rng, 60)
    sb, mirror = _SortedBins(), set()
    for _ in range(500):
        if mirror and rng.random() < 0.45:
            b = rng.choice(sorted(mirror, key=lambda x: x.location))
            sb.discard(b)
            mirror.discard(b)
        else:
            candidates = [b for b in bins if b not in mirror]
            if not candidates:
                continue
            b = rng.choice(candidates)
            sb.add(b)
            mirror.add(b)
        assert list(sb) == sorted(mirror, key=lambda x: x.location)
        assert len(sb) == len(mirror)
        assert bool(sb) == bool(mirror)
    assert mirror, 'churn left an empty container — the loop tested nothing'


def test_identity_membership_not_location_membership():
    a = _Bin(1, 2, 3)
    twin = _Bin(1, 2, 3)          # same location, different object
    sb = _SortedBins()
    sb.add(a)
    assert a in sb
    assert twin not in sb, 'membership must be identity, like the set it replaces'
    sb.discard(twin)              # discarding a non-member is a silent no-op (set semantics)
    assert a in sb and len(sb) == 1


def test_duplicate_add_rejected():
    b = _Bin(2, 1, 1)
    sb = _SortedBins()
    sb.add(b)
    with pytest.raises(AssertionError):
        sb.add(b)                 # upstream makes this impossible; silence would hide a bug


def test_discard_absent_is_noop_and_empty_is_falsy():
    sb = _SortedBins()
    sb.discard(_Bin(3, 1, 1))
    assert not sb and len(sb) == 0 and list(sb) == []
