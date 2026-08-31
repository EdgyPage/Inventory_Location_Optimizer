"""test_lead_distribution.py — per-trailer leads, and why spread zero is not a distribution.

Leads used to be one scalar every trailer shared, which made arrival order identical to
dispatch order and left the yard/dock ordering lever with nothing to order.  They are now
drawn per trailer from a lognormal — median `INBOUND_LEAD_MINUTES` x spread
`INBOUND_LEAD_SPREAD` — and the whole design rests on two claims this file pins:

  1. SPREAD ZERO IS NOT A DEGENERATE DRAW, it is the absence of one.  No generator is
     constructed, no entropy is consumed, and the median is returned as the same float it
     was handed — so every run in the archive is byte-identical by CONSTRUCTION, not by a
     tolerance on a comparison.  Tested three ways: the seam (`lead_for` returns `lead_s`
     exactly), the mechanism (a booby-trapped `default_rng` is never called), and the
     lockstep (a manager fed a seeded-but-spreadless transit produces the identical put
     queue, ledgers and deliveries as one built the pre-spread way).
  2. THE DRAW IS SEQ-KEYED AND STATELESS, so it is a pure function of (seed, tag, seq).
     Nothing is pickled across the spawn boundary, nothing is restored on a resume,
     trailer #N draws the same lead however many trailers preceded it, and every arm of a
     run sees the same lead schedule — common random numbers, for free.

And the point of all of it: with a spread the yard's arrival order stops being the
dispatch order, which is the gradient the ordering policies are graded on.

Run:  python -m pytest Tests/unit/test_lead_distribution.py -q
"""
from __future__ import annotations

import ast
import inspect
import math
import random
import statistics

import numpy as np
import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.pack import packer
from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import _LEAD_TAG, TrailerTransit, YardTransit
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig)

#: One working day, the pilot's first probe for the median (ticket 02's answer).
DAY_S = 480 * 60.0


def _order(sku: int) -> Order:
    """The `test_trailer_pipeline` fixture shape: hand-set dims, 800 in^3/item."""
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height = 10, 10, 8
    c.weight = 2.0
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty = 20
    c.reorder_point = 10
    c.lead_time_mean = 0.0
    c.supply_cv = 0.0
    c.expected_batch_demand = 3.2
    return c


def _warehouse():
    Aisle.next_aisle_id = 1
    random.seed(0)
    w, h = aisle_width_for(4), aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Warehouse_Builder().from_config(cfg).build()


def _fill(tr: TrailerTransit, n: int, *, sku: int = 1, at_s: float = 0.0) -> None:
    """Dispatch exactly `n` pup-loads: one item per pallet position, so each call of 12
    fills one trailer and the next call departs it."""
    for _ in range(n):
        tr.dispatch(sku, Trailer28.pallet_positions, 0,
                    unit_volume=POSITION_VOLUME, now_s=at_s)


def _landed(tr: TrailerTransit, at_s: float = 1e9) -> list:
    """Every dispatched trailer, standing in the yard, in the transit's own order."""
    tr.release(now_s=at_s)
    return list(tr._yard)


# ── 1. spread zero: the absence of a draw, proven three ways ─────────────────────

@pytest.mark.parametrize('median_s', [0.0, 600.0, DAY_S])
def test_spread_zero_returns_the_median_as_the_same_float(median_s):
    """Not `approx`: the scalar path must hand `Trailer` the identical value it always
    did, or 'byte-identical' is a hope rather than a property."""
    tr = TrailerTransit(Trailer28, lead_s=median_s, lead_seed=42)
    for seq in (0, 1, 7, 1_000_000):
        assert tr.lead_for(seq) == median_s
        assert tr.lead_for(seq) is not None


def test_spread_zero_constructs_no_generator(monkeypatch):
    """The mechanism, not the value: a booby-trapped `default_rng` proves the RNG is never
    reached, so spread zero cannot consume entropy or drift with a numpy version."""
    def boom(*_a, **_k):
        raise AssertionError('spread zero must not construct a generator')

    monkeypatch.setattr(np.random, 'default_rng', boom)
    monkeypatch.setattr(np.random, 'SeedSequence', boom)
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.0, lead_seed=42)
    _fill(tr, 5)
    trailers = _landed(tr)
    assert len(trailers) == 5
    assert all(t.lead_s == DAY_S for t in trailers)


