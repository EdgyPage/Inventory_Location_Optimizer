"""The copy-on-write aisle views are the eager copies, for less money.

`_Evaluator._make_pool` is the inbound package's hottest line, and the reason is the TIER loop
rather than anything about yard depth: measured on the real driver, 12.59 pool opens per
`place_load` against a mean of 2.8 candidate trailers.  The eager copy walked every live aisle
(46) to serve a pool that touches 1.22 of them -- 9.3 million set-element copies in twenty
batches on one leaf.

`AISLE_VIEWS` replaces that with per-aisle laziness.  This file is the proof that it changed
nothing but the bill, and it has four parts, because three of them alone would each pass for the
wrong reason:

  EQUIVALENCE   a real inbound scenario digests identically under the view and the copy.
  PURITY        the live manager dicts are untouched -- the invariant the copy existed for.
  SAVING        the view actually copies less.  Without this the whole file passes if someone
                quietly points `AISLE_VIEWS` back at `AISLE_COPIERS`.
  SABOTAGE      a view that hands out the LIVE container fails the purity test.  Without this,
                purity passing proves only that this scenario happened not to mutate.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, os.path.join(_REPO, 'Tests', 'calltree'), os.path.join(_REPO, 'Tests', 'bench')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import Inbound.gain as gain

# A small scenario: the point is equivalence, not scale, and the shapes are all exercised at
# 200 SKUs.  The measured 117x saving came from the bigger runs recorded in
# `.scratch/inbound-performance/issues/07-*`; this file only has to prove it is not zero.
_SCENARIO = dict(n_skus=200, bins_per_aisle=100, coverage=10.0, safety=2.0, seed=11,
                 put_timing=True, recv_crew=2, inbound=True, trailer_type='53',
                 dock_doors=4, yard_policy='gain_forecast', dock_policy='gain_forecast')
_BATCHES, _WHISTLE = 4, 20.0

#: One arm per VIEW SHAPE, not one per arm -- `_CowSets` + `_CowFloats` (labor), the
#: dict-of-dict-of-list (`minlabor`), and the `values()` materialization path (`random`).
_ARMS = ('uni_rank_labor_norsl', 'uni_rank_minlabor_norsl', 'uni_rank_random_norsl')


# THE TABLES ARE READ THROUGH THEIR MODULE, and this file is the reason.  It REBINDS
# `AISLE_VIEWS` to sabotage the views; a `from ... import AISLE_VIEWS` anywhere in the
# chain would bind the name at import and never see the rebinding, so the sabotage would
# stop biting and this file would go on passing.  `Inbound.gain` does not re-export them.
from Inbound import gain_cow                                        # noqa: E402


def _digest(strategy, views=None):
    """End state of one inbound run: counters, every occupied bin, and the LIVE aisle dicts."""
    import hashlib
    import calltree_scenarios as cs

    saved = gain_cow.AISLE_VIEWS
    if views is not None:
        gain_cow.AISLE_VIEWS = views
    try:
        assets = cs.build_assets(strategy=strategy, **_SCENARIO)
        res = cs.run_meso(assets, n_batches=_BATCHES, seed=11, recv_deadline=_WHISTLE)
    finally:
        gain_cow.AISLE_VIEWS = saved

    h = hashlib.sha256()
    h.update(repr((res.picks, res.placements, res.reorders, res.skipped)).encode())
    for b in sorted(assets.warehouse.bins, key=lambda x: (x.location[0], x.x_phys, x.y_phys)):
        if getattr(b, 'quantity', None):
            sku = getattr(getattr(b, 'order', None), 'sku', None)
            h.update(f'{b.location[0]}|{b.x_phys}|{b.y_phys}|{sku}|{b.quantity};'.encode())
    mgr = assets.mgr
    for name in ('_aisle_sku_sets', '_aisle_idx_sets', '_aisle_demand_sum',
                 '_aisle_pick_load_sum'):
        d = getattr(mgr, name, None) or {}
        for k in sorted(d):
            v = d[k]
            val = sorted(v) if isinstance(v, set) else round(float(v), 9)
            h.update(f'{name}|{k}|{val};'.encode())
    return h.hexdigest(), res


def test_the_two_tables_cover_each_other():
    """A name with a copier and no view falls silently back to the 40x path."""
    assert set(gain_cow.AISLE_VIEWS) == set(gain_cow.AISLE_COPIERS), \
        'AISLE_VIEWS and AISLE_COPIERS name different dicts — every aisle dict needs both'
    assert len(gain_cow.AISLE_VIEWS) >= 6


def test_each_view_leaves_its_live_dict_untouched():
    """PURITY, at the unit level: a view may be written through and the source must not move."""
    live_sets = {1: {10, 11}, 2: {12}}
    v = gain_cow._CowSets(live_sets)
    v[1].add(99)
    assert live_sets == {1: {10, 11}, 2: {12}}, 'a virtual placement advanced the live sets'
    assert 99 in v[1] and 10 in v[1]

    live_floats = {1: 2.5}
    f = gain_cow._CowFloats(live_floats)
    f[1] += 1.5
    assert live_floats == {1: 2.5}, 'a virtual placement advanced the live floats'
    assert f[1] == 4.0 and f.get(9, 0.0) == 0.0

    live_lists = {1: {7: [1.0, 2.0]}}
    L = gain_cow._CowListsByKey(live_lists)
    L[1][7].append(3.0)
    assert live_lists == {1: {7: [1.0, 2.0]}}, 'a virtual placement advanced the live lists'
    assert L[1][7] == [1.0, 2.0, 3.0]


def test_a_view_that_shares_the_live_container_is_caught():
    """SABOTAGE. Purity passing above proves nothing unless a broken view would fail it.

    This is the `_copy_of_lists_by_key` docstring's own failure mode: hand the pool the live
    inner list, get no error and no symptom, and price every later placement in the run against
    a warehouse that never happened.
    """
    class _Sharing(dict):
        def __init__(self, live):
            super().__init__()
            self._live = live

        def __getitem__(self, k):
            return self._live.setdefault(k, set())      # THE BUG: hands out the live set

    live = {1: {10}}
    bad = _Sharing(live)
    bad[1].add(99)
    assert live == {1: {10, 99}}, \
        'the sabotage did not corrupt the live dict, so the purity test above is vacuous'


def test_the_view_copies_strictly_less_than_the_copy():
    """SAVING. Without this the file passes if AISLE_VIEWS is pointed back at AISLE_COPIERS."""
    import calltree_scenarios as cs

    tally = {'cow': 0, 'eager': 0}

    def _run(mode):
        saved_views = gain_cow.AISLE_VIEWS
        saved_get = gain_cow._CowSets.__getitem__
        if mode == 'eager':
            src = gain_cow._copy_of_sets

            def counted(d):
                tally['eager'] += sum(len(v) for v in d.values())
                return src(d)
            gain_cow.AISLE_VIEWS = dict(saved_views, aisle_sku_sets=counted,
                                    aisle_idx_sets=counted)
        else:
            def counted_get(self, k):
                if k not in self._over:
                    s = self._live.get(k)
                    tally['cow'] += len(s) if s is not None else 0
                return saved_get(self, k)
            gain_cow._CowSets.__getitem__ = counted_get
        try:
            a = cs.build_assets(strategy='uni_rank_labor_norsl', **_SCENARIO)
            cs.run_meso(a, n_batches=_BATCHES, seed=11, recv_deadline=_WHISTLE)
        finally:
            gain_cow.AISLE_VIEWS = saved_views
            gain_cow._CowSets.__getitem__ = saved_get

    _run('eager')
    _run('cow')

    assert tally['eager'] > 0, 'the eager path copied nothing — the scenario opened no pool'
    assert tally['cow'] < tally['eager'], (
        f'the view copied {tally["cow"]} set elements against the copy\'s {tally["eager"]} — '
        f'no saving, so AISLE_VIEWS is not lazy')


def test_every_view_shape_reproduces_the_eager_copy_exactly():
    """EQUIVALENCE, one arm per view shape, against the frozen eager table."""
    eager = dict(gain_cow.AISLE_COPIERS)
    seen = {}
    for arm in _ARMS:
        cow_digest, cow_res = _digest(arm)
        eager_digest, eager_res = _digest(arm, views=eager)
        assert cow_digest == eager_digest, (
            f'{arm}: the copy-on-write view diverged from the eager copy\n'
            f'  cow   {cow_res.picks} picks / {cow_res.placements} placements {cow_digest}\n'
            f'  eager {eager_res.picks} picks / {eager_res.placements} placements {eager_digest}')
        assert cow_res.placements > 0, f'{arm} placed nothing — the comparison is vacuous'
        seen[arm] = cow_digest

    # NON-VACUITY: if every arm digested the same, the digest is not sensitive to placement and
    # the equality above would hold for any implementation at all.
    assert len(set(seen.values())) == len(seen), (
        f'the three arms produced identical end states, so this digest cannot tell arms apart '
        f'and proves nothing about the views: {seen}')
