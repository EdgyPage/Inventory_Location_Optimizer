"""test_gain_plan.py — the gain-family ordering entries and their evaluator.

What this file pins, per the build ticket ("Build the gain evaluator and the gain-plan
arms", 14) and the cache boundary's Tier-1 contract (06):

  1. REGISTRATION: `lifo` (pure key) and the three gain entries (`@ordering`) resolve
     through both standing registries; the gain entries refuse loudly without their
     machinery (no SpaceView / no bundle) instead of quietly ranking as fifo.
  2. THE MYOPIC SIGNAL IS CONTENTION: with only `empties` visible, the load that loses
     the most by meeting the leftovers is served first; identical loads TIE and keep
     arrival order (the degenerate case that mirrors fifo's inertness).
  3. FORECAST READS `predicted`, MYOPIC DOES NOT — and forecast with an empty
     predicted tier degenerates to myopic exactly.
  4. THE URGENCY GATE composes FIFO-over-plan and nothing else: overdue trailers jump
     the plan in stamp order; a large horizon is pure FIFO (hours never blend in).
  5. TIER-1 EQUIVALENCE + SABOTAGE (06): the structured plan (sort-once /
     slice-under-consumption) equals a naive rebuild-per-candidate plan on seeded
     scenarios, and perturbing the sorted structure makes the comparison FAIL — the
     equivalence check can actually catch a wrong structure.
  6. PURITY through the real seam: an entry called via `yard_order`/`bounded_order`
     consumes no RNG, mutates neither the yard, the frozen view, nor the live aisle
     dicts a pool bundle references (the pool adapter works on copies).
  7. THE FUTURESIGHT WINDOW ("Build the futuresight window feed", 13): the entry
     refuses a missing feed (None) and accepts an empty one (`()`); pricing swaps
     the static rates for the window's realized demand (absent = put only, visits
     capped at the event count), and rates that MATCH the static ones reproduce
     `gain_forecast` exactly — knowledge changed, machinery not; the driver's
     startup gate refuses the arm with the knob unset or the script missing.

Run:  python -m pytest Tests/unit/test_gain_plan.py -q
"""
from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from Inbound.gain import GainBundle, _Evaluator, _load_units, plan_order
from Inbound.priorities import DockContext, bounded_order, dock_key, yard_key
from Inbound.space import SpaceView
from Inbound.trailer import Trailer, Trailer53
from Inbound.transit import YardTransit
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import StorageHandleConfig
from Warehouse.inventory.inventory_common import binkey_of, tier_ranks_for
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.placement import Assignment_Functions as af

_DAY = 86400.0
_WP = WorkloadParams()                       # repo defaults: 4/2 ft/s, intercept 1.0
_PUT = SpeedProfile(2.0, 4.0)                # settings.py PUT_FOOT_X / PUT_FOOT_Y
_KEY_M = ('conveyable', 'food', 'medium', 'pallet')
_KEY_L = ('conveyable', 'food', 'large', 'pallet')


# ── stub shapes (exactly the fields the evaluator reads) ──────────────────────────

class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y=0.0):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Order:
    __slots__ = ('sku', 'storage_handle_config', 'demand', 'labor_cost', 'handle_var',
                 'expected_popularity')

    def __init__(self, sku, freq=1.0, qty_rate=1.0, labor=1.0, hvar=0.5,
                 category='food'):
        self.sku = sku
        self.storage_handle_config = StorageHandleConfig('conveyable', category)
        self.demand = Demand.from_rates(freq, qty_rate)
        self.labor_cost = labor
        self.handle_var = hvar
        self.expected_popularity = freq * qty_rate


class _Unit:
    __slots__ = ('order', 'quantity', 'storage_size', 'unit_category')

    def __init__(self, order, quantity, size='medium', category='pallet'):
        self.order, self.quantity = order, quantity
        self.storage_size, self.unit_category = size, category


def _trailer(seq, arrived, units):
    t = Trailer(Trailer53, seq)
    t.arrived_s = arrived
    t.pending = [SimpleNamespace(unit=u) for u in units]
    t.taken = 0
    return t


def _view(empties, predicted=None, frozen_at=0.0, window=None):
    return SpaceView(
        empties={k: tuple(v) for k, v in empties.items()},
        emptied_at={},
        predicted={k: tuple(v) for k, v in (predicted or {}).items()},
        released_at=None, versions=(0, 0, 0), frozen_at=frozen_at, window=window)


def _bundle(**kw):
    kw.setdefault('put_speed', _PUT)
    kw.setdefault('wp_of', lambda u: _WP)
    kw.setdefault('binkey_of', binkey_of)
    kw.setdefault('tier_ranks_for', tier_ranks_for)
    return GainBundle(**kw)


def _ctx(view, bundle, depth=4):
    ctx = DockContext(doors=4, free_doors=4, yard_depth=depth)
    ctx.space = view
    ctx.gain = bundle
    return ctx


