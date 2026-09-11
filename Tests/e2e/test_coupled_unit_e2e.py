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

The uncoupled comparison is deliberate and is the sharpest thing here: at this commit a
coupled unit is byte-identical to the two independent units it replaces (the site crews are
still FIELDED per leaf -- see the ticket's answer), so the two leaves' batch stats must match
row for row.  When 04's put pool and 01's owner routing land, THIS is the test that will say
so, by failing in a way that names the leaf whose labour moved.

Run:  python -m pytest Tests/e2e/test_coupled_unit_e2e.py -q
"""
from __future__ import annotations

import os
import queue
import sqlite3
import logging

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


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A prepared pair: the shared assets and the pair dir every test below runs into."""
    log = logging.getLogger('coupled-e2e'); log.setLevel(logging.ERROR)
    monkeypatch.setitem(rs.CONFIG['global'], 'n_batches', 3)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])
    _one_arm(monkeypatch)

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / 'mixed'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=300, min_bins=3000, keyframe_interval=1,
        warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    assert mixed, 'the coupled unit needs a mixed catalogue'
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


# ── coupled is byte-identical to uncoupled, AT THIS COMMIT ───────────────────────

def test_a_coupled_unit_matches_the_two_units_it_replaces(site):
    """The site crews are DECLARED once here but still FIELDED per leaf -- 04's pool is what
    makes them one crew, and this ticket does not claim it (see the ticket's answer on the
    ordering).  So a coupled unit must reproduce, row for row, the two independent units it
    replaces.  When the pool lands this test FAILS, and the leaf whose labour moved is named
    in the failure -- which is the point of pinning it now."""
    coupled_units, _ = _prepare(site, name='run_coupled')
    ua = coupled_units[0]
    ua['log_queue'] = queue.Queue()
    sr._run_strategy_worker(ua)

    # the same two arms, prepared and run the way every run does today
    solo_dir = str(site['tmp'] / 'run_solo' / 'mixed'); os.makedirs(solo_dir, exist_ok=True)
    solo = []
    for ch, cfg in site['channel_runs']:
        sa, _ = wu._prepare_channel_run(ch, cfg, True, site['shared'], solo_dir,
                                        site['log'], workers=1)
        a = sa[0]
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
        solo.append(a)

    for leaf, alone in zip(ua['leaves'], solo):
        assert leaf['channel_key'] == alone['channel_key']
        c = load_batch_stats(leaf['db_path'], leaf['run_id'])
        s = load_batch_stats(alone['db_path'], alone['run_id'])
        assert len(c) == len(s) and len(c) >= 1
        for bc, bs in zip(c, s):
            assert bc == bs, (f"{leaf['channel_key']}: coupled and uncoupled disagree on "
                              f'batch stats -- the coupling moved this leaf')
