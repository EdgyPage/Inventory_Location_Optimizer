"""test_putaway_pool.py — one crew of putters over two channels' segregated volume.

Site-dock 19, building 04's answer.  The pool is the thing that ends the double count:
`workunits.py` handed EACH leaf the whole derived site crew, so two independent processes
fielded the site's labour twice.  Under the pool there is ONE clock list, ONE uid block and
ONE absolute carry, and the day is divided between the two channels by their own recorded
expectations.

What is pinned here, and why each of them is a silent failure otherwise:

  * **The division.** With shared clocks and a fixed leaf order the second leaf would absorb
    every cut, every day, as a pure artefact of loop order — and that bias would look exactly
    like a finding.  Both directions of the residue pass are proven, including the one the
    loop order hides (the STORE leaf finishing early).
  * **The reset, owned once.** A leaf drain that reset the shared clocks would zero the other
    leaf's half-spent day with nothing raising and every subsequent row plausible.
  * **The cut, charged once.** `cut` is a LEVEL: it re-counts the standing queue every time
    it is charged, so two drains each charging inflate it INSIDE one batch, where no
    downstream "count the non-zero batches" rule can undo it (memory `cut-is-a-level-not-a-
    flow`, which published a 101x number once already).
  * **Two prices over one crew.** The put price is keyed by CHANNEL and the crew is not, so
    the same putter works at two rates depending on whose pack is on the cart.  That is the
    class of thing the site-dock charter requires be stated and TESTED rather than assumed.

The pool duck-types its leaves, so most of this runs against a recorder rather than a
manager; the three facts that are about a real manager (the shared list, the reset, the two
prices) use one.

Run:  python -m pytest Tests/unit/test_putaway_pool.py -q
"""
from __future__ import annotations

import random

import pytest

from Inbound.putaway_pool import PutawayPool
from Warehouse.kernel.timeline import WorkDay
from Warehouse.kernel import crew_clock
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.kernel.cost_model import SpeedProfile

_DAY = 28800.0
_CH = ('store', 'fulfillment')


class _Recorder:
    """A leaf-shaped recorder: the three ports, and what it was asked for.

    Records the deadlines rather than placing anything, because the pool's whole job is
    deciding WHICH deadline each leaf drains against and in what order.
    """

    def __init__(self, name):
        self.name = name
        self.drains: list = []
        self.cuts: list = []

    def drain_putaway(self, deadline=None, charge_cut=True):
        self.drains.append((deadline, charge_cut))

    def count_put_cut(self, deadline):
        self.cuts.append(deadline)


def _pool(eu=None, crew=2, channels=_CH, **kw):
    eu = {'store': 0.25, 'fulfillment': 0.75} if eu is None else eu
    kw.setdefault('releases_per_day', 1)
    kw.setdefault('cut_at_day_end', True)
    return PutawayPool(crew_clock.new_clocks(crew), tuple(range(crew)), eu,
                       WorkDay(length=_DAY), channels=channels, **kw)


def _bound(eu=None, crew=2, channels=_CH):
    p = _pool(eu=eu, crew=crew, channels=channels)
    leaves = {c: _Recorder(c) for c in channels}
    for c in channels:
        p.bind(leaves[c], c)
    return p, leaves


# ── 1. the day's division ────────────────────────────────────────────────────────

def test_each_leaf_drains_at_its_own_share_of_the_day():
    """`derived.put.expected_utilization` is `load / (crew x S)`, so crew and day cancel and
    the share is just the ratio — no new record field.  Stacked, not flat: the clocks are
    SHARED, so the second leaf's whistle is a point on the same day the first already spent
    part of."""
    p, leaves = _bound(eu={'store': 0.25, 'fulfillment': 0.75})
    base, deadline = p.open_batch(0)
    assert deadline == _DAY
    p.drain(leaves['store'], deadline)
    p.drain(leaves['fulfillment'], deadline)
    assert leaves['store'].drains[0][0] == pytest.approx(0.25 * _DAY)
    assert leaves['fulfillment'].drains[0][0] == pytest.approx(_DAY)


