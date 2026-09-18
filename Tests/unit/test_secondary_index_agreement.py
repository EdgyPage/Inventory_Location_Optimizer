"""test_secondary_index_agreement.py — the free-bin index and its two mirrors must agree.

`Inventory_Manager` maintains its free-bin index `_index` (BinKey -> free bins) through exactly
TWO write points, `_index_add` and `_index_remove`. Two secondary indices mirror through those
same two points, each behind an arming flag set by an `init_*`:

    _aisle_index   BinKey -> {aisle_id: bins sorted by _D}   armed by _travel_costs_ready
    _band_index    BinKey -> [per-velocity-band buckets]     armed by _zoning_enabled

They are maintained by hand, in two hot methods, with three different removal strategies —
`_index` and `_band_index` swap-remove through position maps, `_aisle_index` bisects and then
scans forward for identity. **Nothing tested that they stay in agreement.** A mirror that silently
stopped mirroring would not raise: it would hand a placement function a free bin that is not free,
or hide one that is, and the run would simply place differently.

This file asserts the invariant the code has always had and never stated:

    for every BinKey, the MULTISET of bins in `_index[key]` equals the multiset in
    `_aisle_index[key]` flattened, and equals the multiset in `_band_index[key]` flattened.

It also pins the two position maps, because those are what make the swap-removes O(1) and what a
missed `_bin_index_pos` update would corrupt without any visible symptom.

## How this differs from `test_index_equivalence.py`

That file already pins `_aisle_index` at the RESULT level: the fast path must produce identical
placement to the candidates scan. This one pins it at the STRUCTURAL level. The difference is
what each can catch. A mirror that drifts on some other fixture, or in a way that happens not to
move a placement in the tested scenario, passes an equivalence test and fails this one. And
`_band_index` has NEITHER — no equivalence test exists for the zoning mirror at all, so until now
it was maintained by hand in two hot methods with nothing checking it.

`inventory_zoning.py` records why these must stay inline on the manager rather than move behind an
interface, so the invariant is the thing to hold onto while the shape stays as it is.

Run:  python -m pytest Tests/unit/test_secondary_index_agreement.py -q
"""
from __future__ import annotations

import collections
import random

import pytest

from Tests.unit.test_empty_first_topup import _warehouse
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.inventory.Inventory_Management import Inventory_Manager


def _mgr(*, zoning: bool, travel: bool):
    """A manager with either, both or neither mirror armed."""
    wh = _warehouse(small_aisle=True)
    mgr = Inventory_Manager(wh)
    if zoning:
        mgr.configure_zoning(True, n_bands=2)
    if travel:
        mgr.init_travel_costs(WorkloadParams(x_speed=4.5, y_speed=2.0, pick_intercept=10.0,
                                             pick_weight_coef=0.1, pick_volume_coef=0.5))
    return mgr


def _bag(bins):
    """A multiset keyed on identity — two distinct bins can compare equal on value."""
    return collections.Counter(id(b) for b in bins)


def _assert_agreement(mgr, note=''):
    for key, flat in mgr._index.items():
        want = _bag(flat)

        if mgr._travel_costs_ready:
            got = _bag([b for lst in mgr._aisle_index[key].values() for b in lst])
            assert got == want, (
                f'{note}_aisle_index disagrees with _index at {key}: '
                f'{sum(want.values())} free bins vs {sum(got.values())} mirrored')
            for aid, lst in mgr._aisle_index[key].items():
                ds = [b._D for b in lst]
                assert ds == sorted(ds), f'{note}aisle {aid} lost its _D ordering at {key}'

        if mgr._zoning_enabled:
            got = _bag([b for bucket in mgr._band_index[key] for b in bucket])
            assert got == want, (
                f'{note}_band_index disagrees with _index at {key}: '
                f'{sum(want.values())} vs {sum(got.values())}')

    # The position maps are what make the swap-removes O(1); a stale entry corrupts silently.
    for key, lst in mgr._index.items():
        for i, b in enumerate(lst):
            assert mgr._bin_index_pos[id(b)] == i, (
                f'{note}_bin_index_pos is stale at {key}[{i}]')


# ── the invariant, under randomised churn ─────────────────────────────────────────

@pytest.mark.parametrize('zoning,travel', [(False, True), (True, False), (True, True)])
@pytest.mark.parametrize('seed', range(4))
def test_the_mirrors_track_the_index_through_random_churn(zoning, travel, seed):
    """Interleave removals and re-adds; every mirror must match at every step.

    Removals are drawn from the LIVE index rather than a fixed list, because the swap-remove
    moves elements: a sequence that looks independent on paper is not.
    """
    mgr = _mgr(zoning=zoning, travel=travel)
    rng = random.Random(4000 + seed)
    _assert_agreement(mgr, 'at rest: ')

    held = []
    for step in range(60):
        live = [b for lst in mgr._index.values() for b in lst]
        if held and (not live or rng.random() < 0.4):
            mgr._index_add(held.pop(rng.randrange(len(held))))
        elif live:
            b = live[rng.randrange(len(live))]
            mgr._index_remove(b)
            held.append(b)
        _assert_agreement(mgr, f'step {step}: ')


def test_removing_every_bin_empties_every_mirror():
    """The boundary the swap-remove is most likely to get wrong."""
    mgr = _mgr(zoning=True, travel=True)
    for b in [b for lst in mgr._index.values() for b in lst]:
        mgr._index_remove(b)
    _assert_agreement(mgr, 'drained: ')
    assert sum(len(v) for v in mgr._index.values()) == 0
    assert mgr._bin_index_pos == {}, 'the position map outlived its bins'
    for key in mgr._index:
        assert not [b for lst in mgr._aisle_index[key].values() for b in lst]
        assert not [b for bucket in mgr._band_index[key] for b in bucket]


# ── non-vacuity ───────────────────────────────────────────────────────────────────

def test_the_checker_actually_catches_a_broken_mirror():
    """If `_assert_agreement` cannot fail, every test above is decoration.

    Simulates the regression the invariant exists to catch: a bin removed from `_index` but
    left behind in a mirror, which is what a missed flag check in `_index_remove` produces.
    """
    mgr = _mgr(zoning=False, travel=True)
    key = next(k for k, v in mgr._index.items() if v)
    victim = mgr._index[key][0]
    mgr._index_remove(victim)
    # put it back into the MIRROR only -- the exact shape of a mirror that stopped mirroring
    mgr._aisle_index[key][victim.location[0]].append(victim)
    with pytest.raises(AssertionError, match='_aisle_index disagrees'):
        _assert_agreement(mgr)


def test_an_armed_mirror_is_actually_armed():
    """NON-VACUITY for the parametrisation: a disarmed mirror is skipped, not passed."""
    both = _mgr(zoning=True, travel=True)
    assert both._zoning_enabled and both._travel_costs_ready
    neither = _mgr(zoning=False, travel=False)
    assert not neither._zoning_enabled and not neither._travel_costs_ready
    assert not neither._aisle_index, 'unarmed _aisle_index should hold nothing'
