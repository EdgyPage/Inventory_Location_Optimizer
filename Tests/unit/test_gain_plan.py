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
  8. THE UNIFORM ADAPTER ("Give the fifo rider a faithful gain bundle", 21): `fifo`
     has no pool to rebuild, so it is priced by the EXACT expectation of its uniform
     draw — checked against brute-force enumeration, with the height moment averaged
     per bin rather than read at the mean height. Consumption is a seat COUNT, so
     capacity is the arm's whole lever: an uncontended tier ties every gain to zero
     (the honest degeneracy), an oversubscribed one is shared rather than handed to
     whoever swept first, and the sweep's allocator moves identities, never prices.
  7. THE FUTURESIGHT WINDOW ("Build the futuresight window feed", 13): the entry
     refuses a missing feed (None) and accepts an empty one (`()`); pricing swaps
     the static rates for the window's realized demand (absent = put only, visits
     capped at the event count), and rates that MATCH the static ones reproduce
     `gain_forecast` exactly — knowledge changed, machinery not; the driver's
     startup gate refuses the arm with the knob unset or the script missing.
  9. THE OWNER INDIRECTION ("Seat the one-owner bundle indirection", site-dock 13):
     the evaluator resolves its arm machinery through `for_key` and NEVER reads a
     bundle directly (a bare one is refused, not sniffed for); `OneOwnerBundle` hands
     back the wrapped INSTANCE for every key, which is what makes this byte-identical;
     and the cursor `_params` advances visits each BinKey group once, in group order,
     on a warm evaluator as well as a cold one — the seam the site dock's composite
     dispatches on.  The sabotage: answering one key with a different arm must move
     the priced hours, and move them by exactly that group's own delta.
 10. THE COMPOSITE AND ITS CLAIM ("Build the composite gain bundle", site-dock 26):
     `SiteGainBundle` resolves each group to its OWNING channel's whole arm bundle by
     the key's own regime (never `regime_of`, which answers 'store' for a fulfillment
     key and never raises), refuses a regime it holds no owner for, and refuses two
     owners who disagree on any SITE-wide field — the gate's two knobs, the one put
     crew's paces, the two pure lookups read before any group is keyed.  Then the
     charter's COMMENSURABILITY CLAIM in three parts: a mixed trailer decomposes
     exactly by owner; the exchange rate between a store hour and a fulfillment hour,
     RECOVERED from priced loads rather than assumed, is 1:1; and a planted per-channel
     weight makes that very check fail, at the planted rate.

Run:  python -m pytest Tests/unit/test_gain_plan.py -q
"""
from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from Inbound.gain import (
    GainBundle, OneOwnerBundle, SiteGainBundle, _Evaluator, _load_units, plan_order)
from Inbound.priorities import DockContext, bounded_order, dock_key, yard_key
from Inbound.space import SpaceView
from Inbound.trailer import Trailer, Trailer53
from Inbound.transit import YardTransit
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import StorageHandleConfig
from Warehouse.inventory.inventory_common import binkey_of, tier_ranks_for
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.kernel.regime import regime_of
from Warehouse.kernel.timeline import SECONDS_PER_DAY
from Warehouse.placement import Assignment_Functions as af

#: IMPORTED, not restated.  This line used to read `_DAY = 86400.0`, a third copy of the
#: divisor beside the gate's and the fee metric's -- so it moved WITH the gate instead of
#: pinning it, and the drift it existed to catch was invisible to it.
_DAY = SECONDS_PER_DAY
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
                 category='food', handling='conveyable'):
        self.sku = sku
        self.storage_handle_config = StorageHandleConfig(handling, category)
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


def _arm(**kw):
    """One leaf's raw `GainBundle` — what `_gain_bundle_for` returns."""
    kw.setdefault('put_speed', _PUT)
    kw.setdefault('wp_of', lambda u: _WP)
    kw.setdefault('binkey_of', binkey_of)
    kw.setdefault('tier_ranks_for', tier_ranks_for)
    return GainBundle(**kw)


def _bundle(**kw):
    """What the DRIVER injects and the evaluator resolves through: the one-owner
    provider over one arm's bundle (`strategy_runner` wraps at injection)."""
    return OneOwnerBundle(_arm(**kw))


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


# ── 4b. one knob, two readers: the gate and the fee metric (ticket 30) ────────────
#
# `settings.py` states a guarantee above INBOUND_FEE_THRESHOLD_DAYS — "one knob, two
# readers, so the gate and the metric can never disagree about overdue" — and until this
# ticket it was claimed by COMMENT alone, while each reader converted seconds to days from
# its own literal.  The two cannot import each other (`{forbid: [inbound, evaluations]}`),
# so nothing local could have caught a drift; these two tests are the enforcement.
#
# The span is handed to both readers directly, which is the only comparison that means
# anything: the gate measures `frozen_at - arrived` (drain-quantized, a trailer still
# standing) and the metric measures `emptied - arrived` (the whole detention), so a
# per-TRAILER agreement is not a thing that exists.  What must agree is the CONVERSION.

_GATE_THRESHOLD_DAYS = 2.0


def _gate_says_overdue(span_s, threshold_days=_GATE_THRESHOLD_DAYS):
    """Did `gain_gated` put a trailer of this detention in its urgent prefix?

    Read through the REAL entry rather than by restating its expression — a test that
    recomputes the predicate cannot fail when the predicate is wrong.  Two identical
    loads, so the plan ties and keeps the handed order (pinned above): handed
    `[fresh, aged]`, the aged one leads the output if and only if it was forced.
    """
    now = 10.0 * _DAY
    aged = _trailer(0, now - span_s, [_Unit(_Order(1), 4)])
    fresh = _trailer(1, now, [_Unit(_Order(1), 4)])
    view = _view({_KEY_M: [_Bin(0, 100.0), _Bin(1, 200.0)]}, frozen_at=now)
    bundle = _bundle(fee_threshold_days=threshold_days, urgency_horizon_days=0.0)
    out = yard_key('gain_gated')([fresh, aged], _ctx(view, bundle))
    return out[0].seq == 0


def _metric_says_over(span_s, threshold_days=_GATE_THRESHOLD_DAYS):
    """Did `frames._ydf` flag a trailer of this detention as over the threshold?"""
    from Optimization.Performance_Evaluations.common.frames import _ydf
    row = {'seq': 0, 'status': 'done', 'arrived_s': 0.0,
           'staged_s': 0.0, 'emptied_s': span_s}
    return bool(_ydf([row], run_end_s=span_s, threshold_days=threshold_days)
                .at[0, 'over_threshold'])


#: Spans in CALENDAR days against a 2.0-day threshold.  1.0 and 1.5 carry the test: each
#: is comfortably under the threshold on a calendar day and comfortably OVER it on the
#: 28,800 s site day, so either reader drifting to the wrong day flips it.  That 3x is the
#: live hazard, not a hypothetical — it is the defect inbound-optimization 29 spent a
#: session on, and 0.5 is deliberately NOT sensitive to it (0.5 x 3 is still under 2.0),
#: which is why the set needs the middle of the band rather than just its ends.
_SPANS_DAYS = (0.1, 0.5, 1.0, 1.5, 1.9, 2.5, 3.0, 7.0)