def test_a_seed_is_inert_while_the_spread_is_zero():
    """The seam's real hazard: a seed that leaks entropy even at spread zero.  Two
    transits differing ONLY in seed must produce identical trailers."""
    a, b = (TrailerTransit(Trailer28, lead_s=DAY_S, lead_seed=s) for s in (42, 987654321))
    _fill(a, 6)
    _fill(b, 6)
    assert ([(t.seq, t.lead_s, t.dispatched_s) for t in _landed(a)]
            == [(t.seq, t.lead_s, t.dispatched_s) for t in _landed(b)])


# ── 2. the lockstep: a spreadless transit runs the pre-spread pipeline ────────────
#  The seam tests above prove the INPUT is bit-identical; this proves nothing downstream
#  quietly reacts to the new fields — the put-queue stream, the ledgers and the arrival
#  deliveries are compared drain by drain, never as aggregates.

def _manager(transit, skus=(101, 102, 103)):
    mgr = Inventory_Manager(_warehouse())
    for sku in skus:
        mgr._originals[sku] = _order(sku)
    mgr.transit = transit
    mgr.packer = packer
    mgr.enable_receiving(Dock(DockSpec(size=2, sources=('reorder', 'trailer'))))
    return mgr


def _script(mgr) -> list:
    """Three drains of the trailer pipeline, recorded per drain: what arrived, what the
    ledgers say, and the exact put-queue stream placement physics would see."""
    per_drain = []
    plan = [(101, 15, POSITION_VOLUME), (102, 4, POSITION_VOLUME // 3), (103, 20, 900)]
    for i, (sku, qty, vol) in enumerate(plan):
        epoch = i * 3600.0
        mgr.transit.dispatch(sku, qty, 0, unit_volume=vol, now_s=epoch)
        mgr._deferred_qty[sku] = mgr._deferred_qty.get(sku, 0) + qty
        mgr._now_s = epoch
        plans = mgr._release_arrivals()
        mgr._receive(plans, None)
        per_drain.append((
            [(p.source, p.age, p.unit.order.sku, p.unit.quantity) for p in mgr._stock_queue],
            dict(mgr._deferred_qty), dict(mgr._queued_qty), dict(mgr._queued_sku_counts),
            mgr.transit.depth, mgr.transit.merchandise(), mgr.transit.snapshot(),
        ))
    return per_drain


def test_a_seeded_spreadless_transit_is_lockstep_with_the_pre_spread_one():
    base = _manager(TrailerTransit(Trailer28, lead_s=0.0))
    seeded = _manager(TrailerTransit(Trailer28, lead_s=0.0,
                                     lead_sigma=0.0, lead_seed=987654321))
    lhs, rhs = _script(base), _script(seeded)
    assert lhs == rhs, 'spread zero perturbed the pipeline — the archive is not safe'
    assert any(d[0] for d in lhs), 'the scenario placed nothing; it proves nothing'


# ── 3. the draw: lognormal about the median, strictly positive ───────────────────

def test_a_drawn_lead_is_the_lognormal_of_its_own_key():
    """Recomputed independently from the documented key — the formula is the contract,
    not whatever the method happens to do."""
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    for seq in (0, 3, 41):
        rng = np.random.default_rng(np.random.SeedSequence([42, _LEAD_TAG, seq]))
        expect = DAY_S * math.exp(0.7 * float(rng.standard_normal()))
        assert tr.lead_for(seq) == expect


def test_the_draws_are_positive_and_centred_on_the_median():
    """Lognormal: strictly positive (nothing to clamp) and median-preserving, which is
    what makes `INBOUND_LEAD_MINUTES` mean the same thing at every spread."""
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    draws = [tr.lead_for(i) for i in range(4000)]
    assert all(d > 0.0 for d in draws)
    assert statistics.median(draws) == pytest.approx(DAY_S, rel=0.05)
    logs = [math.log(d / DAY_S) for d in draws]
    assert statistics.stdev(logs) == pytest.approx(0.7, rel=0.05)


def test_a_bigger_spread_spreads_more():
    tight = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.2, lead_seed=42)
    loose = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=1.2, lead_seed=42)
    span = lambda tr: max(tr.lead_for(i) for i in range(200)) - min(  # noqa: E731
        tr.lead_for(i) for i in range(200))
    assert span(loose) > span(tight)


