"""test_gain_bundle_labor_families.py — the three families phase 1 chose (ticket 20).

`rank_minlabor`, `rank_labor` and `rank_cartlabor` are the families the phase-1 ranking
named that the gain evaluator did not already serve — 08's extension cap of three, exactly
consumed.  They arrive on the SAME pool adapter as `rank_popularity`, through their own
builders, so the fidelity argument is the one ticket 14 made.  What is NEW is that their
pools commit to live aisle bookkeeping BEYOND the three dicts the older families touch, and
that is what this file is about.

WHAT THIS FILE PINS

  1. FAITHFUL-TO-ARM, per family: the pool the driver's bundle opens over COPIES makes the
     same placements, from the same state, as the arm's PRODUCTION pool (the `Placement`
     its own `build` hook installs) over the live dicts — the placements AND the state each
     one advanced, with `==` on the floats, because the claim is byte-identity and not
     agreement.  This is the assertion a wrong builder, a dropped `beta`, or a mis-composed
     cart tuple fails.
  2. PURITY over the WHOLE live surface: a real `plan_order` against a driver bundle leaves
     all six aisle dicts exactly as it found them, not only the three the older families
     touch.  `aisle_state` naming one dict too few is not a refusal — it is a virtual
     placement advancing the real warehouse, and nothing downstream would notice.
  3. THE SABOTAGE for (2), per extra dict: with that dict's copier replaced by the identity,
     the same run MUST move the live dict.  Without it, (2) would pass just as well on a
     family that never touched it, and the whole `aisle_state` list would be decoration.
  4. THE BUNDLE HOLDS THE LIVE DICTS.  The copy is taken per evaluation, at `_make_pool`;
     a bundle holding a copy would price every drain of the run against the warehouse as it
     stood at worker startup, silently and increasingly wrongly.
  5. THE ARM-LEVEL HOISTS.  `_make_pool` rebuilds the policy for every virtual placement, so
     anything the builder computes once per ARM would otherwise be recomputed O(yard^2)
     times per drain: the travel-balanced geometry memo (one dict, shared) and
     rank_cartlabor's `total_freq` (the arm's one sum, which its builder's docstring
     forbids moving into the pool).
  6. THE SEAM'S OWN REFUSALS: an `aisle_state` name with no copier, and `aisle_state`
     declared on an adapter that opens no pool.

The fixtures are imported, not copied: the stub shapes from `test_gain_plan` are the ones
the evaluator is pinned against elsewhere, and `_Affinity` from the pool-equivalence file is
a real CSR lift matrix (`rank_minlabor` refuses a null one, and its affinity reward is the
term that picks the bin).

Run:  python -m pytest Tests/unit/test_gain_bundle_labor_families.py -q
"""
from __future__ import annotations

import random
from collections import defaultdict
from types import SimpleNamespace

import pytest

import Inbound.gain as gain
from Inbound.gain import GainBundle, OneOwnerBundle, plan_order
from Optimization.config.strategies import STRATEGY_BY_KEY, StrategyContext
from Optimization.metrics.Workload import WorkloadParams
from Optimization.simdriver.strategy_runner import _gain_bundle_for
from Warehouse.inventory.inventory_common import binkey_of, tier_ranks_for
from Warehouse.placement import Assignment_Functions as af

from Tests.unit.test_gain_plan import _KEY_M, _PUT, _Bin, _Order, _Unit, _trailer, _view
from Tests.unit.test_ranked_assign_pool_equivalence import _Affinity

#: A SMALL cart, deliberately: with the repo-default 125,000 the cart-swap term is ~0 (its
#: builder says so) and `rank_cartlabor` would place identically to `rank_labor` — the
#: equivalence in (1) would then hold for a bundle that composed the cart tuple wrongly.
_WP = WorkloadParams(cart_capacity=800.0)
_SPEC = {'fee_threshold_days': 2.0, 'urgency_horizon_days': 0.0}

#: family -> (strategy key, the live dicts its `take` commits to BEYOND the ranked three).
#: The second element is exactly what ticket 20 added to the seam, and what (3) sabotages.
FAMILIES = {
    'rank_labor':     ('uni_rank_labor_norsl',     ('aisle_pick_load_sum',)),
    'rank_cartlabor': ('uni_rank_cartlabor_norsl', ('aisle_pick_load_sum',
                                                    'aisle_vol_sum')),
    'rank_minlabor':  ('uni_rank_minlabor_norsl',  ('aisle_member_pos',)),
}
RANKED3 = ('aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum')
EXTRAS = [(f, e) for f, (_k, es) in FAMILIES.items() for e in es]


