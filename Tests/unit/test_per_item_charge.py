"""test_per_item_charge.py — the per-item charge, and the hard break it is (ADR-0001).

The at-location model gained a per-UNIT charge:

    M(y) · (intercept + qty · per_item + qty · var)

and the default is NON-ZERO in the dataclass by decision, so no archived pick result stays
comparable with a new run.  These tests pin the decision and the four things the ticket
(`.scratch/department-calibration`, "Add the per-item charge and break the cost model")
demanded proof of:

  1. the gain evaluator and the demand-mass yardstick CARRY the charge — a sabotage test:
     zero it in one and the priced hours must move;
  2. receiving charges the per-item term once per PACK, not per item;
  3. the three crew scales flow CLI -> CONFIG -> run spec -> worker payload (the spawn trap);
  4. the three cost objects share ONE literal set (the kernel's), by reference.

There is deliberately no "byte-identical at the default" test here: the default is 0.5,
and a test proving the old model survives would be the fake the ticket forbids.

Run:  python -m pytest Tests/unit/test_per_item_charge.py -q
"""
from __future__ import annotations

import inspect
import types
from dataclasses import replace

import pytest

from Inbound.unload import UnloadCost, unload_cost
from Optimization.metrics.Workload import WorkloadParams, aisle_workload_components
from Warehouse.catalog.Order import Order
from Warehouse.kernel.cost_model import (
    DEFAULT_HEIGHT_BRACKETS, DEFAULT_PICK_INTERCEPT, DEFAULT_PICK_PER_ITEM,
    DEFAULT_PICK_VOLUME_COEF, DEFAULT_PICK_VOLUME_FN, DEFAULT_PICK_WEIGHT_COEF,
    DEFAULT_PICK_WEIGHT_FN, DEFAULT_PUT_INTERCEPT_SCALE, DEFAULT_PUT_ITEM_RATIO,
    DEFAULT_RECV_INTERCEPT_SCALE, SpeedProfile, handle_var, height_multiplier, per_pick,
)
from Warehouse.operations.putaway import PutawayCost, put_cost
from Warehouse.picking.Pick import PickConfig, _pick_time


# ── 1. the kernel states the model once ──────────────────────────────────────────

def test_the_primitive_is_the_model_stated_once():
    """Longhand, so a change to the expression fails here rather than re-tuning four
    crews at once."""
    m, i, v, q, p = 1.2, 15.0, 0.37, 7, 0.5
    assert per_pick(m, i, v, q, p) == pytest.approx(m * (i + q * p + q * v))


def test_a_zero_charge_is_bit_identical_to_the_old_expression():
    """The PRIMITIVE defaults to 0.0 so it does not fork; only the crews' own values are
    non-zero.  `intercept + 0.0` is exactly `intercept`, so the old expression survives
    bit-for-bit at per_item=0 — which is what lets a caller without a charge be trusted."""
    for m, i, v, q in ((1.0, 1.0, 0.123456789, 1), (1.4, 15.0, 2.71828, 12),
                       (1.2, 0.0, 1e-9, 3), (1.0, 10.0, 0.0, 250)):
        assert per_pick(m, i, v, q) == m * (i + q * v)          # `==`: identity is the claim
        assert per_pick(m, i, v, q, 0.0) == m * (i + q * v)


# ── 2. the default is non-zero, and it reaches every mirror ──────────────────────

def test_the_default_charge_is_non_zero_by_decision():
    assert DEFAULT_PICK_PER_ITEM > 0.0
    assert PickConfig().pick_per_item == DEFAULT_PICK_PER_ITEM
    assert WorkloadParams().pick_per_item == DEFAULT_PICK_PER_ITEM
    cfg = PickConfig(pick_per_item=0.75)
    assert WorkloadParams.from_pick_config(cfg).pick_per_item == 0.75