def test_the_gate_and_the_fee_metric_agree_about_overdue():
    for d in _SPANS_DAYS:
        span = d * _DAY
        assert _gate_says_overdue(span) == _metric_says_over(span), (
            f'a detention of {d} days reads overdue to one reader and not the other: '
            f'gate={_gate_says_overdue(span)}, metric={_metric_says_over(span)}. '
            f'This is the guarantee settings.py states over INBOUND_FEE_THRESHOLD_DAYS')


def test_the_two_readers_split_the_boundary_instant_and_that_is_recorded():
    """EXACTLY at the threshold they differ, and the asymmetry is deliberate-by-omission.

    The gate is `>= threshold` (already overdue jumps the plan) and the fee is
    `overage > 0`, i.e. `> threshold` (a trailer at exactly the free allowance has
    accrued nothing).  Both readings are right for their own job and neither is worth
    changing: the gate's would alter ARM BEHAVIOUR, which ticket 30 is a strict no-op.

    It is pinned rather than smoothed over because it is unreachable in practice for a
    reason a reader should not have to re-derive — the two never measure the same span
    (see the section note), and float equality on `end - arrived == threshold * 86400`
    is a coincidence the simulation has no way to produce.  If this test ever fails, the
    boundary moved; that is a real change, not a rounding one.
    """
    span = _GATE_THRESHOLD_DAYS * _DAY
    assert _gate_says_overdue(span) is True, 'the gate is >=: at the threshold, urgent'
    assert _metric_says_over(span) is False, 'the fee is >: at the threshold, no overage'


def test_a_drifted_divisor_breaks_the_correspondence(monkeypatch):
    """The sabotage half — without it the agreement test could be vacuously true.

    Moving the GATE's divisor to the site day is the exact drift the hoist prevents and
    the one nothing could previously catch, since the gate owned a private literal.
    """
    from Inbound import gain
    from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS
    monkeypatch.setattr(gain, 'SECONDS_PER_DAY', float(DEFAULT_SHIFT_SECONDS))
    broken = [d for d in _SPANS_DAYS
              if _gate_says_overdue(d * _DAY) != _metric_says_over(d * _DAY)]
    assert broken, (
        'the gate read the site day instead of the calendar day and NOTHING disagreed — '
        'then the agreement test above proves nothing.  The span set must keep values '
        'inside the 3x band (see _SPANS_DAYS)')
    assert 1.0 in broken and 1.5 in broken, (
        f'the two spans chosen to carry the 3x drift did not flip: broken={broken}')


