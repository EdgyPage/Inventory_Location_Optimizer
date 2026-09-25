"""test_frozen_tier.py -- a pool opened over a TierSlice makes the byte-identical decisions
the same pool makes when built eagerly over the filtered candidate list.

The gain evaluator used to rebuild a tier's whole ranked structure at every one of its
T(T+1) x K opens per drain (54 s of a 72 s drain at campaign scale).  `FrozenTier` sorts the
tier once per drain and `TierSlice` overlays one exclusion set per open.  The claim is not
"equivalent": it is that every `order` + `take` sequence, every returned bin and score, and
every ledger commit are the SAME, including the tie-breaks the pools take from
first-appearance order -- which is why the fixtures below plant D ties across aisles and
across brackets, and exclude aisles' FIRST bins so the filtered first-appearance order
differs from the unfiltered one.

Three pool classes, each driven both ways over deep-copied state on seeded random
candidate lists and random exclusion sets, cart on and off, minimise and maximise.
Equality is EXACT (float `==`), the repo's rule for "same arithmetic in the same order".

Run:  python -m pytest Tests/unit/test_frozen_tier.py -q
"""
from __future__ import annotations

import copy
import random
from collections import defaultdict

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.kernel.cost_model import height_multiplier, sec_per_inch
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement import Assignment_Functions as af
from Warehouse.placement.frozen_tier import FrozenTier, TierSlice, _Cursor


# ── fixtures ─────────────────────────────────────────────────────────────────────────

class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class _Order:
    __slots__ = ('sku', 'handle_var', 'demand', 'expected_labor', 'labor_cost',
                 'expected_popularity')

    def __init__(self, sku, handle_var, f, q, labor, labor_cost):
        self.sku, self.handle_var = sku, handle_var
        self.demand, self.expected_labor = _Demand(f, q), labor
        self.labor_cost, self.expected_popularity = labor_cost, f * q


class _Unit:
    __slots__ = ('order',)

    def __init__(self, order):
        self.order = order


def _aff(skus, pairs):
    import numpy as np
    from scipy.sparse import csr_matrix
    idx = {s: i for i, s in enumerate(sorted(skus))}
    m = np.zeros((len(idx), len(idx)), dtype=np.float32)
    for a, b, lift in pairs:
        m[idx[a], idx[b]] = m[idx[b], idx[a]] = lift
    obj = type('A', (), {})()
    obj._sku_to_idx, obj._matrix = idx, csr_matrix(m)
    return obj, idx


def _wp():
    return WorkloadParams.from_pick_config(PickConfig(
        num_pickers=2, x_speed=1.0, y_speed=3.0, pick_intercept=1.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0))


#: Columns x heights: several bins share an exact (x, y) ACROSS aisles, so D ties across
#: aisles are real, and three heights straddle the default 96/240/inf brackets.
_COLS = (0.0, 12.0, 24.0, 36.0)
_HEIGHTS = (40.0, 150.0, 300.0)


def _bins(rng, n_aisles=5, per_aisle=6):
    bins = []
    for aid in range(1, n_aisles + 1):
        for _ in range(per_aisle):
            bins.append(_Bin(aid, rng.choice(_COLS), rng.choice(_HEIGHTS)))
    rng.shuffle(bins)                           # appearance order is a tie-break
    return bins


def _units(rng, n=14, n_skus=5):
    skus = list(range(1, n_skus + 1))
    orders = {s: _Order(s, 0.5 + 0.2 * s, 0.1 * s, 1.0 + s, 10.0 - s, 1.0 + 0.25 * s)
              for s in skus}
    orders[5].expected_labor = orders[4].expected_labor          # an LPT tie
    units = [_Unit(orders[rng.choice(skus)]) for _ in range(n)]
    return units, orders, skus


def _exclusion(rng, bins, *, all_of_first_aisle=False):
    """Random exclusions that always take some aisle's FIRST-appearing bin, so the
    filtered first-appearance order can differ from the unfiltered one."""
    excl = {id(b) for b in bins if rng.random() < 0.3}
    first_by_aisle = {}
    for b in bins:
        first_by_aisle.setdefault(b.location[0], b)
    excl.add(id(first_by_aisle[min(first_by_aisle)]))
    if all_of_first_aisle:
        excl.update(id(b) for b in bins if b.location[0] == bins[0].location[0])
    return excl


