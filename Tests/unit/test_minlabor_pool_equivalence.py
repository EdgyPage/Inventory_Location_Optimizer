"""test_minlabor_pool_equivalence.py — the minlabor pool decides what the impl decided.

`_MinLaborPool` is `_ranked_minlabor_impl` split into open / order / take, and it serves both
`rank_minlabor` and `rank_maxlabor` (one body, every extremum flipped by `maximize`).

WHY THIS FILE EXISTS when the arm is already gated. `Tests/calltree/test_rank_cache_
equivalence.py` holds a frozen oracle for this impl, but it drives it through
`calltree_scenarios.run_meso` at 8,000 SKUs over ten batches, twice per test. That is a fine
end-to-end backstop and useless as an inner-loop check while porting: it cannot say which of
the four cached quantities went wrong, and it is far too slow to sabotage against. This file
is the unit-level counterpart, and the calltree gate stays where it is.

WHAT IS CLAIMED: driven in the SAME order, the pool makes bit-identical decisions and leaves
bit-identical manager state. Every test drives it through `pool.order(units)`, the LPT sort
the impl performs internally.

`aisle_member_pos` is compared exactly, including list ORDER, because
`_demand_weighted_partner_centroid` sums those columns left to right and the resulting `cx`
picks the bin — it is not a tie-break.

Run:  python -m pytest Tests/unit/test_minlabor_pool_equivalence.py -q
"""
from __future__ import annotations

import random
from collections import defaultdict

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.picking.Pick import PickConfig
from Warehouse.placement import Assignment_Functions as af

pytest.importorskip('scipy.sparse', reason='minlabor indexes a real CSR lift matrix')


# ── fixtures ──────────────────────────────────────────────────────────────────────
class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class _Order:
    __slots__ = ('sku', 'handle_var', 'demand', 'expected_labor')

    def __init__(self, sku, handle_var, f, q, labor):
        self.sku, self.handle_var = sku, handle_var
        self.demand, self.expected_labor = _Demand(f, q), labor


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
    """Real brackets, so more than one height multiplier is present per aisle and
    `_aisle_best_cost` loops more than once.

    `y_speed` is 3.0, not the 0.5 used elsewhere in the suite, and that is load-bearing.
    Three height brackets need y values spanning 96 and 240 inches; at 0.5 ft/s the vertical
    pace is 1/6 s/inch, so a 260-inch spread costs ~43 s and swamps everything else. The
    bracket choice is then decided by height alone, the centroid term
    `x_pace*|x - cx|` can never change it, and dropping that term from the bin loop becomes
    undetectable. At 3.0 ft/s the three brackets land within a second of each other and the
    compaction lever is live — which is what makes this fixture able to fail."""
    return WorkloadParams.from_pick_config(PickConfig(
        num_pickers=2, x_speed=1.0, y_speed=3.0, pick_intercept=1.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0))


def _state():
    return {'ss': defaultdict(set), 'ii': defaultdict(set), 'dd': defaultdict(float),
            'mp': defaultdict(lambda: defaultdict(list))}


#: y values straddling the default 96/240/inf brackets, each paired with a DIFFERENT column.
#: The pairing is the point: a bracket's deque end is its min-D bin, so if every bracket held
#: bins at the same columns every end would sit at the same x, the centroid term
#: `x_pace*|x - cx|` would be one constant added to all three, and it could never change the
#: argmin. Dropping the centroid entirely would then be undetectable — which is exactly what
#: the first version of this fixture failed to catch.
_BRACKET_COLS = ((40.0, 40.0), (0.0, 150.0), (20.0, 300.0))


def _fixture(rng, n_aisles=3, n_units=10):
    """One aisle is short, so it exhausts mid-run and the `bc_by_aid.pop` path fires."""
    bins = []
    for a in range(1, n_aisles + 1):
        pairs = _BRACKET_COLS if a != n_aisles else _BRACKET_COLS[:1]   # last aisle is thin
        for x, y in pairs:
            for k in range(3 if a != n_aisles else 1):
                bins.append(_Bin(a, x + 3.0 * k, y))
    rng.shuffle(bins)
    skus = [1, 2, 3, 4, 5]
    orders = {s: _Order(s, 0.5 + 0.2 * s, 0.1 * s, 1.0 + s, 10.0 - s) for s in skus}
    # Two SKUs share expected_labor exactly, so the stable sort's tie behaviour is live.
    orders[5].expected_labor = orders[4].expected_labor
    units = [_Unit(orders[rng.choice(skus)]) for _ in range(n_units)]
    aff, idx = _aff(skus + [99], [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5),
                                  (3, 4, 3.0), (4, 5, 2.0)])
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    fbi[idx[99]] = 0.8
    return bins, units, aff, idx, fbi, fbs, qbs