def test_both_readers_resolve_to_the_one_kernel_declaration():
    """Identity, not equality: two modules that happen to agree today is the state this
    ticket found.  `is` is what makes a re-introduced literal fail here rather than in a
    campaign six weeks later."""
    from Inbound import gain
    from Optimization.Performance_Evaluations.common import units
    from Warehouse.kernel import timeline
    assert gain.SECONDS_PER_DAY is timeline.SECONDS_PER_DAY
    assert units.SECONDS_PER_DAY is timeline.SECONDS_PER_DAY
    assert timeline.SECONDS_PER_DAY == 3.0 * timeline.DEFAULT_SHIFT_SECONDS, (
        'the calendar day is three site days — the ambiguity the two declarations sit '
        'side by side to make visible')


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
    static ones (per-event draw == the stamped line law's MEAN, which is what the
    static branch reads since "Stamp the line distribution on the SKU"; more events
    than any quantity can use) price every pair identically, so the plan IS
    gain_forecast's.  128 is a power of two, so total/events reproduces the mean to
    the exact float.  And `_window_rates` aggregates (total, events) per SKU."""
    from Inbound.gain import _window_rates
    assert _window_rates(({1: 3, 2: 1}, {1: 2})) == {1: (5, 2), 2: (1, 1)}
    for seed in range(3):
        trailers, view = _random_scene(seed)
        orders = {it.unit.order for t in trailers for it in t.pending}
        wr = {o.sku: (o.demand.line.mean() * 128.0, 128) for o in orders}
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


# ── the coupled zip: 06's Tier-1 pair for the composed window (34) ────────────────

def _leaf_window_views(store_win, ful_win):
    """Two leaf views carrying only a window, and the site view `compose_site_view` makes
    of them.  `empties` is EMPTY on both deliberately: the composer's regime filter is
    `test_site_space_view.py`'s subject, and a pool that differed between the two sides
    would make a price difference here ambiguous.  Holding the pool fixed is what isolates
    the claim to the window."""
    from Inbound.site_space import compose_site_view

    def _leaf(win):
        return SpaceView(empties={}, emptied_at={}, predicted={}, released_at=0.0,
                         versions=(0, 0, 0), frozen_at=0.0, window=win)

    return compose_site_view([('store', _leaf(store_win)),
                              ('fulfillment', _leaf(ful_win))])


def test_the_composed_window_prices_exactly_as_each_leaf_window_did():
    """06's Tier-1 EQUIVALENCE for the zip, and it is the strong form rather than "the
    composed window has both leaves' keys": because the union is DISJOINT, a load reads
    only its own leaf's entries, so composing is a strict no-op against pricing each leaf's
    window alone — an exact float, not an approximation.

    Two-sided on purpose.  The failure the zip exists to prevent is one leaf's half being
    dropped, and a one-sided test would see that in only one of the two directions."""
    from Inbound.gain import _window_rates
    store_win = ({1: 3}, {1: 2})
    ful_win = ({2: 9}, {2: 4, 5: 1})
    site = _leaf_window_views(store_win, ful_win)
    assert _window_rates(site.window) == {**_window_rates(store_win),
                                          **_window_rates(ful_win)}, (
        'the aggregate a load prices against must be the two leaves\' aggregates, merged '
        'with neither side\'s totals or event counts disturbed')

    view = _view({_KEY_M: [_Bin(0, 480.0, 96.0)]})
    site_rates = _window_rates(site.window)
    for sku, own in ((1, store_win), (2, ful_win)):
        unit = _Unit(_Order(sku, qty_rate=5.0), 30)
        leaf_cost, leaf_takes = _Evaluator(
            _bundle(), view, window_rates=_window_rates(own)).place_load([unit], set(), False)
        site_cost, site_takes = _Evaluator(
            _bundle(), view, window_rates=site_rates).place_load([unit], set(), False)
        assert site_cost == leaf_cost and site_takes == leaf_takes, (
            f'sku {sku}: the composed window moved a price its own leaf\'s window set')


def test_dropping_one_leafs_half_of_the_window_moves_the_other_leafs_price():
    """06's Tier-1 SABOTAGE, and the non-vacuity guard the equivalence above needs: the
    prices it compares must be sensitive to the window at all.  Price the fulfillment load
    against STORE's window alone — the drop-the-other-half defect — and its SKU falls out
    of the aggregate, so it prices put-travel-only and the equality breaks."""
    from Inbound.gain import _window_rates
    store_win = ({1: 3}, {1: 2})
    ful_win = ({2: 9}, {2: 4, 5: 1})
    site = _leaf_window_views(store_win, ful_win)
    b = _Bin(0, 480.0, 96.0)
    view = _view({_KEY_M: [b]})
    unit = _Unit(_Order(2, qty_rate=5.0), 30)

    def _price(rates):
        return _Evaluator(_bundle(), view, window_rates=rates).place_load(
            [unit], set(), False)[0]

    put_only = _PUT.x_pace * b.x_phys + _PUT.y_pace * b.y_phys
    assert _price(_window_rates(store_win)) == pytest.approx(put_only), (
        'sku 2 is absent from store\'s window, so a half-composed feed prices it put-only')
    assert _price(_window_rates(site.window)) > put_only, (
        'under the zip it carries real pick work — which is what the equivalence above is '
        'asserting stayed identical, and what a dropped half would silently remove')
    static = _Evaluator(_bundle(), view).place_load([unit], set(), False)[0]
    assert _price(_window_rates(site.window)) != static, (
        'the composed window must actually be READ: equal to the static price and every '
        'assertion in this pair would hold with the window ignored entirely')


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
        alloc: dict = {}          # part of the plan's definition, not of the structure
        for t in remaining:
            c, tk = _Evaluator(bundle, space).place_load(_load_units(t), taken, False,
                                                         alloc=alloc)
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


@pytest.mark.parametrize('kind', ('merge', 'uniform'))
def test_structured_plan_equals_naive_rebuild_per_candidate(kind):
    for seed in range(5):
        trailers, view = _random_scene(seed)
        bundle = _bundle(uniform=(kind == 'uniform'))
        for predicted in (False, True):
            structured = plan_order(trailers, bundle, view, predicted=predicted)
            naive = _naive_plan(trailers, bundle, view, predicted)
            assert _seqs(structured) == _seqs(naive), (
                f'{kind} seed {seed} predicted={predicted}: the cached structure '
                f'(sorted arrays / tier moments) diverged from the rebuild-per-'
                f'candidate reference')


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


# ── 8. the uniform adapter: fifo's draw, priced exactly (ticket 21) ───────────────

def _price_pair(view, unit, bin_, key=_KEY_M):
    """One (unit, bin) price through the merge adapter's own pricing — the reference
    the uniform expectation is checked against, so nothing here is self-referential."""
    ref = _Evaluator(_bundle(), view)
    wp, xk, yk = ref._params(unit, key)
    return ref._pair_cost(unit, bin_, wp, xk, yk)


def test_the_uniform_price_is_the_exact_expectation_of_the_real_draw():
    """`_uniform_assignment` draws uniformly from the whole tier, so a load's cost is a
    random variable and the adapter must return its MEAN — exactly, not a stand-in
    bin's cost dressed up as one.  Checked against brute force: every injective
    assignment of the three units to the four bins, averaged."""
    from itertools import permutations

    from Warehouse.kernel.cost_model import height_multiplier
    bins = [_Bin(0, 120.0, 0.0), _Bin(0, 900.0, 48.0),
            _Bin(1, 1800.0, 96.0), _Bin(1, 2400.0, 144.0)]
    view = _view({_KEY_M: bins})
    units = [_Unit(_Order(1, qty_rate=2.0), 9), _Unit(_Order(2, qty_rate=0.5), 4),
             _Unit(_Order(3, qty_rate=4.0), 20)]
    assert len({height_multiplier(_WP.height_brackets, b.y_phys) for b in bins}) > 1, (
        'the tier must span more than one height bracket, or the mean multiplier is '
        'the same number however it is taken and this test cannot see the difference')

    got, takes = _Evaluator(_bundle(uniform=True), view).place_load(units, set(), False)
    assert len(takes) == 3, 'three units, three seats'
    perms = list(permutations(bins, 3))
    exact = sum(sum(_price_pair(view, u, b) for u, b in zip(units, perm))
                for perm in perms) / len(perms)
    assert got == pytest.approx(exact), (
        'the adapter is priced as an expectation; if it ever stops matching the '
        'brute-force average it has become an approximation under fifo\'s name')


def test_the_height_multiplier_is_averaged_per_bin_not_read_at_the_mean_height():
    """The one moment that is not a plain average of a coordinate.  `height_multiplier`
    is a STEP over brackets, so the mean of the steps and the step at the mean height
    are different numbers — and only the first is the expectation."""
    from Warehouse.kernel.cost_model import height_multiplier
    low, high = _Bin(0, 600.0, 0.0), _Bin(0, 600.0, 200.0)
    view = _view({_KEY_M: [low, high]})
    unit = _Unit(_Order(1, qty_rate=1.0), 12)
    mean_of_steps = 0.5 * (height_multiplier(_WP.height_brackets, 0.0)
                           + height_multiplier(_WP.height_brackets, 200.0))
    step_at_mean = height_multiplier(_WP.height_brackets, 100.0)
    assert mean_of_steps != step_at_mean, 'the premise: the brackets must actually step'
    got, _ = _Evaluator(_bundle(uniform=True), view).place_load([unit], set(), False)
    assert got == pytest.approx(0.5 * (_price_pair(view, unit, low)
                                       + _price_pair(view, unit, high)))
    assert got != pytest.approx(_price_pair(view, unit, _Bin(0, 600.0, 100.0))), (
        'reading the step at the mean height would be the cheap mistake here, and it '
        'is a different number — so the assertion above is not vacuous')


def test_uniform_consumption_is_a_count_not_a_set():
    """A uniform draw has no preference, so losing the cheapest bin costs exactly what
    losing the dearest costs: only the SEAT count moves.  Which is the whole mechanism
    an inbound policy has against this arm — take enough seats and the load spills."""
    near, mid, far = _Bin(0, 50.0), _Bin(0, 800.0), _Bin(1, 2400.0)
    big = _Bin(1, 40.0)
    view = _view({_KEY_M: [near, mid, far], _KEY_L: [big]})
    unit = _Unit(_Order(1, qty_rate=1.0), 8)
    assert _price_pair(view, unit, near) != pytest.approx(_price_pair(view, unit, far)), (
        'the premise: the two bins must cost visibly different amounts, or "the same '
        'price either way" is a statement about nothing')
    ev = _Evaluator(_bundle(uniform=True), view)
    drop_near, _ = ev.place_load([unit], {id(near)}, False)
    drop_far, _ = ev.place_load([unit], {id(far)}, False)
    assert drop_near == pytest.approx(drop_far), (
        'excluding the near bin and excluding the far one must cost the same — the '
        'price is the FULL tier\'s mean either way, because what another load '
        'consumed was a uniformly random subset'
    )
    spilled, takes = ev.place_load([unit], {id(near), id(mid), id(far)}, False)
    assert [id(b) for b in takes] == [id(big)], 'the tier is out of seats: spill up'
    assert spilled == pytest.approx(_price_pair(view, unit, big, key=_KEY_M))


def test_the_sweep_allocator_hands_each_candidate_its_own_bins():
    """Cost is identity-blind here, so the takes exist ONLY to feed the leftover model,
    which unions the OTHER candidates' takes.  Share one front-of-list block and that
    union collapses to a single load's worth — a whole yard's contention priced as one
    trailer's."""
    view = _view({_KEY_M: [_Bin(0, 100.0 * i) for i in range(1, 7)]})
    a = [_Unit(_Order(1, qty_rate=1.0), 5) for _ in range(2)]
    b = [_Unit(_Order(2, qty_rate=3.0), 7) for _ in range(2)]
    ev = _Evaluator(_bundle(uniform=True), view)
    alloc: dict = {}
    ca, ta = ev.place_load(a, set(), False, alloc=alloc)
    cb, tb = ev.place_load(b, set(), False, alloc=alloc)
    assert len(ta) == len(tb) == 2
    assert not ({id(x) for x in ta} & {id(x) for x in tb}), (
        'the shared allocator must hand out disjoint blocks')
    solo_a = ev.place_load(a, set(), False)[1]
    solo_b = ev.place_load(b, set(), False)[1]
    assert {id(x) for x in solo_a} == {id(x) for x in solo_b}, (
        'without the shared allocator both loads name the same bins — which is '
        'exactly the collapse the allocator exists to prevent')
    assert (ca, cb) == (ev.place_load(a, set(), False)[0],
                        ev.place_load(b, set(), False)[0]), (
        'the allocator moves identities, never prices')


