"""test_assignment_functions.py — the composable, named assignment-function layer.

Three groups:

**Registries and names** — `ASSIGNMENT_BUILDERS` / `RANKED_BUILDERS` are how a strategy key
in `Optimization/config/strategies.py` becomes a callable, and `SCORER_NEEDS` is how the
runner decides whether to load the affinity matrix and the demand maps at all.  A builder
missing from a registry is a strategy that cannot be constructed by name; a wrong
`SCORER_NEEDS` entry is a scorer handed empty inputs.

**Composed placement** — the scorers actually place where their names say: travel picks the
low-travel aisle, cohesion co-locates with an affinity partner, compaction/expansion pick
the near/far column within an aisle.

**Refusal to silently degrade** — every affinity- or demand-driven policy RAISES when built
without the data it weights by, rather than scoring 0 for everything and quietly becoming
uniform placement.  A silent degradation here does not crash a sweep; it publishes a
comparison in which two arms were secretly the same policy.

A note on speeds
----------------
`x_speed`/`y_speed` are **ft/s**, and bin positions are inches, so travel is
`x_phys * sec_per_inch(x_speed) + y_phys * sec_per_inch(y_speed)` (`Warehouse/kernel/
cost_model.py`).  `sec_per_inch` maps a non-positive speed to `inf`, so a stub `wp` with
`y_speed=0.0` does NOT mean "y contributes nothing" — it means `inf*0 = NaN` for any bin on
the floor, which is the defect this file's fixtures used to trip over (see the last two
tests).  `validate_speeds` now rejects it at the config boundary.  Every `wp` below carries a
POSITIVE y_speed and every stub bin sits at `y_phys=0`, which is how the fixtures get their
intended "score is a function of x only".

    python -m pytest Tests/unit/test_assignment_functions.py -q
"""
from __future__ import annotations

import math
import types
from collections import defaultdict

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.kernel.cost_model import sec_per_inch
from Warehouse.placement import Assignment_Functions as A

_TOL = 1e-9

# ── stubs ────────────────────────────────────────────────────────────────────
#
# The scorers duck-type over bins (`location`, `x_phys`, `y_phys`) and units
# (`order.sku`); these three cover that whole surface.  Standing up a real warehouse to
# exercise an aisle-ranking function would couple these tests to the builder and the
# palletizer, neither of which is under test here.


class _B:
    """A candidate bin.  y_phys is always 0 so travel is a function of x_phys alone."""
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x):
        self.location = (aid, 0, 0)
        self.x_phys = x
        self.y_phys = 0


def _wp(x_speed=1.0, y_speed=1.0, **kw):
    """A WorkloadParams stand-in.  y_speed is POSITIVE by default (see module docstring)."""
    return types.SimpleNamespace(x_speed=x_speed, y_speed=y_speed, **kw)


def _aff(skus, pairs):
    """In-memory AffinityStore with a hand-set symmetric CSR lift matrix."""
    aff = AffinityStore(':memory:')
    idx = {s: i for i, s in enumerate(skus)}
    rows, cols, data = [], [], []
    for i, j, l in pairs:
        rows += [idx[i], idx[j]]
        cols += [idx[j], idx[i]]
        data += [l, l]
    aff._sku_to_idx = idx
    aff._matrix = csr_matrix((data, (rows, cols)), shape=(len(skus), len(skus)), dtype=np.float32)
    return aff, idx


def _unit(sku):
    return types.SimpleNamespace(order=types.SimpleNamespace(sku=sku))


def _null_affinity():
    """An AffinityStore-shaped object with no matrix — what a run without affinity data has."""
    return types.SimpleNamespace(_matrix=None, _sku_to_idx={})


# ── registries and names ─────────────────────────────────────────────────────

def test_assignment_builder_registry_is_exactly_the_seven_named_policies():
    """`strategies.py` looks builders up by these keys; a rename here is a KeyError there."""
    expected = {'travel_min', 'travel_max', 'cohesion_max', 'cohesion_min',
                'uniform_min', 'load_min', 'load_max'}
    got = set(A.ASSIGNMENT_BUILDERS)
    assert got == expected, f'{sorted(got)} != {sorted(expected)}'


def test_ranked_builder_registry_is_exactly_the_three_wave_policies():
    """RANKED_BUILDERS is the subset that can place a whole wave (place_wave), not one unit."""
    expected = {'travel_min', 'travel_max', 'uniform_ranked'}
    got = set(A.RANKED_BUILDERS)
    assert got == expected, f'{sorted(got)} != {sorted(expected)}'