# ── the scene ─────────────────────────────────────────────────────────────────────

def _orders(n_skus=5):
    """SKUs whose expected_labor genuinely separates, so the LPT sort has an opinion."""
    return [_Order(sku=100 + i, freq=0.1 + 0.17 * i, qty_rate=1.0 + 1.3 * i,
                   labor=0.7 + 0.4 * i, hvar=0.2 + 0.15 * i) for i in range(n_skus)]


def _scene(seed=0, n_aisles=3, per_aisle=5, n_trailers=4):
    """Bins across several aisles (so the balance has somewhere to disperse to), trailers
    whose loads overlap on SKU (so `take`'s sku-already-here no-op runs), and more units
    than a single aisle can hold."""
    rng = random.Random(seed)
    orders = _orders()
    # The lifts are LARGE on purpose.  At the repo-ish scale of these costs an aisle-to-
    # aisle base gap is ~8 labor-seconds, so a reward of ~2 never flips an argmin and
    # `rank_minlabor` would place identically with its affinity term switched off -- a
    # fixture that cannot tell the compaction policy from a pure minimiser.
    aff = _Affinity([o.sku for o in orders],
                    [(orders[0].sku, orders[1].sku, 50.0),
                     (orders[1].sku, orders[2].sku, 30.0),
                     (orders[0].sku, orders[3].sku, 25.0)])
    bins = [_Bin(aid, 40.0 * k + 7.0 * aid, 48.0 * (k % 4))
            for aid in range(1, n_aisles + 1) for k in range(per_aisle)]
    rng.shuffle(bins)
    trailers = [_trailer(seq, 100.0 * seq,
                         [_Unit(rng.choice(orders), rng.randint(1, 30))
                          for _ in range(rng.randint(2, 4))])
                for seq in range(n_trailers)]
    return orders, aff, bins, trailers, _view({_KEY_M: bins})


def _mgr(orders, aff):
    """The live manager surface `_gain_bundle_for` and the arms' build hooks read.

    One aisle is PRE-SEATED so every term is live from the first unit: the LPT balance
    starts unequal, the cart carries mass, and the affinity reward has a partner already on
    the shelf for the centroid to measure against.
    """
    m = SimpleNamespace(
        _zoning_enabled=False,
        _aisle_sku_sets=defaultdict(set),
        _aisle_idx_sets=defaultdict(set),
        _aisle_demand_sum=defaultdict(float),
        _aisle_pick_load_sum=defaultdict(float),
        _aisle_vol_sum=defaultdict(float),
        _aisle_member_pos=defaultdict(lambda: defaultdict(list)),
        _sku_pick_load_product={o.sku: o.expected_labor for o in orders},
        _sku_vol_product={o.sku: o.expected_popularity * 900.0 for o in orders},
    )
    seed = orders[0]
    m._aisle_sku_sets[1].add(seed.sku)
    m._aisle_idx_sets[1].add(aff._sku_to_idx[seed.sku])
    m._aisle_demand_sum[1] = seed.expected_popularity
    m._aisle_pick_load_sum[1] = seed.expected_labor
    m._aisle_vol_sum[1] = m._sku_vol_product[seed.sku]
    m._aisle_member_pos[1][aff._sku_to_idx[seed.sku]].append(12.0)
    return m


def _ctx(orders, aff):
    return StrategyContext(
        affinity=aff, wp=_WP,
        freq_by_idx={aff._sku_to_idx[o.sku]: o.demand.relative_frequency for o in orders},
        freq_by_sku={o.sku: o.demand.relative_frequency for o in orders},
        qty_by_sku={o.sku: o.demand.quantity_rate for o in orders},
        beta=1.0, orders=orders,
        # Small enough that the cart's raw capacity binds — see `_WP`.
        expected_batch_skus=4.0)


def _bundle_for(family, mgr, ctx):
    return _gain_bundle_for(STRATEGY_BY_KEY[FAMILIES[family][0]], mgr, ctx,
                            _WP, _PUT, _SPEC)