def test_an_oversubscribed_tier_is_shared_rather_than_handed_to_whoever_swept_first():
    """Two loads, two bins, two units each: whoever defers finds nothing, so the load
    with more to lose must go first REGARDLESS of where it sits in the sweep.  A
    truncating allocator would leave the second candidate empty-handed and the first
    with an untouched tier, and the answer would turn on sweep position instead."""
    def scene(hot_seq):
        view = _view({_KEY_M: [_Bin(0, 100.0), _Bin(0, 2000.0)]})
        cold = _trailer(1 - hot_seq, 0.0,
                        [_Unit(_Order(1, qty_rate=1.0), 1) for _ in range(2)])
        hot = _trailer(hot_seq, 0.0,
                       [_Unit(_Order(2, qty_rate=1.0), 60) for _ in range(2)])
        pair = [hot, cold] if hot_seq == 0 else [cold, hot]
        return _seqs(yard_key('gain_myopic')(pair, _ctx(view, _bundle(uniform=True))))

    assert scene(0) == [0, 1], 'the hot load is swept first and must still win'
    assert scene(1) == [1, 0], 'and it wins from second place too'


def test_an_uncontended_uniform_tier_gives_the_myopic_arm_nothing():
    """The finding this adapter forced into the open, pinned so nobody 'fixes' it.
    Uniform placement has no preference over bins, so with seats for everyone the
    myopic gains are EXACTLY zero and the plan is arrival order.  That is a property
    of the arm — it is why `fifo` is the order-blind control — and not an inert
    evaluator: the same scene under a ranked bundle reorders."""
    cold, hot, view = _contention_pair()
    assert _seqs(yard_key('gain_myopic')([cold, hot], _ctx(view, _bundle()))) == [1, 0]
    for pair in ([cold, hot], [hot, cold]):
        out = yard_key('gain_myopic')(pair, _ctx(view, _bundle(uniform=True)))
        assert _seqs(out) == _seqs(pair), (
            'every gain is zero, so the plan is whatever order it was handed — and '
            'reversing the input reverses the plan, which a fixed answer would not')


def test_uniform_forecast_reads_the_predicted_tier_and_a_sabotaged_moment_is_caught():
    """Contention is not the only lever the adapter has: the deferral side draws from a
    DIFFERENT pool, so the blend of empties and predicted clears moves the gain.  The
    second half is 06's Tier-1 sabotage for this adapter — perturb the cached predicted
    moments and the plan must change, or the equivalence check above is vacuous."""
    key2 = ('conveyable', 'general', 'medium', 'pallet')
    m1, m2, p = _Bin(0, 500.0), _Bin(1, 500.0), _Bin(1, 5.0)
    b = _trailer(0, 0.0, [_Unit(_Order(2, qty_rate=1.0, category='general'), 10)])
    a = _trailer(1, 50.0, [_Unit(_Order(1, qty_rate=1.0), 10)])
    view = _view({_KEY_M: [m1], key2: [m2]}, predicted={key2: [p]})
    bundle = _bundle(uniform=True)
    assert _seqs(plan_order([b, a], bundle, view, predicted=True)) == [1, 0], (
        'B defers into a much cheaper predicted clear, so A is served first')
    assert _seqs(plan_order([b, a], bundle, view, predicted=False)) == [0, 1], (
        'the myopic arm cannot see it: gains tie, arrival order')
    ev = _Evaluator(bundle, view)
    ev.place_load(_load_units(b), set(), True)               # warm the moment cache
    ck = (key2, True, _WP.height_brackets)
    assert ck in ev._mom, 'the predicted moments must be the thing being cached'
    ev._mom[ck] = (9000.0, 0.0, 1.0)                          # the sabotage: make it dear
    assert _seqs(plan_order([b, a], bundle, view, predicted=True, _ev=ev)) == [0, 1], (
        'a perturbed tier moment must change the plan, or nothing pins the moments')