def _filtered(bins, excl):
    return [b for b in bins if id(b) not in excl]


def _tier(bins, wp):
    return af.freeze_tier(bins, wp)


def _drive(pool, units):
    """The eager and the sliced pool driven identically: the pool's own order, one take
    per unit, everything returned recorded."""
    out = []
    for u in pool.order(list(units)):
        b, score = pool.take(u)
        out.append((u.order.sku, None if b is None else (b.location[0], b.x_phys, b.y_phys),
                    score))
    return out


def _frozen_state(st):
    return {k: {a: (sorted(v) if isinstance(v, set) else
                    ({kk: list(vv) for kk, vv in v.items()} if isinstance(v, dict) else v))
                for a, v in d.items()} for k, d in st.items()}


# ── the slice's own arithmetic ───────────────────────────────────────────────────────

def test_a_cursor_yields_exactly_the_filtered_stable_order():
    rng = random.Random(3)
    bins = _bins(rng)
    wp = _wp()
    tier = _tier(bins, wp)
    excl = _exclusion(rng, bins)
    x_pace, y_pace = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)
    D_of = af._D_map(_filtered(bins, excl), x_pace, y_pace)
    sl = tier.slice(excl)
    for aid, cur in sl.aisles().items():
        eager = sorted([b for b in _filtered(bins, excl) if b.location[0] == aid],
                       key=lambda b: D_of[id(b)])
        got = []
        while cur:
            got.append(cur[0])
            cur.popleft()
        assert got == eager, aid
    # ... and the descending stable sort is NOT the reverse of the ascending one
    desc = sl.aisles(reverse=True)
    for aid, cur in desc.items():
        eager = sorted([b for b in _filtered(bins, excl) if b.location[0] == aid],
                       key=lambda b: D_of[id(b)], reverse=True)
        got = []
        while cur:
            got.append(cur[0])
            cur.popleft()
        assert got == eager, aid


def test_first_appearance_order_is_recomputed_under_exclusion():
    """Excluding an aisle's first bin can move the aisle behind another; the slice must
    order aisles (and brackets) by the FILTERED first appearance, and drop an aisle whose
    every bin is excluded."""
    a1, b1, a2, a3 = _Bin(1, 0, 40), _Bin(2, 0, 40), _Bin(1, 12, 40), _Bin(1, 0, 150)
    bins = [a1, b1, a2, a3]
    tier = _tier(bins, _wp())
    assert list(tier.slice(set()).aisles()) == [1, 2]
    assert list(tier.slice({id(a1)}).aisles()) == [2, 1]          # aisle 1 now first-appears at a2
    assert list(tier.slice({id(a1), id(a2), id(a3)}).aisles()) == [2]
    bk = tier.slice({id(a1)}).aisle_buckets()
    assert list(bk) == [2, 1]
    m_low = height_multiplier(_wp().height_brackets, 40.0)
    m_mid = height_multiplier(_wp().height_brackets, 150.0)
    # unfiltered, aisle 1's brackets first-appear low (a1) then mid (a3); with a1 gone it is
    # a2 (low) then a3 (mid) -- same here, but with a2 gone too it is mid ONLY
    assert list(bk[1]) == [m_low, m_mid]
    assert list(tier.slice({id(a1), id(a2)}).aisle_buckets()[1]) == [m_mid]


def test_a_cursor_end_reads_skip_excluded_bins_from_both_ends():
    bins = [_Bin(1, 0, 40), _Bin(1, 12, 40), _Bin(1, 24, 40), _Bin(1, 36, 40)]
    tier = _tier(bins, _wp())
    cur = tier.slice({id(bins[0]), id(bins[3])}).aisles()[1]
    assert isinstance(cur, _Cursor)
    assert cur[0] is bins[1] and cur[-1] is bins[2] and len(cur) == 2
    cur.pop()
    assert cur[-1] is bins[1] and bool(cur)
    cur.popleft()
    assert not cur and len(cur) == 0
    with pytest.raises(IndexError):
        cur[0]