def _plain(d):
    """A dict-of-what-it-holds, comparable with `==` and detached from the original."""
    out = {}
    for k, v in d.items():
        if isinstance(v, set):
            out[k] = set(v)
        elif isinstance(v, dict):
            out[k] = {i: list(xs) for i, xs in v.items()}
        else:
            out[k] = v
    return out


def _snapshot(mgr):
    return {n: _plain(getattr(mgr, '_' + n)) for n in gain.AISLE_COPIERS}


def _where(b):
    """A placement as reproducible geometry, never id(bin)."""
    return None if b is None else (b.location[0], b.x_phys, b.y_phys)


# ── 1. faithful-to-arm: the virtual pool is the arm's pool ────────────────────────

@pytest.mark.parametrize('family', sorted(FAMILIES))
def test_the_bundles_pool_places_exactly_as_the_arms_own_pool(family):
    """Both sides open from IDENTICAL state and are driven through the pool's own
    precedence, so only the CHOICE — and the state that choice advances — is under test."""
    orders, aff, bins, trailers, _view_ = _scene()
    units = [u for t in trailers for u in (i.unit for i in t.pending)]
    ctx = _ctx(orders, aff)

    prod_mgr = _mgr(orders, aff)
    STRATEGY_BY_KEY[FAMILIES[family][0]].build(prod_mgr, ctx)
    prod_pool = prod_mgr.placement.open_pool(list(bins))

    ev_mgr = _mgr(orders, aff)
    bundle = _bundle_for(family, ev_mgr, ctx)
    state = {n: gain.AISLE_COPIERS[n](d) for n, d in bundle.aisle_state.items()}
    ev_pool = bundle.pool_factory(list(bins), state, _WP)

    ref = [(u.order.sku, _where(prod_pool.take(u)[0])) for u in prod_pool.order(list(units))]
    got = [(u.order.sku, _where(ev_pool.take(u)[0])) for u in ev_pool.order(list(units))]
    assert got == ref, f'{family}: the evaluator priced a different policy than the arm runs'
    assert len([b for _s, b in ref if b is not None]) > len(bins) // 2, (
        'the fixture seated almost nothing — an equivalence about two empty pools')
    assert len({b[0] for _s, b in ref if b is not None}) > 1, (
        'every placement landed in one aisle: the per-aisle terms never separated')
    for name in bundle.aisle_state:
        assert _plain(state[name]) == _plain(getattr(prod_mgr, '_' + name)), (
            f'{family}: the copy of {name} the virtual pool advanced is not what the '
            f'arm advanced from the same start')


# ── 2/3. purity over the whole surface, and the sabotage that makes it mean something ──

@pytest.mark.parametrize('family', sorted(FAMILIES))
def test_a_real_plan_leaves_every_live_aisle_dict_untouched(family):
    orders, aff, _bins, trailers, view = _scene()
    mgr = _mgr(orders, aff)
    bundle = OneOwnerBundle(_bundle_for(family, mgr, _ctx(orders, aff)))
    before = _snapshot(mgr)
    out = plan_order(trailers, bundle, view, predicted=False)
    assert sorted(t.seq for t in out) == sorted(t.seq for t in trailers), (
        'the plan must be a permutation of the yard')
    assert [t.seq for t in plan_order(trailers, bundle, view, predicted=False)] == \
        [t.seq for t in out], 'same frozen inputs, same plan'
    assert _snapshot(mgr) == before, (
        f'{family}: a virtual placement advanced the LIVE aisle bookkeeping — either a '
        f'dict its `take` commits to is missing from aisle_state, or its copier is not '
        f'deep enough')


@pytest.mark.parametrize('family,extra', EXTRAS)
def test_the_identity_copier_lets_the_virtual_placement_reach_the_warehouse(
        family, extra, monkeypatch):
    """06's Tier-1 sabotage for this seam, and the non-vacuity guard the purity test needs:
    hand the pool the LIVE dict and it must move.  A family that did not commit to `extra`
    would pass the purity test with nothing copied at all."""
    orders, aff, _bins, trailers, view = _scene()
    mgr = _mgr(orders, aff)
    bundle = OneOwnerBundle(_bundle_for(family, mgr, _ctx(orders, aff)))
    monkeypatch.setitem(gain.AISLE_COPIERS, extra, lambda d: d)
    before = _plain(getattr(mgr, '_' + extra))
    plan_order(trailers, bundle, view, predicted=False)
    assert _plain(getattr(mgr, '_' + extra)) != before, (
        f'{family} priced a whole plan without ever committing to {extra} — the purity '
        f'assertion about it is vacuous, and so is its place in aisle_state')