def test_a_single_channel_site_gets_the_whole_day_and_no_residue():
    """04 section 3's degenerate case, asserted rather than assumed: share 1.0 and the
    residue pass is a no-op, so a site with one channel is correct for free."""
    p, leaves = _bound(eu={'store': 1.0}, channels=('store',))
    _, deadline = p.open_batch(0)
    p.drain(leaves['store'], deadline)
    # One pass-1 drain at the whole day, then the residue pass -- which asks for the same
    # day it just drained against, so nothing can move.
    assert [d for d, _ in leaves['store'].drains] == [_DAY, _DAY]
    assert leaves['store'].cuts == [_DAY]


def test_a_site_expecting_no_put_work_starves_nobody():
    """No expectation to divide is not a reason to give one channel nothing: with a zero
    total every leaf drains against the whole day, which is exactly today's behaviour."""
    p, leaves = _bound(eu={'store': 0.0, 'fulfillment': 0.0})
    _, deadline = p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], deadline)
    assert leaves['store'].drains[0][0] == _DAY


def test_a_channel_absent_from_the_expectation_is_refused():
    """Silently starved is the alternative: a missing key would read as share 0.0 and the
    leaf would place nothing until the residue pass, every day, looking like a finding."""
    with pytest.raises(ValueError, match='no recorded put expectation'):
        _pool(eu={'store': 1.0})


def test_the_last_share_is_exactly_one():
    """A whistle a float short of the day would cut the last leaf by one job for no
    modelled reason, and the row would look like an ordinary day-boundary cut."""
    p, leaves = _bound(eu={'store': 1 / 3, 'fulfillment': 2 / 3})
    _, deadline = p.open_batch(0)
    p.drain(leaves['store'], deadline)
    p.drain(leaves['fulfillment'], deadline)
    assert leaves['fulfillment'].drains[0][0] == _DAY


# ── 2. the residue pass, in BOTH directions ─────────────────────────────────────

def test_the_residue_pass_re_drains_every_leaf_against_the_whole_day():
    """"A channel that finishes early releases labour to the other", made literal.  The
    earlier-to-later direction is free -- the cumulative deadline hands the later leaf
    whatever the earlier one did not spend -- so this is the direction the loop order
    HIDES: the store leaf, drained first, gets a second chance at the whole day."""
    p, leaves = _bound()
    _, deadline = p.open_batch(0)
    p.drain(leaves['store'], deadline)
    assert leaves['store'].drains == [(0.25 * _DAY, False)], 'the residue ran too early'
    assert not leaves['fulfillment'].drains
    p.drain(leaves['fulfillment'], deadline)
    assert [d for d, _ in leaves['store'].drains] == [0.25 * _DAY, _DAY]
    assert [d for d, _ in leaves['fulfillment'].drains] == [_DAY, _DAY]


def test_the_store_leaf_finishing_early_releases_its_hours_to_fulfillment():
    """The other direction, over a REAL clock list: the store leaf spends a quarter of its
    share, and fulfillment's whistle is still the whole day -- so the unspent hours are
    genuinely available rather than lost with the sub-deadline."""
    p, leaves = _bound(eu={'store': 0.5, 'fulfillment': 0.5})
    _, deadline = p.open_batch(0)

    def _cheap(deadline=None, charge_cut=True):
        # the store leaf works 10 minutes of its half-day and stops
        if len(leaves['store'].drains) == 0:
            crew_clock.charge(p.clocks, 600.0)
        leaves['store'].drains.append((deadline, charge_cut))

    leaves['store'].drain_putaway = _cheap
    p.drain(leaves['store'], deadline)
    p.drain(leaves['fulfillment'], deadline)
    assert crew_clock.can_start(p.clocks, leaves['fulfillment'].drains[0][0]), (
        'fulfillment cannot start inside its own whistle after the store leaf finished '
        'early -- the shared clocks have been spent by somebody')
    assert min(p.clocks) == 0.0, 'the second putter never picked anything up'


# ── 3. the cut, charged ONCE and against the FULL day ───────────────────────────