def _seqs(trailers):
    return [t.seq for t in trailers]


# ── 1. registration, kinds, and the loud refusals ─────────────────────────────────

def test_registry_carries_the_roster():
    from Inbound.gain import GAIN_POLICIES
    from Inbound.priorities import DOCK_POLICIES, YARD_POLICIES
    # The driver keys bundle injection off GAIN_POLICIES: a registered-but-unlisted
    # name would run bundleless (loud at runtime, but the static pin is free).
    assert GAIN_POLICIES <= set(YARD_POLICIES)
    assert GAIN_POLICIES <= set(DOCK_POLICIES)
    assert 'futuresight' in GAIN_POLICIES, (
        'futuresight needs the driver-injected bundle like any gain entry — '
        'unlisted, it would run bundleless')
    for resolve in (yard_key, dock_key):
        for name in ('gain_myopic', 'gain_forecast', 'gain_gated', 'futuresight'):
            entry = resolve(name)
            assert getattr(entry, 'ORDERING', False), (
                f'{name} must be an @ordering entry — a gain plan has no '
                f'per-candidate key')
        assert not getattr(resolve('lifo'), 'ORDERING', False), (
            'lifo is a pure key, the degenerate case')
        assert not getattr(resolve('fifo'), 'ORDERING', False)


def test_gain_entry_refuses_without_machinery():
    entry = yard_key('gain_myopic')
    t = _trailer(0, 0.0, [_Unit(_Order(1), 1)])
    ctx = DockContext(doors=4, free_doors=4, yard_depth=1)   # no space, no bundle
    with pytest.raises(RuntimeError, match='SpaceView'):
        entry([t], ctx)
    ctx.space = _view({_KEY_M: [_Bin(0, 10.0)]})
    with pytest.raises(RuntimeError, match='GainBundle'):
        entry([t], ctx)
    # And a planless trailer is a wiring error, never a silent zero.
    ctx.gain = _bundle()
    bare = Trailer(Trailer53, 7)
    bare.arrived_s = 0.0
    with pytest.raises(ValueError, match='no pack plan'):
        entry([bare], ctx)


def test_lifo_reverses_fifo_and_ranks_stampless_last():
    tr = YardTransit(doors=4, yard_policy='lifo')
    old, new, bare = Trailer(Trailer53, 0), Trailer(Trailer53, 1), Trailer(Trailer53, 2)
    old.arrived_s, new.arrived_s, bare.arrived_s = 0.0, 500.0, None
    tr._yard.extend([old, new, bare])
    assert _seqs(tr.yard_order(tr.freeze_ctx())) == [1, 0, 2], (
        'lifo: newest standing first, the stampless (oldest-by-convention) last')


# ── 2/3. the myopic contention signal, ties, and the predicted tier ───────────────

def _contention_pair():
    """One key, one cheap bin, one far bin; a hot load (many visits) and a cold one.
    Whoever is served second meets the far bin — the myopic arm's whole signal."""
    cheap, far = _Bin(0, 10.0), _Bin(0, 2000.0)
    cold = _trailer(0, 0.0, [_Unit(_Order(1, qty_rate=1.0), 1)])
    hot = _trailer(1, 100.0, [_Unit(_Order(2, qty_rate=1.0), 50)])
    return cold, hot, _view({_KEY_M: [cheap, far]})


def test_myopic_serves_the_contended_hot_load_first():
    cold, hot, view = _contention_pair()
    entry = yard_key('gain_myopic')
    out = entry([cold, hot], _ctx(view, _bundle()))
    assert _seqs(out) == [1, 0], (
        'the hot load loses 50 visits worth of travel by deferring to the far bin; '
        'the cold load loses one — contention decides, not arrival')


def test_identical_loads_tie_to_arrival_order():
    view = _view({_KEY_M: [_Bin(0, 10.0), _Bin(0, 2000.0)]})
    entry = yard_key('gain_myopic')
    order = _Order(1, qty_rate=1.0)
    for seqs in ((0, 1), (1, 0)):
        a = _trailer(seqs[0], 0.0, [_Unit(order, 5)])
        b = _trailer(seqs[1], 50.0, [_Unit(order, 5)])
        out = entry([a, b], _ctx(view, _bundle()))
        assert _seqs(out) == list(seqs), (
            'identical loads have identical gains: the tie keeps the handed '
            '(arrival) order — the degenerate case that mirrors fifo inertness')


