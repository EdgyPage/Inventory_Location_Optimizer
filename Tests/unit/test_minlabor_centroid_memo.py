"""test_minlabor_centroid_memo.py — the per-run partner-centroid memo moves no placement.

`_MinLaborPool.take` memoises `_demand_weighted_partner_centroid` per winning aisle for the
current SKU run (`.scratch/inbound-fullscale-perf/` S10: the walk over every member of the
aisle was the largest term of a 400k take).  The claim: within a run the only write to
member positions is the take's own `add_bin` -- this SKU's index, in the winning aisle --
so the memo is exact, provided the entry is dropped whenever that index is in the SKU's
own partner row.

THE REFERENCE IS THE SAME POOL WITH THE MEMO OFF (`_cen` cleared before every take, so
every take walks the aisle afresh).  Not `_ranked_minlabor_impl`: that has been a thin
driver over `_MinLaborPool` since ticket 04, so it runs the memo too and would compare
the memo with itself (memory `placement-oracles-pin-agreement-not-truth`).

  1. placement sequences and the committed aisle state, member-position order included,
     are IDENTICAL with and without the memo -- on scenes with long SKU runs (so the memo
     hits) and with SKUs that are their own affinity partners (so the invalidation fires);
  2. non-vacuity: the memo served takes without recomputing, and the invalidation ran;
  3. sabotage: a memo that never invalidates diverges from the reference on some scene.

Run:  python -m pytest Tests/unit/test_minlabor_centroid_memo.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.placement import Assignment_Functions as af

from Tests.unit.test_minlabor_pool_equivalence import (
    _Unit, _aff, _fixture, _pool_as_impl, _run)

_REAL_TAKE = af._MinLaborPool.take


def _scene(seed: int):
    """`_fixture`'s bins and orders, with SKU RUNS (each drawn SKU repeated 2-6 times) and an
    affinity where SKUs 2 and 4 are their own partners (the diagonal is stored)."""
    rng = random.Random(4000 + seed)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_aisles=4, n_units=4)
    pool_orders = sorted({u.order.sku: u.order for u in units}.values(),
                         key=lambda o: o.sku)
    run_units = []
    for _ in range(6):
        o = rng.choice(pool_orders)
        run_units += [_Unit(o) for _ in range(rng.randint(2, 6))]
    skus = sorted(idx)
    aff, idx = _aff(skus, [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0),
                           (4, 5, 2.0), (2, 2, 3.5), (4, 4, 3.5)])
    return bins, run_units, aff, idx, fbi, fbs, qbs


def _unmemoised_take(self, unit):
    self._cen.clear()
    return _REAL_TAKE(self, unit)


def _both(monkeypatch, args, maximize):
    """(reference without the memo, the pool as it runs)."""
    with monkeypatch.context() as mp:
        mp.setattr(af._MinLaborPool, 'take', _unmemoised_take)
        ref = _run(_pool_as_impl, *args, maximize=maximize, waves=2)
    got = _run(_pool_as_impl, *args, maximize=maximize, waves=2)
    return ref, got


@pytest.mark.parametrize('maximize', [False, True])
@pytest.mark.parametrize('seed', range(10))
def test_the_memoised_centroid_places_what_the_unmemoised_pool_places(
        monkeypatch, maximize, seed):
    ref, got = _both(monkeypatch, _scene(seed), maximize)
    assert got == ref, f'seed {seed} maximize={maximize}: the memo moved a placement'


def test_the_memo_hits_and_the_invalidation_fires(monkeypatch):
    calls = {'centroid': 0, 'takes': 0, 'self_rows': 0}
    real_c = af._demand_weighted_partner_centroid

    def centroid(*a, **k):
        calls['centroid'] += 1
        return real_c(*a, **k)

    def take(self, unit):
        calls['takes'] += 1
        out = _REAL_TAKE(self, unit)
        if out[0] is not None and self._s2i.get(unit.order.sku) in self._row:
            calls['self_rows'] += 1            # this take's write dropped its entry
        return out

    monkeypatch.setattr(af, '_demand_weighted_partner_centroid', centroid)
    monkeypatch.setattr(af._MinLaborPool, 'take', take)
    for seed in range(10):
        for maximize in (False, True):
            _run(_pool_as_impl, *_scene(seed), maximize=maximize, waves=2)
    assert calls['centroid'] < calls['takes'], calls
    assert calls['self_rows'] > 0, calls


def test_a_memo_that_never_invalidates_is_caught(monkeypatch):
    """Sabotage: the self-partner rule removed -- every entry outlives its own write.
    The comparison above must see it on some scene."""
    class _NoPop(dict):
        def pop(self, k, d=None):
            return self.get(k, d)

    def stale_take(self, unit):
        # Swapped in before the call, so the entry THIS take creates is kept too.
        if not isinstance(self._cen, _NoPop):
            self._cen = _NoPop(self._cen)
        return _REAL_TAKE(self, unit)

    diverged = 0
    for seed in range(10):
        for maximize in (False, True):
            args = _scene(seed)
            with monkeypatch.context() as mp:
                mp.setattr(af._MinLaborPool, 'take', _unmemoised_take)
                ref = _run(_pool_as_impl, *args, maximize=maximize, waves=2)
            with monkeypatch.context() as mp:
                mp.setattr(af._MinLaborPool, 'take', stale_take)
                got = _run(_pool_as_impl, *args, maximize=maximize, waves=2)
            diverged += got != ref
    assert diverged, 'a never-invalidated memo matched the reference everywhere -- vacuous'
