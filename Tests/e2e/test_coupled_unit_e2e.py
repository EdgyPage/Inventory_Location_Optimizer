"""test_coupled_unit_e2e.py — the COUPLED work unit through the real production seam.

Site-dock 18.  One work unit, two channel leaves, one batch loop: `_prepare_site_run` builds
the unit exactly as `_build_work_units` does under `--couple-channels`, and
`_run_strategy_worker` drives it.  What this proves, and none of it is provable from a unit
test over a hand-built payload:

  * a coupled unit RUNS end to end and writes BOTH leaves' databases, with the right
    per-channel identity in each -- the completeness rule site-dock 10 rests on;
  * the site crews are on the UNIT and on NEITHER leaf (site-dock 02 section 6, where the fix
    for the double count is a deletion).  Asserted on the PAYLOAD, because the behavioural
    half of the fix is 04's pool and this ticket does not claim it;
  * the two leaves PARTITION the catalogue.  A regime that matches nothing is a silently
    empty channel rather than an exception, so the sum is checked, and a planted double
    filter proves the check can fail.

The uncoupled comparison is deliberate and is the sharpest thing here.  It used to pin
coupled == uncoupled row for row and to say in its own docstring that 04's put pool would
break it.  Site-dock 19 landed the pool and it did: the site's putters are now fielded ONCE
over both leaves instead of once per leaf, so every absolute put number on a coupled run
moved.  What the test asserts now is the relationship that makes the move a fix — one crew,
one uid block, the same people in both DBs — and it REPORTS the size of the move rather than
pinning it.

Every test here runs under a derived staffing block and the working-day grid, because
coupling is an era feature: the site crews are derived, never declared, and a coupled unit
without them is refused.

Run:  python -m pytest Tests/e2e/test_coupled_unit_e2e.py -q
"""
from __future__ import annotations

import os
import queue
import sqlite3
import logging
import time

import pytest