def test_forecast_reads_predicted_and_myopic_does_not():
    key2 = ('conveyable', 'general', 'medium', 'pallet')
    m1, m2, p = _Bin(0, 500.0), _Bin(1, 500.0), _Bin(1, 5.0)
    # B (older, key2) will regain a super-cheap predicted bin by deferring; A cannot.
    b = _trailer(0, 0.0, [_Unit(_Order(2, qty_rate=1.0, category='general'), 10)])
    a = _trailer(1, 50.0, [_Unit(_Order(1, qty_rate=1.0), 10)])
    empties = {_KEY_M: [m1], key2: [m2]}
    with_pred = _view(empties, predicted={key2: [p]})
    ctx = _ctx(with_pred, _bundle())
    assert _seqs(yard_key('gain_forecast')([b, a], ctx)) == [1, 0], (
        'B defers well (its key gains a cheap predicted clear), so A is served first')
    assert _seqs(yard_key('gain_myopic')([b, a], ctx)) == [0, 1], (
        'the myopic arm cannot see predicted: no contention, gains tie, arrival order')
    no_pred = _ctx(_view(empties), _bundle())
    assert _seqs(yard_key('gain_forecast')([b, a], no_pred)) == [0, 1], (
        'forecast with an empty predicted tier degenerates to myopic exactly')


# ── 4. the urgency gate ───────────────────────────────────────────────────────────

def test_gate_serves_overdue_fifo_ahead_of_the_plan():
    cold, hot, view = _contention_pair()
    # cold arrived 2.0 yard-days ago (>= threshold 2.0 - horizon 0.5); hot just now.
    view = _view(dict(view.empties), frozen_at=2.0 * _DAY)
    cold.arrived_s, hot.arrived_s = 0.0, 2.0 * _DAY - 800.0
    bundle = _bundle(fee_threshold_days=2.0, urgency_horizon_days=0.5)
    assert _seqs(yard_key('gain_forecast')([cold, hot], _ctx(view, bundle))) == [1, 0], (
        'ungated, the plan serves the hot load first')
    assert _seqs(yard_key('gain_gated')([cold, hot], _ctx(view, bundle))) == [0, 1], (
        'gated, the overdue cold trailer jumps the plan — fee-days gate, never blend')


def test_gate_with_large_horizon_is_pure_fifo_and_stampless_is_never_urgent():
    cold, hot, view = _contention_pair()
    view = _view(dict(view.empties), frozen_at=2.0 * _DAY)
    cold.arrived_s, hot.arrived_s = 0.0, 2.0 * _DAY - 800.0
    # A stampless trailer (no clock reached the drain) accrues no yard days: with a
    # horizon that makes EVERY stamped trailer urgent, it alone falls to the plan.
    bare = _trailer(2, None, [_Unit(_Order(3, qty_rate=1.0), 1)])
    bundle = _bundle(fee_threshold_days=2.0, urgency_horizon_days=10.0)
    # Handed DELIBERATELY out of arrival order: the gate must sort by stamp.
    out = yard_key('gain_gated')([hot, bare, cold], _ctx(view, bundle))
    assert _seqs(out) == [0, 1, 2], (
        'horizon >= threshold makes every STAMPED trailer urgent (pure FIFO by '
        'stamp); the stampless one is never urgent and trails as the plan')


def test_gate_plan_prices_space_after_the_urgent_load_consumes():
    """The forced prefix must CONSUME: the urgent load takes the cheap bin, and the
    two plan trailers' relative gains flip — with the cheap bin still available the
    high-visits load wins on travel; with only the two near-height bins left, the
    heavy-handling load wins on the bracket step."""
    c = _Bin(0, 10.0, 0.0)          # cheap, ground level
    m = _Bin(0, 800.0, 95.0)        # below the 96in bracket (multiplier 1.0)
    f = _Bin(0, 800.0, 97.0)        # above it (1.2): ~same travel, pricier handling
    urgent = _trailer(0, 0.0, [_Unit(_Order(9, qty_rate=1.0), 1)])
    travel_heavy = _trailer(1, 2.0 * _DAY - 1000.0,
                            [_Unit(_Order(1, qty_rate=1.0, hvar=0.01), 50)])
    handling_heavy = _trailer(2, 2.0 * _DAY - 500.0,
                              [_Unit(_Order(2, qty_rate=0.1, hvar=40.0), 2)])
    bundle = _bundle(fee_threshold_days=2.0, urgency_horizon_days=0.5)
    plan_view = _view({_KEY_M: [c, m, f]}, frozen_at=2.0 * _DAY)
    ungated = yard_key('gain_forecast')([travel_heavy, handling_heavy],
                                        _ctx(plan_view, bundle))
    assert _seqs(ungated) == [1, 2], (
        'with the cheap bin in play, deferral costs travel and the 50-visit load '
        'loses the most by waiting')
    gated = yard_key('gain_gated')([urgent, travel_heavy, handling_heavy],
                                   _ctx(plan_view, bundle))
    assert _seqs(gated) == [0, 2, 1], (
        'the urgent load consumed the cheap bin, so the plan pair now contends over '
        'the bracket step and the heavy-handling load jumps — the prefix visibly '
        'consumed space')


# ── the futuresight window (the declared-unlawful reference, ticket 13) ───────────