def _seed(st, aid, idx, sku, x):
    st['ss'][aid].add(sku)
    st['ii'][aid].add(idx)
    st['mp'][aid][idx].append(x)


def _pool_as_impl(units, candidates_fn, affinity, wp, ss, ii, dd, mp,
                  fbi, fbs, qbs, lam, maximize=False):
    if not units:
        return []
    pool = af._MinLaborPool(list(candidates_fn(units[0])), affinity, wp, ss, ii, dd, mp,
                            fbi, fbs, qbs, lam, maximize=maximize)
    return [(u, pool.take(u)[0]) for u in pool.order(units)]


def _surface(st):
    return ({a: set(v) for a, v in st['ss'].items()},
            {a: set(v) for a, v in st['ii'].items()},
            dict(st['dd']),
            {a: {i: list(xs) for i, xs in d.items()} for a, d in st['mp'].items()})


def _key(res):
    return [(id(u), None if b is None else (b.location[0], b.x_phys, b.y_phys))
            for u, b in res]


#: The affinity-reward weight. NOT 1.0 on purpose: `max_reward = lam * sum(...)` and
#: `score = base - lam * delta` both multiply by it, so at lam=1.0 a port that dropped the
#: multiplication entirely would be indistinguishable.
_LAM = 2.5


def _run(impl, bins, units, aff, idx, fbi, fbs, qbs, maximize, waves=1, lam=_LAM):
    st = _state()
    _seed(st, 1, idx[99], 99, 20.0)              # a partner on the floor from unit one
    remaining, seq = list(bins), []
    for _w in range(waves):
        res = impl(list(units), lambda _u: list(remaining), aff, _wp(),
                   st['ss'], st['ii'], st['dd'], st['mp'], fbi, fbs, qbs, lam,
                   maximize=maximize)
        seq.append(_key(res))
        taken = {id(b) for _u, b in res if b is not None}
        remaining = [b for b in remaining if id(b) not in taken]
    return seq, _surface(st)


@pytest.mark.parametrize('maximize', [False, True])
@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4, 5, 6, 7])
def test_pool_matches_the_impl_exactly(maximize, seed):
    rng = random.Random(900 + seed)
    args = _fixture(rng)
    ref_seq, ref_state = _run(af._ranked_minlabor_impl, *args, maximize=maximize)
    got_seq, got_state = _run(_pool_as_impl, *args, maximize=maximize)
    assert got_seq == ref_seq, 'placement sequences diverged'
    assert got_state == ref_state          # exact, member-position ORDER included


@pytest.mark.parametrize('maximize', [False, True])
def test_two_consecutive_waves_share_state(maximize):
    rng = random.Random(101)
    args = _fixture(rng, n_units=6)
    ref = _run(af._ranked_minlabor_impl, *args, maximize=maximize, waves=2)
    got = _run(_pool_as_impl, *args, maximize=maximize, waves=2)
    assert got == ref
    assert ref[0][0] != ref[0][1], 'both waves placed identically — state is not shared'


def test_min_and_max_actually_disagree():
    rng = random.Random(5)
    args = _fixture(rng, n_units=8)
    assert _run(_pool_as_impl, *args, maximize=False)[0] != \
           _run(_pool_as_impl, *args, maximize=True)[0]


@pytest.mark.parametrize('maximize', [False, True])
def test_the_fixture_exercises_what_it_claims(maximize):
    rng = random.Random(2)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_units=40)
    seq, (ss, ii, dd, mp) = _run(af._ranked_minlabor_impl, bins, units, aff, idx,
                                 fbi, fbs, qbs, maximize=maximize)
    placed = [p for p in seq[0] if p[1] is not None]
    assert len(placed) == len(bins), 'not every bin was consumed'
    assert any(p[1] is None for p in seq[0]), 'the exhaustion path never ran'
    assert len({p[1][0] for p in placed}) > 1, 'only one aisle was used'
    assert len({p[1][2] for p in placed}) > 1, 'only one height — brackets are untested'
    assert any(len(xs) > 1 for d in mp.values() for xs in d.values()), \
        'no SKU landed twice in one aisle — the centroid never moved'