def test_scorer_needs_declares_which_inputs_each_policy_loads():
    """SCORER_NEEDS is (needs_affinity, needs_demand).

    The runner skips loading the affinity DB and the demand maps when a policy declares it
    does not need them — so a wrong entry either wastes a multi-GB load or hands the scorer
    empty maps.  The empty-map case is caught by the `_require_*` guards below, but only if
    this flag said the data was needed in the first place.
    """
    assert A.SCORER_NEEDS['travel_min'] == (True, True), A.SCORER_NEEDS['travel_min']
    assert A.SCORER_NEEDS['uniform_min'] == (False, False), A.SCORER_NEEDS['uniform_min']


def test_built_scorers_carry_their_programmatic_name():
    """Downstream (metrics, run labels, the viewer) identifies a scorer by `fn.name`."""
    wp = _wp()
    aff, idx = _aff([1, 2], [])
    fbi = {idx[1]: 0.5, idx[2]: 1.0}     # non-empty demand maps — the policies weight by them

    def _build(key):
        return A.ASSIGNMENT_BUILDERS[key](aff, wp, defaultdict(set), defaultdict(set),
                                          defaultdict(float), fbi, {1: 0.5}, {1: 1.0})

    assert getattr(_build('travel_min'), 'name', None) == 'travel_min'
    assert getattr(_build('cohesion_max'), 'name', None) == 'cohesion_max'


# ── composed scorers place where their names say ─────────────────────────────

def test_cohesion_max_co_locates_with_an_affinity_partner_despite_higher_travel():
    """The defining trade: cohesion pays travel to put co-picked SKUs in one aisle.

    Aisle 10 is 9x further out than aisle 20 but already holds sku 2, which sku 1 is
    co-picked with (lift 5).  cohesion_max must take the expensive aisle — if it takes
    aisle 20 it has degenerated into travel_min and the arm is a duplicate.
    """
    wp = _wp()
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi = {idx[2]: 1.0}
    fbs = {1: 0.5, 2: 1.0}
    qbs = {1: 1.0, 2: 1.0}
    cands = [_B(10, 9.0), _B(20, 1.0)]                   # aisle10 far + partner; aisle20 near

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
    ss[10] = {2}
    ii[10] = {idx[2]}                                     # partner sku2 already placed in aisle 10

    b = A.build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(_unit(1), cands)
    assert b.location[0] == 10, f'cohesion_max chose aisle {b.location[0]}, not the partner aisle 10'


def test_travel_min_ignores_the_partner_and_takes_the_cheapest_aisle():
    """The mirror of the test above, on the SAME candidates: with no SKU placed anywhere,
    travel_min ranks purely on f_s*D and must take the near aisle.

    This is the pair that proves the two arms are genuinely different policies rather than
    one scorer wearing two names.
    """
    wp = _wp()
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi = {idx[2]: 1.0}
    fbs = {1: 0.5, 2: 1.0}
    qbs = {1: 1.0, 2: 1.0}
    cands = [_B(10, 9.0), _B(20, 1.0)]

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)   # nothing placed yet
    b = A.build_trip_minimizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(_unit(1), cands)

    d10 = sec_per_inch(wp.x_speed) * 9.0
    d20 = sec_per_inch(wp.x_speed) * 1.0
    assert b.location[0] == 20, (
        f'travel_min chose aisle {b.location[0]} (D={d10:.4f}s) over aisle 20 (D={d20:.4f}s)')


# ── co-demand: compaction vs expansion, within one aisle ─────────────────────

def _co_demand_state(partner_x):
    """Aisle 10 holds partner sku 2 at column `partner_x`."""
    ss, ii, dd, mp = (defaultdict(set), defaultdict(set), defaultdict(float),
                      defaultdict(lambda: defaultdict(list)))
    return ss, ii, dd, mp


def _co_demand_fixture(partner_x, idx):
    ss, ii, dd, mp = _co_demand_state(partner_x)
    ss[10] = {2}
    ii[10] = {idx[2]}
    mp[10][idx[2]] = [partner_x]
    return ss, ii, dd, mp


def _co_demand_candidates():
    return [_B(10, 0.0), _B(10, 5.0), _B(10, 10.0)]      # one aisle, columns 0 / 5 / 10