def test_futuresight_refuses_a_missing_feed_and_accepts_an_empty_window():
    entry = yard_key('futuresight')
    t = _trailer(0, 0.0, [_Unit(_Order(1), 1)])
    ctx = _ctx(_view({_KEY_M: [_Bin(0, 10.0)]}), _bundle())      # window: None
    with pytest.raises(RuntimeError, match='window feed'):
        entry([t], ctx)
    empty = _ctx(_view({_KEY_M: [_Bin(0, 10.0)]}, window=()), _bundle())
    assert _seqs(entry([t], empty)) == [0], (
        'an empty window — a run at the end of its script — is legal, not an error')
    # And the hook latch: a pre-built evaluator carries its own pricing, so pairing
    # it with window_rates would silently drop the window — refused instead.
    view = _view({_KEY_M: [_Bin(0, 10.0)]})
    with pytest.raises(ValueError, match='window_rates'):
        plan_order([t], _bundle(), view, predicted=True,
                   window_rates={1: (1, 1)}, _ev=_Evaluator(_bundle(), view))


def test_futuresight_reads_the_window_and_forecast_does_not():
    """Two loads identical under the static rates (gains tie -> arrival order); the
    window says only B's SKU actually lands.  Futuresight prices A's load put-only,
    so B — whose deferral to the far bin costs a real visit — jumps the tie;
    gain_forecast, blind to the slot, keeps arrival order on the same ctx."""
    cheap, far = _Bin(0, 10.0), _Bin(0, 2000.0)
    a = _trailer(0, 0.0, [_Unit(_Order(1, qty_rate=1.0), 50)])
    b = _trailer(1, 50.0, [_Unit(_Order(2, qty_rate=1.0), 50)])
    ctx = _ctx(_view({_KEY_M: [cheap, far]}, window=({2: 1},)), _bundle())
    assert _seqs(yard_key('gain_forecast')([a, b], ctx)) == [0, 1], (
        'under the static rates the pair is identical: the tie keeps arrival order')
    assert _seqs(yard_key('futuresight')([a, b], ctx)) == [1, 0], (
        'realized knowledge breaks the tie: only B still carries pick work')


def test_a_sku_absent_from_the_window_pays_put_travel_only():
    """The window stands where the static rate stands: absent = not picked in the
    visible future, put still paid.  The static path on the SAME unit prices real
    visits — the guard that keeps this test from passing vacuously."""
    b = _Bin(0, 480.0, 96.0)
    unit = _Unit(_Order(1, qty_rate=5.0), 30)
    view = _view({_KEY_M: [b]})
    put_only = _PUT.x_pace * b.x_phys + _PUT.y_pace * b.y_phys
    cost, takes = _Evaluator(_bundle(), view, window_rates={9: (4, 2)}).place_load(
        [unit], set(), False)
    assert takes == [b]
    assert cost == pytest.approx(put_only)
    static_cost, _ = _Evaluator(_bundle(), view).place_load([unit], set(), False)
    assert static_cost > put_only, 'statically the unit IS picked — rates differ'


def test_window_visits_cap_at_the_event_count():
    """A unit cannot be visited more often than the window holds demand events for
    its SKU — the cap that makes w=inf honestly the oracle.  Both windows draw
    total/events = 1 per event, so the pick terms scale exactly with the cap."""
    b = _Bin(0, 480.0, 0.0)
    unit = _Unit(_Order(1, qty_rate=1.0), 30)
    view = _view({_KEY_M: [b]})
    put_only = _PUT.x_pace * b.x_phys + _PUT.y_pace * b.y_phys
    c2, _ = _Evaluator(_bundle(), view, window_rates={1: (2, 2)}).place_load(
        [unit], set(), False)
    c5, _ = _Evaluator(_bundle(), view, window_rates={1: (5, 5)}).place_load(
        [unit], set(), False)
    assert c2 < c5, 'fewer remaining demand events, less future pick work'
    assert (c2 - put_only) * 5 == pytest.approx((c5 - put_only) * 2), (
        'same per-visit price, visits 2 vs 5 — the cap binds, quantity/draw (30) '
        'does not')


def test_window_matching_the_static_rates_reproduces_forecast_exactly():
    """The window swaps KNOWLEDGE, not machinery: realized rates that agree with the
    static ones (per-event draw == quantity_rate, more events than any quantity can
    use) price every pair identically, so the plan IS gain_forecast's.  128 is a
    power of two, so total/events reproduces quantity_rate to the exact float.  And
    `_window_rates` aggregates (total, events) per SKU."""
    from Inbound.gain import _window_rates
    assert _window_rates(({1: 3, 2: 1}, {1: 2})) == {1: (5, 2), 2: (1, 1)}
    for seed in range(3):
        trailers, view = _random_scene(seed)
        orders = {it.unit.order for t in trailers for it in t.pending}
        wr = {o.sku: (o.demand.quantity_rate * 128.0, 128) for o in orders}
        bundle = _bundle()
        assert (_seqs(plan_order(trailers, bundle, view, predicted=True,
                                 window_rates=wr))
                == _seqs(plan_order(trailers, bundle, view, predicted=True))), (
            f'seed {seed}: matching rates must leave the plan untouched')


