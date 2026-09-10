"""test_fill_headroom.py -- the planner's fill headroom is DERIVED under the calibrated era.

`STORE_FILL` / `FF_FILL` (0.85, `assumed`) used to stand where a closed form belongs: the
planner sized every bucket at `ceil(requirement / (bins_per_aisle x fill))`, so 15% of every
bucket was headroom nobody derived, and the store's free-index slide ended somewhere that
number was never chosen to cover (.scratch/department-calibration, "Band the own-bin share
and the free-index depth" decision 7; built by "Derive the fill headroom from the
fragmentation").  Under the era each bucket is now sized to HOLD its declaration plus the
stationary extra bins the fragmentation chain stamps, never less than a declared minimum
headroom leaves; flag-off the typed fill stands, byte for byte.

What is pinned here:

  * `era_coverage.derive_fill` by hand: requirement + extra, floored at `req / (1 - h)`,
    a negative extra, an empty bucket the plan migrates into, the refusals;
  * the planner: a bucket named in `bucket_hold` is sized to `ceil(hold / eff)` replicas and
    checked against its hold, every other bucket keeps the fill rule, `bucket_hold=None` is
    the old plan exactly, and a refusal names the extra bins short;
  * the loop: the era hands the planner the derived holds and the record carries `fill`,
    `hold`, `headroom_floored` per bucket plus the `fill` block; flag-off the planner is
    called with no hold and the record says the fill was typed;
  * the rebuild: `holds_at` reads the holds back and a re-plan from the record reproduces
    the run's warehouse -- the identity gate `run_map_precompute` refuses on;
  * the seams: `min_headroom` is an era-only staffing key (default, None flag-off, its
    flag, provenance), the typed fills refuse under the era, and the run spec records them
    as None there while both restore sites skip a None.
"""
from __future__ import annotations

import argparse
import inspect
import logging
import math

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import (
    CONFIG, STAFFING_KEYS, ERA_ONLY_KEYS, _ERA_DEFAULTS, staffing_spec, staffing_provenance,
    min_headroom)
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Order import Order
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import UnfieldableRequirement
from Warehouse.layout.Aisle_Dimensions import uniform_aisle_bins

from test_warehouse_sizing import _inventory, _plan, _AISLE_W, _AISLE_H, _TARGET
from test_coverage_rescale import _tiny_pair, _order