def test_the_rider_plans_a_real_drain_through_the_driver_bundle(monkeypatch):
    """The blocker itself, closed end to end rather than at the seam it was found at.

    Everything above prices stub units against stub bins; this runs a REAL
    `Inventory_Manager` drain under `gain_myopic`, with the bundle the DRIVER builds
    from the real `uni_fifo_norsl` strategy — the closures over `_wp_for`,
    `_binkey_of` and `_tier_ranks_for`, and the manager's own aisle bookkeeping.  The
    ticket-21 chain (`_gain_bundle_for` is called for every arm in a gain cell's set,
    and `fifo` was not in the faithful set) died at worker startup, so a test that
    stops at the bundle would not have noticed the drain.

    The harness is `test_standing_yard`'s, imported rather than copied: the point is
    to run the same drain those tests run, not a lookalike."""
    from Inbound.space import SpaceTimeline
    from Inbound.trailer import POSITION_VOLUME, Trailer28
    from Optimization.config.strategies import STRATEGY_BY_KEY
    from Optimization.simdriver.strategy_runner import _gain_bundle_for
    from Warehouse.picking.Workload_Builder import drain_sku
    from Tests.unit.test_standing_yard import _dispatch, _drain, _manager

    tr = YardTransit(Trailer28, lead_s=0.0, doors=1, allocation='merged',
                     yard_policy='gain_myopic', dock_policy='gain_myopic')
    mgr = _manager(tr, crew=2)
    SpaceTimeline(drain_sku).attach(mgr)
    _arm_bundle = _gain_bundle_for(
        STRATEGY_BY_KEY['uni_fifo_norsl'], mgr, None, _WP, _PUT,
        {'fee_threshold_days': 2.0, 'urgency_horizon_days': 0.0})
    assert _arm_bundle.uniform, 'the rider must arrive on the uniform adapter'
    # Wrapped exactly as the driver wraps it at injection (`strategy_runner`): the
    # evaluator resolves per owner, and this leaf has one.
    tr.gain_bundle = OneOwnerBundle(_arm_bundle)

    # One door and three reorders' worth of freight, so the yard is genuinely deep and
    # the plan is a choice rather than a formality.  The counter is the guard: a drain
    # that ranked a one-trailer yard would satisfy every assert below while proving
    # nothing about ordering.
    ranked: list = []
    real_plan = plan_order

    def _spy(candidates, *a, **kw):
        ranked.append(len(candidates))
        return real_plan(candidates, *a, **kw)
    monkeypatch.setattr('Inbound.gain.plan_order', _spy)

    epoch = 10_000.0
    for sku, qty in ((101, 40), (102, 25), (103, 60)):
        _dispatch(mgr, sku, qty, POSITION_VOLUME, epoch)
    _drain(mgr, epoch)
    assert max(ranked, default=0) > 1, (
        'the gain plan never ordered more than one standing trailer — the uniform '
        'adapter was exercised on a yard with no decision in it')
    placed = {i.unit.order.sku for i in mgr._stock_queue}
    assert placed == {101, 102, 103}, (
        'every dispatched SKU must reach the put queue through the planned unload; '
        f'got {sorted(placed)}')


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


# ── 9. the owner indirection: one path, and the cursor it will dispatch on ────────

class _SpyProvider:
    """A provider that RECORDS every key the evaluator resolves with, and can answer
    one chosen key with a different arm (the sabotage below)."""

    def __init__(self, bundle, swap_key=None, swap_bundle=None):
        self.bundle, self.keys = bundle, []
        self.swap_key, self.swap_bundle = swap_key, swap_bundle

    def for_key(self, key):
        self.keys.append(key)
        if self.swap_key is not None and key == self.swap_key:
            return self.swap_bundle
        return self.bundle

    @property
    def fee_threshold_days(self):
        return self.bundle.fee_threshold_days

    @property
    def urgency_horizon_days(self):
        return self.bundle.urgency_horizon_days


def _mixed_load():
    """One load whose units span three BinKeys — so the groups are several and the
    cursor has somewhere to move.  Two of them share a spill chain (medium spills up
    to large within conveyable/food/pallet), which is the case the class docstring's
    safety argument is about."""
    m1 = _Unit(_Order(1, freq=2.0, qty_rate=2.0), 3)                     # _KEY_M
    m2 = _Unit(_Order(2, freq=1.0, qty_rate=1.0), 4)                     # _KEY_M
    lg = _Unit(_Order(3, freq=1.5, qty_rate=2.0), 5, size='large')       # _KEY_L
    sg = _Unit(_Order(4, freq=0.5, qty_rate=1.0), 2, category='singleton')
    view = _view({_KEY_M: [_Bin(0, 20.0), _Bin(1, 60.0)],
                  _KEY_L: [_Bin(2, 40.0, 48.0), _Bin(3, 300.0)],
                  ('conveyable', 'food', 'medium', 'singleton'): [_Bin(4, 15.0)]})
    return [m1, lg, m2, sg], view


def test_one_owner_hands_back_the_same_instance_for_every_key():
    """The whole byte-identity argument: the evaluator reads the object the driver
    built, not a copy or a rebuild.  Identity, never equality."""
    arm = _arm()
    prov = OneOwnerBundle(arm)
    for key in (None, _KEY_M, _KEY_L, ('non_conveyable', 'bulk', 'large', 'pallet'),
                'not even a key'):
        assert prov.for_key(key) is arm, (
            f'for_key({key!r}) must return the wrapped instance itself — a copy would '
            f'break byte-identity silently, and an equal-but-other object would break '
            f'the `taken` bookkeeping that is keyed on bin identity')
    assert prov.fee_threshold_days == arm.fee_threshold_days
    assert prov.urgency_horizon_days == arm.urgency_horizon_days, (
        'the gate reads its two days-denominated knobs off the PROVIDER: it composes '
        'hours and days above any one owner and has no BinKey to resolve with')


def test_a_bare_bundle_is_refused_rather_than_sniffed_for():
    """One path (05 decision 3): there is no branch that reads a bundle directly, so a
    mis-wired driver fails at the seam instead of deep in pricing."""
    _u, view = _mixed_load()
    with pytest.raises(TypeError, match='for_key'):
        _Evaluator(_arm(), view)
    # A second wrap would resolve to itself and price every owner with whatever the
    # outer lookup returned — refused at construction, not discovered in a price.
    with pytest.raises(TypeError, match='OneOwnerBundle'):
        OneOwnerBundle(OneOwnerBundle(_arm()))


def test_the_cursor_tracks_the_group_being_priced():
    """`_params` advances the cursor once per BinKey group, before the adapter branch —
    so a per-owner provider is asked about exactly the keys `place_load` grouped by,
    in group order.  This is the seam the site composite dispatches on; without it
    wired, the composite would price both leaves with one arm."""
    units, view = _mixed_load()
    prov = _SpyProvider(_arm())
    ev = _Evaluator(prov, view)
    assert ev._key is None, 'no group keyed yet'
    ev.place_load(units, set(), False)

    groups = []
    for u in units:                      # place_load's own grouping, in load order
        k = binkey_of(u)
        if k not in groups:
            groups.append(k)
    assert len(groups) == 3, 'the fixture must actually span three BinKeys'
    # Every key the evaluator resolved with, after the pre-cursor grouping reads.
    resolved = [k for k in prov.keys if k is not None]
    assert [k for i, k in enumerate(resolved) if i == 0 or k != resolved[i - 1]] \
        == groups, (
        'the cursor must visit each group exactly once, in group order — a cursor set '
        'inside the wp memo would stick on the first key, and one set after the '
        'adapter branch would price a group with its predecessor\'s arm')
    assert ev._key == groups[-1], 'the cursor is left on the last group priced'
    assert prov.keys[0] is None, (
        'the grouping itself reads binkey_of before any group is keyed — the three '
        'site-wide fields must be answerable at a None cursor')

    # AGAIN on the same evaluator, which is what `plan_order` does: one evaluator
    # prices every candidate every round.  By now `_wp` is warm for all three keys, so
    # a cursor that rode inside that memo would advance on NONE of them and the whole
    # load would price against whichever group was keyed last.  (Mutation-checked: it
    # is the one mutant a single-call version of this test let through.)
    prov.keys.clear()
    ev.place_load(units, set(), False)
    again = [k for i, k in enumerate(prov.keys) if i == 0 or k != prov.keys[i - 1]]
    assert again[0] == groups[-1], (
        'this call\'s grouping reads binkey_of with the PREVIOUS call\'s cursor still '
        'standing — harmless (binkey_of is site-wide) and stated here so the tail '
        'assertion below is not read as sloppiness')
    assert again[-len(groups):] == groups, (
        'the cursor must advance on a warm evaluator too — it is set ahead of the wp '
        'memo, unconditionally, precisely so a memo hit cannot skip it')


