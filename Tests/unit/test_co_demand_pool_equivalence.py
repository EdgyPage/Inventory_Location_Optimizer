"""test_co_demand_pool_equivalence.py — the co-demand pool decides what the wave decided.

`_CoDemandPool` is the first ported policy that scores a unit against what has ALREADY been
placed: the aisle is chosen by demand-weighted lift to its current members, and the bin by
distance to those members' column centroid. Both read state that `take` itself mutates, so
"the pool holds the state" and "the policy is path-dependent" are the same sentence here —
which is exactly why it needs its own equivalence file rather than riding on the ranked-assign
one.

WHAT IS CLAIMED: driven in the SAME order, the pool makes bit-identical decisions and leaves
bit-identical manager state. Every test drives it through `pool.order(units)`, the sort the
impl performs internally. A reordered drain will legitimately produce a different answer;
that is Phase 2's business, not this file's.

The float assertions are EXACT. `aisle_demand_sum` is a running sum, and `aisle_member_pos`
records the column of every placement in the order it happened — `_demand_weighted_partner_
centroid` then sums those columns left to right, so the accumulation order is not merely
observable, it feeds straight back into the next bin choice.

Run:  python -m pytest Tests/unit/test_co_demand_pool_equivalence.py -q
"""
from __future__ import annotations

import random
from collections import defaultdict

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement import Assignment_Functions as af

scipy_sparse = pytest.importorskip('scipy.sparse',
                                   reason='co-demand needs a real CSR lift matrix')


# ── fixtures ──────────────────────────────────────────────────────────────────────
class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y=0.0):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class _Order:
    __slots__ = ('sku', 'labor_cost', 'demand')

    def __init__(self, sku, labor_cost, f, q):
        self.sku, self.labor_cost, self.demand = sku, labor_cost, _Demand(f, q)


class _Unit:
    __slots__ = ('order',)

    def __init__(self, order):
        self.order = order


def _aff(skus, pairs):
    """A real symmetric CSR — `_affinity_row` indexes `indptr`/`indices`/`data`, so a dict
    affinity is not accepted, and a NULL matrix short-circuits every lift to zero and would
    make this whole file an agreement about nothing."""
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
        num_pickers=2, x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0))


def _state():
    return {'ss': defaultdict(set), 'ii': defaultdict(set),
            'dd': defaultdict(float),
            'mp': defaultdict(lambda: defaultdict(list))}


def _seed_partner(st, aid, sku_idx, x):
    """A partner already on the floor, so lift and the centroid are both live from unit one.
    Without this the cold-start branch runs for every unit and the interesting half of the
    policy is never exercised."""
    st['ss'][aid].add(99)
    st['ii'][aid].add(sku_idx)
    st['mp'][aid][sku_idx].append(x)
    st['dd'][aid] = 0.25