def test_the_three_cost_objects_share_one_literal_set():
    """PickConfig, WorkloadParams and PutawayCost default to the kernel's declaration BY
    REFERENCE — the "second literal set" both crew modules forbid is gone."""
    pc, wp, put = PickConfig(), WorkloadParams(), PutawayCost()
    kernel = (DEFAULT_PICK_INTERCEPT, DEFAULT_PICK_PER_ITEM, DEFAULT_PICK_WEIGHT_COEF,
              DEFAULT_PICK_VOLUME_COEF, DEFAULT_PICK_WEIGHT_FN, DEFAULT_PICK_VOLUME_FN)
    assert (pc.pick_intercept, pc.pick_per_item, pc.pick_weight_coef, pc.pick_volume_coef,
            pc.pick_weight_fn, pc.pick_volume_fn) == kernel
    assert (wp.pick_intercept, wp.pick_per_item, wp.pick_weight_coef, wp.pick_volume_coef,
            wp.pick_weight_fn, wp.pick_volume_fn) == kernel
    # put-away: the coefficients verbatim, the two charges scaled
    assert (put.weight_coef, put.volume_coef, put.weight_fn, put.volume_fn) == kernel[2:]
    assert put.intercept == pytest.approx(DEFAULT_PICK_INTERCEPT * DEFAULT_PUT_INTERCEPT_SCALE)
    assert put.per_item == pytest.approx(DEFAULT_PICK_PER_ITEM * DEFAULT_PUT_ITEM_RATIO)
    # receiving: put-away's, by reference, at the class level
    assert (UnloadCost.weight_coef, UnloadCost.volume_coef) == (
        PutawayCost.weight_coef, PutawayCost.volume_coef)
    assert UnloadCost.intercept == pytest.approx(
        PutawayCost.intercept * DEFAULT_RECV_INTERCEPT_SCALE)
    assert UnloadCost.per_item == PutawayCost.per_item


# ── 3. picking charges per item; the docstring's "per line" intercept is real ───────

def test_pick_time_charges_the_intercept_per_line_and_the_charge_per_item():
    cfg = PickConfig()
    y = 100.0                                    # second bracket: M = 1.2
    m = height_multiplier(cfg.height_brackets, y)
    var = handle_var(10, 100, cfg.pick_weight_coef, cfg.pick_volume_coef)
    one = _pick_time(cfg, 10, 100, 1, y)
    ten = _pick_time(cfg, 10, 100, 10, y)
    # nine more units: nine more charges and nine more handling terms, NO more intercept
    assert ten - one == pytest.approx(m * 9 * (cfg.pick_per_item + var))
    # and zeroing the charge removes exactly the per-unit term, ten times, height-scaled
    zero = replace(cfg, pick_per_item=0.0)
    assert ten - _pick_time(zero, 10, 100, 10, y) == pytest.approx(m * 10 * cfg.pick_per_item)


def test_labor_cost_requires_the_charge_and_carries_it():
    """`compute_labor_cost` takes the charge keyword-only and REQUIRED: a caller that
    forgot it would rank against a labor the sim no longer bills."""
    o = Order(('conveyable', 'food'))
    with pytest.raises(TypeError):
        o.compute_labor_cost(1.0, 0.02, 1e-4)
    cfg = PickConfig(pick_intercept=3.0, pick_per_item=0.5)
    lc = o.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef,
                              pick_per_item=cfg.pick_per_item)
    assert lc == pytest.approx(_pick_time(cfg, o.weight, o.volume(), 1))
    assert lc == pytest.approx(3.0 + 0.5 + o.handle_var)


def test_the_analytical_mirror_carries_the_charge():
    cfg = PickConfig(pick_intercept=2.0, pick_per_item=0.5,
                     height_brackets=((96.0, 1.0), (float('inf'), 2.0)))
    lines = [(20, 27000, 3, 10.0), (20, 27000, 2, 300.0)]
    _, P, _ = aisle_workload_components(0, 0, 1, lines, WorkloadParams.from_pick_config(cfg))
    _, P0, _ = aisle_workload_components(
        0, 0, 1, lines, WorkloadParams.from_pick_config(replace(cfg, pick_per_item=0.0)))
    assert P == pytest.approx(sum(_pick_time(cfg, w, v, q, y) for (w, v, q, y) in lines))
    assert P - P0 == pytest.approx(1.0 * 3 * 0.5 + 2.0 * 2 * 0.5)