def test_a_slice_is_falsy_only_when_every_candidate_is_excluded():
    bins = _bins(random.Random(1), n_aisles=2, per_aisle=2)
    tier = _tier(bins, _wp())
    assert tier.slice(set()) and tier.slice({id(bins[0])})
    assert not tier.slice({id(b) for b in bins})


def test_a_tier_frozen_under_other_paces_is_refused():
    bins = _bins(random.Random(2), n_aisles=2, per_aisle=2)
    other = WorkloadParams.from_pick_config(PickConfig(
        num_pickers=2, x_speed=2.0, y_speed=3.0, pick_intercept=1.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0))
    tier = _tier(bins, other)
    aff, _ = _aff([1, 2], [])
    st = defaultdict(set), defaultdict(set), defaultdict(float)
    with pytest.raises(ValueError, match='frozen under'):
        af._RankedAssignPool(tier.slice(set()), aff, _wp(), *st, {}, {}, {}, 1.0, True)


# ── the three pools, eager vs sliced, exact ──────────────────────────────────────────

def _state_ranked(aids):
    return {'aisle_sku_sets': defaultdict(set, {a: set() for a in aids}),
            'aisle_idx_sets': defaultdict(set, {a: set() for a in aids}),
            'aisle_demand_sum': defaultdict(float)}


@pytest.mark.parametrize('seed', [11, 12, 13, 14, 15, 16])
@pytest.mark.parametrize('arm', ['tmin', 'tmax', 'rank_popularity', 'rank_random'])
def test_ranked_assign_pool_is_identical_over_a_slice(seed, arm):
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus, [(1, 2, 5.0), (2, 3, 3.0), (1, 4, 2.5)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    wp = _wp()
    excl = _exclusion(rng, bins, all_of_first_aisle=(seed % 2 == 0))
    st_e, st_s = _state_ranked(range(1, 6)), _state_ranked(range(1, 6))
    for st in (st_e, st_s):
        st['aisle_sku_sets'][2].add(1)
        st['aisle_idx_sets'][2].add(idx[1])
    common = dict(affinity=aff, wp=wp, freq_by_idx=fbi, freq_by_sku=fbs, qty_by_sku=qbs,
                  beta=1.0, minimize=arm != 'tmax')
    extra_e, extra_s = {}, {}
    if arm == 'rank_popularity':
        for st, ex in ((st_e, extra_e), (st_s, extra_s)):
            ads = st['aisle_demand_sum']
            ex['aisle_key'] = lambda a, hd, _ads=ads: (_ads.get(a, 0.0), hd[a])
            ex['order_key'] = af._score_expected_popularity
    elif arm == 'rank_random':
        # the evaluator's stand-in draw: first live aisle in head order
        for ex in (extra_e, extra_s):
            ex['aisle_selector'] = lambda hd, hb: next(iter(hb))
    tier = _tier(bins, wp)
    eager = af._RankedAssignPool(_filtered(bins, excl), aisle_sku_sets=st_e['aisle_sku_sets'],
                                 aisle_idx_sets=st_e['aisle_idx_sets'],
                                 aisle_demand_sum=st_e['aisle_demand_sum'], **common, **extra_e)
    sliced = af._RankedAssignPool(tier.slice(excl), aisle_sku_sets=st_s['aisle_sku_sets'],
                                  aisle_idx_sets=st_s['aisle_idx_sets'],
                                  aisle_demand_sum=st_s['aisle_demand_sum'], **common, **extra_s)
    assert list(sliced._head_bin) == list(eager._head_bin), 'head order (a tie-break) differs'
    assert _drive(eager, units) == _drive(sliced, units)
    assert _frozen_state(st_e) == _frozen_state(st_s)
    assert eager._head_D == sliced._head_D


def _state_tb(aids):
    return {'aisle_sku_sets': defaultdict(set, {a: set() for a in aids}),
            'aisle_idx_sets': defaultdict(set, {a: set() for a in aids}),
            'aisle_demand_sum': defaultdict(float),
            'aisle_pick_load_sum': defaultdict(float, {a: 0.5 * a for a in aids}),
            'aisle_vol_sum': defaultdict(float, {a: 100.0 * a for a in aids})}


@pytest.mark.parametrize('seed', [21, 22, 23, 24, 25, 26])
@pytest.mark.parametrize('cart', [False, True])
def test_travel_balanced_pool_is_identical_over_a_slice(seed, cart):
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus, [])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    plp = {s: 0.7 * s for s in skus}
    vol = {s: 300.0 * s for s in skus}
    wp = _wp()
    excl = _exclusion(rng, bins, all_of_first_aisle=(seed % 2 == 0))
    st_e, st_s = _state_tb(range(1, 6)), _state_tb(range(1, 6))
    tier = _tier(bins, wp)
    pools = []
    for st, cands in ((st_e, _filtered(bins, excl)), (st_s, tier.slice(excl))):
        cart_arg = (st['aisle_vol_sum'], vol, 4.0, sum(fbs.values())) if cart else None
        pools.append(af._TravelBalancedPool(
            cands, aff, wp, st['aisle_sku_sets'], st['aisle_idx_sets'],
            st['aisle_demand_sum'], st['aisle_pick_load_sum'], plp, fbs, qbs, cart=cart_arg))
    eager, sliced = pools
    assert list(sliced._by_aisle) == list(eager._by_aisle)
    for aid in eager._by_aisle:
        assert list(sliced._by_aisle[aid]) == list(eager._by_aisle[aid]), aid
    assert _drive(eager, units) == _drive(sliced, units)
    assert eager._load == sliced._load
    assert _frozen_state(st_e) == _frozen_state(st_s)