def _fixture(rng, n_aisles=3, cols=(0.0, 5.0, 10.0, 15.0), n_units=8):
    """Multi-aisle so the lift argmax genuinely chooses; multi-column so the centroid
    genuinely chooses; more units than bins so the exhaustion path runs."""
    bins = [_Bin(a, x, 10.0 * a) for a in range(1, n_aisles + 1) for x in cols]
    rng.shuffle(bins)
    skus = [1, 2, 3, 4]
    orders = {s: _Order(s, 1.0 + 0.3 * s, 0.1 * s, 1.0 + s) for s in skus}
    units = [_Unit(orders[rng.choice(skus)]) for _ in range(n_units)]
    aff, idx = _aff(skus + [99], [(1, 99, 5.0), (2, 99, 3.0), (1, 2, 2.0), (3, 4, 4.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 1.0
    return bins, units, aff, idx, fbi, fbs, qbs


def _pool_as_impl(units, candidates_fn, affinity, wp, ss, ii, dd, mp,
                  fbi, fbs, qbs, beta, compact):
    """The pool, driven through its own `order` — so the CHOICE is what is compared."""
    if not units:
        return []
    pool = af._CoDemandPool(list(candidates_fn(units[0])), affinity, wp, ss, ii, dd, mp,
                            fbi, fbs, qbs, beta, compact)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


def _surface(st):
    return ({a: set(v) for a, v in st['ss'].items()},
            {a: set(v) for a, v in st['ii'].items()},
            dict(st['dd']),
            {a: {i: list(xs) for i, xs in d.items()} for a, d in st['mp'].items()})


def _key(res):
    return [(id(u), None if b is None else (b.location[0], b.x_phys, b.y_phys))
            for u, b in res]


def _run(impl, bins, units, aff, idx, fbi, fbs, qbs, compact, waves=1):
    st = _state()
    _seed_partner(st, 1, idx[99], 15.0)
    remaining = list(bins)
    seq = []
    for _w in range(waves):
        res = impl(list(units), lambda _u: list(remaining), aff, _wp(),
                   st['ss'], st['ii'], st['dd'], st['mp'], fbi, fbs, qbs, 1.0, compact)
        seq.append(_key(res))
        taken = {id(b) for _u, b in res if b is not None}
        remaining = [b for b in remaining if id(b) not in taken]
    return seq, _surface(st)


@pytest.mark.parametrize('compact', [True, False])
@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4, 5])
def test_pool_matches_the_impl_exactly(compact, seed):
    rng = random.Random(500 + seed)
    args = _fixture(rng)
    ref_seq, ref_state = _run(af._co_demand_ranked_impl, *args, compact=compact)
    got_seq, got_state = _run(_pool_as_impl, *args, compact=compact)
    assert got_seq == ref_seq, 'placement sequences diverged'
    assert got_state == ref_state          # exact: member positions AND the demand sums


@pytest.mark.parametrize('compact', [True, False])
def test_two_consecutive_waves_share_state(compact):
    """`all_idx` is per-pool and the member positions accumulate across waves, so the second
    wave both ranks and compacts against what the first one placed."""
    rng = random.Random(77)
    args = _fixture(rng, n_units=5)
    ref_seq, ref_state = _run(af._co_demand_ranked_impl, *args, compact=compact, waves=2)
    got_seq, got_state = _run(_pool_as_impl, *args, compact=compact, waves=2)
    assert got_seq == ref_seq and got_state == ref_state
    assert len(ref_seq) == 2 and ref_seq[0] != ref_seq[1]


@pytest.mark.parametrize('compact', [True, False])
def test_the_fixture_exercises_what_it_claims(compact):
    """Non-vacuity: affinity has to matter, more than one aisle has to be used, the
    exhaustion path has to run, and a repeat SKU has to hit the commit no-op."""
    rng = random.Random(3)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_units=len([]) + 20)
    seq, (ss, ii, dd, mp) = _run(af._co_demand_ranked_impl, bins, units, aff, idx,
                                 fbi, fbs, qbs, compact=compact)
    placed = [p for p in seq[0] if p[1] is not None]
    assert len(placed) == len(bins), 'not every bin was consumed'
    assert any(p[1] is None for p in seq[0]), 'the exhaustion path never ran'
    assert len({p[1][0] for p in placed}) > 1, 'only one aisle was used'
    assert any(len(xs) > 1 for d in mp.values() for xs in d.values()), \
        'no SKU landed twice in one aisle — the centroid never moved'
    assert sum(len(v) for v in ii.values()) > 1, 'no index was committed'


def test_compact_and_expand_actually_disagree():
    """If the two arms placed the same way the equality above would hold for the wrong
    reason. They must not."""
    rng = random.Random(9)
    args = _fixture(rng, n_units=6)
    a, _ = _run(_pool_as_impl, *args, compact=True)
    b, _ = _run(_pool_as_impl, *args, compact=False)
    assert a != b


