"""test_fill_trial.py -- the FILL TRIAL's driver mode (CONTEXT.md: Fill trial).

`.scratch/inbound-throughput/` 05: a run that starts EMPTY, receives its whole stock
declaration through the site yard at a declared pressure, and starts its ordinary pick stage
once every declared unit is binned.  The end-to-end behaviour is proven by the toy
(`_toy_fill`, and the `fill` smoketest profile): the fill settles, the crews shrink, the pick
stage runs, conservation holds, and two runs digest IDENTICAL.  The ordinary run's
byte-identity is the `_toy_priced` digest.  What this file pins is every piece that toy
cannot see go wrong:

  1. THE RECORD REFUSES every configuration that would run under the fill's name without
     being one -- no trailers, no standing yard, a trailer bound, a ratio that is not a queue
     -- and is None, exactly, when the fill is off.
  2. THE CREWS are ADR-0004's arithmetic over the declaration: `crew_size(load / span)` for
     both departments, the declaration priced by the packer the sim uses.
  3. THE DISPATCH RATE is priced off the SEATED crew: a fill crew the doors cannot seat
     drains at the seats' rate, and a dispatch priced off the whole crew would press the
     yard past one.
  4. THE DISPATCHER sends whole lots in ONE seeded world order, against a cumulative quota,
     books each on its owning leaf, reports settled only when nothing is anywhere, and hands
     the crews back IN PLACE (the pool, the dock and every bound queue share the lists).
  5. THE INTAKE declares without placing, and the uniform stock mode is dropped from a fill
     rather than planned and failed.

Run:  python -m pytest Tests/unit/test_fill_trial.py -q
"""
from __future__ import annotations

import argparse
import logging
import math
import types

import pytest

from Optimization.config.sim_config import CONFIG, fill_spec, inbound_spec
from Optimization.simconfig import staffing as st
from Warehouse.catalog.Order import Order

S = 28800.0


def _fill_on(g, **over):
    """The minimum CONFIG a fill record needs: trailers, the standing yard, a span."""
    g.update({'inbound_trailer_type': '53', 'inbound_standing_yard': True,
              'inbound_trailer_bound': None, 'inbound_fill_span_days': 40.0,
              'inbound_fill_ratio': 0.95, 'recv_crew_size': 5})
    g.update(over)


# ── 1. the record ────────────────────────────────────────────────────────────────────────

def test_the_record_is_none_when_the_fill_is_off():
    CONFIG['global']['inbound_fill_span_days'] = None
    assert fill_spec() is None


def test_the_record_carries_the_span_and_the_ratio():
    _fill_on(CONFIG['global'])
    assert fill_spec() == {'span_days': 40.0, 'ratio': 0.95}
    assert inbound_spec()['fill'] == {'span_days': 40.0, 'ratio': 0.95}


def test_an_ordinary_inbound_record_says_no_fill():
    g = CONFIG['global']
    _fill_on(g)
    g['inbound_fill_span_days'] = None
    assert inbound_spec()['fill'] is None


@pytest.mark.parametrize('over, match', [
    ({'inbound_trailer_type': None}, 'trailer pipeline'),
    ({'inbound_standing_yard': False}, 'STANDING_YARD'),
    ({'inbound_trailer_bound': 3}, 'TRAILER_BOUND'),
    ({'inbound_fill_ratio': 1.0}, 'strictly between 0 and 1'),
    ({'inbound_fill_ratio': 0.0}, 'strictly between 0 and 1'),
    ({'inbound_fill_span_days': 0}, 'positive number'),
    ({'inbound_fill_span_days': True}, 'positive number'),
])
def test_a_fill_that_would_not_be_one_is_refused(over, match):
    _fill_on(CONFIG['global'], **over)
    with pytest.raises(ValueError, match=match):
        fill_spec()


def test_a_fill_with_no_trailers_raises_from_the_inbound_record_too():
    """Validated ABOVE `inbound_spec`'s no-trailer return: otherwise a fill with no trailer
    type would come back as `None` -- an inbound-off run under the fill's name."""
    _fill_on(CONFIG['global'], inbound_trailer_type=None, inbound_standing_yard=False)
    with pytest.raises(ValueError, match='trailer pipeline'):
        inbound_spec()


def test_the_cli_refuses_a_fill_outside_the_coupled_era():
    from Optimization.run_simulation import _check_era_flags
    for era, coupled in ((False, True), (True, False)):
        ns = argparse.Namespace(inbound_fill_span_days=40.0, shift_drain_or_cap=era,
                                couple_channels=coupled)
        with pytest.raises(SystemExit, match='FILL TRIAL'):
            _check_era_flags(ns, set())