def _state_ml(aids):
    return {'ss': defaultdict(set, {a: set() for a in aids}),
            'ii': defaultdict(set, {a: set() for a in aids}),
            'dd': defaultdict(float), 'mp': defaultdict(lambda: defaultdict(list))}


@pytest.mark.parametrize('seed', [31, 32, 33, 34, 35, 36])
@pytest.mark.parametrize('maximize', [False, True])
def test_min_labor_pool_is_identical_over_a_slice(seed, maximize):
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    wp = _wp()
    excl = _exclusion(rng, bins, all_of_first_aisle=(seed % 2 == 0))
    st_e, st_s = _state_ml(range(1, 6)), _state_ml(range(1, 6))
    for st in (st_e, st_s):                          # a placed partner, so the reward is live
        st['ss'][2].add(1)
        st['ii'][2].add(idx[1])
        st['mp'][2][idx[1]].append(12.0)
    tier = _tier(bins, wp)
    pools = []
    for st, cands in ((st_e, _filtered(bins, excl)), (st_s, tier.slice(excl))):
        pools.append(af._MinLaborPool(cands, aff, wp, st['ss'], st['ii'], st['dd'], st['mp'],
                                      fbi, fbs, qbs, 0.5, maximize=maximize))
    eager, sliced = pools
    assert list(sliced._by_aisle_brkt) == list(eager._by_aisle_brkt)
    for aid in eager._by_aisle_brkt:
        assert list(sliced._by_aisle_brkt[aid]) == list(eager._by_aisle_brkt[aid]), aid
    assert _drive(eager, units) == _drive(sliced, units)
    assert _frozen_state(st_e) == _frozen_state(st_s)


# -- the round-shared prologue template (copy-on-write) -------------------------------

def _open_family(family, cands, st, fixtures, maximize=False, cart=False):
    """One pool of `family` over `cands` (a filtered list, a slice, or a slice over a
    shared store) and the fresh state `st` -- the three constructions the tests above
    make, in one place so the template test drives all three."""
    aff, idx, fbs, qbs, fbi, plp, vol, wp, skus = fixtures
    if family == 'assign':
        return af._RankedAssignPool(cands, aff, wp, st['aisle_sku_sets'],
                                    st['aisle_idx_sets'], st['aisle_demand_sum'],
                                    fbi, fbs, qbs, 1.0, True)
    if family == 'travel':
        cart_arg = (st['aisle_vol_sum'], vol, 4.0, sum(fbs.values())) if cart else None
        return af._TravelBalancedPool(
            cands, aff, wp, st['aisle_sku_sets'], st['aisle_idx_sets'],
            st['aisle_demand_sum'], st['aisle_pick_load_sum'], plp, fbs, qbs,
            cart=cart_arg)
    return af._MinLaborPool(cands, aff, wp, st['ss'], st['ii'], st['dd'], st['mp'],
                            fbi, fbs, qbs, 0.5, maximize=maximize)