_LOG = logging.getLogger('test_fill_headroom')
H = _s.MIN_HEADROOM


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    keys = (*STAFFING_KEYS, 'shift_drain_or_cap')
    before = {k: CONFIG['global'].get(k) for k in keys}
    fills = (CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'])
    yield CONFIG['global']
    CONFIG['global'].update(before)
    CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'] = fills


def _eff(bucket) -> int:
    _h, _c, size, unit_type = bucket
    return uniform_aisle_bins(unit_type, size, _AISLE_W, _AISLE_H)


# ═════════════════════════════════════════════════════════════════════════════════════════
# derive_fill, by hand
# ═════════════════════════════════════════════════════════════════════════════════════════

A = ('conveyable', 'food', 'medium', 'pallet')
B = ('conveyable', 'food', 'singleton', 'singleton')
C = ('conveyable', 'food', 'small', 'pallet')
D = ('non-conveyable', 'chemical', 'large', 'pallet')


def test_the_hold_is_the_requirement_plus_the_extra_floored_at_the_minimum_headroom():
    got = ec.derive_fill({A: 100, B: 100, D: 50}, {A: 20.0, B: -10.0, C: 7.5}, 0.05)
    assert list(got) == sorted({A, B, C, D}, key=repr), 'the union of both maps, sorted'
    # A: the fragmentation asks more than the floor -- hold = req + extra, not floored.
    assert got[A] == {'requirement': 100, 'expected_extra': 20.0, 'hold': 120.0,
                      'fill': pytest.approx(100 / 120), 'headroom_floored': False}
    # B: a NEGATIVE extra (a remainder migrating down a tier) -- the floor binds.
    assert got[B]['hold'] == pytest.approx(100 / 0.95) and got[B]['headroom_floored'] is True
    assert got[B]['fill'] == pytest.approx(0.95)
    # C: an empty bucket the plan migrates INTO -- sized to its extra, fill 0, not floored.
    assert got[C] == {'requirement': 0, 'expected_extra': 7.5, 'hold': 7.5, 'fill': 0.0,
                      'headroom_floored': False}
    # D: absent from the extra map reads 0 extra -- the floor binds at exactly 1 - h.
    assert got[D]['hold'] == pytest.approx(50 / 0.95) and got[D]['headroom_floored'] is True
    assert got[D]['fill'] == pytest.approx(1 - 0.05)
    # every fill is at most 1 - h, by construction
    assert all(v['fill'] <= 1 - 0.05 + 1e-12 for v in got.values())


def test_a_zero_headroom_is_the_fragmentation_alone_and_the_bounds_refuse():
    got = ec.derive_fill({A: 100}, {A: 0.0}, 0.0)
    assert got[A]['hold'] == pytest.approx(100.0) and got[A]['fill'] == pytest.approx(1.0)
    assert not got[A]['headroom_floored']
    for bad in (1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match='min_headroom'):
            ec.derive_fill({A: 1}, {}, bad)
    assert ec.derive_fill({}, {}, 0.05) == {}


# ═════════════════════════════════════════════════════════════════════════════════════════
# The planner
# ═════════════════════════════════════════════════════════════════════════════════════════

def _biggest(plan):
    return max(plan.requirement, key=lambda b: plan.requirement[b])


def test_a_named_bucket_is_sized_to_hold_its_map_and_every_other_keeps_the_fill_rule():
    inv = _inventory(120, seed=9)
    base = _plan(inv)
    b = _biggest(base)
    hold = base.fielding[b]['requirement'] * 3.0            # well past req / fill
    plan = _plan(inv, bucket_hold={b: hold})
    eff = _eff(b)
    assert plan.capacity[b] == max(1, math.ceil(hold / eff)) * eff
    assert plan.capacity[b] > base.capacity[b]
    row = plan.fielding[b]
    assert row['hold'] == pytest.approx(hold)
    assert row['fill'] == pytest.approx(row['requirement'] / hold)
    assert row['budget'] == row['capacity'], 'the hold bucket is checked against its capacity'
    for o in base.fielding:
        if o != b:
            assert plan.fielding[o] == base.fielding[o], o
            assert plan.capacity[o] == base.capacity[o], o
    # the fill-rule rows carry what they were sized at: the typed fill and req / fill
    t = base.fielding[b]
    assert t['fill'] == _TARGET and t['hold'] == pytest.approx(t['requirement'] / _TARGET)


def test_no_hold_map_is_the_old_arithmetic_exactly():
    """Byte-identical flag-off: with no map (None, or an empty one -- the truthiness gate)
    every bucket's capacity is the OLD rule by hand, `max(1, ceil(req / (eff x fill))) x eff`,
    computed here independently of the planner.  `test_warehouse_sizing`'s pinned numbers
    carry the same claim on the whole plan; this is the per-bucket oracle."""
    inv = _inventory(120, seed=9)
    for kw in ({}, {'bucket_hold': None}, {'bucket_hold': {}}):
        plan = _plan(inv, **kw)
        for b, cap in plan.capacity.items():
            eff = _eff(b)
            want = max(1, math.ceil(plan.requirement.get(b, 0) / (eff * _TARGET))) * eff
            assert cap == want, (kw, b, cap, want)
            t = plan.fielding[b]
            assert t['fill'] == _TARGET and t['budget'] == math.floor(cap * _TARGET + 1e-9)
        assert plan.total_bins == sum(plan.capacity.values())


def test_an_empty_bucket_named_by_the_map_is_sized_to_its_extra():
    inv = _inventory(120, seed=9)
    base = _plan(inv)
    empty = [b for b in base.capacity if b not in base.requirement]
    assert empty, 'the fixture has no empty structural bucket; pick a smaller catalogue'
    b = empty[0]
    eff = _eff(b)
    hold = 3 * eff + 1                                        # four replicas, not one
    plan = _plan(inv, bucket_hold={b: float(hold)})
    assert plan.capacity[b] == 4 * eff and base.capacity[b] == eff
    assert plan.fielding[b] == {'requirement': 0, 'capacity': 4 * eff, 'budget': 4 * eff,
                                'free': 4 * eff, 'fill': 0.0, 'hold': float(hold)}


def test_a_hold_the_capacity_cannot_meet_refuses_and_names_the_extra_bins():
    inv = _inventory(60, seed=5)
    base = _plan(inv)
    b = _biggest(base)
    req = base.fielding[b]['requirement']
    hold = float(req * 10)
    # A cap at the OLD total trims the grown warehouse back down, so the hold cannot be met.
    with pytest.raises(UnfieldableRequirement, match='and the fragmentation they create') as exc:
        _plan(inv, max_bins=base.total_bins, bucket_hold={b: hold})
    short = {s[0]: s for s in exc.value.short}
    assert b in short, f'the hold bucket is not among the short ones: {exc.value.short[:3]}'
    _b, need, cap, bud = short[b]
    assert need == math.ceil(hold) and bud == cap and cap < need
    assert f'{need - req:,} expected extra' in str(exc.value)
    assert 'must hold' in str(exc.value)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The loop: the era hands the planner the holds; flag-off it hands nothing
# ═════════════════════════════════════════════════════════════════════════════════════════

class _HoldPlan:
    """A plan stub that fields the section and records the hold map it was sized from."""
    def __init__(self, orders, bucket_hold=None):
        self.sampled = list(orders)
        self.total_aisles, self.total_bins = 3, 30
        Inventory_Manager.field_requirement(self.sampled)
        self.requirement = Inventory_Manager.bucket_requirements(self.sampled)
        hold = bucket_hold or {}
        self.fielding = {}
        for b in set(self.requirement) | set(hold):
            n = self.requirement.get(b, 0)
            h = hold.get(b, n / 0.5)
            self.fielding[b] = {'requirement': n, 'capacity': n * 2, 'budget': n, 'free': n,
                                'fill': (n / h) if h else 0.5, 'hold': h}


class _Meta:
    aisles = ()


def _fake_stage_a(orders, geometry, specs, *, inputs, day_seconds, log):
    return {s.name: {'n': 60.0, 'expected': {'lines': 60.0}, 'orders': list(orders)}
            for s in specs}


def _section():
    Order.next_sku = 1
    return [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
            _order(3, freq=0.25, qty=8.0)]


def test_the_loop_derives_the_holds_before_it_plans_and_stamps_them(monkeypatch):
    section = _section()
    calls = []
    planned_before = []

    def plan_fn(**kw):
        calls.append(kw)
        # Captured AT CALL TIME: the chain packs by the plan, so every SKU must carry one
        # before the planner runs (the stub's own fielding would stamp it afterwards).
        planned_before.append([bool(getattr(c, 'stock_plan', None)) for c in section])
        return _HoldPlan(section, kw.get('bucket_hold')), _Meta()

    monkeypatch.setattr(ec, 'seed_lines', lambda *a, **k: {'store': 60.0})
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a)
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    _p, _m, _sa, rec = ec.fixed_point(section, plan_fn, specs, coverage_days=10.0,
                                      safety_days=2.0, floor_lines=1.0,
                                      inputs={'min_headroom': 0.05}, day_seconds=28800.0,
                                      log=_LOG, max_rounds=1)
    assert len(calls) == 1 and set(calls[0]) == {'bucket_hold'}
    holds = calls[0]['bucket_hold']
    assert holds and all(isinstance(b, tuple) and len(b) == 4 for b in holds)
    # every SKU carried a plan BEFORE the planner ran (the chain packs by it)
    assert planned_before == [[True] * len(section)]
    fielded = rec['final']['store']['fielded']
    blk = fielded['fill']
    assert blk['provenance'] == 'derived' and blk['min_headroom'] == 0.05 and blk['typed'] is None
    assert set(blk['derived']) == {'section_fill', 'expected_extra', 'headroom_floored_buckets'}
    assert blk['derived']['headroom_floored_buckets'] == \
        sum(r['headroom_floored'] for r in fielded['buckets'])
    assert 0.0 < blk['derived']['section_fill'] <= 1 - 0.05 + 1e-12
    assert blk['derived']['expected_extra'] == pytest.approx(
        fielded['fragmentation']['expected_extra'])
    for row in fielded['buckets']:
        b = (row['handling'], row['category'], row['size'], row['unit'])
        assert row['hold'] == holds[b], 'the record carries the hold the planner was handed'
        assert row['fill'] <= 1 - 0.05 + 1e-12
        assert row['hold'] >= row['requirement'] + row['expected_extra'] - 1e-9
        assert row['hold'] >= row['requirement'] / 0.95 - 1e-9
        if row['requirement']:
            assert row['headroom_floored'] == (
                row['requirement'] + row['expected_extra'] < row['requirement'] / 0.95 - 1e-9)
    assert ec.holds_at(rec) == holds