# ── 4. put-away is picking's price, scaled — and receiving is put-away's ───────────

def test_put_away_is_pickings_price_at_the_declared_scales():
    """A store arm picks at intercept 15; its put crew must be priced from THAT, not from
    the kernel's 1.0 — the runtime constructor is what carries it across."""
    cfg = PickConfig(pick_intercept=15.0, pick_per_item=0.5, pick_weight_coef=0.03)
    put = PutawayCost.from_pick(cfg)
    assert put.intercept == pytest.approx(15.0 * DEFAULT_PUT_INTERCEPT_SCALE)
    assert put.per_item == pytest.approx(0.5 * DEFAULT_PUT_ITEM_RATIO)
    assert put.weight_coef == 0.03 and put.height_brackets == cfg.height_brackets
    own = PutawayCost.from_pick(cfg, intercept_scale=0.25, item_ratio=1.0)
    assert (own.intercept, own.per_item) == pytest.approx((3.75, 0.5))
    # WorkloadParams is an equally valid source (duck-typed)
    assert PutawayCost.from_pick(WorkloadParams.from_pick_config(cfg)) == put


def test_put_cost_charges_per_item_like_a_pick():
    cost = PutawayCost.from_pick(PickConfig(pick_intercept=15.0))
    speed = SpeedProfile(3.0, 2.0)
    one = put_cost(100.0, 0.0, 10, 100, 1, speed, cost)
    ten = put_cost(100.0, 0.0, 10, 100, 10, speed, cost)
    var = handle_var(10, 100, cost.weight_coef, cost.volume_coef)
    assert ten - one == pytest.approx(9 * (cost.per_item + var))


def test_receiving_is_put_aways_price_by_reference():
    cfg = PickConfig(pick_intercept=15.0, pick_per_item=0.5)
    put = PutawayCost.from_pick(cfg)
    recv = UnloadCost.from_putaway(put)
    assert recv.intercept == pytest.approx(put.intercept * DEFAULT_RECV_INTERCEPT_SCALE)
    assert recv.per_item == put.per_item
    assert (recv.weight_coef, recv.volume_coef) == (put.weight_coef, put.volume_coef)
    assert UnloadCost.from_putaway(put, intercept_scale=2.0).intercept == pytest.approx(
        2.0 * put.intercept)


def test_receiving_charges_per_pack_not_per_item():
    """Five pallets and a bag of singletons is SIX charges, never sixty: the per-item term
    lands once per pack, beside the intercept, and only the variable term is per unit."""
    cost = UnloadCost.from_putaway(PutawayCost.from_pick(PickConfig(pick_intercept=15.0)))
    w, v = 2.0, 800.0
    whole = unload_cost(w, v, 60, cost)                              # one 60-unit pack
    split = sum(unload_cost(w, v, 10, cost) for _ in range(5)) \
        + unload_cost(w, v, 10, cost)                                # 5 pallets + 1 bag
    assert split - whole == pytest.approx(5 * (cost.intercept + cost.per_item))
    assert split - whole != pytest.approx(5 * cost.intercept + 59 * cost.per_item)
    # and a pack's charge does not grow with the units in it
    var = handle_var(w, v, cost.weight_coef, cost.volume_coef)
    assert unload_cost(w, v, 60, cost) - unload_cost(w, v, 1, cost) == pytest.approx(59 * var)


# ── 5. the sabotage tests: the evaluator and the yardstick CARRY the charge ─────────