def test_a_provider_that_answers_one_key_differently_moves_the_price():
    """The sabotage (memory `real-test-coverage-is-317`): the assertions above are
    worth nothing unless a wrong resolution can be seen.  Swap ONE key's arm and the
    priced hours must move — which is also the positive statement that the composite
    has a real lever here."""
    units, view = _mixed_load()
    base, _tk = _Evaluator(OneOwnerBundle(_arm()), view).place_load(units, set(), False)
    swapped, _tk2 = _Evaluator(
        _SpyProvider(_arm(), swap_key=_KEY_L, swap_bundle=_arm(minimize=False)),
        view).place_load(units, set(), False)
    assert abs(swapped - base) > 1e-9, (
        'answering _KEY_L with a tmax arm must reprice that group (its two bins sit '
        '40 vs 300 ft out, so the extremal direction is visible); if this ties, the '
        'cursor is not reaching the adapter and the two tests above are vacuous')
    # ...and the OTHER groups are untouched: no cross-talk between owners.
    only_l = [u for u in units if binkey_of(u) == _KEY_L]
    l_base, _ = _Evaluator(OneOwnerBundle(_arm()), view).place_load(only_l, set(), False)
    l_swap, _ = _Evaluator(OneOwnerBundle(_arm(minimize=False)), view).place_load(
        only_l, set(), False)
    assert abs((swapped - base) - (l_swap - l_base)) < 1e-9, (
        'the whole move must come from the swapped group alone — the caches are '
        'BinKey-keyed, so one owner\'s arm cannot leak into another\'s pricing')


# ── 10. the site composite, and the commensurability claim (site-dock 05/26) ──────
#
# `SiteGainBundle` is the second owner the indirection above was seated for: a mixed
# trailer's store units priced by the store arm's pool and its fulfillment units by the
# fulfillment arm's, summed to ONE trailer score in hours.  Two things are pinned here and
# they are different in kind:
#
#   * the DISPATCH — the key decides, the owners are whole bundles, and every site-wide
#     field is refused rather than resolved when two owners disagree (site-dock 13's
#     stated obligation);
#   * the COMMENSURABILITY CLAIM — "a fulfillment hour and a store hour are worth the same
#     to the site".  It is the charter's, and it is stated so it can be falsified: the
#     exchange rate is RECOVERED from priced loads rather than asserted, and a planted
#     per-channel weight must make the recovery fail.  Memory `a-count-is-not-a-claim` is
#     why it is a rate and not a sum, and `real-test-coverage-is-317` is why part 3 exists.

#: One fulfillment tier and the tier it spills up into (`tier_ranks_for('fulfillment')`).
#: A real fulfillment order carries handling AND category 'fulfillment', which is what
#: makes the key's regime readable from any of its three regime-bearing slots.
_KEY_F = ('fulfillment', 'fulfillment', 'ff_medium', 'fulfillment')
_KEY_FL = ('fulfillment', 'fulfillment', 'ff_large', 'fulfillment')

#: The fulfillment channel's own workload params — a DIFFERENT pick cost and different
#: travel speeds, deliberately.  The two regimes really do price picks differently
#: (`wp.by_regime`), and commensurability does not claim otherwise: it claims both are
#: seconds of the SHARED model, scalarized with one divisor rather than two.  A fixture
#: where the two channels priced identically could not tell those two claims apart.
_WP_F = WorkloadParams(x_speed=3.0, y_speed=1.5, pick_intercept=9.0)


def _store_unit(sku, qty=3, freq=1.0, rate=1.0):
    return _Unit(_Order(sku, freq=freq, qty_rate=rate), qty)


def _ful_unit(sku, qty=3, freq=1.0, rate=1.0):
    return _Unit(_Order(sku, freq=freq, qty_rate=rate,
                        category='fulfillment', handling='fulfillment'),
                 qty, size='ff_medium', category='fulfillment')


def _site_view():
    """One site space view: store bins under the store keys, fulfillment bins under the
    fulfillment keys.  The key set is PARTITIONED BY REGIME, which is exactly what
    `Inbound.site_space.compose_site_view` produces for a coupled drain — so the geometry
    here is the geometry the composite will really read."""
    return _view({_KEY_M: [_Bin(0, 20.0), _Bin(1, 60.0), _Bin(2, 90.0)],
                  _KEY_L: [_Bin(3, 40.0, 48.0), _Bin(4, 300.0)],
                  _KEY_F: [_Bin(5, 30.0), _Bin(6, 75.0, 24.0), _Bin(7, 120.0)],
                  _KEY_FL: [_Bin(8, 50.0, 12.0)]})


def _store_arm():
    """The store leaf's arm: `tmin`, the k-cheapest MERGE adapter."""
    return _arm()


def _ful_arm():
    """The fulfillment leaf's arm: `fifo`, the UNIFORM adapter — a different code path,
    not merely different data.  05's prototype ran store on a merge adapter and
    fulfillment on a uniform one for this reason: the composite must swap the CODE PATH
    mid-trailer, which is what a per-unit owner lookup is FOR."""
    return _arm(uniform=True, wp_of=lambda u: _WP_F)


def _site_bundle(store=None, ful=None):
    b = SiteGainBundle()
    b.bind('store', store if store is not None else _store_arm())
    b.bind('fulfillment', ful if ful is not None else _ful_arm())
    return b


def _priced(units, provider, view, evaluator=_Evaluator):
    """The hours one placement of `units` costs, through `provider`."""
    cost, _takes = evaluator(provider, view).place_load(units, set(), False)
    return cost


# ── the dispatch ──────────────────────────────────────────────────────────────────

def test_the_site_bundle_hands_each_key_its_own_channels_arm():
    """Identity, never equality — the same rule `OneOwnerBundle` is held to, for the same
    reason: the evaluator must read the object `_gain_bundle_for` built for that leaf."""
    store, ful = _store_arm(), _ful_arm()
    site = _site_bundle(store, ful)
    assert site.owners == ('store', 'fulfillment'), 'bind order is the declared order'
    for key in (_KEY_M, _KEY_L, ('conveyable', 'food', 'medium', 'singleton')):
        assert site.for_key(key) is store, f'{key!r} is store merchandise'
    for key in (_KEY_F, _KEY_FL):
        assert site.for_key(key) is ful, f'{key!r} is fulfillment merchandise'
    assert site.for_key(None) is store, (
        'the pre-cursor read (`place_load` keys its groups before resolving any owner) '
        'is answered with the FIRST-BOUND owner — lawful only because bind refuses '
        'owners whose site-wide fields differ')


