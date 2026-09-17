"""test_leaf_scope.py — who answers a leaf's question, and the rung that cannot exist.

`_build_leaf` asked `site is None` / `pool is None` twenty-eight times and re-decided the same
dispatch at every one. `LeafScope` is that decision made once. This file fences three things:

  * **THE LADDER.** `pool`, `site_gain` and `site` looked like three independent `None`-able
    parameters -- eight combinations, three legal, guarded in three different builders. They are
    not independent: `_build_put_pool` never returns None, and `_build_site_dock` reads
    `pool.workers[-1].uid`, so **site implies pool implies coupled**. The fourth corner is
    UNNAMEABLE here rather than guarded, and `scope_for` refuses it if it is ever constructed by
    hand.

  * **THE MIDDLE RUNG ANSWERS INBOUND EXACTLY AS SOLO DOES.** A coupled unit with inbound off is
    supposed to be byte-identical to an uncoupled one on every inbound number (site-dock 06).
    That was a promise held up by twenty-eight call sites each testing the right variable; it is
    now structural, and `test_the_pooled_rung_overrides_nothing_inbound` is what keeps it so.

  * **THE DOMAIN-LAYER REFUSALS BECOME A BACKSTOP.** Eight accessors under `Warehouse/` and
    `Inbound/` raise under a site scope, which is a driver concept leaking into the domain. The
    manager stub here raises on exactly those, so a `SiteScope` that consulted the leaf for a
    site-owned question fails HERE rather than surviving because the refusal caught it.

WHAT THIS FILE DELIBERATELY DOES NOT CLAIM. The ticket asked for "each leaf gets its own share
and the shares close against the dock's totals" as a unit assertion. That property belongs to
the COORDINATOR, not the scope -- asserting it against a fake coordinator would assert the fake.
What the scope owns, and what is asserted below, is that every site-owned question is routed to
the coordinator WITH THE ASKING LEAF's manager, which is the precondition that makes a partition
possible at all. The closure itself stays where it is proved against real rows
(`Tests/unit/test_site_receiving_totals.py`).

Run:  python -m pytest Tests/unit/test_leaf_scope.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.simdriver.leaf_scope import (
    LeafScope, PooledScope, SiteScope, scope_for)


#: The members whose answer moves to the SITE. Written out rather than derived from
#: `SiteScope.__dict__` so that adding an override without deciding it belongs to inbound is a
#: failure here, not a silently widened seam.
INBOUND_MEMBERS = frozenset({
    'workers', 'dock', 'transit', 'coord', 'bind_receiving',
    'transit_census', 'lead_depth', 'in_transit', 'receiving_snapshot', 'dock_depth',
    'yard_rows', 'standing_yard', 'recv_drain', 'note_recv', 'drives_own_composition',
})

#: The members whose answer moves to the POOL.
PUT_MEMBERS = frozenset({
    'put_workers', 'put_clocks', 'put_uid_after', 'bind_put', 'put_window',
    'put_clock_for', 'put_base_for_batch', 'note_put', 'reset_put_clocks',
})


# ── the stubs ─────────────────────────────────────────────────────────────────────────

class _Mgr:
    """A leaf's manager. The six accessors that REFUSE under a site scope refuse here too, for
    the same reason the real ones do -- three of the four receiving values reset, so the first
    caller would take the site's whole batch and leave the other channel reporting an idle
    dock."""

    def __init__(self, name: str, *, site_scoped: bool = False):
        self.name = name
        self._site_scoped = site_scoped
        self.asked: list = []

    def _refuse(self, what):
        if self._site_scoped:
            raise RuntimeError(
                f'{self.name}.{what} is site-scoped and refuses; a driver asked the LEAF a '
                f'question the SITE owns')
        self.asked.append(what)

    def transit_snapshot(self):
        self._refuse('transit_snapshot')
        return [(10, 4, 2)]

    def receiving_snapshot(self):
        self._refuse('receiving_snapshot')
        return (1, 2, 3, 4.0)

    def drain_receiving_records(self):
        self._refuse('drain_receiving_records')
        return [('r', self.name)]

    def drain_repack_records(self):
        self._refuse('drain_repack_records')
        return [('rp', self.name)]

    def drain_yard_trailers(self):
        self._refuse('drain_yard_trailers')
        return [('t', self.name)]

    def drain_yard_drains(self):
        self._refuse('drain_yard_drains')
        return [(1, 2)]

    def standing_yard_trailers(self):
        self._refuse('standing_yard_trailers')
        return [('standing', self.name)]

    @property
    def dock_depth(self):
        self._refuse('dock_depth')
        return 7

    lead_queue_depth = 11
    in_transit_qty = 22
    putaway_pool = None


class _Coord:
    """A recording coordinator. Every `*_for` takes the ASKING leaf's manager -- that argument is
    the whole precondition for a partition, and dropping it is the failure this records."""

    def __init__(self):
        self.calls: list = []

    def _note(self, what, mgr):
        self.calls.append((what, mgr.name))

    def transit_census_for(self, mgr):
        self._note('transit_census_for', mgr)
        return [(10, 2, 2)], 5, 3

    def snapshot_for(self, mgr):
        self._note('snapshot_for', mgr)
        return (9, 8, 7, 6.0)

    def drain_records_for(self, mgr):
        self._note('drain_records_for', mgr)
        return [('site-r', mgr.name)]

    def drain_repacks_for(self, mgr):
        self._note('drain_repacks_for', mgr)
        return [('site-rp', mgr.name)]

    def dock_depth_for(self, mgr):
        self._note('dock_depth_for', mgr)
        return 99

    def open_batch(self, day):
        return 1000.0 * day, 28800.0

    def note_records(self, mgr, recv_clock):
        self._note(f'note_records:{recv_clock}', mgr)

    def bind(self, mgr, regime):
        self._note(f'bind:{regime}', mgr)


class _Pool:
    def __init__(self):
        self.workers = [_W(50), _W(51)]
        self.clocks = [0.0, 0.0]
        self.bound: list = []
        self.noted: list = []

    def bind(self, mgr, channel):
        self.bound.append((mgr.name, channel))

    def open_batch(self, day):
        return 500.0 * day, 1234.0

    def note_records(self, mgr, put_clock):
        self.noted.append((mgr.name, put_clock))


class _W:
    def __init__(self, uid):
        self.uid = uid


class _Site:
    def __init__(self, coord):
        self.coord = coord
        self.workers = [_W(60)]
        self.dock = 'THE-DOCK'
        self.transit = 'THE-YARD'


# ── the ladder ────────────────────────────────────────────────────────────────────────

def test_the_three_legal_rungs_and_their_classes():
    assert type(scope_for(None, None)) is LeafScope
    assert type(scope_for(_Pool(), None)) is PooledScope
    assert type(scope_for(_Pool(), _Site(_Coord()))) is SiteScope


def test_a_site_with_no_pool_is_refused_by_name():
    """The fourth corner. It cannot arise from the builders -- `_build_site_dock` reads
    `pool.workers[-1].uid` -- and the symptom if it ever did is a receiving crew whose uids sit
    inside the putters' block, merging two crews in one DB with no table saying so."""
    with pytest.raises(ValueError) as ei:
        scope_for(None, _Site(_Coord()))
    assert 'put pool' in str(ei.value)