def test_the_gain_evaluator_carries_the_charge():
    """Zero the charge in the WorkloadParams the evaluator prices with and the priced
    seconds must move — by exactly visits × M(y) × q × per_item."""
    from Inbound.gain import GainBundle, _Evaluator
    from Inbound.space import SpaceView
    from Warehouse.catalog.Demand import Demand
    from Warehouse.catalog.Order import StorageHandleConfig
    from Warehouse.inventory.inventory_common import binkey_of, tier_ranks_for

    class _Bin:
        __slots__ = ('location', 'x_phys', 'y_phys')

        def __init__(self, aid, x, y):
            self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)

    class _Order:
        __slots__ = ('sku', 'storage_handle_config', 'demand', 'labor_cost', 'handle_var',
                     'expected_popularity')

        def __init__(self):
            self.sku = 1
            self.storage_handle_config = StorageHandleConfig('conveyable', 'food')
            self.demand = Demand.from_rates(1.0, 1.0)      # q = 1 per visit
            self.labor_cost, self.handle_var, self.expected_popularity = 1.0, 0.5, 1.0

    class _Unit:
        __slots__ = ('order', 'quantity', 'storage_size', 'unit_category')

        def __init__(self, order, quantity):
            self.order, self.quantity = order, quantity
            self.storage_size, self.unit_category = 'medium', 'pallet'

    key = ('conveyable', 'food', 'medium', 'pallet')
    b = _Bin(0, 480.0, 96.0)                                  # second bracket: M = 1.2
    view = SpaceView(empties={key: (b,)}, emptied_at={}, predicted={}, released_at=None,
                     versions=(0, 0, 0), frozen_at=0.0, window=None)

    def _price(wp):
        bundle = GainBundle(put_speed=SpeedProfile(2.0, 4.0), wp_of=lambda u: wp,
                            binkey_of=binkey_of, tier_ranks_for=tier_ranks_for)
        cost, takes = _Evaluator(bundle, view).place_load([_Unit(_Order(), 30)], set(), False)
        assert takes == [b]
        return cost

    with_charge = _price(WorkloadParams())
    without = _price(WorkloadParams(pick_per_item=0.0))
    m = height_multiplier(DEFAULT_HEIGHT_BRACKETS, b.y_phys)
    assert with_charge - without == pytest.approx(30 * m * 1 * DEFAULT_PICK_PER_ITEM)


def test_the_demand_mass_yardstick_carries_the_charge(monkeypatch):
    """`optimal_work` (the W* floor the leaf's config record carries) reads the charge
    off the WorkloadParams: zero it and the floor must drop."""
    import Warehouse.inventory.inventory_optimal as io_mod

    class _Bin:
        __slots__ = ('x_phys', 'y_phys', 'handling_type', 'storage_type',
                     'storage_size', 'unit_type')

        def __init__(self, x, y):
            self.x_phys, self.y_phys = float(x), float(y)
            self.handling_type, self.storage_type = 'conveyable', 'food'
            self.storage_size, self.unit_type = 'medium', 'pallet'

    class _Unit:
        def __init__(self, order):
            self.order = order
            self.storage_size, self.unit_category = 'medium', 'pallet'

    class _Order:
        def __init__(self, sku):
            self.sku = sku
            self.storage_handle_config = type(
                'SHC', (), {'handling': 'conveyable', 'category': 'food'})()

    class _Mgr(io_mod.OptimalLayoutMixin):
        def __init__(self, n_bins):
            self.warehouse = types.SimpleNamespace(
                bins=[_Bin(i * 10, (i % 3) * 100) for i in range(n_bins)])

        @staticmethod
        def _key(b):
            return (b.handling_type, b.storage_type, b.storage_size, b.unit_type)

        @staticmethod
        def _handle_var(order, wp):
            return 1.0

    orders = [_Order(s) for s in range(6)]
    monkeypatch.setattr(io_mod, 'viable_storage_units', lambda o, q: [_Unit(o)])
    monkeypatch.setattr(io_mod, '_equilibrium_qty', lambda o: 1)
    monkeypatch.setattr(io_mod, 'binkey_of',
                        lambda u: ('conveyable', 'food', 'medium', 'pallet'))
    freq = {o.sku: 1.0 + o.sku for o in orders}
    qty = {o.sku: 2.0 for o in orders}

    def _wp(charge):
        return types.SimpleNamespace(x_speed=1.0, y_speed=1.0, pick_intercept=1.0,
                                     pick_per_item=charge,
                                     height_brackets=DEFAULT_HEIGHT_BRACKETS)

    w_with = _Mgr(12)._optimal_work_assign(orders, freq, qty, _wp(DEFAULT_PICK_PER_ITEM))[0]
    w_zero = _Mgr(12)._optimal_work_assign(orders, freq, qty, _wp(0.0))[0]
    assert w_with > w_zero
    # a lower bound on the move: every unit pays at least f·q·per_item at M >= 1
    assert w_with - w_zero >= sum(freq[o.sku] * qty[o.sku] for o in orders) * DEFAULT_PICK_PER_ITEM - 1e-9