def test_futuresight_entry_with_matching_rates_is_forecast_through_the_seam():
    """The same invariance, through `yard_key('futuresight')` itself: a broken
    entry that ignored the window (or reordered wholesale) could still pass the
    tie-flip test, so this pins entry -> `_window_rates` -> evaluator end to end.
    Integer per-batch quantities, so 128 of them sum exactly back to the static
    rate."""
    cheap, far = _Bin(0, 10.0), _Bin(0, 2000.0)
    cold = _trailer(0, 0.0, [_Unit(_Order(1, qty_rate=1.0), 1)])
    hot = _trailer(1, 100.0, [_Unit(_Order(2, qty_rate=1.0), 50)])
    win = tuple({1: 1, 2: 1} for _ in range(128))
    ctx = _ctx(_view({_KEY_M: [cheap, far]}, window=win), _bundle())
    assert (_seqs(yard_key('futuresight')([cold, hot], ctx))
            == _seqs(yard_key('gain_forecast')([cold, hot], ctx)) == [1, 0]), (
        'a window whose realized rates equal the static ones must reproduce the '
        'forecast plan — nontrivially (contention decides, not arrival order)')


# ── exhaustion: the spill rule's two docstring claims, pinned directly ────────────

def test_seats_next_drain_but_not_now_defers_correctly():
    key2 = ('conveyable', 'general', 'medium', 'pallet')
    # X's key has NO empties, one predicted clear; Y's key has one empty, no predicted.
    x = _trailer(0, 0.0, [_Unit(_Order(1, qty_rate=1.0, category='general'), 5)])
    y = _trailer(1, 50.0, [_Unit(_Order(2, qty_rate=1.0), 5)])
    view = _view({_KEY_M: [_Bin(0, 500.0)]}, predicted={key2: [_Bin(1, 500.0)]})
    out = yard_key('gain_forecast')([x, y], _ctx(view, _bundle()))
    assert _seqs(out) == [1, 0], (
        'X seats nowhere NOW (penalty) but seats in its predicted clear on deferral '
        '(negative gain): it must defer behind Y, whose uncontended gain is zero')


def test_a_never_demanded_unit_pays_put_travel_only():
    """quantity_rate 0 means the unit is never picked: pick term exactly zero, put
    travel still paid — not quantity visits, the maximum possible reading."""
    b = _Bin(0, 480.0, 96.0)
    unit = _Unit(_Order(1, qty_rate=0.0), 30)
    cost, takes = _Evaluator(_bundle(), _view({_KEY_M: [b]})).place_load(
        [unit], set(), False)
    assert takes == [b]
    assert cost == pytest.approx(_PUT.x_pace * b.x_phys + _PUT.y_pace * b.y_phys)


def test_a_load_seatable_nowhere_prices_zero_and_cancels():
    order = _Order(1, qty_rate=1.0, category='void')   # a key no tier carries
    unit = _Unit(order, 5)
    view = _view({_KEY_M: [_Bin(0, 500.0)]})
    ev = _Evaluator(_bundle(), view)
    cost, takes = ev.place_load([unit], set(), False)
    assert cost == 0.0 and takes == [], (
        'no bin anywhere in the spill chain: the penalty must be exactly zero (a '
        'constant fallback would skew every gain it touches) — it cancels out')
    assert ev.unseated == 1, 'the exhaustion was still counted'


# ── 5. Tier-1: structured ≡ naive, and the sabotage assertion (06) ────────────────

def _naive_plan(candidates, bundle, space, predicted):
    """The rebuild-per-candidate reference: a FRESH evaluator (fresh sorts from the
    raw view) for every single virtual placement.  Same greedy, same leftover model."""
    taken: set = set()
    out, remaining = [], list(candidates)
    while remaining:
        swept, counts = [], {}
        for t in remaining:
            c, tk = _Evaluator(bundle, space).place_load(_load_units(t), taken, False)
            ids = set(map(id, tk))
            swept.append((t, c, tk, ids))
            for i in ids:
                counts[i] = counts.get(i, 0) + 1
        best = None
        for t, c, tk, ids in swept:
            others = {i for i, n in counts.items() if n > 1 or i not in ids}
            dc, _ = _Evaluator(bundle, space).place_load(
                _load_units(t), taken | others, predicted)
            g = dc - c
            if best is None or g > best[1]:
                best = (t, g, tk)
        t, _g, tk = best
        taken.update(map(id, tk))
        out.append(t)
        remaining = [r for r in remaining if r is not t]
    return out