def test_flag_off_the_loop_calls_a_planner_that_never_learned_the_keyword(monkeypatch):
    section = _section()
    calls = []

    def plan_fn():                                   # no **kw: the flag-off contract
        calls.append(True)
        return _HoldPlan(section), _Meta()

    monkeypatch.setattr(ec, 'seed_lines', lambda *a, **k: {'store': 60.0})
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a)
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    _p, _m, _sa, rec = ec.fixed_point(section, plan_fn, specs, coverage_days=10.0,
                                      safety_days=2.0, floor_lines=1.0, inputs={},
                                      day_seconds=28800.0, log=_LOG, max_rounds=1)
    assert calls == [True]
    fielded = rec['final']['store']['fielded']
    assert fielded['fill'] == {'provenance': 'assumed', 'min_headroom': None, 'typed': 0.5,
                               'derived': None}
    assert all(row['headroom_floored'] is False for row in fielded['buckets'])
    assert ec.holds_at(rec) is None


def test_a_mixed_catalogue_derives_one_hold_map_and_stamps_each_section_its_own(monkeypatch):
    """Two sections, two chains, ONE hold map for the planner: each channel's block carries
    only its own regime's buckets, and `holds_at` merges the two back into the map."""
    from test_fulfillment_channels import _mixed_inventory
    from Warehouse.kernel.regime import STORE, FULFILLMENT
    Order.next_sku = 1
    orders = _mixed_inventory(n_store=30, n_ff=12, seed=4)
    calls = []

    def plan_fn(**kw):
        calls.append(kw)
        return _HoldPlan(orders, kw.get('bucket_hold')), _Meta()

    monkeypatch.setattr(ec, 'seed_lines', lambda *a, **k: {'store': 40.0, 'fulfillment': 30.0})
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a)
    specs = [ec.ChannelSpec('store', STORE, None, 'store'),
             ec.ChannelSpec('fulfillment', FULFILLMENT, None, 'ff')]
    _p, _m, _sa, rec = ec.fixed_point(orders, plan_fn, specs, coverage_days=10.0,
                                      safety_days=2.0, floor_lines=1.0,
                                      inputs={'min_headroom': 0.05}, day_seconds=28800.0,
                                      log=_LOG, max_rounds=1)
    holds = calls[0]['bucket_hold']
    units = {b[3] for b in holds}
    assert units == {'pallet', 'singleton', FULFILLMENT} or units >= {FULFILLMENT, 'pallet'}, units
    merged: dict = {}
    for ch, regime in (('store', STORE), ('fulfillment', FULFILLMENT)):
        fb = rec['final'][ch]['fielded']
        assert fb['fill']['provenance'] == 'derived'
        rows = {(r['handling'], r['category'], r['size'], r['unit']): r for r in fb['buckets']}
        assert all((b[3] == FULFILLMENT) == (regime == FULFILLMENT) for b in rows), ch
        assert fb['fill']['derived']['expected_extra'] == pytest.approx(
            fb['fragmentation']['expected_extra'])
        merged.update({b: r['hold'] for b, r in rows.items() if r['hold'] > 0.0})
    assert ec.holds_at(rec) == holds == merged