# ── 6. the three scales have all five seams (the spawn trap) ─────────────────────

def test_the_scales_are_declared_and_default_to_the_kernel():
    from Optimization.config import settings
    assert settings.PUT_INTERCEPT_SCALE == DEFAULT_PUT_INTERCEPT_SCALE
    assert settings.PUT_ITEM_RATIO == DEFAULT_PUT_ITEM_RATIO
    assert settings.RECV_INTERCEPT_SCALE == DEFAULT_RECV_INTERCEPT_SCALE


def test_the_scales_are_read_from_config_at_call_time():
    from Optimization.config.sim_config import CONFIG, crew_cost_spec
    g = CONFIG['global']
    was = {k: g.get(k) for k in ('put_intercept_scale', 'put_item_ratio', 'recv_intercept_scale')}
    try:
        g['put_intercept_scale'], g['put_item_ratio'], g['recv_intercept_scale'] = 0.3, 0.9, 2.0
        assert crew_cost_spec() == {'put_intercept_scale': 0.3, 'put_item_ratio': 0.9,
                                    'recv_intercept_scale': 2.0}
        # a pre-field run spec restores None; None resolves to the kernel default
        g['put_item_ratio'] = None
        assert crew_cost_spec()['put_item_ratio'] == DEFAULT_PUT_ITEM_RATIO
        # a declared 0.0 is a value, not an absence
        g['put_item_ratio'] = 0.0
        assert crew_cost_spec()['put_item_ratio'] == 0.0
    finally:
        g.update(was)


@pytest.mark.parametrize('flag', ['--put-intercept-scale', '--put-item-ratio',
                                  '--recv-intercept-scale'])
def test_the_scales_have_cli_flags(flag):
    import Optimization.run_simulation as rs
    assert f"'{flag}'" in inspect.getsource(rs), f'{flag} is not registered on the parser'


def test_the_scales_are_recorded_and_restored_on_resume():
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    for key in ("'put_intercept_scale'", "'put_item_ratio'", "'recv_intercept_scale'"):
        assert src.count(key) >= 2, f'{key} must be written to run_spec AND restored on resume'
    # (the standalone re-analysis half is covered by prefix in test_run_shaping_params:
    #  every recorded put_/recv_ key must be restored by _apply_run_shape)


def test_the_worker_payload_carries_the_scales():
    """Seam five.  Not in `_shared` = silently the kernel default in every spawned worker."""
    import Optimization.simdriver.workunits as wu
    assert 'crew_cost           = crew_cost_spec()' in inspect.getsource(wu)


def test_the_runner_prices_both_crews_from_the_payload_and_the_pick_config():
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    assert "args.get('crew_cost')" in src
    assert '_PutawayCost.from_pick(' in src, 'the put crew is not priced from its pick config'
    assert '_UnloadCost.from_putaway(' in src, 'receiving is not priced from the put crew'
    assert 'enable_putaway_timing(_put_crew.speed, size=_put_crew.size)' not in src, (
        'the put crew is back on the class-default price')


# ── 7. the archive: the leaf record carries the charge; a pre-charge leaf stays one ──

def test_the_leaf_config_record_carries_the_charge():
    import Optimization.simdriver.workunits as wu
    assert "'pick_per_item'   : pick_cfg.pick_per_item" in inspect.getsource(wu)


def test_a_pre_charge_archive_is_rebuilt_without_the_charge():
    """`run_map_precompute` rebuilds a PickConfig from an archived leaf config by field
    filtering, which would hand a pre-charge archive THIS checkout's 0.5.  The vintage is
    reconstructed at 0.0 instead — the page and the map describe the run, not the code."""
    import Optimization.run_map_precompute as rmp
    assert "kw.setdefault('pick_per_item', 0.0)" in inspect.getsource(rmp)
    import docs.macros as macros
    assert 'c.get("pick_per_item")' in inspect.getsource(macros)