def _random_scene(seed):
    rng = random.Random(seed)
    orders = [_Order(sku=i + 1, freq=rng.uniform(0.1, 1.0),
                     qty_rate=rng.uniform(0.5, 8.0), labor=rng.uniform(0.5, 3.0),
                     hvar=rng.uniform(0.1, 1.0)) for i in range(6)]
    med = [_Bin(rng.randrange(3), rng.uniform(0.0, 2400.0), 48.0 * rng.randrange(6))
           for _ in range(8)]
    lg = [_Bin(rng.randrange(3), rng.uniform(0.0, 2400.0), 48.0 * rng.randrange(6))
          for _ in range(4)]
    pred = [_Bin(rng.randrange(3), rng.uniform(0.0, 400.0)) for _ in range(3)]
    trailers = []
    for seq in range(5):
        units = [_Unit(rng.choice(orders), rng.randint(1, 40),
                       size=rng.choice(('medium', 'large')))
                 for _ in range(rng.randint(1, 6))]
        trailers.append(_trailer(seq, 100.0 * seq, units))
    return trailers, _view({_KEY_M: med, _KEY_L: lg}, predicted={_KEY_M: pred})


def test_structured_plan_equals_naive_rebuild_per_candidate():
    for seed in range(5):
        trailers, view = _random_scene(seed)
        bundle = _bundle()
        for predicted in (False, True):
            structured = plan_order(trailers, bundle, view, predicted=predicted)
            naive = _naive_plan(trailers, bundle, view, predicted)
            assert _seqs(structured) == _seqs(naive), (
                f'seed {seed} predicted={predicted}: sort-once/slice-under-'
                f'consumption diverged from the rebuild-per-candidate reference')


def test_sabotaged_sort_structure_is_caught():
    """Perturb the pre-sorted structure and assert the naive comparison FAILS — the
    equivalence test above can actually catch a wrong structure (06's sabotage pin)."""
    cold, hot, view = _contention_pair()
    bundle = _bundle()
    intact = plan_order([cold, hot], bundle, view, predicted=False)
    assert _seqs(intact) == _seqs(_naive_plan([cold, hot], bundle, view, False))
    ev = _Evaluator(bundle, view)
    ev.place_load(_load_units(cold), set(), False)          # warm the sorted array
    lst = ev._sorted_now[_KEY_M]
    lst[0], lst[1] = lst[1], lst[0]                          # the sabotage
    tampered = plan_order([cold, hot], bundle, view, predicted=False, _ev=ev)
    assert _seqs(tampered) != _seqs(intact), (
        'a perturbed sort order must change the plan, or the equivalence test is '
        'vacuous')


# ── 6. purity through the real seam, and the pool adapter ─────────────────────────

def test_entry_through_yard_order_is_pure_and_permutes():
    cold, hot, view = _contention_pair()
    tr = YardTransit(doors=2, yard_policy='gain_forecast', dock_policy='gain_forecast')
    tr.gain_bundle = _bundle()
    tr._yard.extend([cold, hot])
    ctx = tr.freeze_ctx()
    assert ctx.gain is tr.gain_bundle, 'freeze_ctx must deliver the injected bundle'
    ctx.space = view
    random.seed(4242)
    rng_before = random.getstate()
    empties_before = view.empties[_KEY_M]
    out = tr.yard_order(ctx)
    assert _seqs(out) == [1, 0]
    assert random.getstate() == rng_before, 'an ordering entry may consume no RNG'
    assert tr._yard == [cold, hot], 'the yard itself must not be reordered'
    assert view.empties[_KEY_M] is empties_before, 'the frozen view is read-only'
    # Same-drain consumption contract: the ranking is one call; re-ranking on the
    # same frozen ctx is deterministic.
    assert _seqs(tr.yard_order(ctx)) == [1, 0]


def _pool_bundle(orders, live_ass, live_ais, live_ads, expect=False):
    """(bundle, factory_calls, heads_calls) — the two counters guard against silent
    fallback to the merge adapter / a dead expectation branch (asserts inside an
    uncalled closure fail nothing)."""
    aff = SimpleNamespace(_sku_to_idx={})
    fbs = {o.sku: o.demand.relative_frequency for o in orders}
    qbs = {o.sku: o.demand.quantity_rate for o in orders}
    okey = lambda u: u.order.demand.relative_frequency * u.order.labor_cost
    factory_calls: list = []
    heads_calls: list = []

    def factory(cands, ass, ais, ads, wp_local):
        factory_calls.append(1)
        assert ass is not live_ass and ais is not live_ais and ads is not live_ads, (
            'the pool adapter must hand the pool COPIES of the aisle state')
        sel = (lambda head_D, head_bin: next(iter(head_bin))) if expect else None
        return af._RankedAssignPool(cands, aff, wp_local, ass, ais, ads,
                                    {}, fbs, qbs, 1.0, True,
                                    aisle_selector=sel, order_key=okey)

    def heads_of(pool):
        heads_calls.append(1)
        return pool._head_bin

    bundle = _bundle(pool_factory=factory, expect_heads=expect,
                     heads_of=heads_of if expect else None,
                     aisle_sku_sets=live_ass, aisle_idx_sets=live_ais,
                     aisle_demand_sum=live_ads)
    return bundle, factory_calls, heads_calls