def test_stamp_fill_refuses_a_plan_that_drifted_from_the_derivation():
    fielded = {'buckets': [{'handling': 'conveyable', 'category': 'food', 'size': 'medium',
                            'unit': 'pallet', 'requirement': 10, 'capacity': 20, 'free': 10,
                            'fill': 0.5, 'hold': 20.0}]}
    fills = ec.derive_fill({A: 10}, {A: 2.0}, 0.05)          # asks a hold of 12, not 20
    with pytest.raises(ValueError, match='drifted apart'):
        ec.stamp_fill(fielded, fills, 0.05, name='store')


def test_holds_at_reads_only_a_derived_record():
    row = {'handling': 'conveyable', 'category': 'food', 'size': 'medium', 'unit': 'pallet',
           'requirement': 10, 'capacity': 20, 'free': 10, 'fill': 0.8, 'hold': 12.5,
           'expected_extra': 2.5, 'headroom_floored': False}
    derived = {'final': {'store': {'fielded': {'buckets': [row],
                                               'fill': {'provenance': 'derived'}}}}}
    assert ec.holds_at(derived) == {A: 12.5}
    typed = {'final': {'store': {'fielded': {'buckets': [row],
                                             'fill': {'provenance': 'assumed'}}}}}
    assert ec.holds_at(typed) is None
    assert ec.holds_at({'final': {'store': {'fielded': {'buckets': [row]}}}}) is None
    assert ec.holds_at(None) is None and ec.holds_at({}) is None