_FAMILY_STATE = {'assign': _state_ranked, 'travel': _state_tb, 'minlabor': _state_ml}


@pytest.mark.parametrize('seed', [41, 42, 43, 44, 45, 46])
@pytest.mark.parametrize('family', ['assign', 'travel', 'minlabor'])
def test_opens_over_one_shared_template_match_the_eager_build_and_each_other(seed, family):
    """A round of the gain greedy opens many pools over the SAME (tier, exclusion set), so
    the prologue is built once and every open reads it copy-on-write (`_CowBuckets`).

    Three claims, and the second is the one a single-open test cannot make:

      1. an open over the shared template decides exactly what the EAGER build decides;
      2. a SECOND open over the same template, after the first has consumed it to
         exhaustion, decides the same thing -- the first open's takes cloned cursors and
         left the template standing;
      3. the template really was shared: both opens saw one `_CowBuckets`/`_CowAisles`
         over one base dict, asserted by identity rather than assumed.
    """
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    plp = {s: 0.7 * s for s in skus}
    vol = {s: 300.0 * s for s in skus}
    wp = _wp()
    fixtures = (aff, idx, fbs, qbs, fbi, plp, vol, wp, skus)
    excl = _exclusion(rng, bins, all_of_first_aisle=(seed % 2 == 0))
    tier = _tier(bins, wp)
    mkstate = _FAMILY_STATE[family]

    st_e = mkstate(range(1, 6))
    eager = _open_family(family, _filtered(bins, excl), st_e, fixtures)
    want = _drive(eager, units)
    assert any(b is not None for _s, b, _c in want), (
        'the eager pool seated nothing -- the scene cannot show a shared template is right')

    store: dict = {}
    got = []
    bases = []
    for _ in range(2):
        st = mkstate(range(1, 6))
        pool = _open_family(family, tier.slice(excl, store), st, fixtures)
        bucket_map = getattr(pool, '_by_aisle', None)
        if bucket_map is None:
            bucket_map = pool._by_aisle_brkt
        bases.append(bucket_map._base)
        got.append(_drive(pool, units))
        assert _frozen_state(st) == _frozen_state(st_e), (
            'a shared-template open committed different aisle state than the eager build')

    # The store also holds DERIVED structures memoised over the same template
    # (`TierSlice.memo` -- `_TravelVec`'s head matrix, inbound-fullscale-perf S10), keyed
    # ('memo', kind); the claim here is about the bucket template, so those are counted
    # apart: at most one each, i.e. built once for the two opens and then reused.
    templates = [k for k in store if not (isinstance(k[0], tuple) and k[0][0] == 'memo')]
    memos = [k for k in store if isinstance(k[0], tuple) and k[0][0] == 'memo']
    assert len(templates) == 1, (
        f'{len(templates)} templates for one (tier, exclusion set): the store key is not '
        f'collapsing the opens a round makes, and nothing here is being reused')
    assert len(memos) == len({k[0] for k in memos}) <= 1, (
        f'derived memos {memos}: one kind was built more than once for one template')
    if family in ('travel', 'minlabor'):
        assert len(memos) == 1, (
            f'the {family} pool opened over a template but memoised no head matrix -- the '
            f'second open rebuilt it, and the sharing this test pins is untested for it')
    assert bases[0] is bases[1], (
        'the two opens did not share one base dict -- each rebuilt its own prologue and '
        'the copy-on-write path is untested')
    assert got[0] == want, 'the first shared-template open diverged from the eager build'
    assert got[1] == want, (
        'the SECOND open over the template diverged: the first open consumed the shared '
        'structure instead of cloning what it moved')