from Optimization import run_simulation as rs
from Optimization.simdriver import strategy_runner as sr
import Optimization.simdriver.workunits as wu
from Warehouse.generation import generate_affinity as ga
from Optimization.persistence.Picking_Data import load_batch_stats
from Warehouse.generation.generate_inventory import (
    Family, fulfillment_family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _mixed_dbs(tmp_path):
    """A small mixed catalogue + a real affinity matrix -- the `test_channel_runner_smoke`
    fixture, which is the one every mixed e2e in this directory builds from."""
    plan = [
        Family('food', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        Family('clothing', 0.3, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        fulfillment_family(share=0.3, cube_sizes=(4, 6, 8)),
    ]
    inv = build_inventory_from_plan(num_skus=300, plan=plan, seed=1)
    inv_db = str(tmp_path / 'mixed_inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'mixed', 'num_skus': 300})
    aff_db = str(tmp_path / 'mixed_aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit(); conn.close()
    return inv_db, aff_db


def _one_arm(monkeypatch):
    """Pin BOTH channels to one restock rule: the diagonal then has exactly one pair, which
    is all this test needs and keeps the run to two arms instead of sixty-eight."""
    from Optimization.config import strategies
    monkeypatch.setitem(strategies.CHANNEL_RESTOCKS, 'store', ('fifo',))
    monkeypatch.setitem(strategies.CHANNEL_RESTOCKS, 'fulfillment', ('fifo',))
    for _ch in ('store', 'fulfillment'):
        monkeypatch.setitem(rs.CONFIG['channels'][_ch], 'restocks', ('fifo',))


#: The site put crew this fixture derives.  Two, not one: a crew of one is a serial clock
#: and would make "the pool books to whoever is free earliest" unobservable.
_PUT_CREW = 2

#: The two channels' recorded put expectations, deliberately LOPSIDED.  The shares come from
#: `derived.put.expected_utilization` (crew and day cancel), so equal values would make the
#: sub-deadlines equal and the day's division indistinguishable from a straight halving.
_PUT_EU = {'store': 0.25, 'fulfillment': 0.75}


def _era_staffing(channel_runs) -> dict:
    """A derived staffing block for this pair, minimal but CONSISTENT with the payload.

    Coupling is an era feature — the site crews are derived, never declared — so a coupled
    unit without this block is refused, and every test here goes through it.  The pickers
    are read off the channel profiles rather than written down, because
    `_check_declared_crew` compares the two and a literal here would only ever be a second
    place for the same number to rot.
    """
    return {'derived': {
        'channels': {ch.name: {'pickers': ch.picker.num_pickers} for ch, _ in channel_runs},
        'put': {'crew': _PUT_CREW, 'expected_utilization': dict(_PUT_EU)},
        # No receiving crew: `recv_crew_spec(size=0)` returns None, so no dock is
        # constructed and the leaves keep the shape every test in this file had.
        'receiving': {'crew': 0},
    }}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A prepared pair: the shared assets and the pair dir every test below runs into."""
    log = logging.getLogger('coupled-e2e'); log.setLevel(logging.ERROR)
    monkeypatch.setitem(rs.CONFIG['global'], 'n_batches', 3)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])
    # THE WORKING-DAY GRID, which the site put pool refuses to run without: one batch is one
    # site day, and a whistle blows at the end of it.  The era completes both
    # (`--releases-per-day` to 1, `--shift-drain-or-cap` forces the cut); set here directly
    # so the fixture asserts the pool's precondition rather than a whole era regime.
    monkeypatch.setitem(rs.CONFIG['global'], 'releases_per_day', 1)
    monkeypatch.setitem(rs.CONFIG['global'], 'cut_at_day_end', True)
    _one_arm(monkeypatch)

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=300, min_bins=3000, keyframe_interval=1,
        warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    assert mixed, 'the coupled unit needs a mixed catalogue'
    shared['staffing'] = _era_staffing(channel_runs)
    return dict(log=log, shared=shared, channel_runs=channel_runs, tmp=tmp_path)


def _prepare(site, name='run'):
    pair_dir = str(site['tmp'] / name / 'mixed'); os.makedirs(pair_dir, exist_ok=True)
    return wu._prepare_site_run(site['channel_runs'], True, site['shared'], pair_dir,
                                site['log'], workers=1)


def _n_arms():
    """Arms per channel under the pinned rule.  One restock RULE is more than one arm --
    `Strategy.stock_mode` splits it -- so the count is read from the registry rather than
    assumed, which is also what makes the diagonal's rank alignment meaningful."""
    from Optimization.config.strategies import strategies_for
    return len(strategies_for(('fifo',)))


# ── the unit runs, and writes both leaves ─────────────────────────────────────────

def test_a_coupled_unit_runs_and_writes_both_leaves(site):
    units, skeletons = _prepare(site)
    assert len(units) == _n_arms(), 'one unit per rank-aligned arm pair'
    ua = units[0]
    assert [lf['channel_key'] for lf in ua['leaves']] == ['store', 'fulfillment'], \
        'leaves are store-first, the declared channel order'
    assert {sk['channel'] for sk in skeletons} == {'store', 'fulfillment'}

    ua['log_queue'] = queue.Queue()
    res = sr._run_strategy_worker(ua)

    # ONE RESULT PER LEAF, and nothing at top level a one-leaf reader could mis-take for the
    # unit's own number.
    assert set(res) == {'leaves'}, res.keys()
    assert len(res['leaves']) == 2
    for leaf_args, leaf_res in zip(ua['leaves'], res['leaves']):
        db_path = leaf_args['db_path']
        assert os.path.exists(db_path)
        ch = leaf_args['channel_key']
        assert (os.sep + ch + os.sep) in db_path
        assert len(load_batch_stats(db_path, leaf_args['run_id'])) >= 1
        con = sqlite3.connect(db_path)
        row = con.execute('SELECT channel FROM simulation_runs WHERE run_id=?',
                          (leaf_args['run_id'],)).fetchone()
        con.close()
        assert row and row[0] == ch, f'{ch} leaf persisted {row!r}'
        assert leaf_res['strategy'] == leaf_args['strategy']
        assert leaf_res['cons_breaks'] == 0, 'the conservation ledger broke on a coupled leaf'


# ── the double count: the site crews are on the unit and on NEITHER leaf ──────────

def test_the_site_crews_are_on_the_unit_and_on_neither_leaf(site):
    """Site-dock 02 section 6.  `workunits.py` handed EACH leaf the whole derived site crew
    for put and receiving, so two processes fielded the site's labour twice.  The payload is
    asserted, not only the behaviour: at this commit the crews are still FIELDED per leaf
    (04's pool is what changes that), so a behavioural assertion would pass on the defect."""
    units, _ = _prepare(site)
    ua = units[0]
    for key in ('put_crew', 'recv_crew'):
        assert key in ua, f'the unit does not carry {key}'
        for lf in ua['leaves']:
            assert key not in lf, f'a leaf still carries {key} -- that IS the double count'
    # `k_pickers` is NOT a site crew: pickers really are per-channel crews, and the two
    # channels' counts differ, so a unit-scope pick crew would be the opposite error.
    assert {lf['k_pickers'] for lf in ua['leaves']}, 'leaves carry their own pick crews'
    for lf in ua['leaves']:
        assert 'k_pickers' in lf


def test_a_leaf_that_still_carries_a_site_crew_is_refused(site):
    """The guard has to fail on the defect, not merely be absent from the happy path."""
    units, _ = _prepare(site)
    ua = units[0]
    ua['leaves'][0]['put_crew'] = dict(ua['put_crew'])     # the old per-leaf payload
    ua['log_queue'] = queue.Queue()
    with pytest.raises(ValueError, match='carries.*put_crew|double count'):
        sr._run_strategy_worker(ua)


# ── the partition ────────────────────────────────────────────────────────────────

def test_the_leaves_partition_the_catalogue(site):
    """Site-dock 02 section 4.  Each leaf loads its OWN inventory, so the double filter that
    answer warned about cannot arise -- but the failure it was guarding against can: a regime
    matching nothing is a silently EMPTY leaf, not an exception."""
    units, _ = _prepare(site)
    ua = units[0]
    ua['log_queue'] = queue.Queue()
    leaves = [sr._build_leaf(la, unit=ua) for la in ua['leaves']]
    wholes = {lf.n_catalogue for lf in leaves}
    assert len(wholes) == 1, f'the leaves saw different catalogues: {wholes}'
    assert sum(lf.n_skus for lf in leaves) == next(iter(wholes))
    assert all(lf.n_skus > 0 for lf in leaves), 'a leaf fielded no SKUs at all'


def test_a_double_filtered_leaf_is_refused(site, monkeypatch):
    """The planted trap: a second regime filter over an already-filtered list, which is what
    a SHARED inventory object would have produced.  The leaf is empty, the run would complete,
    and only the sum says so."""
    units, _ = _prepare(site)
    ua = units[0]
    # Make the fulfillment leaf filter to the store regime -- the shape of "filtered twice".
    ua['leaves'][1]['channel_regime'] = ua['leaves'][0]['channel_regime']
    ua['log_queue'] = queue.Queue()
    with pytest.raises(ValueError, match='PARTITION'):
        sr._run_strategy_worker(ua)


# ── coupled is NOT uncoupled: the double count ends here ─────────────────────────

def _put_rows(db_path, run_id):
    """`(actor_uid, t_abs)` for every put row of one run, in row order."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            'SELECT actor_uid, t_abs FROM work_events WHERE run_id=? AND role=? '
            'ORDER BY rowid', (run_id, 'put')).fetchall()
    finally:
        con.close()


def test_a_coupled_unit_matches_the_two_units_it_replaces(site):
    """THE COMPARABILITY BREAK, measured rather than argued.

    This test used to pin coupled == uncoupled row for row, and said in its own docstring
    that 04's pool would make it fail.  It does.  `workunits.py` handed EACH leaf the whole
    derived site crew, so two independent processes fielded the site's labour TWICE; the
    pool fields it once, over both leaves, and every absolute put number on a coupled run
    moves as a result.

    What replaces the equality is the relationship that makes the move a FIX and not a
    regression, and it is structural rather than numeric — a numeric pin on a three-batch
    fixture would be a different claim every time the fixture moved:

      * the site fields ONE put crew, not one per leaf.  Uncoupled, the two leaves' put
        rosters sum to `2 x _PUT_CREW` people; coupled, they are the SAME people.
      * a putter's uid means the same person in BOTH channels' DBs, and sits above both
        channels' dense picker uids so no rollup joining on `actor_uid` can merge a putter
        with a picker.
      * both leaves' put queues are bound to the SAME clock list — identity, which is what
        makes "a putter busy on one stream is busy on the other" true rather than modelled.

    The size of the move is REPORTED per leaf (see the assertion messages), which is what
    the ticket asked the failure to produce.

    THIS IS THE FIRST OF TWO HALVES.  It measures the PUT pool's break (site-dock 19) under
    the fixture's default shape, which fields no dock at all: `receiving.crew` is 0, so
    `recv_crew_spec` returns None and no dock, yard or receiving crew is constructed.  The
    second half is `test_a_coupled_site_dock_matches_the_two_docks_it_replaces` below, which
    turns the standing yard on and measures the RECEIVING break (site-dock 24).  They are
    separate runs rather than one, deliberately: folding the dock into this fixture would
    change the put numbers too, and a single test reporting one move for two causes is a
    measurement nobody can attribute.
    """
    coupled_units, _ = _prepare(site, name='run_coupled')
    ua = coupled_units[0]
    ua['log_queue'] = queue.Queue()
    sr._run_strategy_worker(ua)

    # the same two arms, prepared and run the way an uncoupled run does
    solo_dir = str(site['tmp'] / 'run_solo' / 'mixed'); os.makedirs(solo_dir, exist_ok=True)
    solo = []
    for ch, cfg in site['channel_runs']:
        sa, _ = wu._prepare_channel_run(ch, cfg, True, site['shared'], solo_dir,
                                        site['log'], workers=1)
        a = sa[0]
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
        solo.append(a)

    k_max = max(lf['k_pickers'] for lf in ua['leaves'])
    block = set(range(k_max, k_max + _PUT_CREW))
    moved, coupled_actors, solo_actors = {}, {}, {}
    for leaf, alone in zip(ua['leaves'], solo):
        assert leaf['channel_key'] == alone['channel_key']
        ch = leaf['channel_key']
        cp = _put_rows(leaf['db_path'], leaf['run_id'])
        sp = _put_rows(alone['db_path'], alone['run_id'])
        # A LEAF THAT PUT NOTHING AWAY IS NOT A FAILURE HERE -- three batches is short
        # enough that a channel can genuinely fire no reorder -- but it must be the same
        # story coupled and uncoupled, or the pool changed WHETHER work happened rather
        # than who did it.
        assert bool(cp) == bool(sp), (
            f'{ch}: coupled recorded {len(cp)} put row(s) and uncoupled {len(sp)}; the '
            f'pool moves who does the work, never whether there is any')
        # ONE SITE BLOCK, THE SAME IN BOTH DBs.  Uncoupled, each leaf slices its put uids
        # off its OWN pickers, so the two channels' putters collide with each other and
        # with somebody's pickers; coupled, they are one block above both.
        assert {u for u, _ in cp} <= block, (
            f'{ch}: coupled put rows name actors outside the site block {sorted(block)}: '
            f'{sorted({u for u, _ in cp} - block)}')
        coupled_actors[ch] = {u for u, _ in cp}
        solo_actors[ch] = {u for u, _ in sp}
        c = load_batch_stats(leaf['db_path'], leaf['run_id'])
        s = load_batch_stats(alone['db_path'], alone['run_id'])
        assert len(c) == len(s) and len(c) >= 1, 'the coupled run lost a batch'
        moved[ch] = (sum(1 for bc, bs in zip(c, s) if bc != bs), len(c),
                     len(cp), len(sp))
    # ONE CREW, NAMED THE SAME WAY IN BOTH DBs.  Uncoupled, each leaf slices its put uids
    # off its own picker count, so the two channels' putters are two crews that a site-level
    # rollup joining on `actor_uid` would merge or split at random; coupled, they are the
    # same people under the same names.
    _named = [a for a in coupled_actors.values() if a]
    assert _named, f'neither leaf put anything away: {moved}'
    assert all(a == _named[0] for a in _named), (
        f'the two leaves named different putters: {coupled_actors}. The pool is ONE crew, '
        f'so both channels must stamp the same uids')
    assert len(_named[0]) <= _PUT_CREW
    # The site put crew, once.  Built here rather than read off a leaf because a `_Leaf`
    # deliberately hands nothing back -- a driver sequences leaves and does not reach into
    # one -- and this IS the number the double count doubled.
    pool = sr._build_put_pool(ua)
    assert len(pool.clocks) == _PUT_CREW, (
        f'the site put crew is {len(pool.clocks)}, not the derived {_PUT_CREW}')
    # THE MEASUREMENT, reported rather than pinned: what the break cost, per leaf.
    print(f'\n  coupled vs uncoupled -- batches differing/total, put rows coupled/solo: '
          f'{moved}\n  put actors coupled={coupled_actors} solo={solo_actors}')


# ── the site dock: one dock, one crew, one yard, where two of each stood ─────────

def _recv_rows(db_path, run_id, role='receive'):
    """`(actor_uid, t_abs, duration)` for every receive row of one run, in row order."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            'SELECT actor_uid, t_abs, duration FROM work_events WHERE run_id=? AND role=? '
            'ORDER BY rowid', (run_id, role)).fetchall()
    finally:
        con.close()


def _table_rows(db_path, table, run_id):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id=?',
                           (run_id,)).fetchone()[0]
    finally:
        con.close()


def _standing_yard(monkeypatch, site, crew=2):
    """The standing-yard shape both halves of the dock measurement run under."""
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_dock_doors', 2)
    site['shared']['staffing']['derived']['receiving']['crew'] = crew
    return crew


def test_a_coupled_site_dock_matches_the_two_docks_it_replaces(site, monkeypatch):
    """THE SECOND COMPARABILITY BREAK, measured rather than argued — site-dock 24.

    The first half above measured the PUT pool's move.  This is the receiving half, and the
    cause is the same deletion seen from the other side: `workunits.py` handed EACH leaf the
    whole derived RECEIVING crew, so two independent processes fielded the site's dock twice
    — two docks, two yards, two crews of `crew` under one site that derives one crew of
    `crew`.  A coupled unit now fields ONE of each, and every absolute receiving number on a
    coupled run moves as a result.

    What is asserted is again the relationship that makes the move a FIX:

      * ONE dock, ONE yard and ONE receiving crew for the site, where uncoupled there were
        two of each — so the site's receivers are `crew` people rather than `2 x crew`;
      * a receiver's uid means the same person in BOTH channels' DBs and sits above the PUT
        POOL's block, which itself sits above both channels' dense picker uids — so no
        rollup joining on `actor_uid` can merge a receiver with a putter or a picker;
      * the trailer- and door-denominated rows go to the SITE's own DB and to NEITHER leaf,
        which is the artifact ADR-0005 declares and what stops one channel's yard scorecard
        being rendered over the whole site's trailers while the other renders empty;
      * THE PRICE LIST IS THE TWO CONSTANTS THAT ALREADY EXISTED (site-dock 27): the site
        dock's per-regime entries are each channel's own `UnloadCost`, equal field for field
        to the one an uncoupled leaf builds for itself — which is why the pricing decision
        costs no comparability break of its own and this one comes wholly from FIELDING one
        dock where two stood.

    The size of the move is REPORTED per leaf.  A three-batch fixture cannot honestly PIN a
    receiving number (site-dock 19's finding about this same fixture), so the numeric claim
    lives outside the suite and what runs here is the structure.
    """
    crew = _standing_yard(monkeypatch, site)
    coupled_units, _ = _prepare(site, name='run_dock_coupled')
    ua = coupled_units[0]
    ua['log_queue'] = queue.Queue()
    sr._run_strategy_worker(ua)

    # the same two arms, prepared and run the way an uncoupled run does: two docks, two
    # yards, two crews of `crew` for the one site crew the record derives.
    solo_dir = str(site['tmp'] / 'run_dock_solo' / 'mixed'); os.makedirs(solo_dir, exist_ok=True)
    solo = []
    for ch, cfg in site['channel_runs']:
        sa, _ = wu._prepare_channel_run(ch, cfg, True, site['shared'], solo_dir,
                                        site['log'], workers=1)
        a = sa[0]
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
        solo.append(a)

    # ── one dock, one crew, one price list ───────────────────────────────────
    pool = sr._build_put_pool(ua)
    dock = sr._build_site_dock(ua, pool, site['log'])
    assert dock is not None, 'the coupled unit fielded no site dock under a standing yard'
    assert dock.dock.crew_size == crew, (
        f'the site dock crew is {dock.dock.crew_size}, not the derived {crew}; uncoupled '
        f'this site fields {2 * crew} receivers for a record that derives {crew}')
    assert sorted(dock.dock.costs) == ['fulfillment', 'store'], dock.dock.costs
    assert dock.dock.cost is None, (
        'a site dock holds a price LIST and no single price; one of the two would silently '
        'win and which one decides every receiving second on the run')
    # THE TWO ENTRIES ARE THE TWO CONSTANTS AN UNCOUPLED LEAF ALREADY CHARGES, field for
    # field — the pricing decision costs no break, and this is the assertion that says so.
    for alone in solo:
        want = sr._site_unload_cost(alone)
        assert dock.dock.cost_for(alone['channel_regime']) == want, (
            f"the site dock prices {alone['channel_regime']} differently from the dock "
            f"that channel builds for itself")
    # THE UID BLOCK chains off the POOL's end, which is above BOTH channels' pickers.
    first_uid = dock.workers[0].uid
    assert first_uid == pool.workers[-1].uid + 1, (
        f'the receiving block starts at {first_uid}, not at the put pool block end '
        f'{pool.workers[-1].uid + 1}; the smaller leaf receivers would land inside the '
        f'putters block')

    # ── the rows: the site DB carries the yard, and neither leaf does ────────
    site_db = ua['site_db']
    assert os.path.exists(site_db), (
        f'a coupled standing run wrote no site DB at {site_db}; the trailer and drain rows '
        f'belong to neither leaf (ADR-0005) and would otherwise be lost')
    con = sqlite3.connect(site_db)
    try:
        site_run = con.execute('SELECT run_id FROM simulation_runs').fetchone()[0]
        n_trailers = con.execute('SELECT COUNT(*) FROM yard_trailers').fetchone()[0]
        n_drains = con.execute('SELECT COUNT(*) FROM yard_drains').fetchone()[0]
    finally:
        con.close()
    assert n_trailers or n_drains, (
        'the site DB holds no yard rows at all, so every assertion below about WHERE they '
        'went would pass over an empty table')

    moved, coupled_actors, solo_actors = {}, {}, {}
    for leaf, alone in zip(ua['leaves'], solo):
        ch = leaf['channel_key']
        assert ch == alone['channel_key']
        # NEITHER LEAF CARRIES A YARD ROW.  Uncoupled each leaf has its own; coupled they
        # are the site's, and a leaf holding one would be a site number wearing one
        # channel's name.
        assert _table_rows(leaf['db_path'], 'yard_trailers', leaf['run_id']) == 0, (
            f'{ch}: a coupled leaf recorded trailer rows; a trailer load is MIXED, so a '
            f'per-leaf trailer table double-counts the site yard across the pair')
        assert _table_rows(leaf['db_path'], 'yard_drains', leaf['run_id']) == 0, \
            f'{ch}: a coupled leaf recorded yard drain rows'
        assert _table_rows(alone['db_path'], 'yard_trailers', alone['run_id']) >= 0
        cr = _recv_rows(leaf['db_path'], leaf['run_id'])
        sr_ = _recv_rows(alone['db_path'], alone['run_id'])
        coupled_actors[ch] = {u for u, _t, _d in cr}
        solo_actors[ch] = {u for u, _t, _d in sr_}
        moved[ch] = (len(cr), len(sr_),
                     round(sum(d for _u, _t, d in cr), 1),
                     round(sum(d for _u, _t, d in sr_), 1))
    block = set(range(first_uid, first_uid + crew))
    for ch, actors in coupled_actors.items():
        assert actors <= block, (
            f'{ch}: coupled receive rows name actors outside the site block '
            f'{sorted(block)}: {sorted(actors - block)}')
    _named = [a for a in coupled_actors.values() if a]
    assert _named, f'neither leaf received anything: {moved}'
    assert all(a == _named[0] for a in _named), (
        f'the two leaves named different receivers: {coupled_actors}. One dock is ONE crew, '
        f'so both channels must stamp the same uids')
    print(f'\n  [site dock] coupled vs uncoupled -- receive rows c/u, seconds c/u: {moved}'
          f'\n  receive actors coupled={coupled_actors} solo={solo_actors}'
          f'\n  site DB run {site_run}: {n_trailers} trailer row(s), {n_drains} drain row(s)')


# ── the cross-leaf reconciliation: the site's own total against the two leaves ───

def test_a_coupled_pair_reconciles_across_both_leaves_and_the_site_dock(site, monkeypatch,
                                                                       tmp_path):
    """SITE-DOCK 25, through the production seam rather than over hand-built DBs.

    `Tests/unit/test_receiving_report_pair.py` pins every clause against planted defects on
    synthetic files. What it cannot show is that a REAL coupled unit produces the three
    surfaces the clauses read: the site dock's own per-batch totals in `site_receiving`, the
    two leaves' receive rows partitioned by SKU, and a uid layout where the site crews sit
    above both channels' pickers. Site-dock 24's worst defect was invisible to its own
    review and to 110 tests and showed only when the stage was driven end to end, which is
    why this test exists beside those.

    THE CLOSURE IS THE POINT. The site total accrues at the dock (`Dock.charge`) and the
    leaves' rows carry per-row durations stamped from drained records — two accumulators on
    different code — so their agreement is evidence. A planted leak proves it can fail.
    """
    import shutil

    from Diagnostics.receiving_report import reconcile_pair

    _standing_yard(monkeypatch, site)
    units, _ = _prepare(site, name='run_reconcile')
    ua = units[0]
    ua['log_queue'] = queue.Queue()
    sr._run_strategy_worker(ua)

    site_db = ua['site_db']
    assert os.path.exists(site_db), 'the coupled unit wrote no site DB'
    con = sqlite3.connect(site_db)
    try:
        totals = con.execute('SELECT batch, recv_unloaded, recv_seconds '
                             'FROM site_receiving ORDER BY batch').fetchall()
    finally:
        con.close()
    # THE EVIDENCE, COUNTED BEFORE THE VERDICT IS TRUSTED: an empty table closes against
    # two empty leaves, and every clause below would pass over nothing.
    assert totals, 'the site DB holds no per-batch dock totals'
    assert sum(s for _b, _u, s in totals) > 0.0, 'the site dock charged no labour'

    leaf_dbs = [lf['db_path'] for lf in ua['leaves']]
    r = reconcile_pair(leaf_dbs, site_db)
    assert r['verdict'] == 'PASS', r
    assert r['active'], 'nothing was received; the reconciliation proves nothing'
    assert r['site_batches'] == len(totals)
    assert r['leaf_receive_seconds'] > 0.0
    assert r['checks']['site_seconds_close'] and r['checks']['site_counts_close']
    # The uid clauses had a subject: the site crews really are above both pick blocks, and
    # the floor is the LARGER channel's picker count, which is the whole of site-dock 19.
    assert r['site_uid_floor'] == max(lf['k_pickers'] for lf in ua['leaves'])
    assert r['checks']['site_uids_above_both_pick_blocks']
    assert r['checks']['no_uid_is_site_and_channel']

    # ── and it can FAIL: one receive row dropped from a copy of one leaf ──────
    cp = str(tmp_path / 'leak.db')
    which = next(d for d in leaf_dbs
                 if sqlite3.connect(d).execute(
                     "SELECT COUNT(*) FROM work_events WHERE role='receive'").fetchone()[0])
    shutil.copy(which, cp)
    con = sqlite3.connect(cp)
    try:
        cur = con.execute("DELETE FROM work_events WHERE rowid = "
                          "(SELECT MIN(rowid) FROM work_events WHERE role='receive')")
        assert cur.rowcount == 1, 'the leak changed nothing'
        con.commit()
    finally:
        con.close()
    leaked = reconcile_pair([cp if d is which else d for d in leaf_dbs], site_db)
    assert leaked['verdict'] == 'FAIL', leaked
    assert leaked['checks']['site_seconds_close'] is False
    print(f"\n  [reconcile] site batches={r['site_batches']} "
          f"site secs={r['site_receive_seconds']:,.3f} "
          f"leaf secs={r['leaf_receive_seconds']:,.3f} "
          f"per leaf={ {c: lf['receive_rows'] for c, lf in r['leaves'].items()} }")


# ── the site gain bundle: one provider, two owners, both transits ────────────────

def test_a_coupled_unit_binds_both_arms_into_one_site_gain_bundle(site, monkeypatch):
    """Site-dock 26.  One shared yard will carry MIXED trailers, and a mixed trailer's
    store units must be priced by the store arm's pool and its fulfillment units by the
    fulfillment arm's — so the two leaves' transits hold ONE provider with two owners in
    it, not a bundle each.

    Driven through the whole production seam — `_run_strategy_worker` on the real payload,
    not a hand-assembled leaf — because every link in the chain is a place the composite
    can go missing: the unit may not build one, the builder may not be handed it, the leaf
    may hang its own bundle instead.  Asserted on the production objects, which the unit
    tier cannot reach at all: a `_Leaf` deliberately hands nothing back, so the transits
    are captured at construction (a `YardTransit` carries `__slots__`, so the spy is on
    the CLASS — site-dock 20's finding, the same one that made an instance spy raise).

    What is claimed here is the WIRING — two calls, two owners, one provider on both
    transits.  A three-batch fixture cannot honestly claim more (site-dock 19's finding:
    it produces one store put row and no fulfillment ones, so a pricing claim made on it
    would be vacuous).  The PRICING — a mixed load, two adapters, and the commensurability
    of the two channels' hours — is `Tests/unit/test_gain_plan.py` section 10.
    """
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_dock_doors', 2)
    monkeypatch.setitem(g, 'inbound_yard_policy', 'gain_myopic')
    monkeypatch.setitem(g, 'inbound_dock_policy', 'gain_myopic')
    # A RECEIVING CREW, because the gain arms only ever run under the standing yard and
    # the standing drain needs a dock.  Derived, like every site crew under the era.
    site['shared']['staffing']['derived']['receiving']['crew'] = 2

    units, _ = _prepare(site, name='run_yard')
    ua = units[0]
    ua['log_queue'] = queue.Queue()
    assert [lf['inbound']['yard_policy'] for lf in ua['leaves']] == \
        ['gain_myopic', 'gain_myopic'], 'both leaves must name the gain arm'

    built, made_site = [], []
    real_bundle_for, real_site_gain = sr._gain_bundle_for, sr._build_site_gain

    def _spy_bundle_for(*a, **kw):
        got = real_bundle_for(*a, **kw)
        built.append(got)
        return got
    monkeypatch.setattr(sr, '_gain_bundle_for', _spy_bundle_for)

    def _spy_site_gain(*a, **kw):
        got = real_site_gain(*a, **kw)
        made_site.append(got)
        return got
    monkeypatch.setattr(sr, '_build_site_gain', _spy_site_gain)

    transits = []

    class _SpyYard(sr._YardTransit):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            transits.append(self)
    monkeypatch.setattr(sr, '_YardTransit', _SpyYard)

    res = sr._run_strategy_worker(ua)
    assert len(res['leaves']) == 2, 'the coupled unit did not run both leaves'

    assert len(made_site) == 1 and made_site[0] is not None, (
        f'the unit built {len(made_site)} site gain bundle(s); it is built ONCE at unit '
        f'scope, because the first leaf\'s transit needs the object the second leaf '
        f'binds into')
    site_gain = made_site[0]
    # THE SECOND `_gain_bundle_for` CALL, which is the whole ticket at this seam: the
    # builder is unchanged and called once per leaf, with that leaf's own machinery.
    assert len(built) == 2, f'{len(built)} bundle(s) built for two leaves'
    assert site_gain.owners == ('store', 'fulfillment'), site_gain.owners
    for regime, made in zip(site_gain.owners, built):
        assert site_gain._owners[regime] is made, (
            f'the {regime} owner is not the object `_gain_bundle_for` returned for that '
            f'leaf — faithful-to-arm is structural here, so a copy or a rebuild is the '
            f'defect itself')
    # ONE PROVIDER, ON BOTH TRANSITS.  A bundle each is the shape that cannot survive the
    # one shared yard (site-dock 24), and it would look identical until a trailer mixed.
    # ONE standing yard for the whole site (site-dock 24).  This asserted TWO until the
    # site dock landed, and the comment beside it already said why two could not survive:
    # a mixed trailer needs one yard, and two would tick the SUPPLIER lead queue twice.
    assert len(transits) == 1, f'{len(transits)} standing yard(s) for one site'
    for tr in transits:
        assert tr.gain_bundle is site_gain, (
            'a leaf hung its own bundle on its transit; one site is one gain provider')


# ── the timer laps partition each leaf's OWN work (2026-09-18) ────────────────────
#
# `SectionTimers` is a lap counter: `start()` sets a cursor, `split(s)` charges the time
# since the cursor to `s` and moves it.  A coupled unit interleaves two leaves and a site
# drive through ONE loop, and until 2026-09-18 each leaf opened its lap in `_replenish` and
# did not close it until its own `split('reord')` in `_step`.  Between the two lay the
# sibling's replenish, the site drive, and -- for the second leaf -- the first leaf's ENTIRE
# step.  So `reord_s` carried the drive on both leaves and the first leaf's whole batch on
# the second, and `Sum(total_s) - Sum(sections)` (the calltree ladder's batch-loop residual)
# read nonsense on exactly the run shape the phase-2 campaign publishes `reord_s` from.
#
# The invariant that names the defect: A LAP IS PRIVATE.  No other timers instance records
# a lap event between a lap's opening and its closing split.  The site drive is charged, not
# lapped, to every leaf by the driver: once per batch, the same seconds to each.


class _RecordingTimers(sr.SectionTimers):
    """`SectionTimers` that logs every lap event with its instance id and the clock.

    Subclassing a `__slots__` class adds a `__dict__`; nothing here is pickled, since the
    unit runs in-process under `_run_strategy_worker`.
    """
    EVENTS: list = []                                   # (timers_id, kind, sections, value)

    def start(self, now=None):
        t = time.perf_counter() if now is None else now
        type(self).EVENTS.append((id(self), 'start', (), t))
        super().start(now=t)

    def split(self, *sections, now=None):
        t = time.perf_counter() if now is None else now
        type(self).EVENTS.append((id(self), 'split', tuple(sections), t))
        return super().split(*sections, now=t)

    def add(self, section, seconds):
        type(self).EVENTS.append((id(self), 'add', (section,), seconds))
        super().add(section, seconds)


def _intruded_laps(events):
    """Every charged lap inside which another timers instance recorded a lap event.

    A lap opens at a `start` or a `split` (both set the cursor) and is CHARGED by the next
    `split` on the same instance; a lap that ends in `start` charges nothing and is not a
    lap.  `add` events never move a cursor and are ignored on both sides.  Returns
    `(owner, sections_charged, intruders)` triples; empty means every lap was private.
    """
    bad = []
    opened = {}
    for k, (tid, kind, secs, _v) in enumerate(events):
        if kind == 'add':
            continue
        if kind == 'split' and tid in opened:
            j = opened[tid]
            intruders = sorted({e[0] for e in events[j + 1:k]
                                if e[0] != tid and e[1] in ('start', 'split')})
            if intruders:
                bad.append((tid, secs, intruders))
        opened[tid] = k
    return bad


def _synthetic(fixed: bool):
    """Two leaves, one batch, in the pre-fix and post-fix choreographies.  The clock values
    are labels: only ORDER matters to `_intruded_laps`."""
    A, B = 1, 2

    def step(who, t):
        return [(who, 'split', ('reord',), t), (who, 'split', ('sim',), t + 1),
                (who, 'split', ('inv',), t + 2)]
    if not fixed:
        return ([(A, 'start', (), 0), (B, 'start', (), 1)]           # both laps open ...
                + step(A, 10) + step(B, 20))                         # ... across everything
    return ([(A, 'start', (), 0), (A, 'split', ('reord',), 1),
             (B, 'start', (), 2), (B, 'split', ('reord',), 3),
             (A, 'add', ('reord',), 0.5), (B, 'add', ('reord',), 0.5),
             (A, 'start', (), 10)] + step(A, 11)
            + [(B, 'start', (), 20)] + step(B, 21))


def test_the_lap_checker_convicts_the_old_choreography_and_clears_the_new():
    """NON-VACUITY for the measurement below: the invariant must fail on the defect it
    names, and the worse half of it -- the second leaf's reord lap swallowing the first
    leaf's whole step -- must be among the convictions."""
    old = _intruded_laps(_synthetic(fixed=False))
    assert old, 'the pre-fix choreography must show an intruded lap'
    assert any(owner == 2 and secs == ('reord',) and 1 in intr
               for owner, secs, intr in old), old
    assert _intruded_laps(_synthetic(fixed=True)) == []


def test_a_coupled_leafs_laps_are_private_and_the_drive_is_charged_once_per_batch(
        site, monkeypatch):
    """THE MEASUREMENT.  A site-docked coupled unit through the real seam, with the timers
    recording: no leaf's lap contains another leaf's lap event, every leaf receives the
    site drive as one `add('reord', s)` per batch, both leaves receive the same seconds,
    and each leaf's `t_reord` result is at least its drive charges and at most its elapsed.
    """
    _standing_yard(monkeypatch, site)
    monkeypatch.setattr(sr, 'SectionTimers', _RecordingTimers)
    _RecordingTimers.EVENTS = []
    coupled_units, _ = _prepare(site, name='run_timer_laps')
    ua = coupled_units[0]
    ua['log_queue'] = queue.Queue()
    res = sr._run_strategy_worker(ua)
    ev = _RecordingTimers.EVENTS
    n_batches = rs.CONFIG['global']['n_batches']

    # Leaf order is first-appearance order: the first leaf replenishes first on batch 0.
    owners = list(dict.fromkeys(e[0] for e in ev))
    assert len(owners) == 2, f'expected one timers per leaf, saw {len(owners)}'
    intruded = _intruded_laps(ev)
    assert intruded == [], (
        "a leaf lap contained another leaf's lap event -- the drive or the sibling's work "
        f'is being charged to a leaf section: {intruded[:3]}')

    drive = {o: [v for (t, k, s, v) in ev if t == o and k == 'add' and s == ('reord',)]
             for o in owners}
    for o in owners:
        assert len(drive[o]) == n_batches, (o, len(drive[o]), n_batches)
        assert all(v > 0.0 for v in drive[o]), drive[o]
    a, b = owners
    assert drive[a] == drive[b], 'the drive is ONE measurement handed to every leaf'

    # `res['leaves']` is positional with the unit's leaves, and the timers were built in
    # that order.  `t_reord` carries the drive plus the leaf's own laps, never more than the
    # leaf lived.
    assert len(res['leaves']) == 2
    for o, leaf_res in zip(owners, res['leaves']):
        assert leaf_res['t_reord'] >= sum(drive[o]) - 1e-9, (leaf_res['t_reord'], sum(drive[o]))
        assert leaf_res['t_reord'] <= leaf_res['elapsed'], leaf_res