# ═════════════════════════════════════════════════════════════════════════════════════════
# End to end on a real tiny pair: the run, and the rebuild that must reproduce it
# ═════════════════════════════════════════════════════════════════════════════════════════

def _spy_planner(monkeypatch, calls):
    real = Inventory_Manager.plan_warehouse.__func__

    def spy(cls, orders, **kw):
        calls.append(kw)
        return real(cls, orders, **kw)

    monkeypatch.setattr(Inventory_Manager, 'plan_warehouse', classmethod(spy))


def test_the_era_sizes_every_bucket_from_the_derived_fill_and_a_rebuild_reproduces_it(
        tmp_path, restore, monkeypatch):
    from Optimization.simdriver import sim_assets
    restore.update(shift_drain_or_cap=True, coverage_days=10.0, safety_days=2.0,
                   floor_lines=None, store_demand=0.05, min_headroom=None)
    inv_db, aff_db = _tiny_pair(tmp_path)
    calls: list = []
    _spy_planner(monkeypatch, calls)
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    rec = shared['coverage']
    # ONE round under the era, ONE planner call, with the derived holds
    assert len(calls) == 1 and calls[0]['bucket_hold']
    holds = calls[0]['bucket_hold']
    fielded = rec['final']['store']['fielded']
    blk = fielded['fill']
    assert blk['provenance'] == 'derived' and blk['min_headroom'] == H and blk['typed'] is None
    assert ec.holds_at(rec) == holds, 'the record round-trips the planner input exactly'
    rows = {(r['handling'], r['category'], r['size'], r['unit']): r for r in fielded['buckets']}
    assert set(rows) >= set(holds)
    from Optimization.config.sim_config import _AISLE_W as _W, _AISLE_H as _H
    for b, r in rows.items():
        assert r['capacity'] >= math.ceil(r['hold'] - 1e-9), r
        assert r['fill'] <= 1 - H + 1e-12, r
        if r['requirement']:
            assert r['hold'] >= r['requirement'] / (1 - H) - 1e-9, r
            assert r['hold'] >= r['requirement'] + r['expected_extra'] - 1e-9, r
        if b in holds:
            # TIGHT: sized to the hold, not merely above it -- one replica fewer would be short
            eff = uniform_aisle_bins(b[3], b[2], _W, _H)
            assert r['capacity'] - eff < r['hold'], (b, r['capacity'], eff, r['hold'])
    # the floor binds on some buckets and not on others, and the block counts them
    floored = {r['headroom_floored'] for r in rows.values() if r['requirement']}
    assert floored == {True, False}, 'the fixture exercises only one side of the floor'
    assert blk['derived']['headroom_floored_buckets'] == \
        sum(r['headroom_floored'] for r in rows.values())
    # the section fill the block reports is sum requirement / sum hold, and the store's
    # warehouse-stats target is that number rather than the typed 0.85
    sec = sum(r['requirement'] for r in rows.values()) / sum(r['hold'] for r in rows.values())
    assert blk['derived']['section_fill'] == pytest.approx(sec)
    assert sim_assets._target_fill(rec, 0.85) == pytest.approx(sec)

    # THE FROZEN CELL (a multi-cell run's every cell after the freeze): the frozen inventory
    # carries the run's declaration and plans, so the holds derive to the same map.
    calls.clear()
    frozen = sim_assets.build_shared_assets(inv_db, aff_db, _LOG,
                                            frozen_inventory_db=shared['planned_inv_db'])
    assert len(calls) == 1 and calls[0]['sample'] is False
    assert calls[0]['bucket_hold'] == pytest.approx(holds)
    assert (frozen['total_aisles'], frozen['total_bins']) == \
           (shared['total_aisles'], shared['total_bins'])

    # THE REBUILD: from the catalogue and the run's record alone, sized by the recorded
    # holds -- never a re-derivation -- and reproducing the run's geometry exactly.
    def no_chain(*a, **k):
        raise AssertionError('a rebuild must read the holds off the record, not re-derive')

    monkeypatch.setattr(ec, 'derived_holds', no_chain)
    calls.clear()
    again = sim_assets.build_shared_assets(inv_db, aff_db, _LOG, coverage_record=rec)
    assert len(calls) == 1 and calls[0]['bucket_hold'] == holds and calls[0]['sample'] is False
    assert (again['total_aisles'], again['total_bins']) == \
           (shared['total_aisles'], shared['total_bins'])
    assert again['warehouse_cfg'].total_aisles == shared['warehouse_cfg'].total_aisles