def test_a_cursor_clone_is_independent_and_leaves_its_source_alone():
    """The copy-on-write primitive, directly: a clone pops without moving its source, and
    a source that pops afterwards is unaffected by the clone."""
    rng = random.Random(5)
    bins = _bins(rng, n_aisles=1, per_aisle=8)
    tier = _tier(bins, _wp())
    aid = bins[0].location[0]
    cur = _Cursor(tier, tier.aisle_order(aid), set())
    seq = [cur[0]]
    clone = cur.clone()
    assert clone[0] is cur[0], 'a clone must start exactly where its source stands'
    clone.popleft()
    clone.popleft()
    assert cur[0] is seq[0], 'popping the clone moved the source'
    assert len(cur) == len(bins), 'popping the clone shortened the source'
    order = tier.aisle_order(aid)
    cur.popleft()
    assert cur[0] is tier.bins[order[1]], (
        'the source advanced by more than its own single pop')
    assert clone[0] is tier.bins[order[2]], (
        'the clone was dragged back by the source popping -- each side owns its own '
        'position and nothing else')
    assert len(cur) == len(bins) - 1 and len(clone) == len(bins) - 2


# -- the min-labor pool's aisle order: one sorted list, repaired one entry at a time ---

def _check_sel(pool, fq, where):
    """`_sel` is sorted by `(key, rank)` and holds exactly the live aisles, with the key
    each one's CURRENT `fq*bc`.  Every claim `take` makes about its prefix walk rests on
    this, and nothing else in the pool re-derives it."""
    sel = pool._sel
    assert sel == sorted(sel), f'{where}: the aisle order is not sorted'
    assert len(sel) == len(set(e[2] for e in sel)), f'{where}: an aisle appears twice'
    assert set(e[2] for e in sel) == set(pool._bc_by_aid), (
        f'{where}: the order and the cost book name different aisles')
    for key, _rank, aid in sel:
        want = fq * pool._bc_by_aid[aid]
        want = -want if pool._maximize else want
        assert key == want, (
            f'{where}: aisle {aid} is filed under {key!r} but its current cost is {want!r}')


@pytest.mark.parametrize('seed', [51, 52, 53, 54, 55, 56])
@pytest.mark.parametrize('maximize', [False, True])
def test_the_min_labor_aisle_order_stays_sorted_through_a_whole_drive(seed, maximize):
    """The pool walks a PREFIX of `_sel` until the affinity prune fires and then repairs the
    one entry its take moved (bisect out, insort back).  That is only correct while the list
    is sorted, and the walk itself never checks -- so this does, after every take.

    A heap was tried here on 2026-09-20 and reverted: reading a heap in order means
    destroying it, so a walk that does not prune early cost a full drain plus a full rebuild
    (28% slower at 400k SKUs on a `uni_` warehouse).  A list survives being read, which is
    the whole reason it is the right structure -- and this test is what makes that safe.
    """
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    st = _state_ml(range(1, 6))
    st['ss'][2].add(1)
    st['ii'][2].add(idx[1])
    st['mp'][2][idx[1]].append(12.0)
    tier = _tier(bins, _wp())
    excl = _exclusion(rng, bins, all_of_first_aisle=(seed % 2 == 0))
    pool = af._MinLaborPool(tier.slice(excl), aff, _wp(), st['ss'], st['ii'], st['dd'],
                            st['mp'], fbi, fbs, qbs, 0.5, maximize=maximize)
    seated = 0
    for u in pool.order(list(units)):
        b, _score = pool.take(u)
        seated += b is not None
        fq = fbs.get(u.order.sku, 0.0) * qbs.get(u.order.sku, 0.0)
        _check_sel(pool, fq, f'after {u.order.sku}')
    assert seated, 'the pool seated nothing, so no repair was ever exercised'


def test_the_walk_order_is_the_stable_argsort_of_the_live_costs():
    """RE-PINNED 2026-09-24 (inbound-fullscale-perf O3).  The walk used to follow a sorted
    list repaired one entry per take, and a list that stopped being sorted would have
    mis-priced silently -- so the pool checked and raised.  The vectorised pool re-derives the
    order (`argsort(key, kind='stable')` over the live rows) at every boundary and after every
    take that moves a cost; this pins that it IS that argsort after every take, and -- the
    non-vacuity half -- that the walk really reads it: scrambling the order inside a SKU run
    moves some take on these scenes."""
    import numpy as np

    def drive(seed, scramble=False):
        rng = random.Random(seed)
        bins = _bins(rng)
        units, orders, skus = _units(rng)
        aff, idx = _aff(skus, [])
        fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
        qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
        st = _state_ml(range(1, 6))
        pool = af._MinLaborPool(list(bins), aff, _wp(), st['ss'], st['ii'], st['dd'],
                                st['mp'], {}, fbs, qbs, 0.5)
        out = []
        for u in pool.order(list(units)):
            mx = pool._mx
            if (scramble and mx is not None and mx.order is not None
                    and u.order.sku == pool._last_sku and len(mx.order) > 1):
                mx.order = mx.order[::-1].copy()        # the sabotage, inside a run
            b, score = pool.take(u)
            out.append(None if b is None else (b.location[0], b.x_phys, b.y_phys, score))
            mx = pool._mx
            if not scramble and mx is not None and mx.bc is not None:
                live = np.flatnonzero(np.isfinite(mx.bc))
                want = live[np.argsort(mx.key[live], kind='stable')]
                assert (mx.order == want).all(), 'the walk order drifted from its costs'
        return out

    moved = sum(drive(seed) != drive(seed, scramble=True) for seed in range(6))
    assert moved >= 1, 'scrambling the walk order moved nothing: the order is not read'