def test_pool_adapter_agrees_on_contention_and_never_mutates_live_state():
    cold, hot, view = _contention_pair()
    orders = [cold.pending[0].unit.order, hot.pending[0].unit.order]
    live_ass, live_ais, live_ads = {0: {9}}, {0: {0}}, {0: 1.5}
    snap = (copy.deepcopy(live_ass), copy.deepcopy(live_ais), copy.deepcopy(live_ads))
    bundle, factory_calls, _ = _pool_bundle(orders, live_ass, live_ais, live_ads)
    out = yard_key('gain_myopic')([cold, hot], _ctx(view, bundle))
    assert factory_calls, (
        'pool_factory was never invoked — the pool path was not taken and every '
        'assert below would pass on the merge adapter')
    assert _seqs(out) == [1, 0], 'the pool adapter sees the same contention signal'
    assert (live_ass, live_ais, live_ads) == snap, (
        'a virtual placement advanced the LIVE aisle bookkeeping — purity broken')


def test_expectation_pricing_consumes_no_rng_and_permutes():
    trailers, view = _random_scene(3)
    orders = sorted({it.unit.order for t in trailers for it in t.pending},
                    key=lambda o: o.sku)
    bundle, factory_calls, heads_calls = _pool_bundle(orders, {}, {}, {}, expect=True)
    random.seed(99)
    before = random.getstate()
    out = plan_order(trailers, bundle, view, predicted=True)
    assert factory_calls and heads_calls, (
        'the expectation branch must actually consult the aisle heads — a dead '
        'branch would price at the chosen bin and still pass every other assert')
    assert random.getstate() == before, (
        'rank_random\'s virtual pool prices by expectation precisely so no RNG runs')
    assert sorted(_seqs(out)) == [0, 1, 2, 3, 4]
    assert _seqs(plan_order(trailers, bundle, view, predicted=True)) == _seqs(out), (
        'deterministic: same frozen inputs, same plan')


# ── the knobs ride inbound_spec, and 0.0 survives (never `or`-swallowed) ──────────