def test_flag_off_the_run_is_sized_at_the_typed_fill_and_records_it(tmp_path, restore,
                                                                    monkeypatch):
    from Optimization.simdriver import sim_assets
    restore.update(shift_drain_or_cap=False, store_pickers=3, floor_lines=None)
    CONFIG['channels']['store']['fill'] = 0.8
    inv_db, aff_db = _tiny_pair(tmp_path)
    calls: list = []
    _spy_planner(monkeypatch, calls)
    # Flag-off the fixed point iterates; nothing here is about convergence, so one round.
    _orig = ec.fixed_point
    monkeypatch.setattr(ec, 'fixed_point', lambda *a, **k: _orig(*a, **{**k, 'max_rounds': 1}))
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    assert calls and all(kw['bucket_hold'] is None for kw in calls)
    rec = shared['coverage']
    fielded = rec['final']['store']['fielded']
    assert fielded['fill'] == {'provenance': 'assumed', 'min_headroom': None, 'typed': 0.8,
                               'derived': None}
    assert all(r['fill'] == pytest.approx(0.8) and not r['headroom_floored']
               for r in fielded['buckets'])
    assert ec.holds_at(rec) is None
    assert sim_assets._target_fill(rec, 0.8) == pytest.approx(0.8)
    # ...and a rebuild from that record hands the planner no hold either
    calls.clear()
    sim_assets.build_shared_assets(inv_db, aff_db, _LOG, coverage_record=rec)
    assert calls and calls[0]['bucket_hold'] is None