def test_score_is_the_paced_distance_from_the_partner_centroid():
    """The reported score must be the objective the bin choice actually optimised.

    `take` mutates `aisle_member_pos`, so the centroid has to be recomputed from a snapshot
    taken BEFORE the call — comparing against the post-placement centroid would silently
    accept a pool that scored against its own new position."""
    import copy
    rng = random.Random(21)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_units=4)
    st = _state()
    _seed_partner(st, 1, idx[99], 15.0)
    wp = _wp()
    pool = af._CoDemandPool(list(bins), aff, wp, st['ss'], st['ii'], st['dd'], st['mp'],
                            fbi, fbs, qbs, 1.0, True)
    x_pace = af.sec_per_inch(wp.x_speed)
    n_scored = n_cold = 0
    for u in pool.order(units):
        before = {a: {i: list(xs) for i, xs in d.items()} for a, d in st['mp'].items()}
        b, score = pool.take(u)
        assert b is not None
        aid = b.location[0]
        _m, cx = af._demand_weighted_partner_centroid(
            aff, u.order.sku, before.get(aid, {}), fbi)
        if cx is None:
            assert score is None, 'no centroid, so there is no distance to report'
            n_cold += 1
        else:
            n_scored += 1
            assert score == x_pace * abs(b.x_phys - cx)      # exact, not approx
    assert n_scored > 0, 'no unit ever had a partner — the score path never ran'
    assert n_cold > 0, 'the cold-start branch never ran — the None path is untested'


def test_an_exhausted_pool_reports_nothing():
    rng = random.Random(31)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_aisles=1, cols=(0.0,), n_units=3)
    st = _state()
    pool = af._CoDemandPool(list(bins), aff, _wp(), st['ss'], st['ii'], st['dd'], st['mp'],
                            fbi, fbs, qbs, 1.0, True)
    assert pool.take(units[0])[0] is not None
    assert pool.take(units[1]) == (None, None)


def _two_aisle_tie(idx_partner, aff, fbi):
    """Two aisles with IDENTICAL members — so the lift masses tie and the `d0` tie-break
    decides — and a head bin in one of them that moves a long way after one pop."""
    bins = [_Bin(1, 0.0, 0.0), _Bin(1, 100.0, 0.0), _Bin(2, 10.0, 0.0), _Bin(2, 12.0, 0.0)]
    st = _state()
    for aid in (1, 2):
        _seed_partner(st, aid, idx_partner, 10.0)
    return bins, st


def test_a_stale_winner_key_would_send_the_next_unit_to_the_wrong_aisle():
    """The winner refresh is load-bearing, and the random fixtures above do not reach it.

    Same-SKU units are adjacent, so the cache is not rebuilt between them; only the explicit
    refresh after each placement keeps the winner's entry true. Two aisles hold identical
    members here, so their lift masses tie exactly and the `d0` tie-break — the aisle's
    current head bin — is what decides. Aisle 1's head jumps from column 0 to column 100 the
    moment its first bin is taken, so a pool that skipped the refresh keeps handing units to
    aisle 1 on a `d0` that no longer exists.

    This is the same failure the cluster_map cache was fixed for in b91cf38, where a stale
    cached value moved one placement in 293,887. Here it is made loud on purpose.
    """
    aff, idx = _aff([1, 99], [(1, 99, 5.0)])
    fbi = {idx[99]: 1.0}
    fbs = qbs = {1: 1.0}
    order = _Order(1, 1.0, 1.0, 1.0)
    units = [_Unit(order) for _ in range(3)]          # one SKU run: no cache rebuild
    bins, st = _two_aisle_tie(idx[99], aff, fbi)

    pool = af._CoDemandPool(list(bins), aff, _wp(), st['ss'], st['ii'], st['dd'], st['mp'],
                            fbi, fbs, qbs, 1.0, True)
    got = [pool.take(u)[0] for u in units]
    aisles = [b.location[0] for b in got]
    assert aisles[0] == 1, f'expected aisle 1 to win on the lower head, got {aisles}'
    assert aisles[1] == 2, (
        f'aisle 1 won twice ({aisles}) — its head moved from column 0 to column 100 after '
        f'the first pop, so the second unit was placed on a stale d0')

    # And the impl this pool replaced agrees, unit for unit.
    bins2, st2 = _two_aisle_tie(idx[99], aff, fbi)
    ref = af._co_demand_ranked_impl(list(units), lambda _u: list(bins2), aff, _wp(),
                                    st2['ss'], st2['ii'], st2['dd'], st2['mp'],
                                    fbi, fbs, qbs, 1.0, compact=True)
    assert [b.location[0] for _u, b in ref] == aisles