# ── 4. the bundle holds the LIVE dicts; the copy is per evaluation ────────────────

@pytest.mark.parametrize('family', sorted(FAMILIES))
def test_each_family_arrives_on_the_pool_adapter_over_the_live_dicts(family):
    orders, aff, _bins, _trailers, _view_ = _scene()
    mgr = _mgr(orders, aff)
    b = _bundle_for(family, mgr, _ctx(orders, aff))
    assert b.pool_factory is not None and not b.uniform and not b.expect_heads, (
        f'{family} must take the pool adapter — the merge default would price the '
        f'extremal-D residue under its name')
    assert set(b.aisle_state) == set(RANKED3) | set(FAMILIES[family][1])
    for name, d in b.aisle_state.items():
        assert d is getattr(mgr, '_' + name), (
            f'{family}: aisle_state must hold the LIVE {name}.  A copy here would freeze '
            f'the warehouse at worker startup and price every later drain against it')


# ── 5. what must be computed once per ARM, not once per evaluation ────────────────

@pytest.mark.parametrize('family,builder', [
    ('rank_labor', 'build_ranked_labor_pool_fn'),
    ('rank_cartlabor', 'build_ranked_cartlabor_pool_fn'),
])
def test_the_arm_level_values_are_hoisted_out_of_the_per_evaluation_path(
        family, builder, monkeypatch):
    """`_make_pool` rebuilds the policy for every virtual placement, and `plan_order` is
    O(yard^2) of those per drain.  So the geometry memo must be ONE dict for the arm, and
    rank_cartlabor's `total_freq` the arm's one sum — a `{}` or a `sum(...)` that drifted
    inside the factory closure is silent, and pays O(catalogue) per pool open."""
    orders, aff, _bins, trailers, view = _scene()
    ctx = _ctx(orders, aff)
    mgr = _mgr(orders, aff)
    seen: list = []
    real = getattr(af, builder)

    def _spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(af, builder, _spy)

    bundle = OneOwnerBundle(_bundle_for(family, mgr, ctx))
    plan_order(trailers, bundle, view, predicted=False)

    assert len(seen) > 1, (
        'the policy was not rebuilt per evaluation — this test pins nothing about a '
        'hoist that has nothing to be hoisted out of')
    assert len({id(kw['geo_memos']) for kw in seen}) == 1, (
        'the geometry memo differs between evaluations: it is being rebuilt inside the '
        'factory, so every candidate bin re-derives geometry that cannot have moved')
    assert seen[0]['geo_memos'], 'the memo never filled, so sharing it buys nothing'
    if family == 'rank_cartlabor':
        assert {kw['total_freq'] for kw in seen} == {sum(ctx.freq_by_sku.values())}, (
            "total_freq is not the arm's one sum over freq_by_sku")


# ── 6. the seam's own refusals ────────────────────────────────────────────────────

def _kw(**over):
    base = dict(put_speed=_PUT, wp_of=lambda u: _WP, binkey_of=binkey_of,
                tier_ranks_for=tier_ranks_for)
    base.update(over)
    return base


def test_an_aisle_state_name_with_no_copier_is_refused():
    """Refused at CONSTRUCTION, which is worker startup, rather than at the first drain —
    and refused rather than guessed at, because `dict(d)` over a nested dict is a copy that
    shares its inner containers and breaks purity with no symptom."""
    with pytest.raises(ValueError, match='no copier'):
        GainBundle(**_kw(pool_factory=lambda *a: None,
                         aisle_state={'aisle_lift_sum': {}}))


def test_aisle_state_on_an_adapter_that_opens_no_pool_is_refused():
    """The merge and uniform adapters never call `pool_factory`, so state declared for them
    would be copied by nobody and read by nobody — a wiring error that looks like care."""
    with pytest.raises(ValueError, match='copy list'):
        GainBundle(**_kw(aisle_state={'aisle_sku_sets': {}}))