def test_neither_pass_charges_the_cut_and_the_pool_charges_once():
    """`cut` is a LEVEL.  Two drains each charging would re-count the standing queue inside
    one batch, and no downstream "count the non-zero batches" rule can undo an inflation
    that lands there."""
    p, leaves = _bound()
    _, deadline = p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], deadline)
    for c in _CH:
        assert all(charge is False for _, charge in leaves[c].drains), (
            f'{c}: a drain charged the cut; with two passes that is the level inflated')
        assert leaves[c].cuts == [deadline], (
            f'{c}: the cut was charged {len(leaves[c].cuts)} time(s), not once')


def test_the_cut_is_counted_after_the_residue_not_before():
    """Counted before, it would charge a queue the residue pass then serves -- work that
    was never actually stopped by the whistle."""
    p, leaves = _bound()
    order: list = []
    for c in _CH:
        leaves[c].drain_putaway = (lambda n: lambda d=None, charge_cut=True:
                                   order.append(('drain', n)))(c)
        leaves[c].count_put_cut = (lambda n: lambda d: order.append(('cut', n)))(c)
    _, deadline = p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], deadline)
    assert [k for k, _ in order][-2:] == ['cut', 'cut']
    assert order.index(('cut', 'store')) > order.index(('drain', 'fulfillment'))


# ── 4. the site day: one base, one carry, one reset ─────────────────────────────

def test_both_leaves_read_the_same_epoch():
    """One shared clock list cannot carry two epochs: two carries over one list would stamp
    the same worker's same second at two different absolute instants."""
    p, _ = _bound()
    assert p.open_batch(3) == p.open_batch(3)
    assert p.open_batch(3)[0] == 3 * _DAY


def test_the_base_is_the_site_day_start_not_a_leaf_batch_start():
    """04 section 9's rejected alternative: basing on either leaf's batch start would idle
    the site's putters whenever EITHER pick crew overran its day, and would misattribute a
    picking overrun to put-away's cut."""
    p, leaves = _bound()
    base, deadline = p.open_batch(2)
    assert base == 2 * _DAY and deadline == _DAY


def test_an_overrunning_carry_shortens_the_day_it_lands_in():
    """base + deadline is the day's END however far the carry has run, so a crew that
    worked into the evening starts tomorrow with less of it, not with a whole fresh one."""
    p, leaves = _bound()
    p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], _DAY)
    for c in _CH:
        p.note_records(leaves[c], _DAY + 1000.0 if c == 'store' else None)
    base, deadline = p.open_batch(1)
    assert base == _DAY + 1000.0
    assert base + deadline == 2 * _DAY


def test_the_clocks_reset_once_after_every_leaf_has_reported():
    """THE TRAP THIS MODULE EXISTS FOR.  A leaf drain that reset would zero the other leaf's
    half-spent day with nothing raising, and every subsequent row would be plausible."""
    p, leaves = _bound()
    p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], _DAY)
    crew_clock.charge(p.clocks, 500.0)
    p.note_records(leaves['store'], 500.0)
    assert max(p.clocks) == 500.0, 'the first leaf report reset the shared crew'
    p.note_records(leaves['fulfillment'], None)
    assert max(p.clocks) == 0.0, 'the last leaf report did not reset the shared crew'
    assert p.put_clock == 500.0


def test_the_carry_does_not_move_when_nobody_worked():
    p, leaves = _bound()
    p.open_batch(0)
    for c in _CH:
        p.drain(leaves[c], _DAY)
    for c in _CH:
        p.note_records(leaves[c], None)
    assert p.put_clock == 0.0


# ── 5. the refusals ────────────────────────────────────────────────────────────

def test_a_pool_without_a_whistle_is_refused():
    with pytest.raises(ValueError, match='working day to divide'):
        _pool(cut_at_day_end=False)


def test_a_pool_over_a_grid_that_is_not_one_batch_per_day_is_refused():
    with pytest.raises(ValueError, match='one batch is one site day'):
        _pool(releases_per_day=2)
    with pytest.raises(ValueError, match='one batch is one site day'):
        _pool(releases_per_day=None)


def test_an_unbound_leaf_is_refused():
    p, _ = _bound()
    p.open_batch(0)
    with pytest.raises(ValueError, match='without being bound'):
        p.drain(_Recorder('stranger'), _DAY)