def test_spec_carries_the_gate_knobs_and_zero_survives(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    spec = inbound_spec()
    assert spec is not None, 'a named trailer type must yield a spec'
    assert spec['fee_threshold_days'] == 2.0, 'the stated placeholder default'
    assert spec['urgency_horizon_days'] == 0.0, 'the pure-gain pole default'
    monkeypatch.setitem(g, 'inbound_fee_threshold_days', 0.0)
    monkeypatch.setitem(g, 'inbound_urgency_horizon_days', 0.0)
    spec = inbound_spec()
    assert spec['fee_threshold_days'] == 0.0, (
        'a 0.0 threshold (overdue from arrival) is a legal sweep point — an `or` '
        'default would silently revert it to 2.0')
    assert spec['urgency_horizon_days'] == 0.0


def test_driver_bundle_carries_the_spec_knobs_and_refuses_unserved_arms():
    """The spec->bundle half of the knob ride (a config knob has five seams), plus
    the two loud refusals in `_gain_bundle_for`."""
    from Optimization.simdriver.strategy_runner import _gain_bundle_for
    strat = SimpleNamespace(restock='tmax', key='uni_tmax_norsl')
    mgr = SimpleNamespace(_zoning_enabled=False, _aisle_sku_sets={},
                          _aisle_idx_sets={}, _aisle_demand_sum={})
    spec = {'fee_threshold_days': 0.0, 'urgency_horizon_days': 0.0}
    bundle = _gain_bundle_for(strat, mgr, None, _WP, _PUT, spec)
    assert bundle.fee_threshold_days == 0.0, 'a swept 0.0 must survive this seam too'
    assert bundle.urgency_horizon_days == 0.0
    assert bundle.minimize is False and bundle.pool_factory is None, (
        'tmax rides the merge adapter, maximizing')
    with pytest.raises(ValueError, match='zoning'):
        _gain_bundle_for(strat, SimpleNamespace(_zoning_enabled=True), None,
                         _WP, _PUT, spec)
    with pytest.raises(ValueError, match='cluster_map'):
        _gain_bundle_for(SimpleNamespace(restock='cluster_map',
                                         key='uni_cluster_map_norsl'),
                         mgr, None, _WP, _PUT, spec)


def test_spec_carries_the_futuresight_knob_and_zero_and_all_survive(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    assert inbound_spec()['futuresight_batches'] is None, 'default: the knob is inert'
    monkeypatch.setitem(g, 'inbound_futuresight_batches', 0)
    assert inbound_spec()['futuresight_batches'] == 0, (
        'w=0 (a futuresight arm that sees nothing ahead) is a legal pole — an `or` '
        'default would swallow it')
    monkeypatch.setitem(g, 'inbound_futuresight_batches', 'all')
    assert inbound_spec()['futuresight_batches'] == 'all', (
        "the oracle sentinel survives the spec as the string 'all'")
    for bad in (-1, 2.5, 'oracle', True):
        monkeypatch.setitem(g, 'inbound_futuresight_batches', bad)
        with pytest.raises(ValueError, match='INBOUND_FUTURESIGHT_BATCHES'):
            inbound_spec()   # every bad shape raises the KNOB-NAMED error, not
        #                      whatever int() says — the signpost is the contract


def test_spec_refuses_standing_policies_without_the_standing_yard(monkeypatch):
    """The fake-arm hazard worn as configuration: the yard/dock knobs are UNREAD
    without the standing yard, so naming a policy there must fail at spec build —
    not complete as v1 fifo under the policy's name."""
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_yard_policy', 'futuresight')
    with pytest.raises(ValueError, match='UNREAD without INBOUND_STANDING_YARD'):
        inbound_spec()
    monkeypatch.setitem(g, 'inbound_yard_policy', 'fifo')
    monkeypatch.setitem(g, 'inbound_dock_policy', 'gain_forecast')
    with pytest.raises(ValueError, match='UNREAD without INBOUND_STANDING_YARD'):
        inbound_spec()


def test_driver_gate_refuses_futuresight_without_knob_or_script():
    """The startup half of refusal-until-clean: the arm never runs with an unstated
    window or an inline-sampled future (10 decision 6)."""
    from Optimization.simdriver.strategy_runner import _futuresight_window_w
    lawful = {'yard_policy': 'gain_forecast', 'dock_policy': 'fifo',
              'futuresight_batches': None}
    assert _futuresight_window_w(lawful, None) is None, (
        'no futuresight named: no gate, whatever the script situation')
    spec = {'yard_policy': 'futuresight', 'dock_policy': 'fifo',
            'futuresight_batches': None}
    with pytest.raises(ValueError, match='INBOUND_FUTURESIGHT_BATCHES'):
        _futuresight_window_w(spec, ['scripted'])
    spec['futuresight_batches'] = 3
    with pytest.raises(ValueError, match='precomputed batch script'):
        _futuresight_window_w(spec, None)
    assert _futuresight_window_w(spec, ['scripted']) == 3
    spec['futuresight_batches'] = 0
    assert _futuresight_window_w(spec, ['scripted']) == 0, (
        'w=0 is the legal blind pole: the gate tests `is None`, and a truthiness '
        'rewrite would refuse it with every other case green')
    other = {'yard_policy': 'fifo', 'dock_policy': 'futuresight',
             'futuresight_batches': 'all'}
    assert _futuresight_window_w(other, ['scripted']) == 'all', (
        'either registry knob names the arm; the oracle sentinel passes through')


def test_driver_window_slice_clamps_and_copies():
    """The feed half of the ticket, pinned at the slice that produces it: exact
    membership (an off-by-one would ship a subtly wrong future with no error), the
    n_batches clamp, the empty tail, and the shared-pickle copy rule."""
    from Optimization.simdriver.strategy_runner import _futuresight_window
    script = [SimpleNamespace(items={j: 10 + j}) for j in range(6)]
    assert _futuresight_window(script, 1, 2, n_batches=6) == ({2: 12}, {3: 13}), (
        'w=2 at batch 1 is EXACTLY batches 2 and 3 — never batch 1, never batch 4')
    assert _futuresight_window(script, 1, 'all', n_batches=5) == (
        {2: 12}, {3: 13}, {4: 14}), (
        "'all' reaches n_batches, not len(batches): past the run's end nothing "
        'releases, so the longer script must not leak in')
    assert _futuresight_window(script, 4, 3, n_batches=5) == (), (
        'the last released batch has nothing lawful ahead: an empty tail')
    assert _futuresight_window(script, 4, 'all', n_batches=5) == ()
    assert _futuresight_window(script, 2, 0, n_batches=6) == (), (
        'w=0 sees nothing ahead — an empty window, not an error')
    window = _futuresight_window(script, 0, 1, n_batches=6)
    window[0][1] = 999
    assert script[1].items == {1: 11}, (
        'the window must be COPIES: a reference would let downstream mutation '
        'corrupt the shared pickle every sibling arm reads')


# ── the bound composes bound-first with a gain entry (the 12 seam, real entry) ────

def test_bound_composes_bound_first_with_a_gain_entry():
    cold, hot, view = _contention_pair()
    entry = yard_key('gain_myopic')
    ctx = _ctx(view, _bundle())
    out = bounded_order([cold, hot], entry, ctx, bound=1)
    assert _seqs(out) == [0, 1], (
        'bound=1 windows the plan to the single longest-waiting trailer: strict '
        'arrival order, however the gains point')