def test_the_key_decides_and_regime_of_would_get_it_wrong():
    """The trap under the whole dispatch, pinned in both directions.

    A `BinKey` is a plain tuple, so every getattr in `regime_of` falls through it.  It used
    to answer 'store' for a fulfillment key SILENTLY, always in the same direction; since
    site-dock 24 it RAISES instead, which is the same claim made loud.  `for_key` must use
    `regime_of_key`; if it ever reverts, every fulfillment unit on a mixed trailer would
    now take the arm down rather than being priced by the store arm in silence."""
    from Warehouse.kernel.regime import regime_of, regime_of_key
    with pytest.raises(TypeError, match='regime_of_key'):
        regime_of(_KEY_F)
    assert regime_of(_ful_unit(sku=9001)) == 'fulfillment', (
        'the entity form still answers — the refusal is about TUPLES, not about '
        'fulfillment')
    assert regime_of_key(_KEY_F) == 'fulfillment'
    assert regime_of_key(_KEY_M) == 'store'
    ful = _ful_arm()
    assert _site_bundle(ful=ful).for_key(_KEY_F) is ful, (
        'the fulfillment key must reach the fulfillment arm — via the KEY, because the '
        'object form cannot read a key and does not refuse one')


def test_a_regime_with_no_owner_refuses_rather_than_borrowing_the_other_arm():
    site = SiteGainBundle()
    site.bind('store', _store_arm())
    with pytest.raises(ValueError, match='fulfillment'):
        site.for_key(_KEY_F)
    empty = SiteGainBundle()
    with pytest.raises(ValueError, match='no owner bound'):
        empty.for_key(None)


def test_the_site_bundle_refuses_a_provider_a_non_regime_and_a_second_claim():
    site = SiteGainBundle()
    with pytest.raises(TypeError, match='GainBundle'):
        site.bind('store', OneOwnerBundle(_store_arm()))   # a provider, not a bundle
    with pytest.raises(ValueError, match='storage regime'):
        site.bind('warehouse', _store_arm())
    site.bind('store', _store_arm())
    with pytest.raises(ValueError, match='already bound'):
        site.bind('store', _store_arm())


def test_the_owners_must_agree_on_every_site_wide_field():
    """Site-dock 13's stated obligation, discharged.  These five fields are the SITE's:
    the gate's two knobs (it composes hours and days above any owner and has no BinKey to
    resolve with), the one put crew's paces, and the two pure lookups the evaluator reads
    before any group is keyed.  All come from one `inbound_spec()` and one `put_crew`
    record, so they cannot differ on a lawful run — which is exactly why a silent
    first-wins would never be noticed."""
    for kw in ({'fee_threshold_days': 3.0}, {'urgency_horizon_days': 1.0},
               {'put_speed': SpeedProfile(2.0, 9.0)},
               {'binkey_of': lambda o: binkey_of(o)},
               {'tier_ranks_for': lambda c: tier_ranks_for(c)}):
        site = SiteGainBundle()
        site.bind('store', _store_arm())
        with pytest.raises(ValueError, match='disagree on'):
            site.bind('fulfillment', _arm(uniform=True, wp_of=lambda u: _WP_F, **kw))
    # ...and the lawful case is ACCEPTED: the driver builds a SpeedProfile per leaf from
    # one payload record, so two equal-valued instances are what a coupled run really
    # produces.  An identity test on `put_speed` would refuse every one of them.
    site = SiteGainBundle()
    site.bind('store', _arm(put_speed=SpeedProfile(2.0, 4.0)))
    site.bind('fulfillment', _arm(uniform=True, wp_of=lambda u: _WP_F,
                                  put_speed=SpeedProfile(2.0, 4.0)))
    assert site.fee_threshold_days == _store_arm().fee_threshold_days
    assert site.urgency_horizon_days == _store_arm().urgency_horizon_days, (
        'the gate reads its knobs off the PROVIDER, which forwards the value bind has '
        'proven every owner shares')


def test_the_composite_swaps_the_code_path_mid_trailer():
    """Not merely different data: the store group runs the merge adapter and the
    fulfillment group the uniform one, inside ONE `place_load`.  The non-vacuity guard is
    the comparison against pricing the whole mixed load with either arm alone — if the
    composite tied with those, the dispatch would be doing nothing."""
    store, ful = _store_arm(), _ful_arm()
    assert store.uniform is False and ful.uniform is True, (
        'the fixture must span two adapters, or "the composite swaps the code path" is '
        'untested here')
    view = _site_view()
    units = [_store_unit(1, 4), _ful_unit(201, 3), _store_unit(2, 2), _ful_unit(202, 5)]
    mixed = _priced(units, _site_bundle(store, ful), view)
    assert abs(mixed - _priced(units, OneOwnerBundle(store), view)) > 1e-9, (
        'pricing the whole mixed load with the store arm alone must differ — otherwise '
        'the fulfillment half is not reaching its own arm')
    assert abs(mixed - _priced(units, OneOwnerBundle(ful), view)) > 1e-9


# ── commensurability: the rate is RECOVERED, and a weight must break the recovery ──

#: The two mixes the exchange rate is solved from.  Deliberately LOPSIDED in opposite
#: directions, because a 2x2 solve needs two linearly independent rows: two mixes with the
#: same store/fulfillment ratio would leave the system singular and `a = b = 1` would be
#: one of infinitely many answers.  The units also INTERLEAVE, so `place_load`'s groups
#: alternate owners and the cursor really does move back and forth mid-load.
def _mixes():
    return ([_store_unit(1, 5, freq=2.0), _ful_unit(201, 2),
             _store_unit(2, 4, rate=2.0), _store_unit(3, 3)],
            [_ful_unit(202, 5, freq=2.0), _store_unit(4, 2),
             _ful_unit(203, 4, rate=2.0), _ful_unit(205, 3)])


def _rows(evaluator, store, ful, view):
    """Per mix: `(store hours alone, fulfillment hours alone, the site's trailer score)`.

    The two reference hours are priced through each arm's OWN `OneOwnerBundle` over that
    channel's units — the unweighted, faithful-to-arm truth, which is the whole point: if
    the references came through the composite too, any coefficient inside it would cancel
    and the recovery below would report 1.0 whatever the code did (memory
    `a-count-is-not-a-claim`, the same shape one level up)."""
    out = []
    for units in _mixes():
        s = [u for u in units if regime_of(u) == 'store']
        f = [u for u in units if regime_of(u) == 'fulfillment']
        assert s and f, 'every mix must be genuinely mixed'
        out.append((_priced(s, OneOwnerBundle(store), view),
                    _priced(f, OneOwnerBundle(ful), view),
                    _priced(units, _site_bundle(store, ful), view, evaluator)))
    return out