# ── 4. seq-keyed and stateless: what the spawn pool and the arms rely on ─────────

def test_the_same_seed_draws_the_same_schedule():
    a = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    b = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    assert [a.lead_for(i) for i in range(50)] == [b.lead_for(i) for i in range(50)]


def test_a_different_world_seed_draws_a_different_schedule():
    """Otherwise the seed is decoration and `--seed-world` would not move the leads."""
    a = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    b = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=43)
    assert [a.lead_for(i) for i in range(50)] != [b.lead_for(i) for i in range(50)]


def test_a_seqs_lead_does_not_depend_on_how_many_trailers_preceded_it():
    """The stateless claim, at the dispatch seam rather than the method: a shared
    generator would make trailer #2's lead depend on #0 and #1 having been drawn."""
    few = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    many = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    _fill(few, 3)
    _fill(many, 9)
    shared = {t.seq: t.lead_s for t in _landed(few)}
    assert len(shared) == 3
    for t in _landed(many):
        if t.seq in shared:
            assert t.lead_s == shared[t.seq]
        assert t.lead_s == many.lead_for(t.seq), 'dispatch drew something else'


def test_every_arm_of_a_run_sees_the_same_lead_schedule():
    """Common random numbers: two arms differ in policy and doors, never in weather."""
    arm_a = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42,
                        doors=2, yard_policy='fifo', dock_policy='fifo')
    arm_b = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42,
                        doors=6, yard_policy='lifo', dock_policy='lifo')
    _fill(arm_a, 8)
    _fill(arm_b, 8)
    assert ([(t.seq, t.lead_s) for t in _landed(arm_a)]
            == [(t.seq, t.lead_s) for t in _landed(arm_b)])


# ── 5. the point of it: arrival order stops being dispatch order ─────────────────

def test_a_spread_reorders_the_yard_against_the_dispatch_order():
    """The gradient the ordering policies are graded on.  Without this the yard is FIFO
    by construction and every policy ties the baseline."""
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    _fill(tr, 8, at_s=0.0)
    order = [t.seq for t in _landed(tr)]
    assert sorted(order) == list(range(8)), 'every dispatched trailer must stand'
    assert order != list(range(8)), 'a spread that preserves dispatch order is no spread'


def test_the_yard_stays_sorted_by_arrival_then_seq():
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.7, lead_seed=42)
    _fill(tr, 8, at_s=0.0)
    yard = _landed(tr)
    keys = [(t.arrived_s, t.seq) for t in yard]
    assert keys == sorted(keys)
    for t in yard:
        assert t.arrived_s == pytest.approx(t.dispatched_s + t.lead_s)


def test_without_a_spread_the_yard_is_dispatch_order():
    """The tiebreak still holds when every stamp is identical — seq, stably."""
    tr = YardTransit(Trailer28, lead_s=DAY_S, lead_sigma=0.0, lead_seed=42)
    _fill(tr, 8, at_s=0.0)
    assert [t.seq for t in _landed(tr)] == list(range(8))


# ── 6. the config seam: two loud contradictions, and what the spec carries ───────

def _standing(monkeypatch, **over):
    from Optimization.config.sim_config import CONFIG
    g = CONFIG['global']
    base = {'inbound_standing_yard': True, 'inbound_trailer_type': '28',
            'recv_crew_size': 2, 'inbound_lead_minutes': 480.0,
            'inbound_lead_spread': 0.7}
    for k, v in {**base, **over}.items():
        monkeypatch.setitem(g, k, v)