def test_compaction_and_expansion_pick_opposite_columns_around_the_partner():
    """Co-demand placement is COLUMN-aware, not just aisle-aware.

    Both policies choose the same aisle (there is only one); they differ in which bay
    within it.  With the partner at column 10, compaction must take column 10 (shortest
    in-aisle sweep between the two picks) and expansion column 0 (longest).  Getting these
    the same way round would make the expansion arm a copy of the compaction arm.
    """
    wp = _wp(pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])                # sku1 co-demanded with sku2 (lift 5)
    fbi = {idx[2]: 1.0}
    fbs = {1: 1.0, 2: 1.0}
    qbs = {1: 1.0, 2: 1.0}

    ss, ii, dd, mp = _co_demand_fixture(10.0, idx)
    compact = A.build_co_demand_placement(True, aff, wp, ss, ii, dd, mp, fbi, fbs, qbs)
    assert compact.is_ranked, 'co-demand placement must place a whole group at once'
    assert compact.name == 'compaction', compact.name
    b = compact.place_one(_unit(1), _co_demand_candidates())
    assert abs(b.x_phys - 10.0) < _TOL, f'compaction placed at x={b.x_phys}, partner is at x=10.0'

    ss, ii, dd, mp = _co_demand_fixture(10.0, idx)
    expand = A.build_co_demand_placement(False, aff, wp, ss, ii, dd, mp, fbi, fbs, qbs)
    assert expand.name == 'expansion', expand.name
    be = expand.place_one(_unit(1), _co_demand_candidates())
    assert abs(be.x_phys - 0.0) < _TOL, f'expansion placed at x={be.x_phys}, partner is at x=10.0'