def test_the_ladder_is_an_inheritance_chain_not_three_siblings():
    """Each rung is the one below it with one more group of answers moved to the site. Siblings
    would mean two copies of the put half, and the copy that was not updated would be the
    coupled inbound-off pole -- the one configuration nobody looks at."""
    assert issubclass(SiteScope, PooledScope) and issubclass(PooledScope, LeafScope)


# ── the middle rung ───────────────────────────────────────────────────────────────────

def test_the_pooled_rung_overrides_nothing_inbound():
    """THE BYTE-IDENTITY OF THE COUPLED INBOUND-OFF POLE, as a structural fact.

    That pole is supposed to answer every inbound question exactly as an uncoupled run does. It
    used to be a promise kept by twenty-eight call sites each testing the right variable
    (`site is None`, not `pool is None`). Here it is one assertion, and it fails the moment
    someone moves an inbound answer onto the put rung.
    """
    overridden = set(PooledScope.__dict__) & INBOUND_MEMBERS
    assert not overridden, (
        f'PooledScope overrides inbound member(s) {sorted(overridden)}; a coupled unit with '
        f'inbound OFF must answer them exactly as a solo leaf does')


def test_the_site_rung_overrides_nothing_in_the_put_half():
    """The mirror, and the reason the chain is ordered this way: `SiteScope` inherits the put
    half rather than restating it, so there is one pooled implementation, not two."""
    overridden = set(SiteScope.__dict__) & PUT_MEMBERS
    assert not overridden, (
        f'SiteScope re-implements put member(s) {sorted(overridden)}; the pooled answer is '
        f'inherited, and a second copy is the one that goes stale')