def test_the_fixtures_actually_plant_ties_and_reorderings():
    """Non-vacuity: at least one seed excludes the first bin of the first-appearing aisle
    so the filtered aisle order differs, and the bin grid carries D ties across aisles."""
    rng = random.Random(11)
    bins = _bins(rng)
    excl = _exclusion(rng, bins)
    tier = _tier(bins, _wp())
    assert list(tier.slice(set()).aisles()) != list(tier.slice(excl).aisles())
    x_pace, y_pace = sec_per_inch(1.0), sec_per_inch(3.0)
    ds = {}
    for b in bins:
        ds.setdefault(x_pace * b.x_phys + y_pace * b.y_phys, set()).add(b.location[0])
    assert any(len(a) > 1 for a in ds.values()), 'no D tie across aisles -- the tie-break is idle'

# -- the min-labor deltas through the inverse equal the per-aisle fold ------------------

def _ml_owner(idx):
    """The same memberships `_state_ml` seeds, written through the ledger so the inverse and
    `n_placed` are maintained; aisle 3 additionally holds partner 99 from the start."""
    from Warehouse.inventory.aisle_ledger import AisleLedger
    led = AisleLedger()
    for a in range(1, 6):
        led.sku_sets[a]
        led.idx_sets[a]
    led.add_sku(2, 1, idx[1], demand=0.0)
    led.add_bin(2, idx[1], 12.0)
    led.add_sku(3, 99, idx[99], demand=0.0)
    led.add_bin(3, idx[99], 30.0)
    return led


def _ml_plain_like(led):
    """Plain dicts with the owner's exact contents: the fallback (no inverse) path."""
    st = _state_ml(range(1, 6))
    for a, ss in led.sku_sets.items():
        st['ss'][a] = set(ss)
    for a, ii in led.idx_sets.items():
        st['ii'][a] = set(ii)
    for a, mp in led.member_pos.items():
        for k, xs in mp.items():
            st['mp'][a][k] = list(xs)
    return st