def test_binding_the_same_channel_twice_is_refused():
    p, leaves = _bound()
    with pytest.raises(ValueError, match='already bound'):
        p.bind(_Recorder('store'), 'store')


def test_a_leaf_that_drains_twice_in_one_day_is_refused():
    p, leaves = _bound()
    _, d = p.open_batch(0)
    p.drain(leaves['store'], d)
    with pytest.raises(RuntimeError, match='drained twice'):
        p.drain(leaves['store'], d)


def test_a_leaf_that_drains_against_another_day_is_refused():
    p, leaves = _bound()
    _, d = p.open_batch(0)
    with pytest.raises(RuntimeError, match='drained against a deadline'):
        p.drain(leaves['store'], d - 1.0)


def test_a_day_that_opens_while_a_leaf_still_owes_is_refused():
    p, leaves = _bound()
    _, d = p.open_batch(0)
    p.drain(leaves['store'], d)
    with pytest.raises(RuntimeError, match='still owes'):
        p.open_batch(1)


def test_reporting_records_before_draining_is_refused():
    """Put-away runs before the rows are stamped, so this ordering means a leaf is about to
    place work against clocks that are about to be reset."""
    p, leaves = _bound()
    p.open_batch(0)
    p.drain(leaves['store'], _DAY)
    with pytest.raises(RuntimeError, match='has not\\s+drained|has not drained'):
        p.note_records(leaves['store'], 1.0)


# ── 6. the driver's builder: one crew, one uid block above BOTH channels ───────

def _unit(k_store=10, k_ful=30, crew=3, **wd):
    """A unit payload with only what `_build_put_pool` reads -- no leaves are built."""
    day = {'seconds': _DAY, 'releases_per_day': 1, 'cut_at_day_end': True, **wd}
    return {
        'put_crew': {'size': crew, 'mode': 'foot', 'x_speed': 2.0, 'y_speed': 4.0},
        'staffing': {'derived': {
            'put': {'crew': crew, 'expected_utilization': {'store': 0.4,
                                                           'fulfillment': 0.6}}}},
        'leaves': [
            {'channel_name': 'store', 'k_pickers': k_store,
             'work_day': day, 'shift_seconds': _DAY},
            {'channel_name': 'fulfillment', 'k_pickers': k_ful,
             'work_day': day, 'shift_seconds': _DAY},
        ],
    }


def test_the_put_uid_block_clears_both_channels_pickers():
    """04 section 2.  Chained off EITHER leaf's own pickers, the same physical putter gets
    uid `k_store` in the store DB and `k_ful` in the fulfillment DB, and any site-level
    rollup joining on `actor_uid` merges two different people.  The smaller leaf gets a gap
    instead -- nothing reads uids densely, and a dense-but-lying uid is worse than a
    sparse-but-true one."""
    from Optimization.simdriver import strategy_runner as sr
    p = sr._build_put_pool(_unit(k_store=10, k_ful=30, crew=3))
    assert [w.uid for w in p.workers] == [30, 31, 32]
    assert len(p.clocks) == 3, 'the site crew is the DERIVED crew, once, not once per leaf'
    # and the other way round, so the rule is `max` and not "the second leaf"
    p2 = sr._build_put_pool(_unit(k_store=40, k_ful=5, crew=2))
    assert [w.uid for w in p2.workers] == [40, 41]


def test_a_coupled_unit_without_a_derived_block_is_refused():
    """Coupling is an era feature: the site crews are DERIVED, never declared.  Without the
    block there is no site crew to pool and no recorded expectation to divide the day by,
    and the alternative -- falling back to the per-leaf crews -- is the double count with a
    coupled label on it."""
    from Optimization.simdriver import strategy_runner as sr
    ua = _unit()
    ua['staffing'] = {'inputs': {}}
    with pytest.raises(ValueError, match='no derived staffing block'):
        sr._build_put_pool(ua)