# ═════════════════════════════════════════════════════════════════════════════════════════
# The seams
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_min_headroom_is_an_era_only_staffing_key_with_its_default(restore):
    assert 'min_headroom' in STAFFING_KEYS and 'min_headroom' in ERA_ONLY_KEYS
    assert 'min_headroom' in CONFIG['global']
    assert _ERA_DEFAULTS['min_headroom'] == _s.MIN_HEADROOM == 0.05
    restore.update(shift_drain_or_cap=False, min_headroom=None)
    assert staffing_spec()['min_headroom'] is None and min_headroom() is None
    assert 'min_headroom' not in staffing_provenance(set())
    restore['shift_drain_or_cap'] = True
    assert staffing_spec()['min_headroom'] == _s.MIN_HEADROOM == min_headroom()
    assert staffing_provenance(set())['min_headroom'] == 'assumed'
    restore['min_headroom'] = 0.1
    assert staffing_spec()['min_headroom'] == 0.1
    assert staffing_provenance({'min_headroom'})['min_headroom'] == 'declared'
    restore['min_headroom'] = 1.0
    with pytest.raises(ValueError, match='min_headroom'):
        min_headroom()


def test_the_knob_has_a_flag_and_settings_names_it():
    from Optimization import run_simulation
    assert "'--min-headroom'" in inspect.getsource(run_simulation)
    src = inspect.getsource(_s)
    assert '--min-headroom' in src, 'the flag is not named beside its setting'
    assert 'MIN_HEADROOM = 0.05' in src


def _ns(**over) -> argparse.Namespace:
    base = dict(shift_drain_or_cap=False, releases_per_day=None, roll_over_unpicked=False,
                cut_at_day_end=False, put_queue_split=False, put_swap_coef=0.0)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.parametrize('flag', ['store_fill', 'ff_fill'])
def test_a_typed_fill_under_the_era_is_an_error(flag):
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match='fill headroom is DERIVED'):
        _check_era_flags(_ns(shift_drain_or_cap=True), explicit={'shift_drain_or_cap', flag})
    assert _check_era_flags(_ns(), explicit={flag}) == [], 'flag-off the typed fill stands'


def test_the_flag_takes_a_free_share_and_a_pre_feature_era_resume_is_refused():
    from Optimization.run_simulation import _free_share, _apply_run_spec
    assert _free_share('0') == 0.0 and _free_share('0.05') == 0.05
    for bad in ('1', '1.5', '-0.1', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            _free_share(bad)
    # An era run recorded before the derived fill carries a numeric store_fill: resumed here
    # it would be re-sized at the derived fill under its completed arms -- refused.
    with pytest.raises(SystemExit, match='predates the derived fill'):
        _apply_run_spec(argparse.Namespace(), {'shift_drain_or_cap': True, 'store_fill': 0.85},
                        explicit=set())
    # ...while an era run of this checkout (None) and any flag-off run resume as before.
    ns = argparse.Namespace(store_fill=None, ff_fill=None, shift_drain_or_cap=False)
    _apply_run_spec(ns, {'shift_drain_or_cap': True, 'store_fill': None, 'ff_fill': None},
                    explicit=set())
    assert ns.shift_drain_or_cap is True
    _apply_run_spec(ns, {'shift_drain_or_cap': False, 'store_fill': 0.85}, explicit=set())
    assert ns.store_fill == 0.85


def test_a_typed_min_headroom_without_the_era_is_an_error():
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match='min-headroom'):
        _check_era_flags(_ns(), explicit={'min_headroom'})
    notes = _check_era_flags(_ns(shift_drain_or_cap=True),
                             explicit={'shift_drain_or_cap', 'min_headroom'})
    assert all('min_headroom' not in n for n in notes)