def test_a_spread_without_the_standing_yard_refuses(monkeypatch):
    """v1's dock ranks by dispatch seq, so a spread there shifts arrival batches without
    scrambling any order — half-working, silently, which is the doctrine's whole target."""
    from Optimization.config.sim_config import inbound_spec
    _standing(monkeypatch, inbound_standing_yard=False)
    with pytest.raises(ValueError, match='INBOUND_LEAD_SPREAD'):
        inbound_spec()


def test_a_spread_with_no_trailer_type_refuses(monkeypatch):
    """The guard sits ABOVE the trailer-type return, or this configuration returns None
    and the spread is discarded without one word."""
    from Optimization.config.sim_config import inbound_spec
    _standing(monkeypatch, inbound_standing_yard=False, inbound_trailer_type=None)
    with pytest.raises(ValueError, match='INBOUND_LEAD_SPREAD'):
        inbound_spec()


def test_a_spread_over_a_zero_median_refuses(monkeypatch):
    """median * exp(sigma * Z) with median 0 is constant zero — a spread that is not one."""
    from Optimization.config.sim_config import inbound_spec
    _standing(monkeypatch, inbound_lead_minutes=0.0)
    with pytest.raises(ValueError, match='INBOUND_LEAD_MINUTES'):
        inbound_spec()


def test_a_zero_spread_over_a_zero_median_is_the_default_and_passes(monkeypatch):
    from Optimization.config.sim_config import inbound_spec
    _standing(monkeypatch, inbound_lead_minutes=0.0, inbound_lead_spread=0.0)
    spec = inbound_spec()
    assert (spec['lead_s'], spec['lead_sigma']) == (0.0, 0.0)


def test_the_spec_converts_the_median_and_carries_the_spread_dimensionless(monkeypatch):
    from Optimization.config.sim_config import inbound_spec
    _standing(monkeypatch)
    spec = inbound_spec()
    assert spec['lead_s'] == 480.0 * 60.0, 'minutes are converted once, at this seam'
    assert spec['lead_sigma'] == 0.7, 'sigma is dimensionless and crosses as authored'


def test_the_spec_keys_the_draw_on_the_world_seed(monkeypatch):
    """Leads are a world fact all arms share — no seed knob of their own, by decision."""
    from Optimization.config.sim_config import CONFIG, inbound_spec
    _standing(monkeypatch)
    monkeypatch.setitem(CONFIG['global'], 'seed_world', 4242)
    assert inbound_spec()['lead_seed'] == 4242


def test_the_settings_names_are_the_config_sources():
    """The rename landed on BOTH halves of the seam, or the constant is edited and the
    run keeps the old value."""
    from Optimization.config import settings
    assert hasattr(settings, 'INBOUND_LEAD_MINUTES')
    assert hasattr(settings, 'INBOUND_LEAD_SPREAD')
    assert not hasattr(settings, 'INBOUND_TRAILER_LEAD_MINUTES'), 'the old name lingers'
    src = inspect.getsource(
        __import__('Optimization.config.sim_config', fromlist=['x']))
    assert "'inbound_lead_minutes'  : _s.INBOUND_LEAD_MINUTES" in src
    assert "'inbound_lead_spread'   : _s.INBOUND_LEAD_SPREAD" in src


# ── 7. the driver seam: both transits are built from the spec, not from a default ─

def test_the_driver_hands_both_transits_the_lead_shape():
    """The fifth seam in miniature: a spec field nothing forwards reverts to the
    constructor default in every real run and in no test."""
    from Optimization.simdriver import strategy_runner
    tree = ast.parse(inspect.getsource(strategy_runner))
    built = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, 'id', '') in ('_TrailerTransit', '_YardTransit')):
            built[node.func.id] = {kw.arg for kw in node.keywords}
    assert set(built) == {'_TrailerTransit', '_YardTransit'}, (
        f'transit construction sites moved: {sorted(built)}')
    for name, kwargs in built.items():
        assert {'lead_s', 'lead_sigma', 'lead_seed'} <= kwargs, (
            f'{name} is built without the whole lead shape: {sorted(kwargs)}')