def _recover(evaluator=_Evaluator):
    """`(a, b)` solving `site score = a x store hours + b x fulfillment hours` over the
    two mixes, with the system's own well-posedness asserted first."""
    store, ful, view = _store_arm(), _ful_arm(), _site_view()
    (s1, f1, m1), (s2, f2, m2) = _rows(evaluator, store, ful, view)
    det = s1 * f2 - f1 * s2
    assert abs(det) > 1e-6 * max(s1 * f2, f1 * s2), (
        f'the two mixes are collinear (det={det}) — the exchange rate would be '
        f'unidentifiable and `a = b = 1` would be one answer among infinitely many')
    return ((m1 * f2 - f1 * m2) / det, (s1 * m2 - m1 * s2) / det)


def _assert_commensurable(evaluator=_Evaluator):
    """Parts 1 and 2 of the three-part test, as ONE callable — so part 3 can assert that a
    planted per-channel weight makes this very check fail, rather than asserting something
    adjacent to it."""
    store, ful, view = _store_arm(), _ful_arm(), _site_view()
    for s, f, m in _rows(evaluator, store, ful, view):
        # PART 1, the precondition: a mixed trailer decomposes EXACTLY by owner.  BinKeys
        # partition bins by regime, so the two owners never contend — there is no bin both
        # halves could want — and without that exactness the exchange rate below is not a
        # well-defined quantity at all.  A tolerance, never `==`: the groups sum in a
        # different order in the mixed load than they do apart.
        assert abs(m - (s + f)) <= 1e-9 * max(1.0, abs(m)), (
            f'the mixed score {m!r} is not its two halves {s!r} + {f!r}: the owners are '
            f'contending for bins, so "hours per channel" is not even separable')
    # PART 2: the rate the site actually applies, recovered rather than assumed.
    a, b = _recover(evaluator)
    assert abs(a - 1.0) <= 1e-9 and abs(b - 1.0) <= 1e-9, (
        f'the site weights a store hour {a!r} and a fulfillment hour {b!r}; the objective '
        f'is put + pick hours from the SHARED cost model with no per-channel weighting, '
        f'so both must be 1 — the charter\'s commensurability claim, falsified here')


def test_a_mixed_trailer_decomposes_exactly_by_owner_and_prices_hours_one_to_one():
    """Parts 1 and 2 (05 decision 4).  On their own these pass trivially; part 3 below is
    what makes them worth having."""
    _assert_commensurable()
    a, b = _recover()
    assert abs(a - b) <= 1e-12, 'a fulfillment hour and a store hour are the same hour'


class _WeightedSiteEvaluator(_Evaluator):
    """THE SABOTAGE: an evaluator that weights a channel's hours on their way into the
    trailer score.

    The plant sits where a per-channel coefficient could actually live — the SUM over
    owner groups — because everything below it is an hour at a bin, priced by that
    channel's own arm.  It is deliberately a pure re-weighting: with `FUL_WEIGHT = 1.0`
    it must reproduce the real evaluator exactly, so the check that fails at 1.3 fails
    because of the WEIGHT and not because the load was re-partitioned to apply it."""

    FUL_WEIGHT = 1.3

    def place_load(self, units, excluded, predicted, *, alloc=None):
        cost, takes = 0.0, []
        for regime, weight in (('store', 1.0), ('fulfillment', self.FUL_WEIGHT)):
            part = [u for u in units if regime_of(u) == regime]
            if not part:
                continue
            c, tk = super().place_load(part, excluded, predicted, alloc=alloc)
            cost += weight * c
            takes.extend(tk)
        return cost, takes


class _UnweightedSiteEvaluator(_WeightedSiteEvaluator):
    """The control: the same re-partitioning, no weight."""

    FUL_WEIGHT = 1.0


def test_a_planted_per_channel_weight_makes_the_commensurability_test_fail():
    """PART 3, and it is not optional (memory `real-test-coverage-is-317`).  Parts 1 and 2
    pass trivially today, so the standing question is whether they COULD fail — the answer
    has to be demonstrated, not argued."""
    # The control first: re-partitioning alone moves nothing, so the failure below is
    # attributable to the weight.
    _assert_commensurable(_UnweightedSiteEvaluator)
    # PART 1 IS THE FIRST LINE TO BREAK, which is itself the finding: a per-channel
    # coefficient stops the trailer score being the sum of its owners' honest hours, so
    # the decomposition fails before the rate is even solved for.
    with pytest.raises(AssertionError, match='not its two halves'):
        _assert_commensurable(_WeightedSiteEvaluator)
    # ...and it fails by naming the planted rate, which is what makes the recovery an
    # instrument rather than an alarm: a test that only knows "something moved" cannot
    # tell a per-channel weight from a pricing bug.
    a, b = _recover(_WeightedSiteEvaluator)
    assert abs(a - 1.0) <= 1e-9, 'the unweighted channel must still recover at 1'
    assert abs(b - _WeightedSiteEvaluator.FUL_WEIGHT) <= 1e-9, (
        f'the recovery must return the planted weight itself, not merely a number '
        f'different from 1 (got {b!r})')


# ── the driver builds the composite only when something will read it ──────────────

def test_the_driver_builds_a_site_bundle_only_for_a_named_gain_policy():
    from Optimization.simdriver.strategy_runner import _build_site_gain
    def _unit(store_policy, ful_policy, standing=True):
        return {'leaves': [
            {'channel_name': ch,
             'inbound': {'standing': standing, 'yard_policy': p, 'dock_policy': 'fifo'}}
            for ch, p in (('store', store_policy), ('fulfillment', ful_policy))]}
    assert _build_site_gain(_unit('fifo', 'fifo')) is None, (
        'the seeded keys never read a bundle — a composite over arm machinery nothing '
        'consumes is unconsumed infra')
    assert _build_site_gain(_unit('gain_myopic', 'gain_myopic', standing=False)) is None, (
        'the standing knobs are UNREAD without the standing yard')
    assert _build_site_gain({'leaves': [{'channel_name': 'store', 'inbound': None}]}) \
        is None, 'no inbound spec at all: nothing to compose'
    # ALL OR NONE. An asymmetric pair would build a composite holding ONE owner, which
    # is inert on a leaf with its own transit and a mid-batch `for_key` ValueError on the
    # first mixed trailer once the site fields ONE transit (site-dock 24). Unreachable
    # either way -- `inbound_spec()` is site-wide, so both leaves carry identical policies
    # -- so the refusal is what makes the difference between those two outcomes moot.
    with pytest.raises(ValueError, match='some leaves of a coupled unit name a gain'):
        _build_site_gain(_unit('gain_gated', 'fifo'))
    made = _build_site_gain(_unit('gain_gated', 'gain_gated'))
    assert isinstance(made, SiteGainBundle) and made.owners == (), (
        'built empty at unit scope and bound per leaf — the first leaf\'s transit needs '
        'the object the second leaf will bind into')