def test_a_cells_derivation_keeps_the_coverage_the_freeze_recorded(tmp_path):
    """A multi-cell era run: the freeze records the pair's coverage block, then the first
    cell's derivation (over the FROZEN inventory, so `coverage: None`) records its calibration
    block for the same pair.  Replacing the block wholesale dropped the coverage record, and
    the analysis stage then found "no stock declaration" for every cell and rebuilt nothing --
    which is how the derived fill's sweep canary found it.  The recorded coverage stands and
    the derived-fill holds it carries (`holds_at`) reach the rebuild."""
    from Optimization.simdriver.workunits import _record_derived, _record_coverage
    from Optimization.runschema.sim_manifest import _write_run_spec, _load_run_spec
    root = str(tmp_path)
    _write_run_spec(root, {'n_batches': 2, 'staffing': {'inputs': {}, 'era': True}})
    cov = {'lines_per_day': {'store': 10.0}, 'final': {'store': {'fielded': {
        'buckets': [{'handling': 'conveyable', 'category': 'food', 'size': 'medium',
                     'unit': 'pallet', 'requirement': 10, 'capacity': 20, 'free': 10,
                     'fill': 0.8, 'hold': 12.5, 'expected_extra': 2.5,
                     'headroom_floored': False}],
        'fill': {'provenance': 'derived'}}}}}
    _record_coverage(root, 'pair', cov, _LOG)                       # the freeze
    cal = {'method': 'expected_travel', 's_pick': {}, 'coverage': None}
    _record_derived(root, 'pair', {'put': {'crew': 1}}, cal, _LOG)  # the first cell
    spec = _load_run_spec(root)
    got = spec['staffing']['calibration']['pair']
    assert got['coverage'] == cov and got['method'] == 'expected_travel'
    assert spec['staffing']['derived']['pair'] == {'put': {'crew': 1}}
    assert ec.holds_at(got['coverage']) == {A: 12.5}
    # ...and a derivation that carries its own coverage (a single-cell run) records that one
    _write_run_spec(root, {'n_batches': 2, 'staffing': {'inputs': {}, 'era': True}})
    own = {**cal, 'coverage': {'lines_per_day': {'store': 11.0}}}
    _record_derived(root, 'pair', {'put': {'crew': 1}}, own, _LOG)
    assert _load_run_spec(root)['staffing']['calibration']['pair']['coverage'] == own['coverage']


def test_the_run_spec_records_the_typed_fills_as_none_under_the_era_and_a_none_is_skipped(
        tmp_path, monkeypatch):
    import copy
    from Optimization import run_simulation, run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    src = inspect.getsource(run_simulation)
    assert "'store_fill'   : None if g['shift_drain_or_cap'] else" in src
    assert "'ff_fill'      : None if g['shift_drain_or_cap'] else" in src
    # the resume overlay: a restored None reaches `args` and the write-back skips it
    ns = argparse.Namespace(store_fill=None, ff_fill=None)
    run_simulation._apply_run_spec(ns, {'store_fill': None, 'ff_fill': None}, explicit=set())
    assert ns.store_fill is None and ns.ff_fill is None
    assert 'if args.store_fill is not None:' in src and 'if args.ff_fill is not None:' in src
    # the analysis restore skips a None outright.  `_apply_run_shape` writes ~30 CONFIG keys
    # and a module global, so the whole global dict and both sizing blocks are snapshotted.
    _write_run_spec(str(tmp_path), {'n_batches': 1, 'store_fill': None, 'ff_fill': None,
                                    'sampler': 'v2', 'staffing': {'inputs': {}}})
    saved_g = copy.deepcopy(CONFIG['global'])
    saved_ch = copy.deepcopy(CONFIG['channels'])
    monkeypatch.setattr(run_analysis, '_RUN_STAFFING', run_analysis._RUN_STAFFING)
    try:
        CONFIG['channels']['store']['fill'] = 0.77
        run_analysis._apply_run_shape(str(tmp_path), _LOG)
        assert CONFIG['channels']['store']['fill'] == 0.77, 'a None must not clobber the fill'
    finally:
        CONFIG['global'].clear()
        CONFIG['global'].update(saved_g)
        for ch in ('store', 'fulfillment'):
            CONFIG['channels'][ch]['fill'] = saved_ch[ch]['fill']
            CONFIG['channels'][ch]['sizing'].clear()
            CONFIG['channels'][ch]['sizing'].update(saved_ch[ch]['sizing'])