@pytest.mark.parametrize('seed', [41, 42, 43, 44])
@pytest.mark.parametrize('maximize', [False, True])
def test_min_labor_deltas_through_the_inverse_equal_the_per_aisle_fold(seed, maximize):
    """Three books, one answer: plain dicts (the per-aisle fold), the owner ledger (the fold
    through `partner_aisles`), and a copy-on-write view over the owner with one aisle
    overridden before the run (the recompute path) -- eager AND sliced, exact."""
    from Inbound.gain_cow import AISLE_VIEWS
    rng = random.Random(seed)
    bins = _bins(rng)
    units, orders, skus = _units(rng)
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0),
                                  (2, 99, 1.5)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    wp = _wp()
    excl = _exclusion(rng, bins)
    tier = _tier(bins, wp)
    results = {}
    for kind in ('plain', 'owner', 'cow'):
        for shape, make_cands in (('eager', lambda: _filtered(bins, excl)),
                                  ('sliced', lambda: tier.slice(excl))):
            led = _ml_owner(idx)
            if kind == 'plain':
                st = _ml_plain_like(led)
                books = (st['ss'], st['ii'], st['dd'], st['mp'])
            elif kind == 'owner':
                books = (led.sku_sets, led.idx_sets, led.demand_sum, led.member_pos)
            else:
                views = {n: AISLE_VIEWS[n](d) for n, d in (
                    ('aisle_sku_sets', led.sku_sets), ('aisle_idx_sets', led.idx_sets),
                    ('aisle_demand_sum', led.demand_sum),
                    ('aisle_member_pos', led.member_pos))}
                # override aisle 4 virtually with partner 99 BEFORE the run: the fold must
                # read the view's set for that aisle and the inverse for every other
                views['aisle_idx_sets'][4].add(idx[99])
                views['aisle_sku_sets'][4].add(99)
                views['aisle_member_pos'][4][idx[99]].append(5.0)
                books = (views['aisle_sku_sets'], views['aisle_idx_sets'],
                         views['aisle_demand_sum'], views['aisle_member_pos'])
            pool = af._MinLaborPool(make_cands(), aff, wp, *books, fbi, fbs, qbs, 0.5,
                                    maximize=maximize)
            if kind == 'cow':
                assert pool._partner_deltas([(idx[99], 1.0)]) is not None
            if kind == 'plain':
                assert pool._partner_deltas([(idx[99], 1.0)]) is None
            results[(kind, shape)] = _drive(pool, units)
    assert (results[('plain', 'eager')] == results[('plain', 'sliced')]
            == results[('owner', 'eager')] == results[('owner', 'sliced')])
    assert results[('cow', 'eager')] == results[('cow', 'sliced')]


def test_the_override_actually_moves_a_choice_on_some_seed():
    """Non-vacuity for the copy-on-write branch: across the seeds above, the virtual partner
    in aisle 4 changes at least one placement versus the plain books."""
    from Inbound.gain_cow import AISLE_VIEWS
    moved = False
    for seed in (41, 42, 43, 44, 45, 46):
        rng = random.Random(seed)
        bins = _bins(rng)
        units, orders, skus = _units(rng)
        aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0),
                                      (2, 99, 1.5)])
        fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
        qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
        fbi = {idx[s]: fbs[s] for s in skus}
        fbi[idx[99]] = 0.8
        wp = _wp()
        led = _ml_owner(idx)
        st = _ml_plain_like(led)
        plain = af._MinLaborPool(list(bins), aff, wp, st['ss'], st['ii'], st['dd'], st['mp'],
                                 fbi, fbs, qbs, 0.5)
        views = {n: AISLE_VIEWS[n](d) for n, d in (
            ('aisle_sku_sets', led.sku_sets), ('aisle_idx_sets', led.idx_sets),
            ('aisle_demand_sum', led.demand_sum), ('aisle_member_pos', led.member_pos))}
        views['aisle_idx_sets'][4].add(idx[99])
        views['aisle_sku_sets'][4].add(99)
        views['aisle_member_pos'][4][idx[99]].append(5.0)
        cow = af._MinLaborPool(list(bins), aff, wp, views['aisle_sku_sets'],
                               views['aisle_idx_sets'], views['aisle_demand_sum'],
                               views['aisle_member_pos'], fbi, fbs, qbs, 0.5)
        if _drive(plain, units) != _drive(cow, units):
            moved = True
            break
    assert moved, 'the virtual partner never changed a placement -- the recompute path is idle'


def test_the_inverse_fold_is_the_per_aisle_loop_term_for_term():
    """Non-vacuity for `_partner_deltas`: hand-built memberships, hand-summed in row order."""
    from Warehouse.inventory.aisle_ledger import AisleLedger
    led = AisleLedger()
    led.add_bin(1, 10, 0.0)
    led.add_bin(1, 11, 0.0)
    led.add_bin(2, 11, 0.0)
    led.add_bin(3, 12, 0.0)
    aff, _ = _aff([1], [])
    pool = af._MinLaborPool([_Bin(1, 0, 40)], aff, _wp(), led.sku_sets, led.idx_sets,
                            led.demand_sum, led.member_pos, {}, {}, {}, 0.5)
    row_items = [(10, 0.1), (11, 0.2), (12, 0.4), (13, 0.8)]
    got = pool._partner_deltas(row_items)
    assert got == {1: 0.0 + 0.1 + 0.2, 2: 0.0 + 0.2, 3: 0.0 + 0.4}
    assert pool._partner_deltas([(13, 1.0)]) == {}