def test_the_site_rung_overrides_every_inbound_member():
    """NON-VACUITY for the two tests above: the partition is real only if the top rung actually
    moves all of them. A member listed in INBOUND_MEMBERS that SiteScope never overrides would
    make `test_the_pooled_rung_overrides_nothing_inbound` pass for free."""
    missing = INBOUND_MEMBERS - set(SiteScope.__dict__)
    assert not missing, (
        f'INBOUND_MEMBERS names {sorted(missing)} which SiteScope does not override -- either '
        f'it is not an inbound member or the site is not answering it')


# ── solo answers out of the manager ───────────────────────────────────────────────────

def test_solo_asks_the_manager_and_only_the_manager():
    mgr = _Mgr('store')
    s = scope_for(None, None)
    assert s.transit_census(mgr) == ([(10, 4, 2)], None, None)
    assert s.receiving_snapshot(mgr) == (1, 2, 3, 4.0)
    assert s.dock_depth(mgr) == 7
    assert s.yard_rows(mgr, 3) == ([('t', 'store')], [(3, 1, 2)])
    assert s.standing_yard(mgr) == [('standing', 'store')]
    assert s.drives_own_composition is True
    assert s.reset_put_clocks is True
    assert 'transit_snapshot' in mgr.asked and 'dock_depth' in mgr.asked


def test_solo_reads_the_lead_numbers_off_the_manager_not_the_census():
    """THE INSTANT ASYMMETRY, preserved deliberately.

    A solo leaf reads `lead_queue_depth` and `in_transit_qty` AFTER `check_reorders` has fired
    this batch's reorders; the site takes all three transit numbers off one partitioning pass
    BEFORE. So the two poles legitimately answer as of different moments, and the census value
    handed in here must be IGNORED -- resolving these at census time would silently align the
    two and move every solo run's published numbers.
    """
    mgr = _Mgr('store')
    s = scope_for(None, None)
    assert s.lead_depth(mgr, 999) == 11, 'solo took the census value; the instant moved'
    assert s.in_transit(mgr, 999) == 22


def test_solo_recv_drain_bases_on_the_later_of_the_two_clocks():
    mgr = _Mgr('store')
    recs, repacks, base = scope_for(None, None).recv_drain(
        mgr, arm_clock=300.0, recv_clock=700.0, day=2)
    assert recs == [('r', 'store')] and repacks == [('rp', 'store')]
    assert base == 700.0, 'the base must be max(arm_clock, recv_clock)'


# ── the site answers on the leaf's behalf ─────────────────────────────────────────────

def test_the_site_scope_never_consults_the_leaf_for_a_site_owned_question():
    """THE BACKSTOP TEST. The manager here refuses exactly what the real one refuses under a
    site scope, so a scope that fell through to the leaf raises rather than being quietly
    rescued by the domain-layer refusal -- which is the whole point of moving the decision out
    of `Warehouse/`."""
    mgr = _Mgr('store', site_scoped=True)
    s = scope_for(_Pool(), _Site(_Coord()))
    s.transit_census(mgr)
    s.receiving_snapshot(mgr)
    s.dock_depth(mgr)
    assert s.yard_rows(mgr, 3) == ([], [])
    assert s.standing_yard(mgr) == []
    s.recv_drain(mgr, arm_clock=1.0, recv_clock=2.0, day=1)
    assert mgr.asked == [], 'the leaf was consulted for a question the site owns'


def test_every_site_question_carries_the_ASKING_leafs_manager():
    """The precondition for a partition, and what the scope actually owns.

    Two leaves, one coordinator. Every routed call must name the leaf that asked -- a scope that
    dropped the argument, or cached one leaf's answer, would give both channels the same share
    and the totals would still close.
    """
    coord = _Coord()
    site = _Site(coord)
    pool = _Pool()
    store, fulfil = _Mgr('store', site_scoped=True), _Mgr('fulfil', site_scoped=True)
    for mgr in (store, fulfil):
        s = scope_for(pool, site)
        s.transit_census(mgr)
        s.receiving_snapshot(mgr)
        s.dock_depth(mgr)
        s.recv_drain(mgr, arm_clock=1.0, recv_clock=2.0, day=1)
        s.note_recv(mgr, 42.0)

    by_leaf: dict = {}
    for what, name in coord.calls:
        by_leaf.setdefault(name, []).append(what)
    assert set(by_leaf) == {'store', 'fulfil'}, f'a leaf never reached the coordinator: {by_leaf}'
    assert by_leaf['store'] == by_leaf['fulfil'], (
        f'the two leaves asked different questions: {by_leaf}')
    assert 'note_records:42.0' in by_leaf['store']