def test_take_returns_the_aisle_decision_score():
    """The score is `fq*bc - lam*delta`, the objective the aisle argmin compared — not the
    chosen bin's `cbest`, which carries a centroid term that is tie-shaping and not labor."""
    rng = random.Random(13)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_units=6)
    st = _state()
    _seed(st, 1, idx[99], 99, 20.0)
    pool = af._MinLaborPool(list(bins), aff, _wp(), st['ss'], st['ii'], st['dd'], st['mp'],
                            fbi, fbs, qbs, _LAM)
    n = 0
    for u in pool.order(units):
        b, score = pool.take(u)
        assert b is not None and score is not None
        assert isinstance(score, float)
        n += 1
    assert n == len(units)


def test_an_exhausted_pool_reports_nothing():
    rng = random.Random(17)
    bins, units, aff, idx, fbi, fbs, qbs = _fixture(rng, n_aisles=1, n_units=4)
    st = _state()
    pool = af._MinLaborPool(list(bins[:1]), aff, _wp(), st['ss'], st['ii'], st['dd'],
                            st['mp'], fbi, fbs, qbs, _LAM)
    assert pool.take(units[0])[0] is not None
    assert pool.take(units[1]) == (None, None)


def test_the_delta_sum_stays_in_csr_column_order():
    """A ratchet on the one refactor that must never happen here.

    `_delta_lift_from_row` switches which side it iterates on `len(row) <= len(member_set)`,
    so its summation ORDER flips as an aisle fills. That flip is the one-ulp drift b91cf38
    was written to fix. `_MinLaborPool` inlines its own loop over `row_items` in CSR column
    order for exactly that reason, and it looks enough like a duplicate that someone will
    eventually unify them.
    """
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(af._MinLaborPool.take))
    tree = ast.parse(src)
    for node in ast.walk(tree):          # drop the docstring so its prose cannot match
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    body = ast.unparse(tree)
    assert '_delta_lift_from_row' not in body, (
        '_MinLaborPool.take must not call _delta_lift_from_row — it flips summation order '
        'as an aisle fills, which is the b91cf38 ulp bug')
    assert 'for ci, w in row_items' in body, 'the inlined CSR-order delta loop is gone'


def test_the_centroid_term_actually_moves_a_bin_choice():
    """Non-vacuity for the compaction half of the objective.

    `_BRACKET_COLS` puts each height bracket at its own column so the three bracket ends
    differ in x; then a partner sitting at one of those columns must pull the choice to a
    different bracket than the pure labor term would pick. Without this, dropping
    `x_pace*|x - cx|` from the bin loop is invisible.
    """
    aff, idx = _aff([1, 99], [(1, 99, 8.0)])
    fbi, fbs, qbs = {idx[99]: 5.0, idx[1]: 1.0}, {1: 1.0}, {1: 1.0}
    order = _Order(1, 0.5, 1.0, 1.0, 5.0)
    units = [_Unit(order)]

    def _place(partner_x):
        bins = [_Bin(1, x, y) for x, y in _BRACKET_COLS]        # one bin per bracket
        st = _state()
        if partner_x is not None:
            _seed(st, 1, idx[99], 99, partner_x)
        pool = af._MinLaborPool(bins, aff, _wp(), st['ss'], st['ii'], st['dd'], st['mp'],
                                fbi, fbs, qbs, _LAM)
        return pool.take(units[0])[0]

    cold = _place(None)                       # no partner: pure labor picks the bracket
    pulled = _place(0.0)                      # partner at the y=150 bracket's column
    assert cold is not None and pulled is not None
    assert (pulled.x_phys, pulled.y_phys) != (cold.x_phys, cold.y_phys), (
        f'the centroid did not move the choice: both took {cold.x_phys},{cold.y_phys}')
    assert pulled.x_phys == 0.0, f'the pull should land on the partner column, got {pulled.x_phys}'