def test_leaves_that_describe_different_working_days_are_refused():
    """One site put crew works ONE day.  The record reaches the worker per leaf, so two
    copies is a shape a hand-assembled unit can have -- and two days here would give the
    two channels different whistles over the same people."""
    from Optimization.simdriver import strategy_runner as sr
    ua = _unit()
    ua['leaves'][1]['work_day'] = {**ua['leaves'][1]['work_day'], 'seconds': _DAY / 2}
    with pytest.raises(ValueError, match='different work_day'):
        sr._build_put_pool(ua)


def test_the_builder_refuses_a_unit_without_the_working_day_grid():
    """The pool's own precondition, reached through the builder -- so the refusal is on the
    path production takes and not only on a hand-made pool."""
    from Optimization.simdriver import strategy_runner as sr
    with pytest.raises(ValueError, match='working day to divide'):
        sr._build_put_pool(_unit(cut_at_day_end=False))
    with pytest.raises(ValueError, match='one batch is one site day'):
        sr._build_put_pool(_unit(releases_per_day=4))


# ── 7. against a real manager: one list, two prices ────────────────────────────

def _manager(seed: int = 0) -> Inventory_Manager:
    Aisle.next_aisle_id = 1
    random.seed(seed)
    w, h = aisle_width_for(2), aisle_height_for(2)
    cfg = WarehouseConfig(
        total_aisles=2, aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium'], None),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ])
    return Inventory_Manager(Warehouse_Builder().from_config(cfg).build())


def test_two_managers_bound_to_one_pool_share_the_SAME_list():
    """Identity, not equality.  Two lists of the same length is exactly the defect the pool
    replaces: `crew_clock.reset` mutates in place, so sharing the object is what makes a
    putter busy on one stream busy on the other."""
    p = _pool()
    speed = SpeedProfile(2.0, 4.0)
    a, b = _manager(1), _manager(2)
    for m in (a, b):
        m.enable_putaway_timing(speed, size=len(p.clocks), clocks=p.clocks)
    qa = a.put_queues.queues[0]
    qb = b.put_queues.queues[0]
    assert qa.clocks is p.clocks and qb.clocks is p.clocks
    crew_clock.charge(qa.clocks, 120.0)
    assert qb.finish == 120.0, 'a put on one channel left the other channel idle'


def test_a_manager_drain_can_leave_the_shared_crew_standing():
    """`drain_putaway_records(reset_clocks=False)` is the seam the pool owns the reset
    through; the default is still every uncoupled caller."""
    p = _pool()
    m = _manager()
    m.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=len(p.clocks), clocks=p.clocks)
    crew_clock.charge(p.clocks, 90.0)
    m.drain_putaway_records(reset_clocks=False)
    assert max(p.clocks) == 90.0
    m.drain_putaway_records()
    assert max(p.clocks) == 0.0


def test_one_crew_works_at_two_prices_and_the_ratio_is_the_two_put_constants():
    """04 section 6, stated and tested.  The put price is built from the CHANNEL's pick
    config (`staffing.py`: "a site-wide put price is what this derivation used to get
    wrong"), and the crew is not — so the same putter works at two rates depending on whose
    pack is on the cart.  That is defensible (the price is a property of the work's geometry
    and handling, not of the person) and it must FAIL the day a site-wide price sneaks back
    in."""
    from Warehouse.operations.putaway import PutawayCost, put_cost
    cheap = PutawayCost(intercept=10.0)
    dear = PutawayCost(intercept=30.0)
    speed = SpeedProfile(2.0, 4.0)
    kw = dict(x=40.0, y=10.0, weight=5.0, volume=100.0, quantity=1)
    a = put_cost(kw['x'], kw['y'], kw['weight'], kw['volume'], kw['quantity'], speed, cheap)
    b = put_cost(kw['x'], kw['y'], kw['weight'], kw['volume'], kw['quantity'], speed, dear)
    assert b - a == pytest.approx(dear.intercept - cheap.intercept), (
        'the two channels priced the same trip identically; one crew at two prices is the '
        'coupled model, and a single site-wide price is the defect it replaced')
    # And the charge lands on one shared crew whichever price produced it.
    clocks = crew_clock.new_clocks(1)
    crew_clock.charge(clocks, a)
    crew_clock.charge(clocks, b)
    assert clocks[0] == pytest.approx(a + b)