# ── 2. the crews ─────────────────────────────────────────────────────────────────────────

def _order(sku, eq, *, weight=10, dims=(10, 10, 10)):
    return Order.build(sku, 'conveyable', 'seasonal', *dims, weight, 0.5, 4.0,
                       lead_time_mean=0.0, supply_cv=0.0).declare_stock(eq, max(1, eq // 3))


@pytest.fixture()
def pricing():
    return st.PricingConfig(name='t', intercept=1.0, per_item=0.5, weight_coef=0.0,
                            volume_coef=0.0, weight_fn='log', volume_fn='log')


def test_the_declaration_is_priced_by_the_packer_the_sim_uses(pricing):
    """Each SKU's level is ONE lot, packed by `Inbound.pack.receive` and unloaded at the
    exact law -- the same seconds `implied_reorders` prices a reorder in."""
    from Inbound.pack import receive
    from Inbound.unload import unload_cost
    Order.next_sku = 1
    orders = [_order(1, 60), _order(2, 7)]
    got = st.declared_work(orders, pricing, put_intercept_scale=0.5, put_item_ratio=0.2,
                           recv_intercept_scale=1.0)
    recv_cost = pricing.recv(put_intercept_scale=0.5, put_item_ratio=0.2,
                             recv_intercept_scale=1.0)
    units = packs = 0
    recv_s = 0.0
    for c in orders:
        plan = receive(c, c.equilibrium_qty)
        units += plan.packed_qty
        packs += plan.unit_count
        recv_s += sum(unload_cost(c.weight, c.volume(), u.quantity, recv_cost)
                      for u in plan.units)
    assert (got['units'], got['packs']) == (units, packs) and units == 67
    assert math.isclose(got['recv_s'], recv_s)


def test_an_undeclared_order_raises_rather_than_pricing_a_level_of_one(pricing):
    Order.next_sku = 1
    c = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 4.0)
    with pytest.raises(ValueError, match='no declared level'):
        st.declared_work([c], pricing, put_intercept_scale=0.5, put_item_ratio=0.2,
                         recv_intercept_scale=1.0)


def test_the_fill_crews_are_adr_0004_over_the_span():
    declared = {'store': {'units': 1000, 'packs': 100, 'recv_s': 40_000.0},
                'fulfillment': {'units': 500, 'packs': 80, 'recv_s': 20_000.0}}
    s_put = {'store': {'value': 30.0}, 'fulfillment': {'value': 10.0}}
    f = st.derive_fill(declared=declared, s_put=s_put, span_days=2.0, ratio=0.9,
                       day_seconds=S, rho_put=0.85, rho_recv=0.85)
    put_load = (1000 * 30.0 + 500 * 10.0) / 2.0
    recv_load = 60_000.0 / 2.0
    assert f['put']['crew'] == math.ceil(put_load / (S * 0.85))
    assert f['receiving']['crew'] == math.ceil(recv_load / (S * 0.85))
    assert (f['units'], f['recv_s'], f['ratio'], f['span_days']) == (1500, 60_000.0, 0.9, 2.0)


# ── 3. the dispatch rate ─────────────────────────────────────────────────────────────────

def _fill(recv_crew, *, units=10_000, recv_s=200_000.0, ratio=0.95):
    return {'receiving': {'crew': recv_crew}, 'units': units, 'recv_s': recv_s,
            'ratio': ratio}


def test_the_rate_is_the_ratio_of_what_the_seated_crew_unloads():
    r = st.fill_dispatch_rate(_fill(8), doors=4, door_team=10, day_seconds=S)
    per_unit = 200_000.0 / 10_000
    assert r['seated'] == 8 and not r['seat_bound']
    assert math.isclose(r['units_per_day'], 0.95 * 8 * S / per_unit)
    assert math.isclose(r['dispatch_days'], 10_000 / r['units_per_day'])


def test_a_crew_the_doors_cannot_seat_drains_at_the_seats_rate():
    """115 receivers at four doors of ten: forty work at once.  Priced off 115 the dispatch
    would press the yard at 0.95 x 115 / 40 = 2.7 -- no queue at all."""
    r = st.fill_dispatch_rate(_fill(115), doors=4, door_team=10, day_seconds=S)
    assert r['seated'] == 40 and r['seat_bound']
    capped = st.fill_dispatch_rate(_fill(40), doors=4, door_team=10, day_seconds=S)
    assert math.isclose(r['units_per_day'], capped['units_per_day'])


def test_an_uncapped_dock_seats_the_whole_crew():
    r = st.fill_dispatch_rate(_fill(115), doors=1, door_team=None, day_seconds=S)
    assert r['seated'] == 115 and not r['seat_bound']


def test_the_payload_prices_the_rate_off_the_cells_own_doors():
    """Per CELL, not per pair: the door-scarcity axis varies the doors across the cells of
    one pair, so the rate cannot live in the pair's derivation."""
    from Optimization.simdriver.workunits import _fill_payload
    derived = {'span_days': 40.0, 'ratio': 0.95, 'units': 10_000, 'recv_s': 200_000.0,
               'put': {'crew': 30}, 'receiving': {'crew': 115}}
    leaf = {'inbound': {'fill': {'span_days': 40.0, 'ratio': 0.95}, 'doors': 2,
                        'door_team': 10},
            'staffing': {'derived': {'fill': derived}}}
    p = _fill_payload(leaf)
    assert p['seated'] == 20 and p['recv_crew'] == 115 and p['put_crew'] == 30
    assert p['max_days'] == math.ceil(2 * p['dispatch_days']) + 10
    assert _fill_payload({'inbound': {'fill': None}}) is None
    leaf['staffing'] = {'derived': {}}
    with pytest.raises(ValueError, match='derived no fill crews'):
        _fill_payload(leaf)


# ── 4. the dispatcher ────────────────────────────────────────────────────────────────────

class _Transit:
    def __init__(self):
        self.sent = []
        self.depth = 0

    def dispatch(self, sku, qty, lead, unit_volume=None, now_s=None):
        assert lead == 0, 'a declared lot has no supplier lead'
        self.sent.append((sku, qty, unit_volume, now_s))


def _unit(lots_by_leaf, *, put=(0.0,) * 6, recv=(0.0,) * 5, rate=100.0, seed=7):
    from Optimization.simdriver.strategy_runner import _FillDispatch
    booked = {k: [] for k in range(len(lots_by_leaf))}
    settled = {k: True for k in range(len(lots_by_leaf))}
    leaves = [types.SimpleNamespace(
                  channel=f'ch{k}', fill_lots=lots,
                  fill_credit=(lambda sku, qty, k=k: booked[k].append((sku, qty))),
                  fill_settled=(lambda k=k: settled[k]))
              for k, lots in enumerate(lots_by_leaf)]
    coord = types.SimpleNamespace(transit=_Transit(),
                                  dock=types.SimpleNamespace(depth=0, clocks=list(recv)))
    pool = types.SimpleNamespace(clocks=list(put))
    rec = {'units_per_day': rate, 'put_crew': len(put), 'recv_crew': len(recv)}
    fd = _FillDispatch(rec, leaves, coord, pool, seed_world=seed, pick_put=2, pick_recv=1,
                       log=logging.getLogger('test-fill'))
    return fd, coord, pool, booked, settled


_STORE = [(1, 30, 5), (3, 40, 5), (5, 10, 5)]
_FUL = [(2, 20, 7), (4, 50, 7)]


def test_the_world_order_is_one_seeded_shuffle_of_both_channels():
    a, *_ = _unit([_STORE, _FUL])
    b, *_ = _unit([_STORE, _FUL])
    assert a.lots == b.lots, 'same seed, same declaration: same world order'
    assert sorted(r[0] for r in a.lots) == [1, 2, 3, 4, 5]
    assert {r[3] for r in a.lots} == {0, 1}, 'both channels ride one order'
    # the order is a function of the declaration, not of the order the leaves listed it in
    rev, *_ = _unit([list(reversed(_STORE)), list(reversed(_FUL))])
    assert [r[:3] for r in rev.lots] == [r[:3] for r in a.lots]
    orders = {tuple(r[0] for r in _unit([_STORE, _FUL], seed=s)[0].lots) for s in range(12)}
    assert len(orders) > 1, 'a different world seed must be able to move the order'


def test_the_quota_is_cumulative_and_sends_whole_lots():
    fd, coord, _, booked, _ = _unit([_STORE, _FUL], rate=45.0)
    fd.dispatch(100.0)
    sent1 = sum(q for _, q, _, _ in coord.transit.sent)
    assert sent1 >= 45 and sent1 - coord.transit.sent[-1][1] < 45, 'stops at the first lot past the quota'
    fd.dispatch(200.0)
    sent2 = sum(q for _, q, _, _ in coord.transit.sent)
    assert sent2 >= 90 and sent2 - coord.transit.sent[-1][1] < 90
    while fd._next < len(fd.lots):
        fd.dispatch(300.0)
    assert sum(q for _, q, _, _ in coord.transit.sent) == 150
    # every lot booked on the leaf that owns it, once
    assert sorted(booked[0]) == sorted((s, q) for s, q, _ in _STORE)
    assert sorted(booked[1]) == sorted((s, q) for s, q, _ in _FUL)
    assert {t for *_, t in coord.transit.sent} == {100.0, 200.0, 300.0}


def test_settled_needs_everything_sent_nothing_on_site_and_every_leaf_binned():
    fd, coord, _, _, settled = _unit([_STORE, _FUL], rate=1e9)
    assert not fd.settled(), 'nothing sent yet'
    fd.dispatch(0.0)
    assert fd.settled()
    coord.transit.depth = 1
    assert not fd.settled()
    coord.transit.depth, coord.dock.depth = 0, 3
    assert not fd.settled()
    coord.dock.depth = 0
    settled[1] = False
    assert not fd.settled()


def test_the_crews_are_handed_back_in_place():
    """The pool, the dock and every put queue bound to the pool hold the SAME lists, so the
    shrink must mutate them -- a rebind would leave the queues on the fill crew."""
    fd, coord, pool, _, _ = _unit([_STORE, _FUL], put=(0.0,) * 6, recv=(0.0,) * 5)
    put_list, recv_list = pool.clocks, coord.dock.clocks
    fd.end(3)
    assert pool.clocks is put_list and coord.dock.clocks is recv_list
    assert (len(put_list), len(recv_list)) == (2, 1)
    grow, coord2, pool2, _, _ = _unit([_STORE], put=(0.0,), recv=(0.0,))
    grow.end(0)
    assert (len(pool2.clocks), len(coord2.dock.clocks)) == (2, 1), 'a smaller fill crew grows back'


def test_a_leaf_without_lots_is_refused():
    from Optimization.simdriver.strategy_runner import _FillDispatch
    leaf = types.SimpleNamespace(channel='store', fill_lots=None, fill_credit=None,
                                 fill_settled=None)
    with pytest.raises(ValueError, match='declared no lots'):
        _FillDispatch({'units_per_day': 1.0, 'put_crew': 1, 'recv_crew': 1}, [leaf],
                      types.SimpleNamespace(), types.SimpleNamespace(), seed_world=1,
                      pick_put=1, pick_recv=1, log=logging.getLogger('x'))


# ── 5. the intake and the arms ───────────────────────────────────────────────────────────

def test_declare_all_records_the_declaration_and_places_nothing():
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    Order.next_sku = 1
    orders = [_order(1, 60), _order(2, 9)]
    mgr = types.SimpleNamespace(_originals={}, _initial_quantities={}, _queued_qty={},
                                _deferred_qty={})
    lots = Inventory_Manager.declare_all(mgr, orders)
    assert lots == [(1, 60, orders[0].volume()), (2, 9, orders[1].volume())]
    assert set(mgr._originals) == {1, 2}
    assert mgr._initial_quantities == {1: 60, 2: 9}
    assert mgr._queued_qty == {} and mgr._deferred_qty == {}, \
        'nothing is on order until the dispatcher sends it'


def test_a_fill_drops_the_uniform_stock_arms_and_an_ordinary_run_keeps_them():
    from Optimization.config.strategies import strategies_for
    from Optimization.simdriver.workunits import _channel_strategies
    ch = types.SimpleNamespace(restocks=('fifo', 'rank_cartlabor'))
    g = CONFIG['global']
    g['inbound_fill_span_days'] = None
    assert [s.key for s in _channel_strategies(ch)] == [s.key for s in strategies_for(ch.restocks)]
    _fill_on(g)
    kept = _channel_strategies(ch)
    assert kept and all(s.stock_mode == 'policy' for s in kept)
    assert [s.key for s in kept] == [s.key for s in strategies_for(ch.restocks)
                                     if s.stock_mode == 'policy']


# ── 6. the review's findings, pinned ─────────────────────────────────────────────────────

def test_a_fill_spec_refuses_its_unfillable_cells_before_anything_runs():
    from Optimization.config.whatif_config import (PHASE2_RUN_DEFAULTS, _refuse_unfillable_cells,
                                                   get_spec, phase2_inbound_axis)
    get_spec('_toy_fill')                           # the registered fill spec passes
    rd = {**PHASE2_RUN_DEFAULTS, 'inbound_fill_span_days': 2.0}
    with pytest.raises(ValueError, match=r"\['gmyopic_k8', 'inb_off'\]"):
        _refuse_unfillable_cells({'run_defaults': rd, 'inbound': phase2_inbound_axis()}, 'x')
    with pytest.raises(ValueError, match='coupled era'):
        _refuse_unfillable_cells({'run_defaults': {**rd, 'couple_channels': False},
                                  'inbound': []}, 'x')
    # an ordinary spec is untouched, inbound-off cell and all
    _refuse_unfillable_cells({'run_defaults': PHASE2_RUN_DEFAULTS,
                              'inbound': phase2_inbound_axis()}, 'x')


def test_a_fill_refuses_the_unload_cost_overlays():
    """The fill is priced at the put-away-derived unload law; a dock charging another price
    would press the yard at some ratio other than the declared one."""
    _fill_on(CONFIG['global'], inbound_unload_intercept=2.0)
    with pytest.raises(ValueError, match='unload-cost overlays'):
        fill_spec()


class _Stub:
    """A leaf as the driver sees it: its halves record the batches they were handed."""

    def __init__(self, channel, start_i, n):
        self.channel, self.start_i, self.n_batches = channel, start_i, n
        self.n_catalogue, self.n_skus = 10, 5
        self.seen, self.off = [], None
        self.fill_lots = []

    def replenish(self, i):
        self.seen.append(i)

    def step(self, i):
        pass

    def finish(self):
        return {'strategy': self.channel}

    def note_triggered(self, _t):
        pass

    def charge(self, *_a):
        pass

    def begin_pick(self, off):
        self.off = off


def _drive(monkeypatch, *, start_i, n=4, settle_after=3):
    from Optimization.simdriver import strategy_runner as sr
    leaves = [_Stub('store', start_i, n), _Stub('fulfillment', start_i, n)]
    it = iter(leaves)
    calls = {'settled': 0}

    class _Fill:
        def settled(self):
            calls['settled'] += 1
            return calls['settled'] >= settle_after

        def end(self, i):
            calls['end'] = i

        def census(self):
            return 'stub'

    dock = types.SimpleNamespace(drive=lambda i, p: {}, collect=lambda i: None,
                                 finish=lambda: None)
    monkeypatch.setattr(sr, '_check_site_crews', lambda a: None)
    monkeypatch.setattr(sr, '_build_put_pool', lambda a: None)
    monkeypatch.setattr(sr, '_build_site_gain', lambda a: None)
    monkeypatch.setattr(sr, '_build_site_dock', lambda a, p, l: dock)
    monkeypatch.setattr(sr, '_build_leaf', lambda la, **k: next(it))
    monkeypatch.setattr(sr, '_build_fill', lambda a, lv, s, p, l: _Fill())
    sr._run_strategy_worker_impl({'leaves': [{}, {}], 'fill': {'max_days': 20}})
    return leaves, calls


def test_a_fresh_fill_fills_until_settled_then_runs_the_pick_stage(monkeypatch):
    leaves, calls = _drive(monkeypatch, start_i=0, n=4, settle_after=3)
    assert calls['end'] == 2, 'settled after the third fill day (batch 2)'
    for lf in leaves:
        assert lf.off == 3
        assert lf.seen == list(range(0, 3 + 4)), 'three fill days, then exactly n pick batches'


def test_a_finished_fill_arm_resubmitted_runs_no_batch(monkeypatch):
    """The review's critical case: a finished pair re-planned before its cell finalized
    arrives at its pinned marker (n, in pick-stage batches) and must write nothing."""
    leaves, calls = _drive(monkeypatch, start_i=4, n=4)
    assert all(lf.seen == [] for lf in leaves)
    assert calls['settled'] == 0 and 'end' not in calls


def test_a_fill_unit_refuses_a_resume_from_the_middle(monkeypatch):
    with pytest.raises(RuntimeError, match='cannot resume at batch 2'):
        _drive(monkeypatch, start_i=2, n=4)


def test_an_unsettled_fill_raises_at_its_cap(monkeypatch):
    with pytest.raises(RuntimeError, match='did not settle in 20'):
        _drive(monkeypatch, start_i=0, settle_after=10**6)