def test_the_site_recv_base_is_the_site_days_not_the_leafs():
    """One crew on one dock has one epoch. Basing it on a pick crew's release would idle the
    site's receivers whenever EITHER channel overran its day."""
    coord = _Coord()
    s = scope_for(_Pool(), _Site(coord))
    _r, _rp, base = s.recv_drain(_Mgr('store', site_scoped=True),
                                 arm_clock=99_999.0, recv_clock=99_999.0, day=3)
    assert base == 3000.0, 'the base came from the leaf clocks, not the site day'


def test_the_site_takes_the_lead_numbers_from_the_census():
    mgr = _Mgr('store', site_scoped=True)
    s = scope_for(_Pool(), _Site(_Coord()))
    _rows, qty, depth = s.transit_census(mgr)
    assert s.lead_depth(mgr, depth) == 3 and s.in_transit(mgr, qty) == 5


# ── the put half ──────────────────────────────────────────────────────────────────────

def test_the_put_half_moves_at_the_MIDDLE_rung_not_the_top():
    """The whole reason the ladder has three rungs: a coupled unit with inbound off already
    shares a put crew."""
    pool = _Pool()
    for s in (scope_for(pool, None), scope_for(pool, _Site(_Coord()))):
        assert s.put_workers is pool.workers
        assert s.put_clocks is pool.clocks
        assert s.reset_put_clocks is False
        assert s.put_window(day=2, day_end=None, arm_clock=0.0, put_clock=0.0) == (1000.0, 1234.0)
        assert s.put_clock_for(7.0, 900.0) == 900.0
        assert s.put_base_for_batch(900.0, 10.0, 20.0) == 900.0


def test_solo_put_keeps_its_own_clock_and_its_own_whistle():
    s = scope_for(None, None)
    assert s.put_workers is None and s.put_clocks is None
    assert s.reset_put_clocks is True
    base, deadline = s.put_window(day=2, day_end=28800.0, arm_clock=300.0, put_clock=900.0)
    assert base is None and deadline == 28800.0 - 900.0
    assert s.put_window(day=2, day_end=None, arm_clock=0.0, put_clock=0.0) == (None, None)
    assert s.put_clock_for(7.0, 900.0) == 7.0
    assert s.put_base_for_batch(900.0, 10.0, 20.0) == 20.0, 'solo takes max(batch_start, clock)'


def test_the_uid_cursor_jumps_to_the_pools_block_end_only_when_pooled():
    """A cursor left at this leaf's own block end would hand the smaller leaf receivers whose
    uids sit INSIDE the putters' block, merging two crews in one DB."""
    crews = {'store': [_W(50), _W(51)]}
    assert scope_for(None, None).put_uid_after(9, crews, 'store') == 9
    assert scope_for(_Pool(), None).put_uid_after(9, crews, 'store') == 52


def test_binding_happens_only_on_the_rung_that_has_something_to_bind():
    mgr = _Mgr('store')
    scope_for(None, None).bind_put(mgr, 'store')
    scope_for(None, None).bind_receiving(mgr, 'store')
    assert mgr.putaway_pool is None

    pool, coord = _Pool(), _Coord()
    scope_for(pool, None).bind_put(mgr, 'store')
    assert mgr.putaway_pool is pool and pool.bound == [('store', 'store')]
    scope_for(pool, None).bind_receiving(mgr, 'store')
    assert coord.calls == [], 'the middle rung bound a coordinator it does not have'

    scope_for(pool, _Site(coord)).bind_receiving(mgr, 'store')
    assert coord.calls == [('bind:store', 'store')]


# ── the object ────────────────────────────────────────────────────────────────────────

def test_every_rung_is_slotted_so_a_typo_cannot_shadow_a_member():
    for s in (scope_for(None, None), scope_for(_Pool(), None),
              scope_for(_Pool(), _Site(_Coord()))):
        with pytest.raises(AttributeError):
            s.drives_own_compositon = True          # noqa: B010  (deliberate typo)


def test_the_runner_consults_the_scope_and_nothing_else():
    """THE RATCHET. The point of this module is that `_build_leaf` stopped re-deciding the
    dispatch, so a reintroduced `asm.site is None` is the regression -- and it would work,
    which is why nothing else would catch it."""
    from Optimization.simdriver import strategy_runner as sr
    src = inspect.getsource(sr._build_leaf) + inspect.getsource(sr._build_arm)
    for banned in ('asm.site', 'asm.pool', 'site is None', 'site is not None',
                   'pool is None', 'pool is not None'):
        hits = [ln.strip() for ln in src.split('\n')
                if banned in ln and not ln.strip().startswith('#')]
        assert not hits, f'{banned!r} is back in the runner: {hits}'