def test_the_co_demand_pool_agrees_with_its_per_unit_twin():
    """The group path and the straggler path are one policy and must not disagree about
    the bin. They are separate code (`_CoDemandPool.take` vs `_build_co_demand_place_one`),
    and the straggler path is what runs whenever a group's snapshot is exhausted — so a
    divergence here would show up as a placement that depends on queue depth."""
    wp = _wp(pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi, fbs, qbs = {idx[2]: 1.0}, {1: 1.0, 2: 1.0}, {1: 1.0, 2: 1.0}
    for compact, want_x in ((True, 10.0), (False, 0.0)):
        ss, ii, dd, mp = _co_demand_fixture(10.0, idx)
        pl = A.build_co_demand_placement(compact, aff, wp, ss, ii, dd, mp, fbi, fbs, qbs)
        assert pl.is_pooled
        pool = pl.open_pool(_co_demand_candidates(), _unit(1))
        got, score = pool.take(_unit(1))
        assert abs(got.x_phys - want_x) < _TOL, (compact, got.x_phys)
        # The score is the compaction objective: distance from the partner column, paced.
        assert score is not None and score >= 0.0
        assert abs(score - A.sec_per_inch(wp.x_speed) * abs(want_x - 10.0)) < _TOL


def test_the_co_demand_pool_reports_no_score_before_a_partner_lands():
    """With nothing placed, there is no centroid, so the bin came from the cold-start rule
    (front for compact, back for expand) and there is no distance to report. A pool that
    returned 0.0 here would read downstream as a perfect placement."""
    wp = _wp(pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    ss, ii, dd, mp = _co_demand_state(10.0)          # NO partner seated
    pl = A.build_co_demand_placement(True, aff, wp, ss, ii, dd, mp,
                                     {idx[2]: 1.0}, {1: 1.0}, {1: 1.0})
    b, score = pl.open_pool(_co_demand_candidates(), _unit(1)).take(_unit(1))
    assert b is not None and score is None


# ── never silently degrade ───────────────────────────────────────────────────
#
# Each of these four raises today.  The failure they prevent is not a crash — it is a
# comparison run in which an affinity arm scored 0 lift for every candidate and quietly
# became uniform placement, producing a plausible number nobody could tell was wrong.

def test_affinity_driven_policies_refuse_a_null_lift_matrix():
    wp = _wp(pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi = {idx[2]: 1.0}
    fbs = {1: 1.0, 2: 1.0}
    qbs = {1: 1.0, 2: 1.0}
    ss, ii, dd, mp = _co_demand_fixture(0.0, idx)

    with pytest.raises(ValueError, match='affinity'):
        A.build_co_demand_placement(True, _null_affinity(), wp, ss, ii, dd, mp, fbi, fbs, qbs)
    with pytest.raises(ValueError, match='affinity'):
        A.build_cluster_maximizing_assignment_fn(_null_affinity(), wp, ss, ii, dd, fbi, fbs, qbs)


def test_demand_weighted_policies_refuse_an_empty_frequency_map():
    """A valid lift matrix is not enough: these policies weight lift BY demand, so an empty
    frequency map zeroes the whole score just as thoroughly as a missing matrix."""
    wp = _wp(pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi = {idx[2]: 1.0}
    fbs = {1: 1.0, 2: 1.0}
    qbs = {1: 1.0, 2: 1.0}
    ss, ii, dd, mp = _co_demand_fixture(0.0, idx)

    with pytest.raises(ValueError, match='freq_by_idx'):
        A.build_co_demand_placement(True, aff, wp, ss, ii, dd, mp, {}, fbs, qbs)
    with pytest.raises(ValueError, match='freq_by_sku'):
        A.build_trip_minimizing_assignment_fn(aff, wp, ss, ii, dd, fbi, {}, qbs)


# ── a non-positive speed is rejected, not silently turned into NaN ────────────
#
# This is the defect these fixtures used to trip over, and it is worth stating plainly
# because the failure mode is invisible.  `sec_per_inch(0.0)` is `inf`, every travel
# expression is `x_pace*x_phys + y_pace*y_phys`, and a bin at `y_phys=0` therefore
# evaluates `inf*0.0` = **NaN**.  Every aisle then scores NaN, every comparison against
# NaN is False, and `_pick_extremal_aisle` keeps whichever aisle it saw FIRST — placement
# degrades to insertion order with no error and no visibly wrong number.
#
# The fix rejects the input at the two dataclass boundaries rather than trying to make
# `inf*0` mean 0.  A zero speed is not "this axis is free": it says the picker can never
# move vertically, which makes every bin above the floor unreachable.  There is no
# coherent score to compute, so there is nothing to salvage by patching the arithmetic —
# and patching it would have meant guarding ~10 hot-loop expressions to hide a config
# error.  `validate_speeds` runs once per config instead of once per bin.


def test_nonpositive_speed_is_rejected_at_the_config_boundary():
    """A speed that would produce NaN travel scores must raise where it enters, not spread.

    Both dataclasses are checked because they are separate doors into the same trap:
    `PickConfig` is what the config JSON becomes (`Optimization/config/sim_config.py`
    reads `y_speed` straight out of it), while `WorkloadParams` is what the placement
    scorers actually read.  A run can reach the scorers through either.
    """
    from Optimization.metrics.Workload import WorkloadParams
    from Warehouse.picking.Pick import PickConfig

    for bad in (0.0, -1.0, float('nan'), float('inf')):
        for field in ('x_speed', 'y_speed'):
            with pytest.raises(ValueError, match=field):
                PickConfig(**{field: bad})
            with pytest.raises(ValueError, match=field):
                WorkloadParams(**{field: bad})

    # The guard must not have narrowed what a VALID config may say. A very slow axis is
    # legitimate and is how "y barely matters" is actually spelled.
    assert WorkloadParams(x_speed=1.0, y_speed=1e-6).y_speed == 1e-6
    assert PickConfig(x_speed=1.0, y_speed=1e-6).y_speed == 1e-6


def test_travel_scores_stay_finite_for_every_valid_speed():
    """The property the guard buys: no reachable `wp` can make an aisle score NaN.

    With both bins at ground level the ranking is by x alone, so the near aisle 20 (x=1)
    must beat aisle 10 (x=9) — and must do so no matter how extreme the vertical speed is.
    Candidates are inserted far-first on purpose: under the NaN bug the list order alone
    decided the winner, so a fixture in sorted order would have passed while broken.
    """
    aff, idx = _aff([1, 2], [])
    for y_speed in (1e-6, 0.5, 1e6):
        wp = _wp(y_speed=y_speed)
        cands = [_B(10, 9.0), _B(20, 1.0)]

        best_D, _ = A._aisle_extremal_bins(cands, wp.x_speed, wp.y_speed, minimize=True)
        assert all(math.isfinite(d) for d in best_D.values()), (
            f'y_speed={y_speed}: non-finite travel scores {best_D}')

        ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
        b = A.build_trip_minimizing_assignment_fn(aff, wp, ss, ii, dd, {idx[2]: 1.0},
                                                  {1: 0.5, 2: 1.0}, {1: 1.0, 2: 1.0})(_unit(1), cands)
        assert b.location[0] == 20, (
            f'y_speed={y_speed}: travel_min chose aisle {b.location[0]}; with all bins at '
            f'y_phys=0 the vertical speed is irrelevant and the near aisle 20 must win')
